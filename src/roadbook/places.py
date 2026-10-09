"""The towns, villages and hamlets a climb's road goes through, from OpenStreetMap, for the climb pages.

Not Nominatim's reverse geocoding, as towns.py does for the stops: it answers with the commune a point lies in, and
on a mountain road that is the same commune all the way up, village or not. Instead, one Overpass request asks for
the places (`place=city|town|village|hamlet`, with a name) near every climb's road; each place is put at the km of
the road nearest to it. Answers are cached on disk for a long time, per climb, as villages don't move either.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from .osm import TIMEOUT_S, JsonCache, Report, cache_path, distance_m, overpass
from .osm import http as _http

if TYPE_CHECKING:
    from pathlib import Path

    from .model import ClimbPage, Track
    from .osm import Http

log = logging.getLogger(__name__)

type Place = tuple[str, float, float]  # name, lat, lon

PLACES = "city|town|village|hamlet"
STEP_KM = 0.2  # the road is followed by points this far apart: close enough for a place 250 m off it
CACHE_VERSION = 1


def road(page: ClimbPage, track: Track) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """The climb's road, foot to summit: route km, and (lat, lon) at each."""
    c = page.climb
    kms = np.append(np.arange(c.start_km, c.end_km, STEP_KM), c.end_km)
    return kms, [track.at(float(km)) for km in kms]


def on_road(
    kms: np.ndarray, points: list[tuple[float, float]], places: list[Place], near_m: float
) -> list[tuple[float, str]]:
    """(route km, name) of the places within `near_m` of the road, at its nearest point, in route order; a name
    found twice (a town and its hamlet of the same name) is kept where the road comes closest."""
    best: dict[str, tuple[float, float]] = {}  # name: (distance, km)
    for name, lat, lon in places:
        far = [distance_m(lat, lon, *p) for p in points]
        i = int(np.argmin(far))
        if far[i] <= near_m and (name not in best or far[i] < best[name][0]):
            best[name] = (far[i], float(kms[i]))
    return sorted((km, name) for name, (_, km) in best.items())


def _query(roads: list[list[tuple[float, float]]], near_m: float) -> str:
    lines = "".join(
        f'node["place"~"^({PLACES})$"]["name"](around:{near_m:.0f},{",".join(f"{a:.5f},{b:.5f}" for a, b in pts)});'
        for pts in roads
    )
    return f"[out:json][timeout:{TIMEOUT_S - 10}];({lines});out;"


def _place(element: dict[str, Any]) -> Place | None:
    name = (element.get("tags") or {}).get("name")
    return (name, float(element["lat"]), float(element["lon"])) if name else None


def default_cache_path() -> Path:
    return cache_path("places.json")


def _key(page: ClimbPage, track: Track, near_m: float) -> str:
    (a, b), (c, d) = track.at(page.climb.start_km), track.at(page.climb.end_km)
    return f"{a:.4f},{b:.4f},{c:.4f},{d:.4f},{near_m:g}"  # foot and summit, ~10 m; the same climb asks the same


def lookup(  # noqa: PLR0913  the network's seams for the tests, and offline, come on top of what to look up
    pages: list[ClimbPage],
    track: Track,
    cfg: dict[str, Any],
    max_age_days: float,
    *,
    cache_path: Path | None = None,
    http: Http = _http,
    offline: bool = False,
) -> Report:
    """Set `places` on each climb page: the named places within `cfg["place_m"]` of the road up.

    Never raises for a network problem: climbs left unanswered get no places, and the report says so. Offline, only
    the cache answers, whatever the age of its answers, and the report counts the climbs it leaves unasked.
    """
    started = time.perf_counter()
    near_m = float(cfg["place_m"])
    cache = JsonCache(cache_path or default_cache_path(), CACHE_VERSION, None if offline else max_age_days, "places")
    report = Report(asked=len(pages))
    todo: list[ClimbPage] = []
    for page in pages:
        known, places = cache.get(_key(page, track, near_m))
        if known:  # kept as km from the foot: the route before the climb may change, the climb doesn't
            page.places = [(page.climb.start_km + km, name) for km, name in places]
            report.cached += 1
        else:
            todo.append(page)
    if offline:
        report.unasked, todo = len(todo), []
    if todo:
        log.info("Places on climbs: %d cached; asking Overpass about the other %d", report.cached, len(todo))
        roads = [road(page, track) for page in todo]
        try:
            found = overpass(_query([pts for _, pts in roads], near_m), http, _place)
        except OSError:
            report.failed = True
        else:
            report.answered_by("Overpass")
            for page, (kms, pts) in zip(todo, roads, strict=True):
                page.places = on_road(kms, pts, found, near_m)
                foot = page.climb.start_km
                cache.put(_key(page, track, near_m), [[round(km - foot, 3), name] for km, name in page.places])
        log.info("Places on climbs: looked up in %.1f s", time.perf_counter() - started)
    cache.save()
    report.found = sum(1 for page in pages if page.places)
    for page in pages:
        log.debug("km %.1f: %s", page.climb.start_km, ", ".join(n for _, n in page.places) or "no place on the way")
    return report
