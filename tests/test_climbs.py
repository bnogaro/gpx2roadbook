from pathlib import Path

import numpy as np
import pytest

from roadbook.build import build
from roadbook.climbs import find_climbs
from roadbook.config import load_config
from roadbook.model import Track
from roadbook.profile import Profile

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"


def _profile(*legs: tuple[float, float]) -> Profile:
    """A straight route of (length_m, climb_m) legs, between 1 km of gentle descent on either side."""
    knots_x, knots_e = [0.0, 1000.0], [110.0, 100.0]
    for length, climb in (*legs, (1000, -10)):
        knots_x.append(knots_x[-1] + length)
        knots_e.append(knots_e[-1] + climb)
    dist = np.arange(0, knots_x[-1] + 1, 10.0)
    ele = np.interp(dist, knots_x, knots_e)
    lat = dist / 111_195.0
    return Profile(Track("t", lat, np.zeros_like(lat), ele, dist), step_m=25, smooth_m=100, swing_m=3)


def _climbs(*legs: tuple[float, float]) -> list[tuple[float, float]]:
    return [(c.start_km, c.end_km) for c in find_climbs(_profile(*legs), load_config()["climbs"])]


def test_a_short_shallow_dip_merges_two_climbs() -> None:
    # 20 m down over 400 m: deeper than merge_dip_m, but under a quarter of the smaller climb (90 m)
    assert _climbs((2000, 100), (400, -20), (1500, 90)) == [pytest.approx((1.0, 4.9), abs=0.15)]


@pytest.mark.parametrize(
    "dip",
    [
        pytest.param((400, -40), id="deep"),  # more than a quarter of the smaller climb
        pytest.param((1500, -15), id="long"),  # shallow, but a real break
    ],
)
def test_a_deep_or_long_dip_keeps_climbs_apart(dip: tuple[float, float]) -> None:
    assert len(_climbs((2000, 100), dip, (1500, 90))) == 2


def test_a_chain_of_three_merges_into_one() -> None:
    # the small middle climb (60 m) cannot bridge the second dip alone; merged with the first, it can
    climbs = _climbs((2000, 100), (300, -14), (1000, 60), (300, -20), (2000, 100))
    assert climbs == [pytest.approx((1.0, 6.6), abs=0.15)]


def test_a_false_flat_is_not_merged_into_the_climb_it_leads_to() -> None:
    # 1.5% over 10 km is no climb on its own, so the relative dip does not join it to the col beyond
    assert _climbs((10_000, 150), (300, -15), (3000, 200)) == [pytest.approx((11.3, 14.3), abs=0.15)]


def test_merged_stats_run_from_foot_to_final_top() -> None:
    (climb,) = find_climbs(_profile((2000, 100), (400, -20), (1500, 90)), load_config()["climbs"])
    # net gain, not the 190 m of summed ascents, and the grade that goes with it over the whole span
    assert climb.gain_m == pytest.approx(170, abs=4)
    assert climb.avg_grade == pytest.approx(climb.gain_m / (climb.length_km * 10))
    assert climb.max_grade == pytest.approx(6, abs=0.3)  # the steeper second ramp
    # recategorised as a whole: 2 km at 5% and 1.5 km at 6% are Cat 4 each, 3.9 km at 4.4% is Cat 3
    assert climb.label == "3"


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_ubf_merges_the_climb_split_at_km_114_only() -> None:
    climbs = [(round(c.start_km, 1), round(c.end_km, 1), c.label) for c in build(HILLY, load_config()).climbs]
    assert (111.5, 116.2, "3") in climbs
    assert not [c for c in climbs if 111.5 < c[0] < 116.2]
    # 1.6 km and 134 m apart: two climbs
    assert [c[:2] for c in climbs if 121 < c[0] < 125] == [(121.2, 122.8), (124.4, 131.0)]
