"""Opening hours from OpenStreetMap: find the OSM place at each shop's spot, and keep its `opening_hours`.

The GPX only gives a POI's name and position, so a POI is matched to the OSM element within `match_m` of it whose
name is closest. onroutemap.de builds its POIs from OSM, so names usually line up.

Overpass answers in one request per chunk of POIs when asked only for elements that have `opening_hours`; its
public servers are often overloaded, so a mirror is tried next, then Nominatim, slow (1 request/s) but steady.
Answers are cached on disk: a later render of the same route needs no network.
"""

from __future__ import annotations

import difflib
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .osm import M_PER_DEG, TIMEOUT_S, JsonCache, Report, cache_path, distance_m, nominatim, one_by_one, overpass
from .osm import http as _http
from .pois import norm

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Poi
    from .osm import Http

log = logging.getLogger(__name__)

type Answer = Callable[[Poi, list[Place], str], None]  # matches a POI among places found by a source

CHUNK = 50  # POIs per Overpass request; Overpass is told a little less than TIMEOUT_S, so it gives up before we do
MIN_SIMILARITY = 0.6
CACHE_VERSION = 2  # 2: places carry their kind
KIND_KEYS = ("amenity", "shop", "craft")  # in this order: a petrol station is amenity=fuel, with shop=gas


@dataclass(frozen=True)
class Place:
    """An OSM element that lists opening hours."""

    osm_id: str  # "node/123", "way/456"
    name: str
    lat: float
    lon: float
    hours: str
    kind: str = ""  # its main tag, "shop=supermarket", "amenity=fuel"; "" if it has none of KIND_KEYS


def _kind(tags: dict[str, str]) -> str:
    key = next((k for k in KIND_KEYS if tags.get(k)), None)
    return f"{key}={tags[key].split(';')[0].strip()}" if key else ""


def _similarity(a: str, b: str) -> float:
    a, b = norm(a).strip(), norm(b).strip()
    if not a or not b:
        return 0.0
    if a in b or b in a:  # "Intermarché" / "Intermarché Super"
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match(poi: Poi, places: list[Place], match_m: float, kinds: list[str] | None = None) -> Place | None:
    """The place within `match_m` of `poi` whose name is closest to the POI's (its type, if unnamed).

    With `kinds`, only a place of one of those kinds counts: a supermarket is not the petrol station of the same
    name next to it.
    """
    name = poi.name or poi.type
    scored = [
        (_similarity(name, p.name), -d, p)
        for p in places
        if (d := distance_m(poi.lat, poi.lon, p.lat, p.lon)) <= match_m and (kinds is None or p.kind in kinds)
    ]
    best = max(scored, key=lambda s: s[:2], default=None)
    return best[2] if best and best[0] >= MIN_SIMILARITY else None


def _overpass_query(pois: list[Poi], match_m: float) -> str:
    around = "".join(f'nwr["opening_hours"](around:{match_m:.0f},{p.lat:.6f},{p.lon:.6f});' for p in pois)
    return f"[out:json][timeout:{TIMEOUT_S - 10}];({around});out tags center;"


def _from_overpass(element: dict[str, Any]) -> Place:
    where = element.get("center", element)
    tags = element["tags"]
    osm_id = f"{element["type"]}/{element["id"]}"
    return Place(osm_id, tags.get("name", ""), where["lat"], where["lon"], tags["opening_hours"], _kind(tags))


def _nominatim(poi: Poi, match_m: float, http: Http, sleep: Callable[[float], None]) -> list[Place]:
    """Places with hours named like `poi` and around it, from Nominatim; raises OSError if it does not answer."""
    d = match_m / M_PER_DEG * 1.5  # a box a little larger than the matching circle; match() trims it
    params = {
        "q": poi.name or poi.type,
        "format": "jsonv2",
        "extratags": "1",
        "bounded": "1",
        "limit": "5",
        "viewbox": f"{poi.lon - d},{poi.lat + d},{poi.lon + d},{poi.lat - d}",
    }
    return nominatim("search", params, http, sleep, _from_nominatim)


