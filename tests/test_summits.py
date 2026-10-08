import urllib.parse
from functools import partial
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from roadbook import build as build_module
from roadbook import render as render_module
from roadbook.build import build
from roadbook.cli import app
from roadbook.config import load_config
from roadbook.model import Climb, Item, Poi, Roadbook, Stop, Track
from roadbook.osm import NOMINATIM, OVERPASS, Report
from roadbook.profile import Profile
from roadbook.render import HEAD_H, MAIN_H, RowLayout, _add_heads, _credit, _details, _row, render_html
from roadbook.summits import Place, choose, lookup

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
SEARCH = f"{NOMINATIM}/search"
CFG = load_config()["climb_names"]
M = 1 / 111_320  # a metre of latitude, in degrees

# summits of entrainement_ubf.gpx, as the route has them, and what OSM has there
MENTE = (42.91940, 0.76206)  # km 14.4
PORTET = (42.94486, 0.85393)  # km 29.9
PORTEL = (42.92945, 1.36019)  # km 86.2


def _node(name: str, lat: float, lon: float, **tags: str) -> dict[str, Any]:
    return {"type": "node", "id": 1, "lat": lat, "lon": lon, "tags": {"name": name, **tags}}


PASS = {"mountain_pass": "yes", "natural": "saddle"}
PASS_ONLY = {"mountain_pass": "yes"}
OVERPASS_ANSWER = {
    "elements": [
        _node("Col de Menté", MENTE[0] + 20 * M, MENTE[1], **PASS),
        _node("Col de Lagues", MENTE[0] + 700 * M, MENTE[1], **PASS),  # another col, too far
        _node("Col de Portet d'Aspet", PORTET[0], PORTET[1] + 0.0002, **PASS),
        _node("Bourdallé", PORTET[0] - 0.004, PORTET[1], natural="peak"),
        _node("Col de Portel", PORTEL[0] + 120 * M, PORTEL[1], **PASS_ONLY),
        {  # a peak mapped as an area: it has a centre instead of its own lat/lon
            "type": "way",
            "id": 2,
            "center": {"lat": PORTEL[0] - 50 * M, "lon": PORTEL[1]},
            "tags": {"name": "Cap de Campets", "natural": "peak"},
        },
        _node("427", 43.06873, 1.43726, natural="peak"),  # an elevation where the name should be
        {"type": "node", "id": 3, "lat": 0, "lon": 0, "tags": {"name": "Somewhere", "tourism": "viewpoint"}},
    ]
}


