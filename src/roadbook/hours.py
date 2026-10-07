"""Opening hours from OpenStreetMap: find the OSM place at each shop's spot, and keep its `opening_hours`.

The GPX only gives a POI's name and position, so a POI is matched to the OSM element within `match_m` of it whose
name is closest. onroutemap.de builds its POIs from OSM, so names usually line up.

Overpass answers in one request per chunk of POIs when asked only for elements that have `opening_hours`; its
public servers are often overloaded, so a mirror is tried next, then Nominatim, slow (1 request/s) but steady.
Answers are cached on disk: a later render of the same route needs no network.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from platformdirs import user_cache_path

from .pois import _norm

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Poi

    Answer = Callable[[Poi, list["Place"], str], None]  # matches a POI among places found by a source

OVERPASS = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"gpx2roadbook/{version('gpx2roadbook')} (+https://github.com/bnogaro/gpx2roadbook)"
CHUNK = 50  # POIs per Overpass request
TIMEOUT_S = 60  # per request; Overpass is told a little less, so it gives up before we do
NOMINATIM_EVERY_S = 1.1  # its usage policy: at most one request per second
NOMINATIM_GIVE_UP = 3  # consecutive failures
MIN_SIMILARITY = 0.6
CACHE_VERSION = 2  # 2: places carry their kind
KIND_KEYS = ("amenity", "shop", "craft")  # in this order: a petrol station is amenity=fuel, with shop=gas


def _http(url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401  parsed JSON
    """GET `url`, or POST `data` as a form; raises OSError (URLError, timeouts) or ValueError (bad JSON)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})  # noqa: S310  fixed https URLs
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
        return json.load(resp)


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


@dataclass
class Report:
    """What the lookup did, for one line of output."""

    asked: int = 0  # POIs whose category gets hours
    found: int = 0
    cached: int = 0  # of `asked`, answered from the cache
    sources: list[str] = field(default_factory=list)
    failed: bool = False  # some POIs could not be looked up at all


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def _similarity(a: str, b: str) -> float:
    a, b = _norm(a).strip(), _norm(b).strip()
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
        (_similarity(name, p.name), -_distance_m(poi.lat, poi.lon, p.lat, p.lon), p)
        for p in places
        if _distance_m(poi.lat, poi.lon, p.lat, p.lon) <= match_m and (kinds is None or p.kind in kinds)
    ]
    best = max(scored, key=lambda s: s[:2], default=None)
    return best[2] if best and best[0] >= MIN_SIMILARITY else None


def _overpass_query(pois: list[Poi], match_m: float) -> str:
    around = "".join(f'nwr["opening_hours"](around:{match_m:.0f},{p.lat:.6f},{p.lon:.6f});' for p in pois)
    return f"[out:json][timeout:{TIMEOUT_S - 10}];({around});out tags center;"


def _from_overpass(element: dict[str, Any]) -> Place:
    where = element.get("center", element)
    tags = element["tags"]
    osm_id = f"{element['type']}/{element['id']}"
    return Place(osm_id, tags.get("name", ""), where["lat"], where["lon"], tags["opening_hours"], _kind(tags))


def _overpass(pois: list[Poi], match_m: float, http: Callable[..., Any]) -> list[Place]:
    """Places with hours around `pois`, from the first Overpass server that answers; raises OSError if none does."""
    query = _overpass_query(pois, match_m)
    error: Exception | None = None
    for url in OVERPASS:
        try:
            return [_from_overpass(e) for e in http(url, {"data": query})["elements"]]
        except (OSError, ValueError, KeyError) as exc:
            error = exc
    raise OSError(error) from error


def _nominatim(poi: Poi, match_m: float, http: Callable[..., Any]) -> list[Place]:
    """Places with hours named like `poi` and around it, from Nominatim; raises OSError if it does not answer."""
    d = match_m / 111_320 * 1.5  # a box a little larger than the matching circle; match() trims it
    params = {
        "q": poi.name or poi.type,
        "format": "jsonv2",
        "extratags": "1",
        "bounded": "1",
        "limit": "5",
        "viewbox": f"{poi.lon - d},{poi.lat + d},{poi.lon + d},{poi.lat - d}",
    }
    try:
        results = http(f"{NOMINATIM}?{urllib.parse.urlencode(params)}", None)
    except ValueError as exc:
        raise OSError(exc) from exc
    return [
        Place(
            f"{r['osm_type']}/{r['osm_id']}",
            r.get("name", ""),
            float(r["lat"]),
            float(r["lon"]),
            hours,
            f"{r['category']}={r['type']}" if r.get("category") in KIND_KEYS else "",  # Nominatim's main tag
        )
        for r in results
        if (hours := (r.get("extratags") or {}).get("opening_hours"))
    ]


