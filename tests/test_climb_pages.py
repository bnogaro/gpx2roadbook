import itertools
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from roadbook.build import build
from roadbook.cli import app
from roadbook.climb_pages import chosen, pages, sections, step_km
from roadbook.config import load_config
from roadbook.model import Climb, ClimbPage, Poi, Stop, Track
from roadbook.osm import M_PER_DEG, OVERPASS
from roadbook.places import Place, lookup, on_road, road
from roadbook.profile import Profile
from roadbook.render import render_html
from roadbook.svg import CARD_GROUND_H, CARD_PLOT_H, climb_svg

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
CATEGORIES = load_config()["climbs"]["categories"]
M = 1 / M_PER_DEG  # a metre north, in degrees


def _track(km: float = 12.0, rise_m: float = 1000.0) -> Track:
    """A straight road north, climbing all the way: from 100 m, `rise_m` over its `km`."""
    dist = np.arange(0, km * 1000 + 1, 10.0)
    lat = 43.0 + dist * M
    return Track("t", lat, np.full_like(lat, 1.0), 100 + dist / dist[-1] * rise_m, dist)


def _profile(track: Track) -> Profile:
    return Profile(track, step_m=25, smooth_m=100, swing_m=3)


def _climb(start: float, end: float, label: str = "1") -> Climb:
    return Climb(start, end, 0.0, 8.0, 10.0, label)


# --- the sections


@pytest.mark.parametrize(
    ("end", "lengths"),
    [
        (4.5, [1, 1, 1, 0.5]),  # whole km from the foot, then what is left to the summit
        (4.2, [1, 1, 1.2]),  # 200 m: too short a stretch for a grade of its own, it joins the last km
        (4.0, [1, 1, 1]),
        (1.7, [0.7]),  # shorter than a km: one section
    ],
)
def test_a_climb_is_cut_into_kilometres_from_its_foot(end: float, lengths: list[float]) -> None:
    profile = _profile(_track())
    cut = sections(_climb(1.0, end), profile)
    assert [s.length_km for s in cut] == pytest.approx(lengths)
    assert cut[0].start_km == 1.0
    assert cut[-1].end_km == end
    # end to end: the sections climb what the climb does, and each grade is its own
    assert sum(s.ele_end - s.ele_start for s in cut) == pytest.approx(profile.ele_at(end) - profile.ele_at(1.0))
    assert all(s.grade == pytest.approx(1000 / 12_000 * 100, abs=0.5) for s in cut)


@pytest.mark.parametrize(
    ("length", "min_sections", "step"),
    [
        (9.6, 6, 1.0),  # a kilometre at a time, as the races print it
        (6.0, 6, 1.0),
        (5.9, 6, 0.5),  # 5 whole km are too few: 11 half km
        (3.0, 6, 0.5),
        (2.3, 6, 0.25),  # once 2 slabs, now 9
        (0.8, 6, 0.25),  # 100 m would be shorter than the profile is smoothed over
        (2.3, 0, 1.0),
    ],
)
def test_a_short_climb_is_cut_into_shorter_steps(length: float, min_sections: int, step: float) -> None:
    assert step_km(_climb(1.0, 1.0 + length), _profile(_track()), min_sections) == step


def test_no_step_is_shorter_than_the_profile_is_smoothed_over() -> None:
    climb = _climb(1.0, 1.8)
    assert step_km(climb, Profile(_track(), smooth_m=50), 6) == 0.1  # a 75 m average: 100 m steps are the road's
    assert step_km(climb, Profile(_track(), smooth_m=2000), 6) == 1.0  # none is: a kilometre, as ever


@pytest.mark.parametrize(
    ("end", "step", "smooth_m", "lengths"),
    [
        (3.4, 0.25, 100, [0.25] * 9 + [0.15]),
        (3.35, 0.25, 100, [0.25] * 8 + [0.35]),  # 100 m: shorter than the profile is smoothed over (125 m)
        (4.6, 0.5, 100, [0.5] * 6 + [0.6]),  # 100 m: under 30 % of a step
        (1.7, 0.1, 50, [0.1] * 7),  # with a 75 m average, 100 m steps
    ],
)
def test_the_steps_run_from_the_foot_and_the_last_one_takes_a_short_remainder(
    end: float, step: float, smooth_m: float, lengths: list[float]
) -> None:
    cut = sections(_climb(1.0, end), Profile(_track(), smooth_m=smooth_m), step)
    assert [s.length_km for s in cut] == pytest.approx(lengths)
    assert (cut[0].start_km, cut[-1].end_km) == (1.0, end)


def test_the_climbs_of_a_category_or_harder_get_a_page(caplog: pytest.LogCaptureFixture) -> None:
    climbs = [_climb(k, k + 1, label) for k, label in enumerate(("4", "HC", "", "2", "3", "1"))]
    assert [c.label for c in chosen(climbs, "3", CATEGORIES)] == ["HC", "2", "3", "1"]  # in route order
    assert [c.label for c in chosen(climbs, "HC", CATEGORIES)] == ["HC"]
    assert [c.label for c in chosen(climbs, "5", CATEGORIES)] == ["4", "HC", "2", "3", "1"]  # never an uncategorised
    assert "no category '5'" in caplog.text


