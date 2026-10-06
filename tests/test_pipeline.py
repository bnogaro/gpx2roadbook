import re
from pathlib import Path

import numpy as np
import pytest

from roadbook.build import _snap_to_climbs, build
from roadbook.config import load_config
from roadbook.kinds import KINDS
from roadbook.model import Climb, Item, Poi, Roadbook, Stop, Track
from roadbook.pois import classify, cluster
from roadbook.profile import Profile, turning_points
from roadbook.render import _row, render_html
from roadbook.snap import Snapper

SAMPLE = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"
HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"


def _track(dist_m: np.ndarray, ele: np.ndarray) -> Track:
    # straight line heading north, 1 degree of latitude ~ 111.2 km
    lat = dist_m / 111_195.0
    return Track("t", lat, np.zeros_like(lat), ele, dist_m)


def test_turning_points_ignore_small_swings() -> None:
    e = np.array([0, 5, 4, 6, 20, 19, 3, 10], dtype=float)
    # the 5 -> 4 dip (1 m) is noise at swing=3; a trailing swing that never reaches the threshold is not a turn
    assert turning_points(e, swing=3) == [0, 4, 6, 7]
    assert turning_points(e, swing=8) == [0, 4, 7]


def test_profile_gain_loss_on_a_ramp() -> None:
    d = np.arange(0, 5001, 50.0)
    t = _track(d, d * 0.05)  # constant 5% => +250 m over 5 km
    p = Profile(t, step_m=25, smooth_m=100, swing_m=3)
    assert p.gain(0, 5) == pytest.approx(250, abs=8)
    assert p.loss(0, 5) == pytest.approx(0, abs=1)


def test_snapper_uses_hint_to_disambiguate_out_and_back() -> None:
    # out 0->1 km north, then back south: the same lat is met twice
    lat = np.concatenate([np.linspace(0, 0.009, 50), np.linspace(0.009, 0, 50)])
    dist = np.concatenate([[0], np.cumsum(np.hypot(np.diff(lat) * 111_195, 0))])
    t = Track("oab", lat, np.zeros_like(lat), np.zeros_like(lat), dist)
    s = Snapper(t)
    out_km, _ = s.snap(0.0045, 0.0, hint_km=0.5)
    back_km, _ = s.snap(0.0045, 0.0, hint_km=1.5)
    assert out_km == pytest.approx(0.5, abs=0.05)
    assert back_km == pytest.approx(1.5, abs=0.05)


def test_classify_french_and_english_types() -> None:
    cats = load_config()["categories"]
    pois = [
        Poi("x", t, 0, 0)
        for t in ("Cimetière (eau potable)", "Toilettes", "Restauration rapide", "Restaurant", "drinking water")
    ]
    classify(pois, cats)
    assert [p.category for p in pois] == ["water", "toilets", "fastfood", "food", "water"]


def test_cluster_chains_close_pois_but_caps_span() -> None:
    pois = [Poi("p", "", 0, 0, km=k) for k in (1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 5.0)]
    stops = cluster(pois, gap_m=300, max_span_m=1000)
    assert [len(s.pois) for s in stops] == [6, 1, 1]  # 1.0..2.0 fit in 1 km span, 2.2 starts a new stop
    assert stops[0].km == 1.0


def test_stops_snap_to_the_nearest_climb_edge_within_reach() -> None:
    climbs = [Climb(10.0, 15.0, 400, 8.0, 10.0, "1")]
    near_foot, mid, near_top, far = (Stop([Poi("p", "", 0, 0, km=k)]) for k in (9.8, 12.0, 15.25, 15.5))
    snapped = _snap_to_climbs([near_foot, mid, near_top, far], climbs, snap_m=300)
    assert snapped == {(0, "foot"): near_foot, (0, "summit"): near_top}


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample GPX not present")
def test_sample_end_to_end() -> None:
    cfg = load_config()
    book = build(SAMPLE, cfg)
    assert book.length_km == pytest.approx(269.8, abs=0.5)
    assert book.items[0].kind == "start"
    assert book.items[-1].kind == "finish"
    kms = [i.km for i in book.items]
    assert kms == sorted(kms)
    assert all(i.dist_to_next is not None for i in book.items[:-1])
    assert sum(i.dist_to_next for i in book.items[:-1]) == pytest.approx(book.length_km, abs=1e-6)
    for layout in ("strip", "line"):
        cfg["render"]["layout"] = layout
        assert "<html" in render_html(book, cfg)


