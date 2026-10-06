import re
from pathlib import Path

import numpy as np
import pytest

from roadbook.build import _snap_to_climbs, build
from roadbook.config import load_config
from roadbook.kinds import KINDS
from roadbook.model import Climb, Glyph, Item, Poi, Roadbook, Stop, Track
from roadbook.pois import classify, cluster, emoji_counts
from roadbook.profile import Profile, turning_points
from roadbook.render import COUNT_W, EMOJI_W, RowLayout, _fit, _row, _wrap, render_html
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


def test_profile_ele_range_spans_the_stretch_or_falls_back_to_its_start() -> None:
    d = np.arange(0, 5001, 50.0)
    p = Profile(_track(d, d * 0.05), step_m=25, smooth_m=100, swing_m=3)
    assert p.ele_range(1, 2) == pytest.approx((50, 100), abs=1)
    # no grid point between the two km: both ends collapse onto the elevation where the stretch starts
    assert p.ele_range(1.001, 1.002) == (p.ele_at(1.001), p.ele_at(1.001))


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


def test_cluster_cuts_long_runs_at_their_widest_gap_keeping_one_spot_together() -> None:
    # one chain under a 5 km gap, too long for 1.5 km: the cut falls in the 1.5 km hole, not inside the 123.9 cluster
    kms = (122.4, 122.4, 123.9, 123.9, 123.9, 124.0, 124.2, 124.9, 125.0)
    stops = cluster([Poi("p", "", 0, 0, km=k) for k in kms], gap_m=5000, max_span_m=1500)
    assert [(s.km, s.km_end) for s in stops] == [(122.4, 122.4), (123.9, 125.0)]


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_max_span_follows_gap_unless_set() -> None:
    def spans(**stops_cfg: float) -> float:
        cfg = load_config()
        cfg["stops"].update(stops_cfg)
        return max(s.km_end - s.km for s in build(HILLY, cfg).stops)

    assert spans() <= 1.5  # default gap 500 m -> 1.5 km
    assert spans(gap_m=5000) > 1.5  # the span grows with the gap instead of capping it
    assert spans(gap_m=5000, max_span_m=1000) <= 1.0


def test_a_long_stop_shows_where_it_ends() -> None:
    book = Roadbook("t", 50, 0, 0, [], [], [], 0, 0)
    long_stop, short_stop = (Stop([Poi("p", "", 0, 0, km=10.0), Poi("p", "", 0, 0, km=k)]) for k in (11.2, 10.4))
    long_row = _row(Item("stop", 10.0, 0, stop=long_stop), book, _layout(20))
    assert long_row["sub"] == long_row["sub_short"] == "→11.2"
    assert long_row["h"] > _row(Item("stop", 10.0, 0, stop=short_stop), book, _layout(20))["h"]


def test_stops_snap_to_the_nearest_climb_edge_within_reach() -> None:
    climbs = [Climb(10.0, 15.0, 400, 8.0, 10.0, "1")]
    near_foot, mid, near_top, far = (Stop([Poi("p", "", 0, 0, km=k)]) for k in (9.8, 12.0, 15.25, 15.5))
    snapped = _snap_to_climbs([near_foot, mid, near_top, far], climbs, snap_m=300)
    assert snapped == {(0, "foot"): near_foot, (0, "summit"): near_top}


def _stops(*kms: float) -> list[Stop]:
    return [Stop([Poi("p", "", 0, 0, km=k)]) for k in kms]


def test_a_stop_whose_nearest_edge_is_taken_falls_back_to_the_next_free_one() -> None:
    # climb 0's summit at 15.0 and climb 1's foot at 15.4: both stops are nearest the summit
    climbs = [Climb(10.0, 15.0, 400, 8.0, 10.0, "1"), Climb(15.4, 18.0, 200, 6.0, 9.0, "3")]
    top, between = _stops(15.05, 15.15)
    assert _snap_to_climbs([top, between], climbs, snap_m=300) == {(0, "summit"): top, (1, "foot"): between}
    # the closer stop wins the edge even when it comes later
    assert _snap_to_climbs([between, top], climbs, snap_m=300) == {(0, "summit"): top, (1, "foot"): between}
    # no other edge in reach: the second stop stays a row of its own
    lone = [Climb(10.0, 15.0, 400, 8.0, 10.0, "1")]
    assert _snap_to_climbs([top, between], lone, snap_m=300) == {(0, "summit"): top}


