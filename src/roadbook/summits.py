"""Climb names from OpenStreetMap: the col, pass or peak at each climb's summit.

The GPX carries no place names, so each climb's top (its `end_km` on the route) is looked up among the OSM places
tagged `mountain_pass=yes`, `natural=saddle` or `natural=peak` that have a name. A pass or saddle wins over a peak,
as the road crosses the pass but only skirts the peak; then the closest one, within its kind's radius.

One Overpass request asks for the whole route; its public servers are often overloaded, so a mirror is tried next,
then Nominatim, which can filter a bounded search by tag (`q=[mountain_pass=yes]`): slower, up to three requests per
climb at one per second, but steady. Answers are cached on disk for a long time, as cols don't move.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from .osm import M_PER_DEG, TIMEOUT_S, JsonCache, Report, cache_path, distance_m, nominatim, one_by_one, overpass
from .osm import http as _http

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Climb, Track
    from .osm import Http

log = logging.getLogger(__name__)

type Point = tuple[float, float]  # lat, lon

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
        if p.kind in radius and _is_name(p.name) and (d := distance_m(*top, p.lat, p.lon)) <= radius[p.kind]
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


def _nominatim(top: Point, cfg: dict[str, Any], http: Http, sleep: Callable[[float], None]) -> list[Place]:
    """Named passes, saddles, then peaks around `top`, from Nominatim; raises OSError if it does not answer."""
    radius = max(cfg["pass_m"], cfg["peak_m"])
    dlat = radius / M_PER_DEG
    dlon = dlat / max(0.1, math.cos(math.radians(top[0])))
    box = f"{top[1] - dlon},{top[0] + dlat},{top[1] + dlon},{top[0] - dlat}"  # choose() trims it to a circle
    places: list[Place] = []
    for q, kind in NOMINATIM_TAGS:
        if kind == "peak" and choose(top, places, cfg):
            break  # a pass is near: no peak could win over it
        params = {"q": q, "format": "jsonv2", "bounded": "1", "limit": "10", "viewbox": box}
        places += nominatim("search", params, http, sleep, partial(_from_nominatim, kind))
    return places


def _from_nominatim(kind: str, results: list[dict[str, Any]]) -> list[Place]:
    return [Place(r.get("name") or "", kind, float(r["lat"]), float(r["lon"])) for r in results]


def default_cache_path() -> Path:
    return cache_path("climb_names.json")


def _key(top: Point, cfg: dict[str, Any]) -> str:
    # ~10 m, and the radii: an answer chosen with other radii may not be the same
    return f"{top[0]:.4f},{top[1]:.4f},{cfg["pass_m"]:g},{cfg["peak_m"]:g}"


def lookup(  # noqa: PLR0913  the network's seams for the tests, and offline, come on top of what to name
    climbs: list[Climb],
    track: Track,
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Http = _http,
    sleep: Callable[[float], None] = time.sleep,
    offline: bool = False,
) -> Report:
    """Set `name` on the climbs whose summit OSM puts a named col, pass or peak at.

    Never raises for a network problem: climbs left unanswered keep no name, and the report says so. Offline, only
    the cache answers, whatever the age of its answers, and the report counts the climbs it leaves unasked.
    """
    started = time.perf_counter()
    log.info("Climb names: %d climbs to name", len(climbs))
    max_age = None if offline else cfg["max_age_days"]
    cache = JsonCache(cache_path or default_cache_path(), CACHE_VERSION, max_age, field="name")
    report = Report(asked=len(climbs))
    tops = {id(c): track.at(c.end_km) for c in climbs}
    todo: list[Climb] = []
    for c in climbs:
        known, name = cache.get(_key(tops[id(c)], cfg))
        if known:
            report.cached += 1
            c.name = name
            _log_name(c, "cached")
        else:
            todo.append(c)
    if offline:
        report.unasked, todo = len(todo), []

    def answer(c: Climb, places: list[Place], source: str) -> None:
        top = tops[id(c)]
        c.name = choose(top, places, cfg)
        cache.put(_key(top, cfg), c.name)
        _log_name(c, source)
        report.answered_by(source)

    if todo:
        log.info("Climb names: %d cached; asking Overpass about the other %d", report.cached, len(todo))
        query = _overpass_query([tops[id(c)] for c in todo], max(cfg["pass_m"], cfg["peak_m"]))
        try:
            places = overpass(query, http, _from_overpass)
        except OSError:
            log.info("Climb names: asking Nominatim about %d climbs, one request a second", len(todo))
            report.failed = one_by_one(
                todo, lambda c: _nominatim(tops[id(c)], cfg, http, sleep), lambda c, ps: answer(c, ps, "Nominatim")
            )
        else:
            for c in todo:
                answer(c, places, "Overpass")
        log.info("Climb names: looked up in %.1f s", time.perf_counter() - started)
    cache.save()
    report.found = sum(1 for c in climbs if c.name)
    return report


def _log_name(c: Climb, how: str) -> None:
    log.debug("km %.1f-%.1f: %s (%s)", c.start_km, c.end_km, c.name or "no named col or peak at its top", how)
