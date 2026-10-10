import datetime as dt
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from roadbook import settings as st
from roadbook.booklet import Block, Booklet, Clock, render_booklet, text_mm, where, wrap
from roadbook.build import build
from roadbook.cli import app
from roadbook.config import load_config
from roadbook.imposition import FORMATS, centre, chosen, page_count, sides
from roadbook.model import Climb, Item, Poi, Roadbook, Stop, Track
from roadbook.opening import Ride, Verdict, Window
from roadbook.osm import M_PER_DEG, Report
from roadbook.profile import Profile
from roadbook.render import render_html
from roadbook.snap import Snapper
from roadbook.sun import local, sun_times

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
A5, A6 = FORMATS["a5"], FORMATS["a6"]
EN = "\u2013"  # the en dash between times
SUNDAY = dt.date(2026, 10, 11)


def _t(hhmm: str, day: dt.date = SUNDAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.fromisoformat(hhmm))


# --- the imposition


@pytest.mark.parametrize(("name", "n"), [("a5", 4), ("a5", 8), ("a5", 20), ("a6", 8), ("a6", 16), ("a6", 24)])
def test_each_page_is_printed_once_with_its_neighbour_behind_it(name: str, n: int) -> None:
    f = FORMATS[name]
    printed = sides(n, f)
    assert sorted(slot.page for side in printed for slot in side) == list(range(n))
    width = 2 * f.page[0]
    for front, back in zip(printed[::2], printed[1::2], strict=True):
        behind = {(width - s.x - f.page[0], s.y): s.page for s in back}  # the back is the front turned over
        for s in front:
            leaf = sorted((s.page, behind[s.x, s.y]))
            assert leaf[1] - leaf[0] == 1  # the two sides of one leaf: a right-hand page and the next
            assert leaf[0] % 2 == 0  # the right-hand one first: page 1, 3, 5…
    assert page_count(n - 1, f) == n


def test_an_a5_booklet_folds_from_its_outer_sheet_in() -> None:
    pages = [[(s.page + 1, s.x) for s in side] for side in sides(8, A5)]
    assert pages == [[(8, 0), (1, 148.5)], [(2, 0), (7, 148.5)], [(6, 0), (3, 148.5)], [(4, 0), (5, 148.5)]]
    assert (centre(8) + 1, centre(8) + 2) == (4, 5)  # side by side, on the innermost sheet's inner side


def test_cut_a6_sheets_stack_in_order() -> None:
    first, *_ = sides(16, A6)  # 4 signature sheets on 2 A4 ones: the first on top of sheet 1, the third below it
    assert [(s.page + 1, s.x, s.y) for s in first] == [(16, 0, 0), (1, 105, 0), (12, 0, 148.5), (5, 105, 148.5)]
    assert page_count(9, A6) == 16  # no blank half sheet to fold in


def test_the_format_follows_the_setting(caplog: pytest.LogCaptureFixture) -> None:
    cfg = load_config()
    assert cfg["booklet"]["format"] == "A5"  # as the GUI offers it, and the paper sizes are written
    assert st.BOOKLET.choices == ("A5", "A6", "off")
    assert chosen(cfg) is A5
    cfg["booklet"]["format"] = "a6"  # whatever the case
    assert chosen(cfg) is A6
    cfg["booklet"]["format"] = "off"
    assert chosen(cfg) is None
    assert not st.booklet(cfg)
    cfg["booklet"]["format"] = "Off"
    assert not st.booklet(cfg)
    cfg["booklet"]["format"] = "A4"
    assert chosen(cfg) is A5
    assert "no format 'A4' (A5, A6 or off)" in caplog.text


# --- the sun and the side of the road


def test_the_sun_rises_and_sets_when_the_almanac_says() -> None:
    times = sun_times(48.8566, 2.3522, dt.date(2026, 6, 21))  # Paris, midsummer: 05:47 and 21:58 in summer time
    assert times is not None
    rise, sets = times
    assert rise.strftime("%H:%M") in {"03:47", "03:48"}
    assert sets.strftime("%H:%M") in {"19:57", "19:58"}
    assert sun_times(69.65, 18.96, dt.date(2026, 6, 21)) is None  # Tromsø: the midnight sun
    assert local(rise) == rise.astimezone().replace(tzinfo=None)


def test_a_place_is_on_the_left_or_right_of_the_road_as_the_rider_goes() -> None:
    lat = np.linspace(45.0, 45.01, 11)
    snapper = Snapper(Track("north", lat, np.full_like(lat, 5.0), np.zeros_like(lat), (lat - 45) * M_PER_DEG))
    east = 5.0 + 100 / M_PER_DEG / math.cos(math.radians(45))  # 100 m east
    assert snapper.snap(45.005, east)[2] == 1  # going north: the east is on the right
    assert snapper.snap(45.005, 10 - east)[2] == -1
    assert snapper.snap(45.005, 5.0)[2] == 0  # on the road itself