def test_a_stop_between_a_summit_and_the_next_foot_takes_the_free_one() -> None:
    climbs = [Climb(10.0, 15.0, 400, 8.0, 10.0, "1"), Climb(15.4, 18.0, 200, 6.0, 9.0, "3")]
    at_foot, between = _stops(15.4, 15.25)
    # between is closer to the foot (150 m) but at_foot sits right on it, so between takes the summit (250 m)
    assert _snap_to_climbs([between, at_foot], climbs, snap_m=300) == {(1, "foot"): at_foot, (0, "summit"): between}


def test_snapping_ties_are_deterministic() -> None:
    # kms exact in binary, so the gaps really tie
    climbs = [Climb(10.0, 15.0, 400, 8.0, 10.0, "1"), Climb(15.5, 18.0, 200, 6.0, 9.0, "3")]
    # midway between the summit and the next foot: the earlier edge wins
    (mid,) = _stops(15.25)
    assert _snap_to_climbs([mid], climbs, snap_m=300) == {(0, "summit"): mid}
    # two stops equally far from one foot: the earlier stop wins, the later finds no other edge in reach
    before, after = _stops(9.75, 10.25)
    assert _snap_to_climbs([after, before], climbs[:1], snap_m=300) == {(0, "foot"): before}


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


def _layout(avail: float, *, max_emojis: int = 99, leg_elevation: bool = False) -> RowLayout:
    return RowLayout(avail=avail, max_emojis=max_emojis, sep=" · ", leg_elevation=leg_elevation, range_m=1000)


def test_row_layout_picks_a_leg_line_or_a_bare_distance() -> None:
    book = Roadbook("t", 50, 0, 0, [], [], [], 0, 0)
    it = Item("stop", 10.0, 0, dist_to_next=4.3, gain_to_next=60, loss_to_next=12)
    bare = _row(it, book, _layout(20))
    assert (bare["leg"], bare["dist"], bare["leg_short"]) == (None, "↓4.3", "4.3")
    legged = _row(it, book, _layout(20, leg_elevation=True))
    assert (legged["leg"], legged["dist"], legged["leg_short"]) == ("4.3 · ↗️60 ↘️12", None, "4.3 ↗️60")
    assert legged["h"] > bare["h"]


def test_climb_row_with_a_stop_folds_its_category_onto_the_emoji() -> None:
    climb = Climb(10.0, 15.0, 400, 8.0, 10.0, "4")
    book = Roadbook("t", 50, 0, 0, [], [], [climb], 0, 0)
    bare = _row(Item("climb", 10.0, 0, climb=climb), book, _layout(12.5))
    loaded = _row(Item("climb", 10.0, 0, climb=climb, emojis=[Glyph("🍔")]), book, _layout(12.5))
    assert bare["emojis"] == [Glyph("⛰️")]
    assert bare["label"] == "Cat 4"
    assert loaded["emojis"] == [Glyph("⛰️", "4"), Glyph("🍔")]
    assert loaded["label"] == ""
    # even when capped to one emoji, the stop riding on the climb keeps its own
    capped = _row(
        Item("climb", 10.0, 0, climb=climb, emojis=[Glyph("🍔"), Glyph("🚰")]), book, _layout(30, max_emojis=1)
    )
    assert capped["emojis"][1:] == [Glyph("🍔")]
    assert capped["more"]


def test_marker_rows_show_their_own_emoji_and_numbered_checkpoints_count_down() -> None:
    book = Roadbook("t", 50, 0, 0, [], [], [], 0, 0)
    rows = [
        _row(Item(kind, km, 0, label=label), book, _layout(20))
        for kind, km, label in (("start", 0, "START"), ("checkpoint", 20, "CP1"), ("checkpoint", 30, "Lunch"))
    ]
    assert [r["emojis"] for r in rows] == [[Glyph("🟢")], [Glyph("🚩")], [Glyph("🚩")]]
    assert [r["label"] for r in rows] == ["START", "CP1 · 30 to go", "Lunch"]


