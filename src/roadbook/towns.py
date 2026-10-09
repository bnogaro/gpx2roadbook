"""Town names from OpenStreetMap, for the busy stops: those grouping enough POIs to be a town or village centre.

The GPX carries no place names, so each busy stop asks Nominatim's reverse geocoding which town its middle POI lies
in. One request per stop, at most one per second; answers are cached on disk for a long time, as towns don't move.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .model import Stop
from .osm import NOMINATIM_GIVE_UP, JsonCache, Report, cache_path, nominatim, one_by_one
from .osm import http as _http

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Poi
    from .osm import Http

log = logging.getLogger(__name__)

ZOOM = 12  # town level; 10 gives the same towns on the samples, 14 a quarter or a farm instead
# in this order; not "municipality", which in France is the arrondissement: Le Mas-d'Azil would read Saint-Girons
ADDRESS_KEYS = ("city", "town", "village")
CACHE_VERSION = 1


def busy(stops: list[Stop], cfg: dict[str, Any]) -> list[Stop]:
    """The stops likely in a town or village: not a fountain, a cemetery tap or a lone bakery.

    That is `min_pois` POIs or more, or `shop_min_pois` when one of them is a village shop (`shops`: a bakery, a
    grocery), as a village with a bakery and a shop has fewer POIs than a town but is a place all the same.
    """
    return [
        s
        for s in stops
        if len(s.pois) >= cfg["min_pois"]
        or (len(s.pois) >= cfg["shop_min_pois"] and any(p.category in cfg["shops"] for p in s.pois))
    ]


def centre(stop: Stop) -> Poi:
    """The stop's POI nearest the middle of them all: a spot in town even when the road through it bends."""
    lat = sum(p.lat for p in stop.pois) / len(stop.pois)
    lon = sum(p.lon for p in stop.pois) / len(stop.pois)
    return min(stop.pois, key=lambda p: (p.lat - lat) ** 2 + (p.lon - lon) ** 2)


def _key(p: Poi) -> str:
    return f"{p.lat:.4f},{p.lon:.4f}"  # ~10 m: the same stop asks the same question next time


def _reverse(p: Poi, http: Http, sleep: Callable[[float], None]) -> str | None:
    """The town `p` lies in, or None if OSM puts it in none; raises OSError if Nominatim does not answer."""
    params = {"format": "jsonv2", "lat": f"{p.lat:.5f}", "lon": f"{p.lon:.5f}", "zoom": str(ZOOM)}
    return nominatim("reverse", params, http, sleep, _town)


def _town(result: dict[str, Any]) -> str | None:
    address = result.get("address") or {}  # absent with {"error": "Unable to geocode"}, e.g. far from any town
    return next((address[k] for k in ADDRESS_KEYS if address.get(k)), None)


def default_cache_path() -> Path:
    return cache_path("towns.json")


def group(stops: list[Stop], within_km: float) -> list[Stop]:
    """Merge each run of stops in one town into a single stop, whatever split them (--gap, --max-span).

    A run goes from a stop named after a town to the next one named after the same town, taking the unnamed stops
    between them, which lie in that town too; it stops at a stop named after another town. Only stops up to
    `within_km` apart join: a route may come back through a town hours later, a visit of its own. 0: no grouping.
    """
    out: list[Stop] = []
    last = -1  # index in `out` of the last stop with a town
    for s in stops:
        named = out[last] if last >= 0 else None
        if within_km and s.town and named and s.town == named.town and s.km - named.km_end <= within_km:
            pois = [p for t in out[last:] for p in t.pois] + s.pois
            out[last:] = [Stop(pois, town=s.town)]
            log.debug("km %.1f-%.1f: one stop in %s, %d POIs", pois[0].km, pois[-1].km, s.town, len(pois))
        else:
            out.append(s)
        if s.town:
            last = len(out) - 1
    if len(out) < len(stops):
        log.info("Towns: %d stops grouped into %d, one per town", len(stops), len(out))
    return out