def test_the_arrival_time_at_a_km_is_inside_its_window() -> None:
    ride = Ride.from_cfg(load_config()["ride"] | {"date": "2026-10-11", "start": "07:00", "speed_kmh": 20})
    at, window = ride.at(50), ride.window(50, 50)
    assert at == _t("09:30")
    assert window is not None
    assert at is not None
    assert window.early < at < window.late
    assert Ride().at(50) is None
    assert ride.riding_h(10, 60) == pytest.approx(2.5)  # 50 km at 20 km/h on the flat, no margin
    assert Ride().riding_h(10, 60) is None


# --- a stop's lines


def _track(km: float) -> Track:
    """A straight road north from 45° N, 1° E, a point every 100 m."""
    dist = np.arange(0, km * 1000 + 1, 100.0)
    return Track("t", 45.0 + dist / M_PER_DEG, np.full_like(dist, 1.0), np.zeros_like(dist), dist)


def _poi(km: float, category: str, name: str = "", off: float = 0.0, side: int = 1, **kw: Any) -> Poi:  # noqa: ANN401
    return Poi(
        name or category, category, 45 + km / 111.32, 1.0, km=km, offset_m=off, side=side, category=category, **kw
    )


def _book(stops: list[Stop], km: float = 100, climbs: list[Climb] | None = None, **kw: Any) -> Roadbook:  # noqa: ANN401
    track = _track(km)
    return Roadbook("Ride", km, 0, 0, [], stops, climbs or [], 0, 0, profile=Profile(track), track=track, **kw)


def _booklet(book: Roadbook, f: str = "a5", **booklet: Any) -> Booklet:  # noqa: ANN401  [booklet] settings
    cfg = load_config()
    cfg["booklet"] |= booklet
    return Booklet(book, cfg, FORMATS[f])


def _rights(block: Block) -> list[dict[str, Any]]:
    return [r["right"] for r in block.rows if r["right"]]


def test_water_and_toilets_are_counted_the_nearest_named() -> None:
    s = Stop([_poi(10, "water", "Fontaine", 200), _poi(10.1, "water", "Cimetière", 40, side=-1), _poi(10.2, "toilets")])
    (count, toilets) = _rights(_booklet(_book([s])).stop(s, after=4.0))
    assert count == {"kind": "count", "emoji": "🚰", "n": 2, "name": "Cimetière", "where": "← 40 m"}
    assert (toilets["n"], toilets["where"]) == (1, "→")
    block = _booklet(_book([s])).stop(s, after=4.0)
    assert [r["left"] for r in block.rows] == ["10.0", "+6.0"]  # its km, and how far from the stop before


def test_each_shop_on_its_line_the_nearest_first_the_rest_counted() -> None:
    shops = [_poi(20, "bakery", f"B{i}", off=100 * i) for i in (3, 1, 2, 4)]
    s = Stop(shops)
    lines = _rights(_booklet(_book([s]), max_per_kind=2).stop(s, after=None))
    assert [line.get("name") for line in lines] == ["B1", "B2", None]
    assert lines[2] == {"kind": "more", "emoji": "🥖", "n": 2}
    assert len(_rights(_booklet(_book([s]), max_per_kind=0).stop(s, after=None))) == 4  # 0: all of them


def test_a_shop_closed_when_the_rider_passes_comes_last_stepped_back() -> None:
    closed = _poi(30, "bakery", "Shut", 10, opening_hours="Mo-Su 06:00-07:00", verdict=Verdict("closed"))
    open_ = _poi(30.1, "grocery", "Open", 50, opening_hours="Mo-Su 06:00-22:00", verdict=Verdict("open"))
    s = Stop([closed, open_], window=Window(_t("09:00"), _t("10:00")))
    book = _book([s], hours=Report(asked=2, found=2), ride=Ride(date=SUNDAY))
    lines = _rights(_booklet(book).stop(s, after=None))
    assert lines[0]["kind"] == "town"  # the arrival time heads it
    assert lines[0]["eta"] == f"~09:00{EN}10:00"
    assert [(line["name"], line["closed"]) for line in lines[1:]] == [("Open", False), ("Shut", True)]
    assert (lines[2]["verdict"], lines[2]["hours"]) == ("closed", f"06:00{EN}07:00")