def _legs(html: str) -> list[str]:
    return re.findall(r'<div class="leg">([^<]*)</div>', html)


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample GPX not present")
def test_leg_elevation_is_opt_in() -> None:
    cfg = load_config()
    book = build(SAMPLE, cfg)
    assert cfg["render"]["leg_elevation"] is False
    html = render_html(book, cfg)
    # by default the bare distance rides on each row's main line instead of a leg line of its own
    assert not _legs(html)
    assert len(re.findall(r'<span class="dist">↓[\d.]+</span>', html)) == len(book.items) - 1
    cfg["render"]["leg_elevation"] = True
    legs = _legs(render_html(book, cfg))
    assert len(legs) == len(book.items) - 1
    assert all("↗" in leg and "↘" in leg for leg in legs)


def test_climb_row_with_a_stop_folds_its_category_onto_the_emoji() -> None:
    climb = Climb(10.0, 15.0, 400, 8.0, 10.0, "4")
    book = Roadbook("t", 50, 0, 0, [], [], [climb], 0, 0)
    bare = _row(Item("climb", 10.0, 0, climb=climb), book, avail=12.5, max_emojis=99)
    loaded = _row(Item("climb", 10.0, 0, climb=climb, emojis=[("🍔", 1)]), book, avail=12.5, max_emojis=99)
    assert bare["emojis"] == [("⛰️", 0)]
    assert bare["label"] == "Cat 4"
    assert loaded["emojis"] == [("⛰️", "4"), ("🍔", 1)]
    assert loaded["label"] == ""
    # even when capped to one emoji, the stop riding on the climb keeps its own
    capped = _row(Item("climb", 10.0, 0, climb=climb, emojis=[("🍔", 1), ("🚰", 1)]), book, avail=30, max_emojis=1)
    assert capped["emojis"][1:] == [("🍔", 1)]
    assert capped["more"]


def test_marker_rows_show_their_own_emoji_and_numbered_checkpoints_count_down() -> None:
    book = Roadbook("t", 50, 0, 0, [], [], [], 0, 0)
    rows = [
        _row(Item(kind, km, 0, label=label), book, avail=20, max_emojis=99)
        for kind, km, label in (("start", 0, "START"), ("checkpoint", 20, "CP1"), ("checkpoint", 30, "Lunch"))
    ]
    assert [r["emojis"] for r in rows] == [[("🟢", 0)], [("🚩", 0)], [("🚩", 0)]]
    assert [r["label"] for r in rows] == ["START", "CP1 · 30 to go", "Lunch"]


def test_summit_elevation_gives_way_to_its_stop() -> None:
    climb = Climb(10.0, 15.0, 400, 8.0, 10.0, "2")
    book = Roadbook("t", 50, 0, 0, [], [], [climb], 0, 0)
    top = Item("summit", 15.0, 1240.4, climb=climb, emojis=[("🍔", 1)])
    assert _row(top, book, avail=30, max_emojis=99)["label"] == "1240 m"
    assert _row(top, book, avail=12, max_emojis=99)["label"] == ""
    assert _row(top, book, avail=12, max_emojis=99)["emojis"] == [("🔝", 0), ("🍔", 1)]


def test_kinds_sort_a_summit_before_whatever_opens_at_its_km() -> None:
    order = sorted(KINDS, key=lambda k: KINDS[k].rank)
    assert order == ["start", "summit", "checkpoint", "stop", "climb", "finish"]


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_summit_rows_only_where_a_stop_sits_at_the_top() -> None:
    book = build(HILLY, load_config())
    summits = [i for i in book.items if i.kind == "summit"]
    assert 0 < len(summits) < len(book.climbs)
    assert all(i.stop is not None and i.km == i.climb.end_km for i in summits)
    # a stop moved onto a summit row is not listed a second time on its own
    assert not {id(i.stop) for i in summits} & {id(i.stop) for i in book.items if i.kind == "stop"}


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_gutter_follows_gutter_mm() -> None:
    cfg = load_config()
    book = build(HILLY, cfg)
    html = render_html(book, cfg)
    assert html.count('<svg class="gutter"') == html.count('<div class="strip">') > 0
    cfg["render"]["gutter_mm"] = 0
    assert 'class="gutter"' not in render_html(book, cfg)