def lookup(
    stops: list[Stop],
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Http = _http,
    sleep: Callable[[float], None] = time.sleep,
    offline: bool = False,
) -> Report:
    """Set `town` on the busy stops (see `busy`) that OSM places in a town.

    Never raises for a network problem: stops left unanswered keep no name, and the report says so. Offline, only
    the cache answers, whatever the age of its answers, and the report counts the stops it leaves unasked.
    """
    started = time.perf_counter()
    wanted = busy(stops, cfg)
    log.info("Towns: %d busy stops to name", len(wanted))
    max_age = None if offline else cfg["max_age_days"]
    cache = JsonCache(cache_path or default_cache_path(), CACHE_VERSION, max_age, field="town")
    report = Report(asked=len(wanted))
    todo: list[Stop] = []
    for s in wanted:
        known, town = cache.get(_key(centre(s)))
        if known:
            report.cached += 1
            s.town = town
            _log_town(s, "cached")
        else:
            todo.append(s)
    if offline:
        report.unasked, todo = len(todo), []

    def answer(s: Stop, town: str | None) -> None:
        cache.put(_key(centre(s)), town)
        s.town = town
        _log_town(s, "Nominatim")
        report.answered_by("Nominatim")

    if todo:
        log.info("Towns: %d cached; asking Nominatim about the other %d, one a second", report.cached, len(todo))
    report.failed = one_by_one(todo, lambda s: _reverse(centre(s), http, sleep), answer)
    edges = 0
    if cfg["group_km"] and cfg["edge_km"] and not report.failed:  # grouped, a town also takes its edges' stops
        ask = None if offline else lambda p: _reverse(p, http, sleep)
        edges = _ask_edges(stops, cfg["edge_km"], cache, ask, report)
    cache.save()
    report.found = sum(1 for s in wanted if s.town)
    if todo or edges or report.failed:
        log.info("Towns: looked up in %.1f s", time.perf_counter() - started)
    return report


def _ask_edges(
    stops: list[Stop], edge_km: float, cache: JsonCache, ask: Callable[[Poi], str | None] | None, report: Report
) -> int:
    """Join the small stops at the named towns' edges to them (see _join_edges); returns how many were asked about.

    `ask` gets Nominatim's answer, see _reverse; None offline, when only the cache answers. Those stops are not in
    the report, which counts the busy stops, as its summary line says; a failure is.
    """
    failures = asked = 0  # failures: in a row

    def town_of(s: Stop) -> str | None:
        nonlocal failures, asked
        known, town = cache.get(_key(centre(s)))
        if known or ask is None or failures >= NOMINATIM_GIVE_UP:
            return town
        asked += 1
        try:
            town = ask(centre(s))
        except OSError:
            failures += 1
            report.failed = True
            return None
        failures = 0
        cache.put(_key(centre(s)), town)
        return town

    _join_edges(stops, edge_km, town_of)
    if asked:
        log.info("Towns: asked Nominatim about %d small stops at a town's edge", asked)
    return asked


def _join_edges(stops: list[Stop], edge_km: float, town_of: Callable[[Stop], str | None]) -> None:
    """Name the small stops at a named town's edges after it, when OSM puts them in it.

    A supermarket on the way out of town is two POIs, too few to be asked about as a busy stop. From each named
    stop, outwards both ways, each unnamed stop up to `edge_km` from the last one in the town is asked which town it
    is in: in the same town, it is named after it, for group() to merge it in, and the walk goes on from it; in
    another, or none, it stays unnamed, with no heading of its own, and the walk stops there.
    """
    joined = 0
    for i, s in enumerate(stops):
        if not s.town:
            continue
        for step in (1, -1):
            j, near = i + step, s
            while 0 <= j < len(stops) and stops[j].town is None:
                other = stops[j]
                apart = other.km - near.km_end if step > 0 else near.km - other.km_end
                if apart > edge_km or town_of(other) != s.town:
                    break
                other.town = s.town
                _log_town(other, "at its edge")
                joined += 1
                near, j = other, j + step
    if joined:
        log.info("Towns: %d small stops at a town's edge joined it", joined)


def _log_town(s: Stop, how: str) -> None:
    log.debug("km %.1f, %d POIs: %s (%s)", s.km, len(s.pois), s.town or "in no town", how)