def test_hours_too_long_for_the_line_go_under_it() -> None:
    week = "; ".join(f"{d} 07:00-12:30,15:30-19:30" for d in ("Mo", "Tu", "We", "Th", "Fr", "Sa"))
    s = Stop([_poi(40, "bakery", "Long hours", opening_hours=week)])
    lines = _rights(_booklet(_book([s], hours=Report(asked=1, found=1)), "a6").stop(s, after=None))
    assert lines[0]["hours"] == ""
    assert [line["kind"] for line in lines[1:]] == ["hours"] * (len(lines) - 1)
    assert len(lines) > 2
    assert " · ".join(line["text"] for line in lines[1:]).startswith(f"Mo 07:00{EN}12:30, 15:30{EN}19:30 · Tu")


def test_where_says_the_side_and_how_far_once_it_is_more_than_a_few_steps() -> None:
    assert where(_poi(1, "water", off=120, side=-1)) == "← 120 m"
    assert where(_poi(1, "water", off=12, side=1)) == "→"
    assert where(_poi(1, "water", off=2, side=0)) == ""


def test_text_is_wrapped_at_its_parts_never_through_one() -> None:
    assert text_mm("WWW", 9) > text_mm("iii", 9)
    assert text_mm("🚰", 9) > text_mm("a", 9)
    mo, tu, we = (f"{d} 07:00{EN}12:30" for d in ("Mo", "Tu", "We"))
    assert wrap([mo, tu, we], " · ", 60, 9) == [f"{mo} · {tu}", we]


# --- warnings, days and nights


def test_a_long_stretch_with_nowhere_to_drink_or_eat_is_warned_of_after_its_last_stop() -> None:
    stops = [Stop([_poi(5, "bakery")]), Stop([_poi(20, "water")]), Stop([_poi(90, "grocery")])]
    gaps = _booklet(_book(stops, km=100), water_gap_km=60, food_gap_km=50).gaps()
    assert [(b.km, b.data["text"]) for b in gaps] == [
        (5.0, "⚠ 85 km without food: next at km 90.0"),
        (20.0, "⚠ 70 km without water: next at km 90.0"),  # a shop sells water too: none from 20 to 90
    ]
    gaps = _booklet(_book(stops, km=150), water_gap_km=60, food_gap_km=50).gaps()
    assert gaps[-1].data["text"] == "⚠ 60 km without water or food: to the finish"
    assert all(b.keep == "prev" for b in gaps)  # on the page of the stop it follows


def test_a_shop_closed_when_passing_or_unknown_at_night_does_not_count() -> None:
    night = _t("23:00")
    closed = Stop([_poi(30, "grocery", verdict=Verdict("closed"))])
    dark = Stop([_poi(60, "bakery")])  # hours unknown, at night
    lit = Stop([_poi(70, "fuel", verdict=Verdict("open"))])  # open 24 h
    ride = Ride(date=night.date(), start=night - dt.timedelta(hours=3), speed_kmh=30)
    b = _booklet(_book([closed, dark, lit], ride=ride), water_gap_h=2, food_gap_h=2)
    assert not b.supplies(closed, {"grocery"})
    assert b.clock.dark(60)
    assert not b.supplies(dark, {"bakery"})
    assert b.supplies(lit, {"fuel"})
    assert b.supplies(Stop([_poi(80, "water")]), {"water"})  # a tap is there at night too
    # a speed: the stretch is measured as the rider drinks and eats, in hours
    assert [g.data["text"] for g in b.gaps()] == ["⚠ 2h20 (70 km) without water or food: next at km 70.0"]


def test_with_a_speed_a_stretch_is_as_long_as_it_takes_to_ride_its_climbing_included() -> None:
    track = _track(100)
    track.ele = np.interp(track.dist, [0, 40_000, 80_000], [0, 800, 0])  # 800 m up to km 40, down again by km 80
    stops = [Stop([_poi(40, "bakery")]), Stop([_poi(80, "bakery")])]
    book = Roadbook("Ride", 100, 800, 800, [], stops, [], 0, 0, profile=Profile(track), track=track)
    book.ride = Ride(speed_kmh=20, climb_min_per_100m=5)  # no date nor start: the speed is enough
    gaps = _booklet(book, water_gap_h=2.6, food_gap_h=0).gaps()
    # 40 km at 20 km/h, and 5 min for each 100 m climbed: 2h40, over the limit; the 40 km down after it, 2h, not
    assert [g.data["text"] for g in gaps] == ["⚠ 2h40 (40 km) without water: next at km 40.0"]
    book.ride = None  # no speed: in km, 50 km by default
    assert [g.data["text"] for g in _booklet(book).gaps()] == []
    assert [g.data["text"] for g in _booklet(book, water_gap_km=40).gaps()] == [
        "⚠ 40 km without water: next at km 40.0",
        "⚠ 40 km without water: next at km 80.0",
    ]