def test_summit_elevation_gives_way_to_its_stop() -> None:
    climb = Climb(10.0, 15.0, 400, 8.0, 10.0, "2")
    book = Roadbook("t", 50, 0, 0, [], [], [climb], 0, 0)
    top = Item("summit", 15.0, 1240.4, climb=climb, emojis=[Glyph("🍔")])
    assert _row(top, book, _layout(30))["label"] == "1240 m"
    assert _row(top, book, _layout(12))["label"] == ""
    assert _row(top, book, _layout(12))["emojis"] == [Glyph("🔝"), Glyph("🍔")]


def test_stop_glyphs_carry_a_count_only_when_pois_share_an_emoji() -> None:
    cats = load_config()["categories"]
    pois = [Poi("p", "", 0, 0, category=c) for c in ("bakery", "water", "bakery")]
    assert emoji_counts(Stop(pois), cats) == [Glyph("🚰"), Glyph("🥖", "2")]


def test_fit_pays_one_count_width_per_superscript() -> None:
    glyphs = [Glyph("🚰", "12"), Glyph("🥖", "3"), Glyph("🚻")]
    _, _, used = _fit(glyphs, avail=99, max_emojis=99)
    assert used == pytest.approx(3 * EMOJI_W + 2 * COUNT_W)


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


@pytest.mark.parametrize(
    ("kind", "avail", "shown"), [("summit", 12.5, ["🔝", "🚰", "+"]), ("climb", 12.5, ["⛰️", "🚰"])]
)
def test_a_crowded_stop_on_a_climb_or_summit_keeps_its_first_emoji(kind: str, avail: float, shown: list[str]) -> None:
    # 🚰 x3 plus two more kinds: with its count and a "+", not even the first fits beside ⛰️⁴ or 🔝
    climb = Climb(10.0, 15.0, 400, 8.0, 10.0, "4")
    book = Roadbook("t", 50, 0, 0, [], [], [climb], 0, 0)
    glyphs = [Glyph("🚰", "3"), Glyph("🚻"), Glyph("🥖")]
    row = _row(Item(kind, 10.0, 0, climb=climb, emojis=glyphs), book, _layout(avail))
    assert [g.emoji for g in row["emojis"]] + (["+"] if row["more"] else []) == shown
    assert row["emojis"][1].sup == ""  # the count gave way first


def test_wrap_with_one_width_is_fit() -> None:
    glyphs = [Glyph("🚰", "2"), Glyph("🚻"), Glyph("🥖"), Glyph("☕")]
    for avail in (5.0, 12.5, 30.0):
        lines, more, w = _wrap(glyphs, [avail], 99)
        assert (lines[0], more, w) == _fit(glyphs, avail, 99)


def test_wrap_carries_a_crowded_stop_onto_the_next_line() -> None:
    glyphs = [Glyph(e) for e in "🚰🚻🥖☕🛒⛽🍔"]
    lines, more, _ = _wrap(glyphs, [12.5, 18.5], 99)
    # two fit beside the km; the next line takes three and keeps room for the "+" only there
    assert [len(line) for line in lines] == [2, 3]
    assert more
    lines, more, _ = _wrap(glyphs[:4], [12.5, 18.5], 99)
    assert [len(line) for line in lines] == [2, 2]
    assert not more


def test_a_crowded_row_grows_a_line_and_a_quiet_one_does_not() -> None:
    book = Roadbook("t", 50, 0, 0, [], [], [], 0, 0)
    crowded = Item("stop", 10.0, 0, emojis=[Glyph(e) for e in "🚰🚻🥖☕"])
    quiet = Item("stop", 10.0, 0, emojis=[Glyph("🚰")])
    one = _layout(12.5)
    two = RowLayout(
        avail=12.5, max_emojis=99, sep=" ", leg_elevation=False, range_m=1000, emoji_lines=2, wrap_avail=18.5
    )
    cut = _row(crowded, book, one)
    assert cut["more"]
    assert not cut["emoji_lines"]
    wrapped = _row(crowded, book, two)
    assert len(wrapped["emojis"]) + sum(map(len, wrapped["emoji_lines"])) == 4
    assert not wrapped["more"]
    assert wrapped["h"] > _row(quiet, book, two)["h"] == _row(quiet, book, one)["h"]
