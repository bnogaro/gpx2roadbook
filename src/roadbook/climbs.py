from __future__ import annotations

from typing import Any

from .model import Climb
from .profile import Profile


def _label(score: float, categories: list[list]) -> str:
    for name, threshold in categories:  # sorted from hardest to easiest
        if score >= threshold:
            return str(name)
    return ""


def find_climbs(profile: Profile, cfg: dict[str, Any]) -> list[Climb]:
    e, x, t = profile.e, profile.x, profile.turns
    ups = [(t[k], t[k + 1]) for k in range(len(t) - 1) if e[t[k + 1]] > e[t[k]]]

    merged: list[tuple[int, int]] = []
    for i0, i1 in ups:
        if merged and e[merged[-1][1]] - e[i0] <= cfg["merge_dip_m"]:
            merged[-1] = (merged[-1][0], i1)
        else:
            merged.append((i0, i1))

    win = max(1, int(200 / profile.step))
    climbs = []
    for i0, i1 in merged:
        length = float(x[i1] - x[i0])
        gain = float(e[i1] - e[i0])
        if length <= 0:
            continue
        grade = gain / length * 100
        if gain < cfg["min_gain_m"] or length < cfg["min_length_m"] or grade < cfg["min_grade_pct"]:
            continue
        seg = e[i0 : i1 + 1]
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