def test_a_timed_ride_marks_sunset_sunrise_and_each_new_day() -> None:
    start = _t("15:00")
    ride = Ride(date=start.date(), start=start, speed_kmh=20)  # 400 km: through the night, to the next afternoon
    b = _booklet(_book([], km=400, ride=ride))
    bands = [(blk.kind, blk.data["text"]) for blk in b.days_and_nights()]
    kinds = [k for k, _ in bands]
    assert kinds == ["sun", "day", "sun"]
    assert bands[0][1].startswith("🌇 Sunset ~")
    assert bands[1][1] == "Monday 12 October"
    assert bands[2][1].startswith("🌅 Sunrise ~")
    sunset = next(blk for blk in b.days_and_nights() if blk.kind == "sun")
    t = b.clock.at(sunset.km)
    sun = b.clock.sun(sunset.km, start.date())
    assert t is not None
    assert sun is not None
    assert t >= sun[1] > t - dt.timedelta(hours=1 / 20)  # within the km it falls in
    assert _booklet(_book([], km=400)).days_and_nights() == []  # untimed: no times to place them at


def test_the_clock_is_untimed_without_a_start() -> None:
    clock = Clock(_book([]))
    assert clock.at(10) is None
    assert not clock.dark(10)


# --- the pages


def test_the_pages_hold_their_blocks_headings_with_what_follows_warnings_with_what_precedes() -> None:
    b = _booklet(_book([]))
    cap = A5.blocks_h
    blocks = [Block("stop", float(k), cap / 3) for k in range(5)]
    blocks.insert(3, Block("day", 3.0, 5, keep="next"))  # would end page 1: goes on with the stop after it
    blocks.insert(5, Block("gap", 3.0, 5, keep="prev"))
    pages = b.paginate(blocks)
    assert [[x.kind for x in p] for p in pages] == [["stop", "stop", "stop"], ["day", "stop", "gap", "stop"]]
    assert all(sum(x.h for x in p) <= cap for p in pages)


def test_a_stop_too_tall_for_a_page_goes_on_over_the_next_continued() -> None:
    s = Stop([_poi(50, "fastfood", f"F{i}") for i in range(80)], town="Rambouillet")
    b = _booklet(_book([s]), "a6", max_per_kind=0)
    parts = b.split(b.stop(s, after=None), A6.blocks_h)
    assert len(parts) > 1
    assert all(p.h <= A6.blocks_h for p in parts)
    assert parts[1].rows[0]["right"]["town"] == "Rambouillet (continued)"
    names = [r["right"]["name"] for p in parts for r in p.rows if r["right"]["kind"] == "shop"]
    assert names == [f"F{i}" for i in range(80)]  # each once, in order


def _climb(km: float) -> Climb:
    return Climb(km, km + 5, 400, 8, 11, "1", name="Col")


def test_the_booklet_has_its_cover_first_its_map_in_the_middle_and_its_credits_last() -> None:
    stops = [Stop([_poi(k, "water")]) for k in range(1, 100, 2)]
    book = _book(stops, climbs=[_climb(30)])
    book.items = [Item("checkpoint", 50, 0, label="Lunch")]
    b = _booklet(book, food_gap_km=0)
    pages = b.pages()
    kinds = [p["kind"] for p in pages]
    assert len(pages) % 4 == 0
    assert kinds[0] == "cover"
    assert kinds[-1] == "back"
    assert "map" not in kinds  # no map on this book
    assert kinds[1 : 1 + kinds.count("stops")] == ["stops"] * kinds.count("stops")
    assert set(kinds[1 + kinds.count("stops") : -1]) <= {"notes"}
    assert [p["n"] for p in pages] == list(range(1, len(pages) + 1))
    first = pages[1]
    assert first["km"].startswith("km 0.0 → ")
    assert first["blocks"][0].kind == "start"
    texts = [x.data.get("text", "") for p in pages if p["kind"] == "stops" for x in p["blocks"]]
    assert "📍 Lunch · km 50.0" in texts
    cover = pages[0]
    assert [lst["title"] for lst in cover["lists"]] == ["Checkpoints", "Climbs"]


def test_the_map_goes_across_the_centre_spread() -> None:
    book = _book([Stop([_poi(k, "water")]) for k in range(1, 100, 2)])
    book.route_map = object()  # ty: ignore[invalid-assignment]  # only its being there counts here
    b = _booklet(book)
    b.map_svg = lambda: "<svg/>"  # ty: ignore[invalid-assignment]
    pages = b.pages()
    mid = centre(len(pages))
    assert (pages[mid]["kind"], pages[mid + 1]["kind"]) == ("map", "map-right")


