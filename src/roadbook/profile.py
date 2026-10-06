from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

if TYPE_CHECKING:
    from .model import Track


def turning_points(e: np.ndarray, swing: float) -> list[int]:
    """Indices of alternating peaks/valleys, ignoring swings smaller than `swing` metres."""
    pts = [0]
    lo = hi = 0
    trend = 0
    for i in range(1, len(e)):
        if e[i] > e[hi]:
            hi = i
        if e[i] < e[lo]:
            lo = i
        if trend <= 0 and e[i] - e[lo] >= swing:
            pts.append(lo)
            trend, hi = 1, i
        elif trend >= 0 and e[hi] - e[i] >= swing:
            pts.append(hi)
            trend, lo = -1, i
    if pts[-1] != len(e) - 1:
        pts.append(len(e) - 1)
    return sorted(set(pts))


class Profile:
    """Smoothed elevation profile on a regular distance grid, with gain/loss queries."""

    def __init__(self, track: Track, step_m: float = 25, smooth_m: float = 200, swing_m: float = 3) -> None:
        self.step = step_m
        self.x = np.arange(0, track.dist[-1] + step_m, step_m)
        raw = np.interp(self.x, track.dist, track.ele)
        med = np.median(sliding_window_view(np.pad(raw, 2, mode="edge"), 5), axis=1)
        k = max(1, round(smooth_m / step_m)) | 1
        self.e = np.convolve(np.pad(med, k // 2, mode="edge"), np.ones(k) / k, mode="valid")

        self.turns = turning_points(self.e, swing_m)
        filtered = np.interp(self.x, self.x[self.turns], self.e[self.turns])
        d = np.diff(filtered, prepend=filtered[0])
        self._gain = np.cumsum(np.clip(d, 0, None))
        self._loss = np.cumsum(np.clip(-d, 0, None))

    @property
    def total_gain(self) -> float:
        return float(self._gain[-1])

    @property
    def total_loss(self) -> float:
        return float(self._loss[-1])

    def ele_at(self, km: float) -> float:
        return float(np.interp(km * 1000, self.x, self.e))

    def ele_range(self, km_a: float, km_b: float) -> tuple[float, float]:
        """(lowest, highest) elevation on the grid between two km; a stretch shorter than a step has only km_a's."""
        seg = self.e[(self.x >= km_a * 1000) & (self.x <= km_b * 1000)]
        if not seg.size:
            ele = self.ele_at(km_a)
            return ele, ele
        return float(seg.min()), float(seg.max())

    def gain(self, km_a: float, km_b: float) -> float:
        return float(np.interp(km_b * 1000, self.x, self._gain) - np.interp(km_a * 1000, self.x, self._gain))

    def loss(self, km_a: float, km_b: float) -> float:
        return float(np.interp(km_b * 1000, self.x, self._loss) - np.interp(km_a * 1000, self.x, self._loss))