class Cache:
    """Matches already looked up, keyed by POI position and name: the OSM place found, or none."""

    def __init__(self, path: Path, max_age_days: float) -> None:
        self.path = path
        self.oldest = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries: dict[str, Any] = data["entries"] if data.get("version") == CACHE_VERSION else {}
        except (OSError, ValueError, KeyError):
            self.entries = {}

    @staticmethod
    def key(poi: Poi) -> str:
        return f"{poi.lat:.5f},{poi.lon:.5f},{_norm(poi.name or poi.type)}"

    def get(self, poi: Poi) -> tuple[bool, Place | None]:
        """(known, place): known is False when the POI was never looked up, or too long ago."""
        entry = self.entries.get(self.key(poi))
        if entry is None or dt.datetime.fromisoformat(entry["at"]) < self.oldest:
            return False, None
        return True, Place(**entry["place"]) if entry["place"] else None

    def put(self, poi: Poi, place: Place | None) -> None:
        self.entries[self.key(poi)] = {"at": dt.datetime.now(dt.UTC).isoformat(), "place": place and vars(place)}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"version": CACHE_VERSION, "entries": self.entries}), encoding="utf-8")
        except OSError:
            pass  # a cache that cannot be written only costs a slower next run


def default_cache_path() -> Path:
    return user_cache_path("gpx2roadbook", appauthor=False) / "opening_hours.json"


def lookup(
    pois: list[Poi],
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Callable[..., Any] = _http,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Set `opening_hours` and `osm_id` on the POIs of the `cfg["categories"]` that OSM knows hours for.

    Never raises for a network problem: POIs left unanswered keep no hours, and the report says so.
    """
    match_m = cfg["match_m"]
    wanted = [p for p in pois if p.category in cfg["categories"]]
    cache = Cache(cache_path or default_cache_path(), cfg["max_age_days"])
    report = Report(asked=len(wanted))

    todo: list[Poi] = []
    for p in wanted:
        known, place = cache.get(p)
        if known:
            report.cached += 1
            _apply(p, place)
        else:
            todo.append(p)

    def answer(p: Poi, places: list[Place], source: str) -> None:
        place = match(p, places, match_m, cfg["kinds"].get(p.category or ""))
        cache.put(p, place)
        _apply(p, place)
        if source not in report.sources:
            report.sources.append(source)

    left = _by_overpass(todo, match_m, http, answer)
    report.failed = _by_nominatim(left, match_m, http, answer, sleep) > 0
    cache.save()
    report.found = sum(1 for p in wanted if p.opening_hours)
    return report


def _by_overpass(todo: list[Poi], match_m: float, http: Callable[..., Any], answer: Answer) -> list[Poi]:
    """Answer the POIs from Overpass, a chunk at a time; returns the ones left once it stops answering."""
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i : i + CHUNK]
        try:
            places = _overpass(chunk, match_m, http)
        except OSError:
            return todo[i:]  # it failed on every server: don't wait for it again
        for p in chunk:
            answer(p, places, "Overpass")
    return []


def _by_nominatim(
    left: list[Poi], match_m: float, http: Callable[..., Any], answer: Answer, sleep: Callable[[float], None]
) -> int:
    """Answer the POIs from Nominatim, one per request; returns how many it could not answer."""
    unanswered = failures = 0  # failures: in a row
    for n, p in enumerate(left):
        if failures >= NOMINATIM_GIVE_UP:
            return unanswered + len(left) - n
        if n:
            sleep(NOMINATIM_EVERY_S)
        try:
            places = _nominatim(p, match_m, http)
        except OSError:
            failures += 1
            unanswered += 1
            continue
        failures = 0
        answer(p, places, "Nominatim")
    return unanswered


def _apply(poi: Poi, place: Place | None) -> None:
    poi.opening_hours = place.hours if place else None
    poi.osm_id = place.osm_id if place else None