def test_a_cover_with_more_climbs_than_room_says_how_many_more_are_inside() -> None:
    book = _book([], km=400, climbs=[_climb(k) for k in range(5, 400, 6)])
    cover = _booklet(book, "a6", water_gap_km=0, food_gap_km=0).cover()
    (climbs,) = cover["lists"]
    assert climbs["items"][-1].startswith("… and ")
    assert climbs["items"][-1].endswith(" more inside")
    shown = len(climbs["items"]) - 1
    assert climbs["items"][-1] == f"… and {66 - shown} more inside"


# --- in the road book


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
@pytest.mark.parametrize("name", ["a5", "a6"])
def test_the_booklet_prints_imposed_on_a4_and_reads_in_order_on_screen(name: str) -> None:
    cfg = load_config()
    cfg["booklet"]["format"] = name
    cfg["ride"] |= {"date": "2026-10-11", "start": "07:30", "speed_kmh": 24}
    book = build(HILLY, cfg)
    html = render_booklet(book, cfg)
    assert html is not None
    f = FORMATS[name]
    assert f"@page {{ size: {f.sheet[0]}mm {f.sheet[1]}mm; margin: 0; }}" in html
    pages = re.search(r"<b>(\d+) pages", html)
    assert pages is not None
    n = int(pages.group(1))
    assert html.count('<section class="side">') == len(sides(n, f))
    orders = sorted(int(o) for o in re.findall(r"order: (\d+)", html))
    assert orders == [k for k in range(1, n + 1) if k != n // 2 + 1]  # each once on screen; the map takes two
    assert "Start · Sun 11 Oct 07:30" in html
    assert "flip on the short edge" in html if name == "a5" else "flip on the long edge" in html
    main = render_html(book, cfg)
    assert '<div class="details">' not in main  # the reference sheet is in the booklet
    cfg["booklet"]["format"] = "off"
    assert render_booklet(book, cfg) is None
    assert '<div class="details">' in render_html(book, cfg)


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_map_is_drawn_for_the_centre_spread() -> None:
    cfg = load_config()
    book = build(HILLY, cfg)
    assert book.route_map is not None
    assert book.route_map.landscape
    assert book.route_map.width == A5.spread[0]
    cfg["booklet"]["format"] = "a6"
    m = build(HILLY, cfg).route_map
    assert m is not None
    assert m.width == A6.spread[0]


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_writes_the_booklet_beside_the_road_book(tmp_path: Path) -> None:
    out = tmp_path / "rb.html"
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(out), "--booklet", "A6", "--water-km", "10"])
    assert result.exit_code == 0, result.output
    assert f"Wrote {tmp_path / 'rb.booklet.html'}" in result.output
    booklet = (tmp_path / "rb.booklet.html").read_text(encoding="utf-8")
    assert "A6." in booklet
    assert "without water" in booklet
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(tmp_path / "off.html"), "--booklet", "off"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "off.booklet.html").exists()


def test_the_reference_sheet_is_only_asked_for_without_a_booklet() -> None:
    cfg = load_config()
    assert st.DETAILS.when is not None
    assert not st.DETAILS.when(cfg)
    cfg["booklet"]["format"] = "off"
    assert st.DETAILS.when(cfg)


def test_the_warnings_are_asked_for_in_hours_with_a_speed_else_in_km() -> None:
    cfg = load_config()
    asked = [s for s in (st.WATER_GAP_H, st.WATER_GAP_KM, st.FOOD_GAP_H, st.FOOD_GAP_KM) if s.when and s.when(cfg)]
    assert asked == [st.WATER_GAP_KM, st.FOOD_GAP_KM]
    cfg["ride"]["speed_kmh"] = 25
    asked = [s for s in (st.WATER_GAP_H, st.WATER_GAP_KM, st.FOOD_GAP_H, st.FOOD_GAP_KM) if s.when and s.when(cfg)]
    assert asked == [st.WATER_GAP_H, st.FOOD_GAP_H]
    assert (cfg["booklet"]["water_gap_h"], cfg["booklet"]["food_gap_h"]) == (3, 4)
    assert (cfg["booklet"]["water_gap_km"], cfg["booklet"]["food_gap_km"]) == (50, 70)
    cfg["booklet"]["format"] = "off"
    assert not any(s.when and s.when(cfg) for s in (st.WATER_GAP_H, st.WATER_GAP_KM, st.FOOD_GAP_H, st.FOOD_GAP_KM))
