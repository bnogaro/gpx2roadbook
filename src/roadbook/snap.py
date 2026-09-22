from __future__ import annotations

import numpy as np

from .model import Poi, Track

_R = 6371008.8


class Snapper:
    """Projects points onto the track and returns (km along track, offset in metres).

    When a hint (km from the exporter) is available the search is limited to a window
    around it, which keeps loops / out-and-backs / crossings unambiguous.
    """

    def __init__(self, track: Track):
        self._lat0 = np.radians(track.lat.mean())
        x, y = self._project(track.lat, track.lon)
        self._ax, self._ay = x[:-1], y[:-1]
        self._dx, self._dy = np.diff(x), np.diff(y)
        self._l2 = np.maximum(self._dx**2 + self._dy**2, 1e-9)
        self._d0 = track.dist[:-1]
        self._seg = np.diff(track.dist)

    def _project(self, lat, lon):
        return np.radians(lon) * np.cos(self._lat0) * _R, np.radians(lat) * _R

    def snap(self, lat: float, lon: float, hint_km: float | None = None):
        px, py = self._project(np.array(lat), np.array(lon))
        t = np.clip(((px - self._ax) * self._dx + (py - self._ay) * self._dy) / self._l2, 0, 1)
        d = np.hypot(self._ax + t * self._dx - px, self._ay + t * self._dy - py)
        along = self._d0 + t * self._seg
        if hint_km is not None:
            # exporters' distances drift slightly from ours (~0.15% seen), so widen the window with distance
            window_km = 0.5 + 0.004 * hint_km
            window = np.abs(along - hint_km * 1000) <= window_km * 1000
            if window.any():
                d = np.where(window, d, np.inf)
        i = int(np.argmin(d))
        return float(along[i]) / 1000, float(d[i])


def snap_pois(track: Track, pois: list[Poi]) -> None:
    snapper = Snapper(track)
    for p in pois:
        p.km, p.offset_m = snapper.snap(p.lat, p.lon, p.hint_km)