class FakeHttp:
    """Answers like Overpass or Nominatim would, or fails for the hosts in `down`; records what was asked."""

    def __init__(self, *, down: tuple[str, ...] = (), nominatim: dict[str, list] | None = None) -> None:
        self.down = down
        self.nominatim = nominatim or {}  # Nominatim's results, by tag filter
        self.urls: list[str] = []
        self.queries: list[str] = []  # Overpass queries
        self.searches: list[dict[str, str]] = []  # Nominatim parameters
        self.sleeps: list[float] = []

    def __call__(self, url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401
        self.urls.append(url)
        if url.startswith(SEARCH):
            assert data is None
            params = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
            self.searches.append(params)
        else:
            assert data is not None
            self.queries.append(data["data"])
        if any(url.startswith(d) for d in self.down):
            msg = "504 Gateway Timeout"
            raise OSError(msg)
        return self.nominatim.get(params["q"], []) if url.startswith(SEARCH) else OVERPASS_ANSWER


def _climb(km: float = 10.0) -> Climb:
    return Climb(km - 5, km, 500, 10, 12, "1")


def _track(*tops: tuple[float, float]) -> Track:
    """A track through `tops`, one every 10 km from km 10."""
    lat = [tops[0][0], *(t[0] for t in tops)]
    lon = [tops[0][1], *(t[1] for t in tops)]
    dist = np.arange(len(lat)) * 10_000.0
    return Track("t", np.array(lat), np.array(lon), np.zeros(len(lat)), dist)


def _lookup(climbs: list[Climb], track: Track, tmp_path: Path, http: FakeHttp, **cfg: Any) -> Any:  # noqa: ANN401
    return lookup(climbs, track, {**CFG, **cfg}, cache_path=tmp_path / "c.json", http=http, sleep=http.sleeps.append)


# --- which name


def _place(name: str, kind: str, metres_north: float) -> Place:
    return Place(name, kind, PORTEL[0] + metres_north * M, PORTEL[1])


def test_a_pass_beats_a_closer_peak() -> None:
    places = [_place("Cap de Campets", "peak", 20), _place("Col de Portel", "pass", 250)]
    assert choose(PORTEL, places, CFG) == "Col de Portel"


def test_the_closest_of_a_kind_wins() -> None:
    places = [_place("Col de Lagues", "pass", 200), _place("Col de Menté", "pass", -30)]
    assert choose(PORTEL, places, CFG) == "Col de Menté"


@pytest.mark.parametrize(
    ("place", "named"),
    [
        (_place("Col", "pass", 290), True),
        (_place("Col", "pass", 320), False),  # beyond pass_m
        (_place("Pic", "peak", 190), True),
        (_place("Pic", "peak", 250), False),  # beyond peak_m: a peak must be closer than a pass
        (_place("1336", "peak", 10), False),  # an elevation, not a name
        (_place("", "pass", 10), False),
    ],
)
def test_each_kind_has_its_radius_and_a_name_needs_letters(place: Place, *, named: bool) -> None:
    assert choose(PORTEL, [place], CFG) == (place.name if named else None)


def test_the_track_knows_where_a_km_is() -> None:
    track = _track(MENTE, PORTET)
    assert track.at(10) == pytest.approx(MENTE)
    assert track.at(15) == pytest.approx(((MENTE[0] + PORTET[0]) / 2, (MENTE[1] + PORTET[1]) / 2))


# --- looking them up


def test_one_overpass_request_names_every_climb(tmp_path: Path) -> None:
    climbs = [_climb(10), _climb(20), _climb(30), _climb(40)]
    track = _track(MENTE, PORTET, PORTEL, (43.06873, 1.43726))
    http = FakeHttp()
    report = _lookup(climbs, track, tmp_path, http)
    assert [c.name for c in climbs] == ["Col de Menté", "Col de Portet d'Aspet", "Col de Portel", None]
    assert (report.asked, report.found, report.cached, report.sources, report.failed) == (4, 3, 0, ["Overpass"], False)
    assert http.urls == [OVERPASS[0]]
    (query,) = http.queries
    assert query.count("around:300,") == 8  # two clauses per climb, at the larger radius
    assert f"{PORTEL[0]:.6f},{PORTEL[1]:.6f}" in query


def test_a_second_run_uses_the_cache_without_network(tmp_path: Path) -> None:
    track = _track(MENTE, (43.06873, 1.43726))
    _lookup([_climb(10), _climb(20)], track, tmp_path, FakeHttp())
    again = [_climb(10), _climb(20)]
    offline = FakeHttp(down=(*OVERPASS, SEARCH))
    report = _lookup(again, track, tmp_path, offline)
    assert [c.name for c in again] == ["Col de Menté", None]  # no name is an answer too
    assert (report.cached, report.found, report.failed, offline.urls) == (2, 1, False, [])


def test_other_radii_ask_again(tmp_path: Path) -> None:
    track = _track(MENTE)
    _lookup([_climb()], track, tmp_path, FakeHttp())
    http = FakeHttp()
    assert _lookup([_climb()], track, tmp_path, http, pass_m=500).cached == 0
    assert _lookup([_climb()], track, tmp_path, http, max_age_days=0).cached == 0
    assert len(http.urls) == 2


def test_the_mirror_answers_when_the_main_server_is_down(tmp_path: Path) -> None:
    climb = _climb()
    http = FakeHttp(down=(OVERPASS[0],))
    report = _lookup([climb], _track(PORTET), tmp_path, http)
    assert climb.name == "Col de Portet d'Aspet"
    assert http.urls == list(OVERPASS)
    assert report.sources == ["Overpass"]


NOMINATIM_RESULTS = {
    "[mountain_pass=yes]": [{"name": "Col de Portel", "lat": str(PORTEL[0] + 120 * M), "lon": str(PORTEL[1])}],
    "[natural=peak]": [{"name": "Cap de Campets", "lat": str(PORTEL[0]), "lon": str(PORTEL[1])}],
}


def test_nominatim_answers_when_overpass_is_down(tmp_path: Path) -> None:
    portel, mente = _climb(10), _climb(20)
    http = FakeHttp(down=OVERPASS, nominatim=NOMINATIM_RESULTS)
    report = _lookup([portel, mente], _track(PORTEL, MENTE), tmp_path, http)
    assert (portel.name, mente.name) == ("Col de Portel", None)  # the fake's peak is far from Menté
    assert (report.sources, report.failed) == (["Nominatim"], False)
    # a pass found: no need to ask for peaks; none found: passes, saddles, then peaks
    asked = [s["q"] for s in http.searches]
    passes = ["[mountain_pass=yes]", "[natural=saddle]"]
    assert asked == [*passes, *passes, "[natural=peak]"]
    assert http.sleeps == [1.1] * 5  # one request per second, the first too
    first = http.searches[0]
    assert first["bounded"] == "1"
    west, north, east, south = map(float, first["viewbox"].split(","))
    assert west < PORTEL[1] < east
    assert south < PORTEL[0] < north


def test_a_peak_names_a_climb_with_no_pass(tmp_path: Path) -> None:
    climb = _climb()
    http = FakeHttp(down=OVERPASS, nominatim={"[natural=peak]": NOMINATIM_RESULTS["[natural=peak]"]})
    _lookup([climb], _track(PORTEL), tmp_path, http)
    assert climb.name == "Cap de Campets"


def test_everything_down_gives_up_quickly_and_caches_nothing(tmp_path: Path) -> None:
    climbs = [_climb(10 * (i + 1)) for i in range(6)]
    track = _track(*[(42.9 + i / 100, 1.0) for i in range(6)])
    http = FakeHttp(down=(*OVERPASS, SEARCH))
    report = _lookup(climbs, track, tmp_path, http)
    assert all(c.name is None for c in climbs)
    assert (report.found, report.failed) == (0, True)
    assert len(http.searches) == 3  # NOMINATIM_GIVE_UP failures in a row
    later = FakeHttp()
    _lookup(climbs[:1], track, tmp_path, later)
    assert len(later.urls) == 1


def test_an_odd_answer_counts_as_a_failure(tmp_path: Path) -> None:
    climb = _climb()

    def odd(url: str, _data: dict[str, str] | None) -> Any:  # noqa: ANN401
        return ["not", "a", "dict"] if url in OVERPASS else [{"no": "name"}]

    report = lookup([climb], _track(PORTEL), CFG, cache_path=tmp_path / "c.json", http=odd, sleep=lambda _: None)
    assert climb.name is None
    assert report.failed


def test_no_climbs_no_request(tmp_path: Path) -> None:
    http = FakeHttp()
    assert _lookup([], _track(PORTEL), tmp_path, http).asked == 0
    assert http.urls == []


# --- strip rows: a line of its own above the climb's row


STRIP = RowLayout(avail=12.5, max_emojis=99, sep=" ", leg_elevation=False, range_m=1000, emoji_lines=2, wrap_avail=18.5)


def _heads(*items: Item) -> list[dict[str, Any]]:
    book = Roadbook("t", 100, 0, 0, list(items), [], [], 0, 0)
    rows = [_row(it, book, STRIP) for it in items]
    plain = [r["h"] for r in rows]
    _add_heads(rows, book)
    for r, h in zip(rows, plain, strict=True):
        assert r["h"] == pytest.approx(h + HEAD_H * len(r["heads"]))
    return rows


def _climb_item(name: str | None, stop: Stop | None = None) -> Item:
    climb = Climb(25.6, 29.9, 408, 9.6, 12, "2", name=name)
    return Item("climb", 25.6, 0, climb=climb, stop=stop, dist_to_next=4.3)


def test_a_named_climb_gets_a_line_above_its_row() -> None:
    named, unnamed = _heads(_climb_item("Col de Portet d'Aspet"), _climb_item(None))
    assert named["heads"] == [{"cls": "col", "text": "Col de Portet d'Aspet"}]
    assert unnamed["heads"] == []


def test_only_the_climbs_foot_row_is_named() -> None:
    foot = _climb_item("Col de Portet d'Aspet")
    top = Item("summit", 29.9, 1069, climb=foot.climb, stop=Stop([Poi("a", "", 0, 0, km=29.9)]))
    assert [r["heads"] for r in _heads(foot, top)][1] == []


def test_a_climb_starting_in_a_named_town_gets_both_lines() -> None:
    stop = Stop([Poi("a", "", 0, 0, km=25.6)], town="Saint-Béat")
    (row,) = _heads(_climb_item("Col de Menté", stop))
    assert [h["text"] for h in row["heads"]] == ["Saint-Béat", "Col de Menté"]  # where you are, then what comes


# --- the reference sheet


def test_the_reference_sheet_lists_named_climbs_among_the_stops() -> None:
    stop = Stop([Poi("Fontaine", "", 0, 0, km=25.6, category="water")])
    after = Stop([Poi("Fontaine", "", 0, 0, km=27.0, category="water")])
    climbs = [Climb(25.6, 29.9, 408, 9.6, 12, "2", name="Col de Portet d'Aspet"), Climb(40, 41, 90, 5, 6, "")]
    book = Roadbook("t", 100, 0, 0, [], [stop, after], climbs, 0, 0)
    entries = _details(book, load_config()["categories"], 1000, [])
    assert [e["km"] for e in entries] == ["25.6", "25.6 → 29.9", "27.0"]  # a stop at its foot first; unnamed: none
    entry = entries[1]
    assert entry["col"] == "Col de Portet d'Aspet"
    assert entry["groups"][0][1][0]["name"] == "Cat 2 · 4.3 km at 9.6 % · ↗️408 m"


def test_the_credit_names_what_came_from_osm() -> None:
    book = Roadbook("t", 100, 0, 0, [], [], [], 0, 0)
    assert _credit(book) == ""
    book.climb_names = Report(asked=3, found=1)
    assert _credit(book) == "Climb names"
    book.hours, book.towns = Report(asked=9, found=4), Report(asked=2, found=2)
    assert _credit(book) == "Opening hours, town names and climb names"


def test_a_lookup_that_found_nothing_is_not_credited() -> None:
    # a flat route has no climbs to name: the sheet must not credit OSM for climb names it doesn't show
    book = Roadbook("t", 100, 0, 0, [], [], [], 0, 0)
    book.climb_names, book.towns = Report(), Report(asked=4, found=0)
    assert _credit(book) == ""


# --- end to end, on a sample, with a fake Overpass


def _named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, http: FakeHttp | None = None) -> FakeHttp:
    http = http or FakeHttp()
    fake = partial(lookup, cache_path=tmp_path / "climbs.json", http=http, sleep=lambda _: None)
    monkeypatch.setattr(build_module, "name_climbs", fake)
    return http


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_on_the_sample_the_pyrenean_cols_are_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = _named(monkeypatch, tmp_path)
    cfg = load_config()
    cfg["climb_names"]["enabled"] = True
    book = build(HILLY, cfg)
    named = {round(c.start_km, 1): c.name for c in book.climbs if c.name}
    assert named == {4.8: "Col de Menté", 25.6: "Col de Portet d'Aspet", 72.4: "Col de Portel"}
    assert len(http.urls) == 1
    html = render_html(book, cfg)
    assert '<div class="head col"><span>Col de Menté</span></div>' in html
    # its swatch in the colour of its average grade, 9.6 %
    swatch = '<span class="swatch" style="background: #b91c1c"></span>'
    assert f'<b>km 25.6 → 29.9 · {swatch}<span class="col">Col de Portet d&#39;Aspet</span></b>' in html
    assert "Climb names © OpenStreetMap" in html


