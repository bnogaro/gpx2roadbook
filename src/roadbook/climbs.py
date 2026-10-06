from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .model import Climb

if TYPE_CHECKING:
    import numpy as np

    from .profile import Profile


def _label(score: float, categories: list[list]) -> str:
    for name, threshold in categories:  # sorted from hardest to easiest
        if score >= threshold:
            return str(name)
    return ""


def _is_steep(e: np.ndarray, x: np.ndarray, span: tuple[int, int], cfg: dict[str, Any]) -> bool:
    return e[span[1]] - e[span[0]] >= (x[span[1]] - x[span[0]]) * cfg["min_grade_pct"] / 100


def _is_climb(e: np.ndarray, x: np.ndarray, span: tuple[int, int], cfg: dict[str, Any]) -> bool:
    gain, length = e[span[1]] - e[span[0]], x[span[1]] - x[span[0]]
    return gain >= cfg["min_gain_m"] and length >= cfg["min_length_m"] and _is_steep(e, x, span, cfg)


def _merge(ups: list[tuple[int, int]], e: np.ndarray, x: np.ndarray, cfg: dict[str, Any]) -> list[tuple[int, int]]:
    """Join ascents a rider would call one climb: a short break with a shallow dip, in metres or next to the climbs."""

    def joins(a: tuple[int, int], b: tuple[int, int]) -> bool:
        gap, dip = x[b[0]] - x[a[1]], e[a[1]] - e[b[0]]
        if gap > cfg["merge_gap_m"]:
            return False
        if dip <= cfg["merge_dip_m"]:
            return True
        # the relative dip only joins two sides steep enough to climb: a long false flat before a col (UBF km 60-72,
        # 1.6%) would otherwise swallow it and turn "13.8 km at 6.4%" into "25.9 km at 4.1%". Steepness, not
        # min_gain_m: a short steep ramp between two climbs is part of them even if it is no climb on its own
        smaller = min(e[a[1]] - e[a[0]], e[b[1]] - e[b[0]])
        return dip <= cfg["merge_dip_ratio"] * smaller and _is_steep(e, x, a, cfg) and _is_steep(e, x, b, cfg)

    merged: list[tuple[int, int]] = []
    for up in ups:
        merged.append(up)
        # a merge grows the last climb, which may now bridge a dip before it that was too deep for its smaller self
        while len(merged) > 1 and joins(merged[-2], merged[-1]):
            merged[-2:] = [(merged[-2][0], merged[-1][1])]
    return merged


def find_climbs(profile: Profile, cfg: dict[str, Any]) -> list[Climb]:
    e, x, t = profile.e, profile.x, profile.turns
    ups = [(t[k], t[k + 1]) for k in range(len(t) - 1) if e[t[k + 1]] > e[t[k]]]

    win = max(1, int(200 / profile.step))
    climbs = []
    for i0, i1 in _merge(ups, e, x, cfg):
        if not _is_climb(e, x, (i0, i1), cfg):
            continue
        # a merged climb reads like any other, foot to final top: net gain (top - foot), not the summed ascents, so
        # "4.6 km at 4.6%" is what the signposts and the gutter's line say; the dips stay in the length, so they
        # lower the average grade and with it the category, as they ease the effort
        length = float(x[i1] - x[i0])
        gain = float(e[i1] - e[i0])
        grade = gain / length * 100
        seg = e[i0 : i1 + 1]  # the whole span, so max grade is the steepest ramp of any of its parts
        peak = float(((seg[win:] - seg[:-win]).max() / (win * profile.step)) * 100) if len(seg) > win else grade
        climbs.append(
            Climb(
                start_km=float(x[i0]) / 1000,
                end_km=float(x[i1]) / 1000,
                gain_m=gain,
                avg_grade=grade,
                max_grade=max(peak, grade),
                label=_label(length * grade, cfg["categories"]),
            )
        )
    return climbs
