from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .model import Climb

if TYPE_CHECKING:
    import numpy as np

    from .config import ClimbsSettings
    from .profile import Profile

# the distance a climb's max grade is measured over. Not 100 or 200 m: the elevation of a GPX (a DEM's, sampled every
# 20 m or so) has humps and steps a couple of hundred metres wide, a bridge, a cutting, a cliff edge, that its 5-point
# median and the 200 m moving average leave whole, and over 200 m they read as ramps: 15 % on the Col de la Pigière,
# a 3.3 % climb (ubf_300_poi km 175, a 15 m hump). Over 500 m a hump is diluted into the road around it (9 % there),
# while a real steep half-kilometre still shows; a step as tall as entrainement_ubf's at km 122 (80 m in 200 m, 29 %
# over 200 m) is only tamed (17 %)
RAMP_M = 500


def _label(score: float, categories: tuple[tuple[str, float], ...]) -> str:
    for name, threshold in categories:  # sorted from hardest to easiest
        if score >= threshold:
            return name
    return ""


def _is_steep(e: np.ndarray, x: np.ndarray, span: tuple[int, int], settings: ClimbsSettings) -> bool:
    return e[span[1]] - e[span[0]] >= (x[span[1]] - x[span[0]]) * settings.min_grade_pct / 100


def _is_climb(e: np.ndarray, x: np.ndarray, span: tuple[int, int], settings: ClimbsSettings) -> bool:
    gain, length = e[span[1]] - e[span[0]], x[span[1]] - x[span[0]]
    return gain >= settings.min_gain_m and length >= settings.min_length_m and _is_steep(e, x, span, settings)


def _merge(ups: list[tuple[int, int]], e: np.ndarray, x: np.ndarray, settings: ClimbsSettings) -> list[tuple[int, int]]:
    """Join ascents a rider would call one climb: a short break with a shallow dip, in metres or next to the climbs."""

    def joins(a: tuple[int, int], b: tuple[int, int]) -> bool:
        gap, dip = x[b[0]] - x[a[1]], e[a[1]] - e[b[0]]
        if gap > settings.merge_gap_m:
            return False
        if dip <= settings.merge_dip_m:
            return True
        # the relative dip only joins two sides steep enough to climb: a long false flat before a col (UBF km 60-72,
        # 1.6%) would otherwise swallow it and turn "13.8 km at 6.4%" into "25.9 km at 4.1%". Steepness, not
        # min_gain_m: a short steep ramp between two climbs is part of them even if it is no climb on its own
        smaller = min(e[a[1]] - e[a[0]], e[b[1]] - e[b[0]])
        return (
            dip <= settings.merge_dip_ratio * smaller and _is_steep(e, x, a, settings) and _is_steep(e, x, b, settings)
        )

    merged: list[tuple[int, int]] = []
    for up in ups:
        merged.append(up)
        # a merge grows the last climb, which may now bridge a dip before it that was too deep for its smaller self
        while len(merged) > 1 and joins(merged[-2], merged[-1]):
            merged[-2:] = [(merged[-2][0], merged[-1][1])]
    return merged


def find_climbs(profile: Profile, settings: ClimbsSettings) -> list[Climb]:
    e, x, t = profile.e, profile.x, profile.turns
    ups = [(t[k], t[k + 1]) for k in range(len(t) - 1) if e[t[k + 1]] > e[t[k]]]

    win = max(1, math.ceil(RAMP_M / profile.step))  # whole steps, so never under RAMP_M whatever the grid
    climbs = []
    for i0, i1 in _merge(ups, e, x, settings):
        if not _is_climb(e, x, (i0, i1), settings):
            continue
        # a merged climb reads like any other, foot to final top: net gain (top - foot), not the summed ascents, so
        # "4.6 km at 4.6%" is what the signposts and the gutter's line say; the dips stay in the length, so they
        # lower the average grade and with it the category, as they ease the effort
        length = float(x[i1] - x[i0])
        gain = float(e[i1] - e[i0])
        grade = gain / length * 100
        seg = e[i0 : i1 + 1]  # the whole span, so max grade is the steepest ramp of any of its parts
        # a climb shorter than the window has no ramp steeper than itself to show
        peak = float(((seg[win:] - seg[:-win]).max() / (win * profile.step)) * 100) if len(seg) > win else grade
        climbs.append(
            Climb(
                start_km=float(x[i0]) / 1000,
                end_km=float(x[i1]) / 1000,
                gain_m=gain,
                avg_grade=grade,
                max_grade=max(peak, grade),
                label=_label(length * grade, settings.categories),
            )
        )
    return climbs