def _stop(km: float, category: str = "water") -> Stop:
    return Stop([Poi("", category, 0.0, 0.0, km=km, category=category)])


def test_a_page_has_the_stops_on_its_climb_its_foot_and_summit_too() -> None:
    profile = _profile(_track())
    stops = [_stop(0.5), _stop(1.8), _stop(4.0), _stop(6.2), _stop(6.6)]
    cfg = {"from_category": "3", "min_sections": 6}
    (page,) = pages([_climb(2.0, 6.0)], stops, profile, cfg, CATEGORIES, snap_m=300)
    assert [s.km for s in page.stops] == [1.8, 4.0, 6.2]  # 200 m from the foot, and from the summit, are on it
    assert [s.length_km for s in page.sections] == pytest.approx([0.5] * 8)  # 4 whole km are too few


# --- the places on the way


def _place(name: str, km: float, east_m: float = 0.0) -> Place:
    """A place by the test track's road, at its `km`, `east_m` off it."""
    return name, 43.0 + km * 1000 * M, 1.0 + east_m * M / np.cos(np.radians(43))


def test_a_place_is_marked_where_the_road_comes_closest() -> None:
    track = _track()
    kms, points = road(ClimbPage(_climb(2.0, 6.0), [], []), track)
    places = [
        _place("Boutx", 3.0, east_m=150),
        _place("Far", 4.0, east_m=400),  # beyond 250 m
        _place("Lez", 2.6, east_m=200),
        _place("Lez", 5.0, east_m=50),  # the same name, closer: a hamlet's and its town's
        _place("Before", 1.0),  # before the foot: the climb's road doesn't pass it
    ]
    assert on_road(kms, points, places, 250) == [(pytest.approx(3.0), "Boutx"), (pytest.approx(5.0), "Lez")]