def test_the_profile_stays_level_with_a_named_climbs_main_line(monkeypatch: pytest.MonkeyPatch) -> None:
    anchors: list[list[tuple[float, float]]] = []
    monkeypatch.setattr(render_module, "gutter_svg", lambda _p, _c, a, *_, **__: anchors.append(a) or "")
    climb = Climb(5, 10, 500, 10, 12, "1", name="Col de Menté")
    track = _track(MENTE, PORTET)
    items = [
        Item("start", 0, 0, label="START", dist_to_next=5),
        Item("climb", 5, 0, climb=climb, dist_to_next=15),
        Item("finish", 20, 0, label="FINISH"),
    ]
    book = Roadbook("t", 20, 0, 0, items, [], [climb], 0, 0, profile=Profile(track))
    render_html(book, load_config())
    (strip,) = anchors
    start_h = MAIN_H  # START: a main line only
    assert strip[1] == (5, pytest.approx(start_h + HEAD_H + MAIN_H / 2))  # the climb's main line, under its name


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_says_how_many_climbs_it_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _named(monkeypatch, tmp_path)
    out = tmp_path / "out.html"
    result = CliRunner().invoke(app, [str(HILLY), "--climb-names", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "Climb names: 3 of 10 climbs named (Overpass)" in result.output
    result = CliRunner().invoke(app, [str(HILLY), "--climb-names", "-o", str(out)])
    assert "Climb names: 3 of 10 climbs named (10 from cache)" in result.output
    _named(monkeypatch, tmp_path / "other", FakeHttp(down=(*OVERPASS, SEARCH)))
    result = CliRunner().invoke(app, [str(HILLY), "--climb-names", "-o", str(out)])
    assert result.exit_code == 0
    assert "did not answer" in result.output


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_climbs_are_named_by_default_unless_turned_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = _named(monkeypatch, tmp_path)
    cfg = load_config()
    assert cfg["climb_names"]["enabled"] is True
    assert build(HILLY, cfg).climb_names is not None
    http.urls.clear()
    cfg["climb_names"]["enabled"] = False
    assert build(HILLY, cfg).climb_names is None
    # nowhere to show them: a ribbon of tokens and no reference sheet
    cfg["climb_names"]["enabled"] = True
    cfg["render"]["layout"], cfg["render"]["details"] = "line", False
    assert build(HILLY, cfg).climb_names is None
    assert http.urls == []
