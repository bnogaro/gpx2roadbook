import re

import numpy as np
import pytest

from roadbook.model import Climb, Track
from roadbook.profile import Profile
from roadbook.svg import profile_svg


def _flat_track(length_m: float = 5000.0, ele: float = 100.0) -> Track:
    d = np.arange(0, length_m + 1, 50.0)
    lat = d / 111_195.0
    return Track("t", lat, np.zeros_like(lat), np.full_like(d, ele), d)


def _ramp_track(length_m: float = 5000.0, grade: float = 0.04) -> Track:
    d = np.arange(0, length_m + 1, 50.0)
    lat = d / 111_195.0
    return Track("t", lat, np.zeros_like(lat), d * grade, d)


def _polyline_points(svg: str) -> list[tuple[float, float]]:
    m = re.search(r'<polyline[^>]*\bpoints="([^"]+)"', svg)
    assert m, f"no <polyline points=...> found in:\n{svg}"
    return [tuple(map(float, pair.split(","))) for pair in m.group(1).split()]


def _climb_rects(svg: str) -> list[tuple[float, float]]:
    return [
        (float(m.group("x")), float(m.group("w")))
        for m in re.finditer(r'<rect class="climb"[^>]*\bx="(?P<x>[^"]+)"[^>]*\bwidth="(?P<w>[^"]+)"', svg)
    ]


def _climb(start_km: float, end_km: float) -> Climb:
    return Climb(start_km=start_km, end_km=end_km, gain_m=40.0, avg_grade=4.0, max_grade=6.0, label="")


def test_profile_svg_has_requested_viewbox_and_size():
    profile = Profile(_flat_track(), step_m=25, smooth_m=100, swing_m=3)
    svg = profile_svg(profile, climbs=[], km_a=0, km_b=5, width_mm=30, height_mm=10)

    assert svg.strip().startswith("<svg") and svg.strip().endswith("</svg>")
    assert 'viewBox="0 0 30 10"' in svg
    assert 'width="30mm"' in svg
    assert 'height="10mm"' in svg


def test_profile_svg_draws_a_rising_line_for_a_climb():
    profile = Profile(_ramp_track(), step_m=25, smooth_m=100, swing_m=3)
    svg = profile_svg(profile, climbs=[], km_a=0, km_b=5, width_mm=30, height_mm=10)
    points = _polyline_points(svg)

    assert len(points) > 2
    xs = [x for x, _ in points]
    assert min(xs) == pytest.approx(0, abs=0.1)
    assert max(xs) == pytest.approx(30, abs=0.1)
    # higher ground plots nearer the top of the box (smaller y), so the last point
    # of a steady climb sits above the first
    assert points[-1][1] < points[0][1]


def test_profile_svg_shades_only_climbs_overlapping_the_range():
    profile = Profile(_ramp_track(), step_m=25, smooth_m=100, swing_m=3)
    climbs = [_climb(1, 2), _climb(6, 7)]  # second is outside the 0..5 km window requested below
    svg = profile_svg(profile, climbs, km_a=0, km_b=5, width_mm=30, height_mm=10)

    rects = _climb_rects(svg)
    assert len(rects) == 1
    x, width = rects[0]
    assert x == pytest.approx(6.0, abs=0.1)      # (1 - 0) / 5 * 30mm
    assert width == pytest.approx(6.0, abs=0.1)  # (2 - 1) / 5 * 30mm
