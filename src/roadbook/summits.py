"""Climb names from OpenStreetMap: the col, pass or peak at each climb's summit.

The GPX carries no place names, so each climb's top (its `end_km` on the route) is looked up among the OSM places
tagged `mountain_pass=yes`, `natural=saddle` or `natural=peak` that have a name. A pass or saddle wins over a peak,
as the road crosses the pass but only skirts the peak; then the closest one, within its kind's radius.

One Overpass request asks for the whole route; its public servers are often overloaded, so a mirror is tried next,
then Nominatim, which can filter a bounded search by tag (`q=[mountain_pass=yes]`): slower, up to three requests per
climb at one per second, but steady. Answers are cached on disk for a long time, as cols don't move.
"""

from __future__ import annotations

import math
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .osm import NOMINATIM_EVERY_S, NOMINATIM_GIVE_UP, OVERPASS, TIMEOUT_S, JsonCache, cache_path
from .osm import http as _http

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Climb, Track

    Point = tuple[float, float]  # lat, lon

SEARCH = "https://nominatim.openstreetmap.org/search"
# Nominatim's tag filters, in order of preference; it files a node tagged both mountain_pass and natural=saddle under
# mountain_pass only, so each needs its own request. The peak is only asked for when no pass or saddle is near.
NOMINATIM_TAGS = (("[mountain_pass=yes]", "pass"), ("[natural=saddle]", "pass"), ("[natural=peak]", "peak"))
RANK = {"pass": 0, "peak": 1}  # lower wins
CACHE_VERSION = 1


@dataclass(frozen=True)
class Place:
    """A named col, pass, saddle ("pass") or peak in OSM."""

    name: str
    kind: str  # "pass" or "peak"
    lat: float
    lon: float


@dataclass
class Report:
    """What the lookup did, for one line of output."""

    asked: int = 0  # climbs
    found: int = 0
    cached: int = 0  # of `asked`, answered from the cache
    sources: list[str] = field(default_factory=list)
    failed: bool = False  # some climbs could not be looked up at all


