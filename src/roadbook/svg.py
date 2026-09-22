from __future__ import annotations

import numpy as np

from .model import Climb
from .profile import Profile


def profile_svg(profile: Profile, climbs: list[Climb], km_a: float, km_b: float, width_mm: float, height_mm: float) -> str:
    """A small elevation profile over [km_a, km_b], with climbs shaded, sized in mm to print true-to-scale."""
    x = np.arange(km_a * 1000, km_b * 1000 + profile.step, profile.step)
    ele = np.interp(x, profile.x, profile.e)

    lo, hi = float(ele.min()), float(ele.max())
    span = max(hi - lo, 1.0)  # avoid a divide-by-zero on a perfectly flat segment
    px = (x - x[0]) / (x[-1] - x[0]) * width_mm
    py = height_mm - (ele - lo) / span * height_mm
    points = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(px, py))

    def to_x(km: float) -> float:
        return (km - km_a) / (km_b - km_a) * width_mm

    bands = "".join(
        f'<rect class="climb" x="{to_x(c.start_km):.2f}" y="0" width="{to_x(c.end_km) - to_x(c.start_km):.2f}" '
        f'height="{height_mm}" fill="#fbe3c4"/>'
        for c in climbs
        if c.end_km > km_a and c.start_km < km_b
    )

    return (
        f'<svg viewBox="0 0 {width_mm} {height_mm}" width="{width_mm}mm" height="{height_mm}mm">'
        f"{bands}"
        f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="0.3"/>'
        f"</svg>"
    )
