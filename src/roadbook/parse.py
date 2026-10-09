from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import gpxpy
import gpxpy.gpx
import numpy as np

from .model import Poi, Track

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

_MIN_POINTS = 2  # a route needs at least two points to have a length


class GpxError(ValueError):
    """A file the road book can't be made from: unreadable, not GPX, or with no route in it."""


# OnRouteMap writes e.g. "Ligne droite jusqu'à l'itinéraire: 8 m, Kilomètre d'itinéraire: 30.8 km"
_KM = re.compile(r"Kilom[eè]tre d'itin[eé]raire:\s*([\d.]+)\s*km", re.IGNORECASE)


def _haversine_cumulative(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.radians(lat), np.radians(lon)
    dla, dlo = np.diff(la), np.diff(lo)
    a = np.sin(dla / 2) ** 2 + np.cos(la[:-1]) * np.cos(la[1:]) * np.sin(dlo / 2) ** 2
    step = 2 * 6371008.8 * np.arcsin(np.sqrt(a))
    return np.concatenate([[0.0], np.cumsum(step)])


def _fill_missing(ele: np.ndarray) -> np.ndarray:
    ok = ~np.isnan(ele)
    if ok.all():
        return ele
    if not ok.any():
        return np.zeros_like(ele)
    idx = np.arange(len(ele))
    return np.interp(idx, idx[ok], ele[ok])


def read_gpx(path: Path) -> tuple[Track, list[Poi]]:
    try:
        with path.open(encoding="utf-8") as fh:
            gpx = gpxpy.parse(fh)
    except (OSError, UnicodeDecodeError, gpxpy.gpx.GPXException) as exc:
        msg = f"{path.name}: not a GPX file the road book can read ({exc})"
        raise GpxError(msg) from exc

    points = [p for t in gpx.tracks for s in t.segments for p in s.points]
    if not points:  # fall back to a planned route
        points = [p for r in gpx.routes for p in r.points]
    if len(points) < _MIN_POINTS:
        msg = f"{path.name}: no track or route points found"
        raise GpxError(msg)

    lat = np.array([p.latitude for p in points])
    lon = np.array([p.longitude for p in points])
    ele = np.array([np.nan if p.elevation is None else p.elevation for p in points])
    if (missing := int(np.isnan(ele).sum())) == len(points):
        log.warning("%s has no elevation: no climbs, and a flat profile.", path.name)
    elif missing:
        log.info("%s: %d of %d points have no elevation, filled in from their neighbours", path.name, missing, len(ele))
    ele = _fill_missing(ele)
    name = (gpx.tracks[0].name if gpx.tracks else None) or gpx.name or path.stem
    track = Track(name=name, lat=lat, lon=lon, ele=ele, dist=_haversine_cumulative(lat, lon))

    pois = []
    for w in gpx.waypoints:
        m = _KM.search(w.description or "")
        pois.append(
            Poi(
                name=w.name or "",
                type=w.type or w.symbol or "",
                lat=w.latitude,
                lon=w.longitude,
                hint_km=float(m.group(1)) if m else None,
            )
        )
    return track, pois
