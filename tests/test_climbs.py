from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from roadbook.build import build
from roadbook.cli import app
from roadbook.climbs import find_climbs
from roadbook.config import Settings, load_config
from roadbook.model import Track
from roadbook.profile import WALL_GRADE, Profile, flatten_walls

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
CLIMBS = Settings.of(load_config()).climbs


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
    return [(c.start_km, c.end_km) for c in find_climbs(_profile(*legs), CLIMBS)]


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
    (climb,) = find_climbs(_profile((2000, 100), (400, -20), (1500, 90)), CLIMBS)
    # net gain, not the 190 m of summed ascents, and the grade that goes with it over the whole span
    assert climb.gain_m == pytest.approx(170, abs=4)
    assert climb.avg_grade == pytest.approx(climb.gain_m / (climb.length_km * 10))
    assert climb.max_grade == pytest.approx(6, abs=0.3)  # the steeper second ramp
    # recategorised as a whole: 2 km at 5% and 1.5 km at 6% are Cat 4 each, 3.9 km at 4.4% is Cat 3
    assert climb.label == "3"


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_ubf_merges_the_climb_split_at_km_114_only() -> None:
    climbs = [
        (round(c.start_km, 1), round(c.end_km, 1), c.label) for c in build(HILLY, Settings.of(load_config())).climbs
    ]
    assert (111.5, 116.2, "3") in climbs
    assert not [c for c in climbs if 111.5 < c[0] < 116.2]
    # the "climb" at km 121.2-122.8 was a hill no road climbs (41 % over 200 m, up and back down): flattened away
    assert [c[:2] for c in climbs if 119 < c[0] < 125] == [(124.4, 131.0)]


def test_an_ascent_under_min_gain_is_no_climb() -> None:
    climbs = CLIMBS
    bump = _profile((1000, 60))  # 6% but only 60 m: under the 80 m default
    assert find_climbs(bump, climbs) == []
    assert len(find_climbs(bump, replace(climbs, min_gain_m=30))) == 1


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_min_climb_flag_sets_the_threshold(tmp_path: Path) -> None:
    out = tmp_path / "rb.html"
    for flag, climbs in (([], 9), (["--min-climb", "30"], 17)):
        result = CliRunner().invoke(app, [str(HILLY), "-o", str(out), *flag])
        assert result.exit_code == 0, result.output
        assert f"; {climbs} climbs" in result.output


def _bumpy(bump_m: float, width_m: float) -> Profile:
    """5 km at 3 % between 1 km of gentle descent on either side, sampled every 20 m as a DEM gives it, with a hump
    `bump_m` high and `width_m` wide halfway up, smoothed as the defaults smooth it."""
    dist = np.arange(0, 7001, 20.0)
    ele = np.interp(dist, [0, 1000, 6000, 7000], [110, 100, 250, 240])
    ele += bump_m * np.clip(1 - np.abs(dist - 3500) / (width_m / 2), 0, None)
    lat = dist / 111_195.0
    return Profile(Track("t", lat, np.zeros_like(lat), ele, dist), step_m=25, smooth_m=200, swing_m=3)


@pytest.mark.parametrize("width_m", [40, 200])
def test_a_bump_in_the_elevation_data_is_no_steep_ramp(width_m: float) -> None:
    # 15 m over a couple of hundred metres, as on the Col de la Pigière (3.3 %, shown at 15 % max): a narrow spike is
    # the median's, a wider one survives the smoothing and read over 200 m as a ramp of about 10 %
    (climb,) = find_climbs(_bumpy(15, width_m), CLIMBS)
    assert climb.avg_grade == pytest.approx(3, abs=0.2)
    assert climb.max_grade < 6


def test_a_short_steep_ramp_keeps_its_grade() -> None:
    # 400 m at 12 % up a 3 % climb: a real ramp, longer than the max's window, is no bump
    (climb,) = find_climbs(_profile((2000, 60), (400, 48), (2000, 60)), CLIMBS)
    assert climb.max_grade == pytest.approx(12, abs=1)


def _climb_with(extra: np.ndarray, dist: np.ndarray) -> Profile:
    """5 km at 3 % between 1 km of gentle descent on either side, every 20 m, plus `extra` metres at `dist`."""
    ele = np.interp(dist, [0, 1000, 6000, 7000], [110, 100, 250, 240]) + extra
    lat = dist / 111_195.0
    return Profile(Track("t", lat, np.zeros_like(lat), ele, dist), step_m=25, smooth_m=200, swing_m=3)


def test_a_hill_no_road_could_climb_is_flattened() -> None:
    # 80 m up and down again within 400 m, as entrainement_ubf's km 122 does: 40 % a side, more than any road
    dist = np.arange(0, 7001, 20.0)
    (climb,) = find_climbs(_climb_with(80 * np.clip(1 - np.abs(dist - 3500) / 200, 0, None), dist), CLIMBS)
    assert climb.gain_m == pytest.approx(150, abs=5)
    assert climb.max_grade < 5


def test_a_step_no_road_could_climb_is_spread_out() -> None:
    # 60 m in 100 m that stays up: the height is the route's, its grade is no road's, so it is spread at a road's
    dist = np.arange(0, 7001, 20.0)
    (climb,) = find_climbs(_climb_with(60 * np.clip((dist - 3450) / 100, 0, 1), dist), CLIMBS)
    assert climb.gain_m == pytest.approx(210, abs=5)
    assert 15 < climb.max_grade <= WALL_GRADE * 100


def test_a_real_wall_keeps_its_grade() -> None:
    # 300 m at 20 %: as steep as roads get, but a road's
    (climb,) = find_climbs(_profile((2000, 60), (300, 60), (2000, 60)), CLIMBS)
    assert climb.max_grade == pytest.approx(20, abs=2)  # the smoothing rounds its corners off


def test_a_track_shorter_than_the_wall_window_is_left_alone() -> None:
    assert list(flatten_walls(np.array([0.0, 50.0, 0.0]), 25)) == [0, 50, 0]
