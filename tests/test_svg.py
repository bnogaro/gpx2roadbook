import re

import numpy as np
import pytest

from roadbook.model import Climb, Track
from roadbook.profile import Profile
from roadbook.svg import gutter_svg


def _ramp_track(length_m: float = 5000.0, grade: float = 0.04) -> Track:
    d = np.arange(0, length_m + 1, 50.0)
    lat = d / 111_195.0
    return Track("t", lat, np.zeros_like(lat), d * grade, d)


def _polyline_points(svg: str) -> list[tuple[float, float]]:
    m = re.search(r'<polyline[^>]*\bpoints="([^"]+)"', svg)
    assert m, f"no <polyline points=...> found in:\n{svg}"
    pairs = (pair.split(",") for pair in m.group(1).split())
    return [(float(x), float(y)) for x, y in pairs]


def _climb(start_km: float, end_km: float) -> Climb:
    return Climb(start_km=start_km, end_km=end_km, gain_m=40.0, avg_grade=4.0, max_grade=6.0, label="")


def test_gutter_svg_puts_a_dot_on_each_row_and_stretches_legs_between_them() -> None:
    profile = Profile(_ramp_track(), step_m=25, smooth_m=100, swing_m=3)
    # three rows unevenly spaced in km but evenly on paper, plus a continuation anchor on the bottom edge
    anchors = [(0.0, 2.0), (0.5, 12.0), (4.0, 22.0), (5.0, 30.0)]
    svg = gutter_svg(profile, [_climb(1, 2)], anchors, width_mm=4, height_mm=30, ele_range=(0, 200))

    dots = [float(y) for y in re.findall(r'<circle[^>]*\bcy="([^"]+)"', svg)]
    assert dots == [2.0, 12.0, 22.0]  # none for the continuation anchor
    points = _polyline_points(svg)
    assert points[0][1] == pytest.approx(2.0)
    assert points[-1][1] == pytest.approx(30.0)
    # km 1..2 lies inside the 10 mm between the 0.5 km and 4.0 km rows
    assert 'class="climb"' in svg


def test_gutter_svg_maps_ele_range_onto_its_width() -> None:
    profile = Profile(_ramp_track(), step_m=25, smooth_m=100, swing_m=3)  # 0 m -> 200 m over 5 km
    svg = gutter_svg(profile, [], [(0.0, 0.0), (5.0, 30.0)], width_mm=4, height_mm=30, ele_range=(0, 400))
    xs = [x for x, _ in _polyline_points(svg)]
    assert min(xs) == pytest.approx(0, abs=0.2)
    assert max(xs) == pytest.approx(2, abs=0.2)  # 200 m of a 400 m span fills half the gutter