def _from_nominatim(results: list[dict[str, Any]]) -> list[Place]:
    return [
        Place(
            f"{r["osm_type"]}/{r["osm_id"]}",
            r.get("name", ""),
            float(r["lat"]),
            float(r["lon"]),
            hours,
            f"{r["category"]}={r["type"]}" if r.get("category") in KIND_KEYS else "",  # Nominatim's main tag
        )
        for r in results
        if (hours := (r.get("extratags") or {}).get("opening_hours"))
    ]


class Cache:
    """Matches already looked up, keyed by POI position and name: the OSM place found, or none."""

    def __init__(self, path: Path, max_age_days: float | None) -> None:
        self.store = JsonCache(path, CACHE_VERSION, max_age_days, field="place")

    @staticmethod
    def key(poi: Poi) -> str:
        return f"{poi.lat:.5f},{poi.lon:.5f},{norm(poi.name or poi.type)}"

    def get(self, poi: Poi) -> tuple[bool, Place | None]:
        """(known, place): known is False when the POI was never looked up, or too long ago."""
        known, place = self.store.get(self.key(poi))
        return known, Place(**place) if place else None

    def put(self, poi: Poi, place: Place | None) -> None:
        self.store.put(self.key(poi), place and vars(place))

    def save(self) -> None:
        self.store.save()


def default_cache_path() -> Path:
    return cache_path("opening_hours.json")


def lookup(
    pois: list[Poi],
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Http = _http,
    sleep: Callable[[float], None] = time.sleep,
    offline: bool = False,
) -> Report:
    """Set `opening_hours` and `osm_id` on the POIs of the `cfg["categories"]` that OSM knows hours for.

    Never raises for a network problem: POIs left unanswered keep no hours, and the report says so. Offline, only
    the cache answers, whatever the age of its answers, and the report counts the POIs it leaves unasked.
    """
    started = time.perf_counter()
    match_m = cfg["match_m"]
    wanted = [p for p in pois if p.category in cfg["categories"]]
    log.info("Opening hours: %d shops to look up", len(wanted))
    cache = Cache(cache_path or default_cache_path(), None if offline else cfg["max_age_days"])
    report = Report(asked=len(wanted))

    todo: list[Poi] = []
    for p in wanted:
        known, place = cache.get(p)
        if known:
            report.cached += 1
            _apply(p, place)
            _log_match(p, place, "cached")
        else:
            todo.append(p)
    if offline:
        report.unasked, todo = len(todo), []

    def answer(p: Poi, places: list[Place], source: str) -> None:
        place = match(p, places, match_m, cfg["kinds"].get(p.category or ""))
        cache.put(p, place)
        _apply(p, place)
        _log_match(p, place, source)
        report.answered_by(source)

    if todo:
        log.info("Opening hours: %d cached; asking Overpass about the other %d", report.cached, len(todo))
    left = _by_overpass(todo, match_m, http, answer)
    if left:
        log.info("Opening hours: asking Nominatim about %d shops, one a second", len(left))
    report.failed = one_by_one(
        left, lambda p: _nominatim(p, match_m, http, sleep), lambda p, places: answer(p, places, "Nominatim")
    )
    cache.save()
    report.found = sum(1 for p in wanted if p.opening_hours)
    if todo:
        log.info("Opening hours: looked up in %.1f s", time.perf_counter() - started)
    return report


def _log_match(p: Poi, place: Place | None, how: str) -> None:
    if place is None:
        log.debug("km %.1f %s: no hours found (%s)", p.km, p.name or p.type, how)
    else:
        log.debug("km %.1f %s: %s %r, %s (%s)", p.km, p.name or p.type, place.osm_id, place.name, place.hours, how)


def _by_overpass(todo: list[Poi], match_m: float, http: Http, answer: Answer) -> list[Poi]:
    """Answer the POIs from Overpass, a chunk at a time; returns the ones left once it stops answering."""
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i : i + CHUNK]
        try:
            places = overpass(_overpass_query(chunk, match_m), http, _from_overpass)
        except OSError:
            return todo[i:]  # it failed on every server: don't wait for it again
        for p in chunk:
            answer(p, places, "Overpass")
    return []


def _apply(poi: Poi, place: Place | None) -> None:
    poi.opening_hours = place.hours if place else None
    poi.osm_id = place.osm_id if place else None
