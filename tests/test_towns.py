import re
import urllib.parse
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from roadbook import build as build_module
from roadbook.build import build
from roadbook.cli import app
from roadbook.config import load_config
from roadbook.model import Glyph, Item, Poi, Roadbook, Stop
from roadbook.osm import NOMINATIM, Report
from roadbook.render import HEAD_H, RowLayout, _add_heads, _paginate, _row, render_html
from roadbook.towns import ZOOM, busy, centre, group, lookup

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
REVERSE = f"{NOMINATIM}/reverse"
CFG = load_config()["towns"]
LAT, LON = 42.9858, 1.1466  # Saint-Girons, km 60.0 of entrainement_ubf.gpx

SAINT_GIRONS = {
    "name": "Saint-Girons",
    "addresstype": "town",
    # "municipality" is the arrondissement in France: never the answer
    "address": {"town": "Saint-Girons", "municipality": "Saint-Girons", "county": "Ariège", "country": "France"},
}


class FakeNominatim:
    """Answers reverse geocoding like Nominatim, with `answer`, or fails while `down`; records what was asked."""

    def __init__(self, answer: Any = None, *, down: bool = False) -> None:  # noqa: ANN401
        self.answer = SAINT_GIRONS if answer is None else answer
        self.down = down
        self.asked: list[dict[str, str]] = []
        self.sleeps: list[float] = []  # the pauses asked for between requests

    def __call__(self, url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401
        assert data is None
        assert url.startswith(REVERSE + "?")
        self.asked.append(dict(urllib.parse.parse_qsl(url.split("?", 1)[1])))
        if self.down:
            msg = "timed out"
            raise OSError(msg)
        return self.answer


def _stop(n: int, lat: float = LAT, lon: float = LON, km: float = 60.0) -> Stop:
    return Stop(
        [Poi(f"p{i}", "Boulangerie", lat + i * 1e-4, lon, km=km + i / 100, category="bakery") for i in range(n)]
    )


def _lookup(stops: list[Stop], tmp_path: Path, http: FakeNominatim, **cfg: Any) -> Report:  # noqa: ANN401
    return lookup(stops, {**CFG, **cfg}, cache_path=tmp_path / "towns.json", http=http, sleep=http.sleeps.append)


def _stop_of(*categories: str) -> Stop:
    return Stop([Poi(f"p{i}", "", LAT, LON, km=60.0, category=c) for i, c in enumerate(categories)])


@pytest.mark.parametrize(
    ("categories", "is_busy"),
    [
        (["water"], False),  # a fountain
        (["bakery"], False),  # a lone bakery
        (["water"] * 7, False),  # a run of fountains and cemetery taps
        (["water"] * 8, True),  # 8 POIs of any kind: a town
        (["water", "water", "toilets", "toilets", "bakery"], True),  # 5 with a village shop: a village
        (["water", "toilets", "bakery", "grocery"], False),  # too few even with shops
        (["water", "water", "toilets", "toilets", "fastfood", "fastfood"], False),  # no village shop
    ],
)
def test_busy_stops_are_towns_or_villages_with_a_shop(categories: list[str], is_busy: bool) -> None:  # noqa: FBT001
    stop = _stop_of(*categories)
    assert busy([stop], CFG) == ([stop] if is_busy else [])


def test_centre_is_the_poi_nearest_the_middle() -> None:
    stop = _stop(5)
    assert centre(stop) is stop.pois[2]


def test_a_busy_stop_gets_its_town_and_a_quiet_one_is_not_asked(tmp_path: Path) -> None:
    town, fountain = _stop(9), _stop(1)
    http = FakeNominatim()
    report = _lookup([town, fountain], tmp_path, http)
    assert (town.town, fountain.town) == ("Saint-Girons", None)
    assert (report.asked, report.found, report.cached, report.failed) == (1, 1, 0, False)
    (asked,) = http.asked
    assert (asked["format"], asked["zoom"]) == ("jsonv2", str(ZOOM))
    assert (float(asked["lat"]), float(asked["lon"])) == pytest.approx((centre(town).lat, centre(town).lon), abs=1e-5)
    assert http.sleeps == [1.1]  # Nominatim: one request per second, the first too (hours may just have asked)


@pytest.mark.parametrize(
    ("address", "town"),
    [
        ({"city": "Le Mans", "municipality": "Le Mans"}, "Le Mans"),
        ({"village": "Le Mas-d'Azil", "municipality": "Saint-Girons"}, "Le Mas-d'Azil"),  # not its arrondissement
        ({"hamlet": "Les Monges", "county": "Haute-Garonne"}, None),
    ],
)
def test_the_town_is_the_city_town_or_village(tmp_path: Path, address: dict[str, str], town: str | None) -> None:
    stop = _stop(8)
    _lookup([stop], tmp_path, FakeNominatim({"address": address}))
    assert stop.town == town


def test_a_second_run_uses_the_cache_without_network(tmp_path: Path) -> None:
    _lookup([_stop(8)], tmp_path, FakeNominatim())
    again = _stop(8)
    offline = FakeNominatim(down=True)
    report = _lookup([again], tmp_path, offline)
    assert again.town == "Saint-Girons"
    assert (report.cached, report.failed, offline.asked, offline.sleeps) == (1, False, [], [])


def test_nowhere_is_cached_as_such(tmp_path: Path) -> None:
    _lookup([_stop(8)], tmp_path, FakeNominatim({"error": "Unable to geocode"}))
    again = _stop(8)
    report = _lookup([again], tmp_path, FakeNominatim(down=True))
    assert again.town is None
    assert (report.cached, report.found, report.failed) == (1, 0, False)


def test_a_stale_cache_is_looked_up_again(tmp_path: Path) -> None:
    _lookup([_stop(8)], tmp_path, FakeNominatim())
    http = FakeNominatim()
    assert _lookup([_stop(8)], tmp_path, http, max_age_days=0).cached == 0
    assert len(http.asked) == 1


def test_an_odd_answer_counts_as_a_failure(tmp_path: Path) -> None:
    stop = _stop(8)
    assert _lookup([stop], tmp_path, FakeNominatim(["not", "a", "dict"])).failed
    assert stop.town is None


def test_nominatim_down_gives_up_quickly_and_caches_nothing(tmp_path: Path) -> None:
    stops = [_stop(8, lat=LAT + i * 0.01) for i in range(10)]
    http = FakeNominatim(down=True)
    report = _lookup(stops, tmp_path, http)
    assert all(s.town is None for s in stops)
    assert (report.found, report.failed) == (0, True)
    assert len(http.asked) == 3  # NOMINATIM_GIVE_UP failures in a row
    later = FakeNominatim()
    _lookup(stops[:1], tmp_path, later)
    assert len(later.asked) == 1


# --- strip rows: a name only goes where the row has room left, and never adds a line


STRIP = RowLayout(avail=12.5, max_emojis=99, sep=" ", range_m=1000, emoji_lines=2, wrap_avail=18.5)
BOOK = Roadbook("t", 100, 0, 0, [], [], [], 0, 0)


def _stop_row(emojis: str, *, km_end: float = 60.0, dist: float = 1.5) -> tuple[Item, dict[str, Any]]:
    stop = Stop([Poi("a", "", LAT, LON, km=60.0), Poi("b", "", LAT, LON, km=km_end)])
    it = Item("stop", 60.0, 0, emojis=[Glyph(e) for e in emojis], stop=stop, dist_to_next=dist)
    return it, _row(it, BOOK, STRIP)


def _town(row: dict[str, Any]) -> str:
    """The town a row's heading lines name, or ""."""
    return next((h["text"] for h in row["heads"] if h["cls"] == "town"), "")


def _named_rows(*towns: str | None, emojis: str = "🚰") -> list[dict[str, Any]]:
    items, rows = [], []
    for town in towns:
        it, row = _stop_row(emojis)
        assert it.stop is not None
        it.stop.town = town
        items.append(it)
        rows.append(row)
    _add_heads(rows, Roadbook("t", 100, 0, 0, items, [], [], 0, 0))
    return rows


def test_a_named_stop_gets_a_line_above_its_row() -> None:
    _, plain = _stop_row("🚰")
    [row] = _named_rows("Le Mas-d'Azil")
    assert (_town(row), row["h"]) == ("Le Mas-d'Azil", plain["h"] + HEAD_H)


def test_a_full_row_still_gets_its_town_in_full() -> None:
    _, plain = _stop_row("🚰🚻🥖☕🛒⛽🍔")
    [row] = _named_rows("Le Mas-d'Azil", emojis="🚰🚻🥖☕🛒⛽🍔")
    assert (_town(row), row["h"]) == ("Le Mas-d'Azil", plain["h"] + HEAD_H)


def test_the_same_town_is_not_named_again_on_the_next_row() -> None:
    rows = _named_rows("Foix", "Foix", None, "Pau", "Foix")
    assert [_town(r) for r in rows] == ["Foix", "", "", "Pau", "Foix"]
    assert [r["h"] for r in rows][1:3] == [rows[2]["h"]] * 2  # no line where the town isn't named


# --- end to end, on a sample, with a fake Nominatim


def _named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: Any = None) -> FakeNominatim:  # noqa: ANN401
    http = FakeNominatim(answer)
    fake = partial(lookup, cache_path=tmp_path / "towns.json", http=http, sleep=lambda _: None)
    monkeypatch.setattr(build_module, "name_towns", fake)
    return http


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_on_the_sample_the_big_stops_are_named_on_a_line_of_their_own(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    http = _named(monkeypatch, tmp_path)
    cfg = load_config()
    cfg["towns"]["enabled"] = False
    plain = render_html(build(HILLY, cfg), cfg)
    cfg["towns"]["enabled"] = True
    book = build(HILLY, cfg)
    named = {(s.km, s.km_end) for s in book.stops if s.town}
    # Saint-Girons, Le Mas-d'Azil and Auterive; Saint-Béat (km 4.3) and Saint-Lary (km 35.4) for their village shops;
    # fountains, cemeteries and lone bakeries are not asked. Saint-Girons' two busy stops, both asked, make one
    assert {(60.0, 61.5), (178.4, 179.7)} <= {(round(a, 1), round(b, 1)) for a, b in named}
    assert {4.3, 35.4} <= {round(a, 1) for a, _ in named}
    assert len(http.asked) == 6
    assert len(named) == len(busy(book.stops, CFG)) == 5
    html = render_html(book, cfg)
    assert "<b>km 60.0 → 61.5 · Saint-Girons</b>" in html
    # on a line above its row; the fake answers Saint-Girons for all, so the strip names it once
    assert html.count('<div class="head town"><span>Saint-Girons</span></div>') == 1
    assert "town names © OpenStreetMap" in html.replace("Town", "town")
    # lines of 3.2 mm: they move rows down to the next strip, but need no strip more
    strips = re.compile(r'<div class="hdr"><span>([^<]*)</span>')
    assert len(strips.findall(html)) == len(strips.findall(plain)) == 3


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_says_how_many_towns_it_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _named(monkeypatch, tmp_path)
    out = tmp_path / "out.html"
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "Towns: 6 of 6 busy stops named" in result.output
    _named(monkeypatch, tmp_path / "other").down = True
    result = CliRunner().invoke(app, [str(HILLY), "--no-details", "-o", str(out)])
    assert result.exit_code == 0
    assert "did not answer" in result.output
    result = CliRunner().invoke(app, [str(HILLY), "--no-towns", "-o", str(out)])
    assert result.exit_code == 0
    assert "Towns" not in result.output


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_towns_are_named_by_default_unless_turned_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = _named(monkeypatch, tmp_path / "on")
    cfg = load_config()
    assert build(HILLY, cfg).towns is not None
    assert len(http.asked) == 6
    http = _named(monkeypatch, tmp_path / "off")
    cfg["towns"]["enabled"] = False
    assert build(HILLY, cfg).towns is None
    # a ribbon of tokens has no room for them, and without the reference sheet they would show nowhere
    cfg["towns"]["enabled"] = True
    cfg["render"]["layout"], cfg["render"]["details"] = "line", False
    assert build(HILLY, cfg).towns is None
    assert http.asked == []


@pytest.mark.parametrize(
    ("heights", "pages"),
    [
        ([40, 50, 15], [[40, 50, 15]]),  # the last row runs 5 mm over: it stays, rather than start a strip alone
        ([40, 50, 17], [[40, 50], [17]]),  # 7 mm over is too much
        ([40, 65, 5], [[40], [65, 5]]),  # any other row starts a new strip as soon as it doesn't fit
    ],
)
def test_only_the_last_row_may_overrun_a_strip(heights: list[float], pages: list[list[float]]) -> None:
    got = _paginate([{"h": h} for h in heights], 100, "h")
    assert [[r["h"] for r in p] for p in got] == pages


class OneTownEach(FakeNominatim):
    """Answers each request with the next of `towns`."""

    def __init__(self, *towns: str) -> None:
        super().__init__()
        self.towns = iter(towns)

    def __call__(self, url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401
        super().__call__(url, data)
        return {"address": {"town": next(self.towns)}}


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_town_lines_do_not_leave_the_finish_alone_on_a_strip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = OneTownEach("Saint-Girons", "Saint-Girons", "Le Mas-d'Azil", "Auterive")
    fake = partial(lookup, cache_path=tmp_path / "towns.json", http=http, sleep=lambda _: None)
    monkeypatch.setattr(build_module, "name_towns", fake)
    cfg = load_config()
    cfg["towns"].update(enabled=True, shop_min_pois=99)  # only the three towns (Saint-Girons is two stops)
    cfg["render"]["length_mm"] = 209  # two strips without the town lines; with them, the finish only just overflows
    html = render_html(build(HILLY, cfg), cfg)
    assert html.count('<div class="head town">') == 3
    headers = re.findall(r'<div class="hdr"><span>([^<]*)</span><span>([^<]*)</span>', html)
    assert [n for _, n in headers] == ["1/2", "2/2"]  # the town lines take the finish past the second strip's length
    assert headers[-1][0].endswith("206.2")
    assert re.search(r'<div class="strip" style="height: calc\(var\(--l\) \+ \d\.\dmm\)">', html)


def _at(km: float, n: int = 1, town: str | None = None) -> Stop:
    return Stop([Poi(f"p{km}", "", LAT, LON, km=km + i / 10, category="water") for i in range(n)], town=town)


def test_the_stops_of_one_town_make_one_whatever_split_them() -> None:
    stops = [_at(10, 9, "Le Mans"), _at(11.5), _at(12.4, 8, "Le Mans"), _at(14, 8, "Le Mans"), _at(20, 8, "Arnage")]
    grouped = group(stops, within_km=5)
    # the unnamed stop between two of Le Mans is in Le Mans too; Arnage stays apart
    assert [(s.km, s.km_end, s.town, len(s.pois)) for s in grouped] == [
        (10.0, pytest.approx(14.7), "Le Mans", 9 + 1 + 8 + 8),
        (20.0, pytest.approx(20.7), "Arnage", 8),
    ]


def test_a_town_the_route_comes_back_through_later_is_a_visit_of_its_own() -> None:
    stops = [_at(10, 8, "Chartres"), _at(30, 8, "Chartres")]
    assert len(group(stops, within_km=5)) == 2
    assert len(group([_at(10, 8, "Chartres"), _at(12, 8, "Chartres")], within_km=0)) == 2  # 0: no grouping
    # an unnamed stop with no named stop of the same town after it stays on its own
    assert len(group([_at(10, 8, "Chartres"), _at(11)], within_km=5)) == 2