class FakeOverpass:
    """Answers with places along the test track's road, or fails; records the queries."""

    def __init__(self, *, down: tuple[str, ...] = ()) -> None:
        self.down = down
        self.queries: list[str] = []

    def __call__(self, url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401
        assert data is not None
        self.queries.append(data["data"])
        if url in self.down:
            msg = "down"
            raise OSError(msg)
        place = lambda name, km: {"type": "node", "lat": 43.0 + km * 1000 * M, "lon": 1.0, "tags": {"name": name}}  # noqa: E731
        return {"elements": [place("Boutx", 3.0), place("Mente", 8.0), {"type": "node", "lat": 43, "lon": 1}]}


def _lookup(tmp_path: Path, page_list: list[ClimbPage], http: FakeOverpass, **kw: Any) -> Any:  # noqa: ANN401
    return lookup(page_list, _track(), {"place_m": 250}, 365, cache_path=tmp_path / "p.json", http=http, **kw)


def test_one_overpass_request_finds_the_places_on_every_climb(tmp_path: Path) -> None:
    first, second = ClimbPage(_climb(2.0, 4.0), [], []), ClimbPage(_climb(7.0, 9.5), [], [])
    http = FakeOverpass()
    report = _lookup(tmp_path, [first, second], http)
    assert first.places == [(pytest.approx(3.0), "Boutx")]
    assert second.places == [(pytest.approx(8.0), "Mente")]
    (query,) = http.queries
    assert query.count('node["place"~"^(city|town|village|hamlet)$"]["name"](around:250,') == 2  # one per climb
    assert (report.asked, report.found, report.sources, report.failed) == (2, 2, ["Overpass"], False)


def test_places_come_from_the_cache_next_time_and_offline(tmp_path: Path) -> None:
    _lookup(tmp_path, [ClimbPage(_climb(2.0, 4.0), [], [])], FakeOverpass())
    again, other = ClimbPage(_climb(2.0, 4.0), [], []), ClimbPage(_climb(7.0, 9.5), [], [])
    http = FakeOverpass()
    report = _lookup(tmp_path, [again, other], http, offline=True)
    assert again.places == [(pytest.approx(3.0), "Boutx")]
    assert other.places == []
    assert (report.cached, report.unasked, http.queries) == (1, 1, [])


def test_overpass_down_leaves_the_climbs_without_places(tmp_path: Path) -> None:
    page = ClimbPage(_climb(2.0, 4.0), [], [])
    report = _lookup(tmp_path, [page], FakeOverpass(down=OVERPASS))
    assert page.places == []
    assert (report.failed, report.found) == (True, 0)


# --- the card


def _slabs(svg: str) -> list[str]:
    return re.findall(r'<polygon points="[^"]+" fill="([^"]+)"', svg)


def test_a_card_draws_each_section_in_its_grade_colour_with_the_marks_above() -> None:
    grades = load_config()["climbs"]["grade_colours"]
    cut = [(0.0, 1.0, 500, 530), (1.0, 2.0, 530, 620), (2.0, 2.4, 620, 670)]  # 3 %, 9 %, 12.5 %
    svg = climb_svg(cut, [(1.5, "Boutx"), (1.52, "🚰"), (2.4, "Col <b>")], 190, grades=grades)
    assert _slabs(svg) == ["#f59e0b", "#b91c1c", "#7f1d1d"]
    for text in (">3.0<", ">9.0<", ">12.5<", ">500 m<", ">670 m<", ">2.4<"):  # grades, foot and top, km from the foot
        assert text in svg
    assert ">Boutx · 🚰<" in svg  # too close to read apart: one label
    assert "Col &lt;b&gt;" in svg


def _size(svg: str) -> tuple[float, float]:
    found = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', svg)
    assert found
    return float(found[1]), float(found[2])


def _ramp(steps: list[float], grade: float = 5.0) -> list[tuple[float, float, float, float]]:
    """Sections of these lengths, km, from km 0 and 500 m up, all at `grade` %."""
    edges = np.cumsum([0.0, *steps])
    return [(a, b, 500 + a * grade * 10, 500 + b * grade * 10) for a, b in itertools.pairwise(edges)]


def _kms(svg: str) -> list[str]:
    return re.findall(r'font-size="2" text-anchor="middle">([^<]+)<', svg)


def _grades(svg: str) -> list[str]:
    return re.findall(r'font-weight="700" text-anchor="middle" fill="[^"]+">([^<]+)<', svg)


def test_the_km_scale_reads_by_quarter_km_or_by_half_km_when_they_crowd_it() -> None:
    kms = _kms(climb_svg(_ramp([0.25] * 8 + [0.3]), [], 190))
    assert kms == ["0", "0.25", "0.5", "0.75", "1", "1.25", "1.5", "1.75", "2", "2.3"]
    crowded = _kms(climb_svg(_ramp([0.25] * 40), [], 190))  # 10 km in 4.5 mm steps
    assert crowded[:4] == ["0", "0.5", "1", "1.5"]
    assert crowded[-2:] == ["9.5", "10.0"]


def test_the_summit_km_wins_over_a_label_too_close_to_it() -> None:
    kms = _kms(climb_svg(_ramp([1.0] * 25 + [0.3]), [], 190))
    assert kms[-3:] == ["23", "24", "25.3"]  # 25 is 2 mm before the summit


def test_a_slab_too_narrow_for_its_grade_rounds_it_then_leaves_it_out() -> None:
    assert _grades(climb_svg(_ramp([1.0] * 25 + [0.3], 6.94), [], 190))[-2:] == ["6.9", "7"]
    assert len(_grades(climb_svg(_ramp([1.0] * 60 + [0.3], 6.94), [], 190))) == 60  # the last one, 0.9 mm wide


def test_the_lowest_slab_has_room_for_its_grade() -> None:
    svg = climb_svg(_ramp([1.0] * 35, 4.0), [], 190)  # long and gentle: a low card
    points = re.search(r'<polygon points="([^"]+)"', svg)
    assert points
    corners = [[float(v) for v in p.split(",")] for p in points[1].split()]
    assert corners[0][1] - corners[1][1] >= CARD_GROUND_H  # from the base up to the foot


def test_every_card_draws_a_grade_as_steep_so_a_steep_climb_gets_a_narrower_card() -> None:
    gentle = climb_svg([(0.0, 10.0, 500, 900)], [], 190)  # 4 %, 10 km
    steep = climb_svg([(0.0, 4.0, 500, 1000)], [], 190)  # 12.5 %, 4 km
    (gentle_w, gentle_h), (steep_w, steep_h) = _size(gentle), _size(steep)
    assert gentle_w == pytest.approx(190)
    assert gentle_h < steep_h
    assert steep_w < gentle_w  # as tall as a card may be, then narrower
    assert steep_h <= 3 + CARD_PLOT_H + 5 + 0.01  # no label: a bare band above


# --- in the road book


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_climb_pages_follow_the_reference_sheet_only_when_asked_for() -> None:
    cfg = load_config()
    book = build(HILLY, cfg)
    assert book.climb_pages == []
    assert 'class="climb-pages"' not in render_html(book, cfg)
    cfg["climb_pages"] |= {"enabled": True, "from_category": "2"}
    book = build(HILLY, cfg)
    html = render_html(book, cfg)
    assert [p.climb.label for p in book.climb_pages] == ["HC", "2", "HC"]
    assert html.count('class="climb-card"') == 3
    assert html.index('class="details"') < html.index('class="climb-pages"')
    assert "Climb at km 4.8" in html  # no name looked up: its km instead


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_turns_the_climb_pages_on(tmp_path: Path) -> None:
    out = tmp_path / "ubf.html"
    args = [str(HILLY), "--climb-pages", "--climb-pages-from", "HC", "-o", str(out)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").count('class="climb-card"') == 2
