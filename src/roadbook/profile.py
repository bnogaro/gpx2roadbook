from __future__ import annotations

import math
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


# No road a GPX route follows climbs or drops more than WALL_GRADE over WALL_M: the steepest paved streets pass 30 %
# for a few dozen metres only. A GPX's elevation (a DEM's, sampled every 20 m or so) does, where the track runs along
# a cliff, a cutting or a gorge's edge: entrainement_ubf km 122.3-123.1 goes 331 m -> 431 m -> 310 m, 41 % over 200 m
# up and as steep down, a hill that is not there. The samples' real climbs stay under 25 % over 200 m in the raw data
# (Menté 24.8, Portet d'Aspet 20.0, Ventoux 19.5), so the line sits well clear of them.
# Not caught: a bump within what a road allows, such as the Col de la Pigière's (ubf_300_poi km 175.7, +35 m at 21 %
# over 200 m then 15 m back, where the road tops out around 6.5 %). Its raw grade and its height are those of the
# real climbs' steepest bits, Menté's and Portet d'Aspet's, which no rule on the elevation alone can tell from it.
WALL_GRADE = 0.30
WALL_M = 200
WALL_GAP_M = 400  # an impossible rise and an impossible drop this close are one hump, its top included


def flatten_walls(e: np.ndarray, step: float) -> np.ndarray:
    """`e`, on a grid of `step` metres, with every stretch steeper than any road replaced by a straight line.

    A stretch is the impossible grade's windows, joined to the next one within WALL_GAP_M (a hump's two sides and its
    top go together, and come out flat), then widened until the line across it is one a road can take: a step that
    stays up keeps its height, spread out at WALL_GRADE.
    """
    k = max(1, math.ceil(WALL_M / step))
    if len(e) <= k:
        return e
    steep = np.flatnonzero(np.abs(e[k:] - e[:-k]) > WALL_GRADE * k * step)
    if not steep.size:
        return e
    spans: list[list[int]] = []
    for i in steep.tolist():  # window i runs from point i to point i + k
        if spans and i <= spans[-1][1] + WALL_GAP_M / step:
            spans[-1][1] = i + k
        else:
            spans.append([i, i + k])
    out = e.copy()
    for span in spans:
        a, b = span
        while abs(out[b] - out[a]) > WALL_GRADE * (b - a) * step and (a > 0 or b < len(e) - 1):
            a, b = max(0, a - 1), min(len(e) - 1, b + 1)
        out[a : b + 1] = np.linspace(out[a], out[b], b - a + 1)
    return out


class Profile:
    """Smoothed elevation profile on a regular distance grid, with gain/loss queries."""

    def __init__(self, track: Track, step_m: float = 25, smooth_m: float = 200, swing_m: float = 3) -> None:
        self.step = step_m
        self.x = np.arange(0, track.dist[-1] + step_m, step_m)
        raw = flatten_walls(np.interp(self.x, track.dist, track.ele), step_m)
        med = np.median(sliding_window_view(np.pad(raw, 2, mode="edge"), 5), axis=1)
        k = max(1, round(smooth_m / step_m)) | 1
        self.smooth_m = k * step_m  # what the moving average spans: no grade over less is the road's own
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
