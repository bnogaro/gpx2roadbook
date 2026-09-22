from pathlib import Path

import numpy as np
import pytest

from roadbook.build import build
from roadbook.config import load_config
from roadbook.model import Poi, Track
from roadbook.pois import classify, cluster
from roadbook.profile import Profile, turning_points
from roadbook.render import render_html
from roadbook.snap import Snapper

SAMPLE = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"


def _track(dist_m: np.ndarray, ele: np.ndarray) -> Track:
    # straight line heading north, 1 degree of latitude ~ 111.2 km
    lat = dist_m / 111_195.0
    return Track("t", lat, np.zeros_like(lat), ele, dist_m)


def test_turning_points_ignore_small_swings():
    e = np.array([0, 5, 4, 6, 20, 19, 3, 10], dtype=float)
    # the 5 -> 4 dip (1 m) is noise at swing=3; a trailing swing that never reaches the threshold is not a turn
    assert turning_points(e, swing=3) == [0, 4, 6, 7]
    assert turning_points(e, swing=8) == [0, 4, 7]


def test_profile_gain_loss_on_a_ramp():
    d = np.arange(0, 5001, 50.0)
    t = _track(d, d * 0.05)  # constant 5% => +250 m over 5 km
    p = Profile(t, step_m=25, smooth_m=100, swing_m=3)
    assert p.gain(0, 5) == pytest.approx(250, abs=8)
    assert p.loss(0, 5) == pytest.approx(0, abs=1)


def test_snapper_uses_hint_to_disambiguate_out_and_back():
    # out 0->1 km north, then back south: the same lat is met twice
    lat = np.concatenate([np.linspace(0, 0.009, 50), np.linspace(0.009, 0, 50)])
    dist = np.concatenate([[0], np.cumsum(np.hypot(np.diff(lat) * 111_195, 0))])
    t = Track("oab", lat, np.zeros_like(lat), np.zeros_like(lat), dist)
    s = Snapper(t)
    out_km, _ = s.snap(0.0045, 0.0, hint_km=0.5)
    back_km, _ = s.snap(0.0045, 0.0, hint_km=1.5)
    assert out_km == pytest.approx(0.5, abs=0.05)
    assert back_km == pytest.approx(1.5, abs=0.05)


def test_classify_french_and_english_types():
    cats = load_config()["categories"]
    pois = [Poi("x", t, 0, 0) for t in ("Cimetière (eau potable)", "Toilettes", "Restauration rapide", "Restaurant", "drinking water")]
    classify(pois, cats)
    assert [p.category for p in pois] == ["water", "toilets", "fastfood", "food", "water"]


def test_cluster_chains_close_pois_but_caps_span():
    pois = [Poi("p", "", 0, 0, km=k) for k in (1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 5.0)]
    stops = cluster(pois, gap_m=300, max_span_m=1000)
    assert [len(s.pois) for s in stops] == [6, 1, 1]  # 1.0..2.0 fit in 1 km span, 2.2 starts a new stop
    assert stops[0].km == 1.0


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample GPX not present")
def test_sample_end_to_end():
    cfg = load_config()
    book = build(SAMPLE, cfg)
    assert book.length_km == pytest.approx(269.8, abs=0.5)
    assert book.items[0].kind == "start" and book.items[-1].kind == "finish"
    kms = [i.km for i in book.items]
    assert kms == sorted(kms)
    assert all(i.dist_to_next is not None for i in book.items[:-1])
    assert sum(i.dist_to_next for i in book.items[:-1]) == pytest.approx(book.length_km, abs=1e-6)
    for layout in ("strip", "line"):
        cfg["render"]["layout"] = layout
        assert "<html" in render_html(book, cfg)
