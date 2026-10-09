from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .model import Climb
    from .profile import Profile

type Scale = Sequence[Sequence[float | str]]  # [[from this grade up, %, colour], ...], by rising grade

GROUND = "#efefef"  # lighter than the gentlest climb, printed in grey too
CLIMB = "#f59e0b"  # a climb's colour without a grade scale
PIECE_KM = 1.0  # a climb is coloured a kilometre at a time, by each one's average grade, as race books do


def grade_colour(grade: float, scale: Scale) -> str:
    """The colour `scale` gives a grade, in %; below its first step, the first colour all the same."""
    colour = str(scale[0][1])
    for start, c in scale:
        if grade >= float(start):
            colour = str(c)
    return colour


def gutter_svg(  # noqa: PLR0913  the grade colours come on top of the geometry
    profile: Profile,
    climbs: list[Climb],
    anchors: list[tuple[float, float]],
    width_mm: float,
    height_mm: float,
    ele_range: tuple[float, float],
    *,
    grades: Scale = (),
) -> str:
    """A vertical profile running down a strip, aligned to its rows rather than to a km scale.

    `anchors` are (km, y_mm) pairs, one per row: the stretch of road between two anchors is drawn across
    exactly the vertical space between those rows, so each row's dot sits on the curve where the rider is.
    Elevation grows to the right, mapped from `ele_range` (lowest, highest) onto the gutter's width. Each stretch of
    a climb, about a kilometre, takes the colour `grades` gives its average grade: the steep ones show where they are,
    and a grade wavering around a step doesn't streak the climb.
    """
    lo, hi = ele_range
    span = max(hi - lo, 1.0)
    step_km = profile.step / 1000

    kms: list[float] = []
    ys: list[float] = []
    for (k0, y0), (k1, y1) in itertools.pairwise(anchors):
        seg = np.append(np.arange(k0, k1, step_km), k1) if k1 > k0 else np.array([k0, k1])
        kms += seg.tolist()
        ys += (y0 + (seg - k0) / max(k1 - k0, 1e-9) * (y1 - y0)).tolist()
    km = np.array(kms)
    y = np.array(ys)
    x = (np.interp(km * 1000, profile.x, profile.e) - lo) / span * width_mm

    def area(points: np.ndarray, cls: str, fill: str) -> str:
        xs, yy = x[points], y[points]
        pts = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(xs, yy, strict=True))
        return f'<polygon class="{cls}" points="0,{yy[0]:.2f} {pts} 0,{yy[-1]:.2f}" fill="{fill}"/>'

    shapes = [area(np.arange(len(km)), "ground", GROUND)]
    for c in climbs:
        points = np.flatnonzero((km >= c.start_km) & (km <= c.end_km))
        if not points.size:
            continue
        edges = np.linspace(c.start_km, c.end_km, max(1, round(c.length_km / PIECE_KM)) + 1)
        ele = np.interp(edges * 1000, profile.x, profile.e)
        piece_grades = np.diff(ele) / np.maximum(np.diff(edges) * 1000, 1e-9) * 100
        piece = np.clip(np.searchsorted(edges, km[points], side="right") - 1, 0, len(piece_grades) - 1)
        fills = [grade_colour(g, grades) if grades else CLIMB for g in piece_grades[piece]]
        start = 0
        for end in [*(i + 1 for i in range(len(fills) - 1) if fills[i + 1] != fills[i]), len(fills)]:
            # one point into the next stretch, so two colours meet without a gap between them
            shapes.append(area(points[start : end + 1], "climb", fills[start]))
            start = end
    line = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(x, y, strict=True))
    dots = "".join(
        f'<circle cx="{(np.interp(k * 1000, profile.x, profile.e) - lo) / span * width_mm:.2f}" '
        f'cy="{yy:.2f}" r="0.45" fill="#111"/>'
        for k, yy in anchors
        if yy < height_mm  # a trailing anchor on the bottom edge only continues the line
    )
    return (
        f'<svg class="gutter" viewBox="0 0 {width_mm} {height_mm}" width="{width_mm}mm" height="{height_mm}mm">'
        f"{''.join(shapes)}"
        f'<polyline points="{line}" fill="none" stroke="#444" stroke-width="0.25"/>'
        f"{dots}"
        f"</svg>"
    )