def _distance_m(a: Point, b: Point) -> float:
    dy = (b[0] - a[0]) * 111_320
    dx = (b[1] - a[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _kind(tags: dict[str, str]) -> str | None:
    if tags.get("mountain_pass") == "yes" or tags.get("natural") == "saddle":
        return "pass"
    return "peak" if tags.get("natural") == "peak" else None


def _is_name(name: str) -> bool:
    """A name, not an elevation some mappers put there instead ("427")."""
    return any(c.isalpha() for c in name)


def choose(top: Point, places: list[Place], cfg: dict[str, Any]) -> str | None:
    """The name for a climb whose summit is at `top`: a pass or saddle within `cfg["pass_m"]` if any, else a peak
    within `cfg["peak_m"]`; the closest of its kind. None if there is neither."""
    radius = {"pass": cfg["pass_m"], "peak": cfg["peak_m"]}
    near = [
        (RANK[p.kind], d, p.name)
        for p in places
        if p.kind in radius and _is_name(p.name) and (d := _distance_m(top, (p.lat, p.lon))) <= radius[p.kind]
    ]
    return min(near, default=(0, 0.0, None))[2]


def _overpass_query(tops: list[Point], radius: float) -> str:
    around = "".join(
        f'nwr["name"]["mountain_pass"="yes"](around:{radius:.0f},{lat:.6f},{lon:.6f});'
        f'nwr["name"]["natural"~"^(saddle|peak)$"](around:{radius:.0f},{lat:.6f},{lon:.6f});'
        for lat, lon in tops
    )
    return f"[out:json][timeout:{TIMEOUT_S - 10}];({around});out center;"


def _from_overpass(element: dict[str, Any]) -> Place | None:
    tags = element.get("tags") or {}
    kind = _kind(tags)
    where = element.get("center", element)  # a node has its own lat/lon; a way or relation, a center
    return Place(tags.get("name", ""), kind, where["lat"], where["lon"]) if kind else None


def _overpass(tops: list[Point], radius: float, http: Callable[..., Any]) -> list[Place]:
    """Named passes and peaks around `tops`, from the first Overpass server that answers; raises OSError if none."""
    query = _overpass_query(tops, radius)
    error: Exception | None = None
    for url in OVERPASS:
        try:
            elements = http(url, {"data": query})["elements"]
            return [p for e in elements if (p := _from_overpass(e))]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error = exc
    raise OSError(error) from error


def _nominatim(
    top: Point, cfg: dict[str, Any], http: Callable[..., Any], sleep: Callable[[float], None]
) -> list[Place]:
    """Named passes, saddles, then peaks around `top`, from Nominatim; raises OSError if it does not answer."""
    radius = max(cfg["pass_m"], cfg["peak_m"])
    dlat = radius / 111_320
    dlon = dlat / max(0.1, math.cos(math.radians(top[0])))
    box = f"{top[1] - dlon},{top[0] + dlat},{top[1] + dlon},{top[0] - dlat}"  # choose() trims it to a circle
    places: list[Place] = []
    for q, kind in NOMINATIM_TAGS:
        if kind == "peak" and choose(top, places, cfg):
            break  # a pass is near: no peak could win over it
        sleep(NOMINATIM_EVERY_S)  # before every request, the first too: the towns lookup may just have asked
        params = {"q": q, "format": "jsonv2", "bounded": "1", "limit": "10", "viewbox": box}
        try:
            results = http(f"{SEARCH}?{urllib.parse.urlencode(params)}", None)
            places += [Place(r.get("name") or "", kind, float(r["lat"]), float(r["lon"])) for r in results]
        except (ValueError, KeyError, TypeError) as exc:
            raise OSError(exc) from exc
    return places


def default_cache_path() -> Path:
    return cache_path("climb_names.json")


def _key(top: Point, cfg: dict[str, Any]) -> str:
    # ~10 m, and the radii: an answer chosen with other radii may not be the same
    return f"{top[0]:.4f},{top[1]:.4f},{cfg['pass_m']:g},{cfg['peak_m']:g}"


def lookup(
    climbs: list[Climb],
    track: Track,
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Callable[..., Any] = _http,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Set `name` on the climbs whose summit OSM puts a named col, pass or peak at.

    Never raises for a network problem: climbs left unanswered keep no name, and the report says so.
    """
    cache = JsonCache(cache_path or default_cache_path(), CACHE_VERSION, cfg["max_age_days"], field="name")
    report = Report(asked=len(climbs))
    tops = {id(c): track.at(c.end_km) for c in climbs}
    todo: list[Climb] = []
    for c in climbs:
        known, name = cache.get(_key(tops[id(c)], cfg))
        if known:
            report.cached += 1
            c.name = name
        else:
            todo.append(c)

    def answer(c: Climb, places: list[Place], source: str) -> None:
        top = tops[id(c)]
        c.name = choose(top, places, cfg)
        cache.put(_key(top, cfg), c.name)
        if source not in report.sources:
            report.sources.append(source)

    if todo:
        try:
            places = _overpass([tops[id(c)] for c in todo], max(cfg["pass_m"], cfg["peak_m"]), http)
        except OSError:
            report.failed = _by_nominatim(todo, tops, cfg, http, sleep, answer)
        else:
            for c in todo:
                answer(c, places, "Overpass")
    cache.save()
    report.found = sum(1 for c in climbs if c.name)
    return report


def _by_nominatim(
    todo: list[Climb],
    tops: dict[int, Point],
    cfg: dict[str, Any],
    http: Callable[..., Any],
    sleep: Callable[[float], None],
    answer: Callable[[Climb, list[Place], str], None],
) -> bool:
    """Answer the climbs from Nominatim, one at a time; returns whether some could not be answered."""
    failures = 0  # in a row
    failed = False
    for c in todo:
        if failures >= NOMINATIM_GIVE_UP:
            return True
        try:
            places = _nominatim(tops[id(c)], cfg, http, sleep)
        except OSError:
            failures += 1
            failed = True
            continue
        failures = 0
        answer(c, places, "Nominatim")
    return failed
