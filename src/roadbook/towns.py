"""Town names from OpenStreetMap, for the busy stops: those grouping enough POIs to be a town or village centre.

The GPX carries no place names, so each busy stop asks Nominatim's reverse geocoding which town its middle POI lies
in. One request per stop, at most one per second; answers are cached on disk for a long time, as towns don't move.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .osm import JsonCache, Report, cache_path, nominatim, one_by_one
from .osm import http as _http

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Poi, Stop
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


def lookup(
    stops: list[Stop],
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Http = _http,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Set `town` on the busy stops (see `busy`) that OSM places in a town.

    Never raises for a network problem: stops left unanswered keep no name, and the report says so.
    """
    started = time.perf_counter()
    wanted = busy(stops, cfg)
    log.info("Towns: %d busy stops to name", len(wanted))
    cache = JsonCache(cache_path or default_cache_path(), CACHE_VERSION, cfg["max_age_days"], field="town")
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

    def answer(s: Stop, town: str | None) -> None:
        cache.put(_key(centre(s)), town)
        s.town = town
        _log_town(s, "Nominatim")
        report.answered_by("Nominatim")

    if todo:
        log.info("Towns: %d cached; asking Nominatim about the other %d, one a second", report.cached, len(todo))
    report.failed = one_by_one(todo, lambda s: _reverse(centre(s), http, sleep), answer)
    cache.save()
    report.found = sum(1 for s in wanted if s.town)
    if todo:
        log.info("Towns: looked up in %.1f s", time.perf_counter() - started)
    return report


def _log_town(s: Stop, how: str) -> None:
    log.debug("km %.1f, %d POIs: %s (%s)", s.km, len(s.pois), s.town or "in no town", how)
