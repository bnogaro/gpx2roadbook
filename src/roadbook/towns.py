"""Town names from OpenStreetMap, for the busy stops: those grouping enough POIs to be a town or village centre.

The GPX carries no place names, so each busy stop asks Nominatim's reverse geocoding which town its middle POI lies
in. One request per stop, at most one per second; answers are cached on disk for a long time, as towns don't move.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .osm import NOMINATIM_EVERY_S, NOMINATIM_GIVE_UP, JsonCache, cache_path
from .osm import http as _http

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from .model import Poi, Stop

REVERSE = "https://nominatim.openstreetmap.org/reverse"
ZOOM = 12  # town level; 10 gives the same towns on the samples, 14 a quarter or a farm instead
# in this order; not "municipality", which in France is the arrondissement: Le Mas-d'Azil would read Saint-Girons
ADDRESS_KEYS = ("city", "town", "village")
CACHE_VERSION = 1


@dataclass
class Report:
    """What the lookup did, for one line of output."""

    asked: int = 0  # busy stops
    found: int = 0
    cached: int = 0  # of `asked`, answered from the cache
    failed: bool = False  # some stops could not be looked up at all


def busy(stops: list[Stop], min_pois: int) -> list[Stop]:
    """The stops grouping at least `min_pois` POIs: a town's shops, not a fountain or a lone bakery."""
    return [s for s in stops if len(s.pois) >= min_pois]


def centre(stop: Stop) -> Poi:
    """The stop's POI nearest the middle of them all: a spot in town even when the road through it bends."""
    lat = sum(p.lat for p in stop.pois) / len(stop.pois)
    lon = sum(p.lon for p in stop.pois) / len(stop.pois)
    return min(stop.pois, key=lambda p: (p.lat - lat) ** 2 + (p.lon - lon) ** 2)


def _key(p: Poi) -> str:
    return f"{p.lat:.4f},{p.lon:.4f}"  # ~10 m: the same stop asks the same question next time


def _reverse(p: Poi, http: Callable[..., Any]) -> str | None:
    """The town `p` lies in, or None if OSM puts it in none; raises OSError if Nominatim does not answer."""
    params = {"format": "jsonv2", "lat": f"{p.lat:.5f}", "lon": f"{p.lon:.5f}", "zoom": str(ZOOM)}
    try:
        result = http(f"{REVERSE}?{urllib.parse.urlencode(params)}", None)
    except ValueError as exc:
        raise OSError(exc) from exc
    if not isinstance(result, dict):
        msg = f"unexpected answer: {result!r}"
        raise OSError(msg)
    address = result.get("address") or {}  # absent with {"error": "Unable to geocode"}, e.g. far from any town
    return next((address[k] for k in ADDRESS_KEYS if address.get(k)), None)


def default_cache_path() -> Path:
    return cache_path("towns.json")


def lookup(
    stops: list[Stop],
    cfg: dict[str, Any],
    *,
    cache_path: Path | None = None,
    http: Callable[..., Any] = _http,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Set `town` on the busy stops (`cfg["min_pois"]` POIs or more) that OSM places in a town.

    Never raises for a network problem: stops left unanswered keep no name, and the report says so.
    """
    wanted = busy(stops, cfg["min_pois"])
    cache = JsonCache(cache_path or default_cache_path(), CACHE_VERSION, cfg["max_age_days"], field="town")
    report = Report(asked=len(wanted))
    failures = 0  # in a row
    for s in wanted:
        where = centre(s)
        key = _key(where)
        known, town = cache.get(key)
        if known:
            report.cached += 1
        elif failures >= NOMINATIM_GIVE_UP:
            report.failed = True
            continue
        else:
            # before every request, the first too: the opening hours lookup may just have asked Nominatim
            sleep(NOMINATIM_EVERY_S)
            try:
                town = _reverse(where, http)
            except OSError:
                failures += 1
                report.failed = True
                continue
            failures = 0
            cache.put(key, town)
        s.town = town
    cache.save()
    report.found = sum(1 for s in wanted if s.town)
    return report
