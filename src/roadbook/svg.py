from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .model import Climb
    from .profile import Profile


def gutter_svg(
    profile: Profile,
    climbs: list[Climb],
    anchors: list[tuple[float, float]],
    width_mm: float,
    height_mm: float,
    ele_range: tuple[float, float],
) -> str:
    """A vertical profile running down a strip, aligned to its rows rather than to a km scale.

    `anchors` are (km, y_mm) pairs, one per row: the stretch of road between two anchors is drawn across
    exactly the vertical space between those rows, so each row's dot sits on the curve where the rider is.
    Elevation grows to the right, mapped from `ele_range` (lowest, highest) onto the gutter's width.
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

    def area(mask: np.ndarray, cls: str, fill: str) -> str:
        if not mask.any():
            return ""
        xs, yy = x[mask], y[mask]
        pts = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(xs, yy, strict=True))
        return f'<polygon class="{cls}" points="0,{yy[0]:.2f} {pts} 0,{yy[-1]:.2f}" fill="{fill}"/>'

    shapes = [area(np.ones_like(km, dtype=bool), "ground", "#e5e5e5")]
    shapes += [area((km >= c.start_km) & (km <= c.end_km), "climb", "#f59e0b") for c in climbs]
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
