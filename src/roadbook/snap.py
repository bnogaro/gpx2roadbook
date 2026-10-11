from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .model import Poi, Track

_R = 6371008.8
ON_ROAD_M = 5.0  # a POI this close to the route is on the road itself: no side to tell


class Snapper:
    """Projects points onto the track and returns (km along track, offset in metres).

    When a hint (km from the exporter) is available the search is limited to a window
    around it, which keeps loops / out-and-backs / crossings unambiguous.
    """

    def __init__(self, track: Track) -> None:
        self._lat0 = np.radians(track.lat.mean())
        x, y = self._project(track.lat, track.lon)
        self._ax, self._ay = x[:-1], y[:-1]
        self._dx, self._dy = np.diff(x), np.diff(y)
        self._l2 = np.maximum(self._dx**2 + self._dy**2, 1e-9)
        self._d0 = track.dist[:-1]
        self._seg = np.diff(track.dist)

    def _project(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.radians(lon) * np.cos(self._lat0) * _R, np.radians(lat) * _R

    def snap(self, lat: float, lon: float, hint_km: float | None = None) -> tuple[float, float, int]:
        """(km along the track, metres off it, side of the road as the rider goes: -1 left, 1 right, 0 on it)."""
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
        # the point's side of the segment it is closest to: x east, y north, so a positive cross product is the left
        cross = self._dx[i] * (py - self._ay[i]) - self._dy[i] * (px - self._ax[i])
        side = 0 if d[i] < ON_ROAD_M else -1 if cross > 0 else 1
        return float(along[i]) / 1000, float(d[i]), side


def snap_pois(track: Track, pois: list[Poi]) -> None:
    snapper = Snapper(track)
    for p in pois:
        p.km, p.offset_m, p.side = snapper.snap(p.lat, p.lon, p.hint_km)
