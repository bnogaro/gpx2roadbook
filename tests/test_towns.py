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
from roadbook.render import RowLayout, _add_towns, _place_town, _row, _shorten, _text_w, render_html
from roadbook.towns import REVERSE, ZOOM, Report, busy, centre, lookup

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
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


def test_only_stops_with_enough_pois_are_busy() -> None:
    fountain, bakery, town = _stop(1), _stop(1), _stop(CFG["min_pois"])
    assert busy([fountain, bakery, town], CFG["min_pois"]) == [town]


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


STRIP = RowLayout(avail=12.5, max_emojis=99, sep=" ", leg_elevation=False, range_m=1000, emoji_lines=2, wrap_avail=18.5)
BOOK = Roadbook("t", 100, 0, 0, [], [], [], 0, 0)


def _stop_row(emojis: str, *, km_end: float = 60.0, dist: float = 1.5) -> tuple[Item, dict[str, Any]]:
    stop = Stop([Poi("a", "", LAT, LON, km=60.0), Poi("b", "", LAT, LON, km=km_end)])
    it = Item("stop", 60.0, 0, emojis=[Glyph(e) for e in emojis], stop=stop, dist_to_next=dist)
    return it, _row(it, BOOK, STRIP)


def test_shorten_cuts_a_name_with_an_ellipsis_or_gives_up() -> None:
    name = "Saint-Arnoult-en-Yvelines"
    assert _shorten(name, 99) == name
    cut = _shorten(name, 10)
    assert cut is not None
    assert cut.endswith("…")
    assert name.startswith(cut[:-1])
    assert _text_w(cut, 6) <= 10
    assert _shorten(name, 3) is None  # "Sai…" says too little: left to the reference sheet


def test_a_long_stop_names_its_town_on_its_end_km_line() -> None:
    _, row = _stop_row("🚰🚻", km_end=61.3)
    h = row["h"]
    assert _place_town(row, "Saint-Girons", STRIP)
    assert (row["town"], row["town_at"], row["h"]) == ("Saint-Girons", "sub", h)


def test_a_quiet_row_names_its_town_after_its_emojis() -> None:
    _, row = _stop_row("🚰")
    assert _place_town(row, "Auterive", STRIP)
    assert (row["town"], row["town_at"]) == ("Auterive", "main")


def test_a_wrapped_row_names_its_town_after_its_last_emojis() -> None:
    _, row = _stop_row("🚰🚻🥖")  # two beside the km, one on the line below
    assert row["emoji_lines"]
    assert _place_town(row, "Maintenon", STRIP)
    assert row["town_at"] == "emo"


def test_a_full_row_leaves_its_town_to_the_reference_sheet() -> None:
    _, row = _stop_row("🚰🚻🥖☕🛒⛽🍔")
    assert not _place_town(row, "Le Mas-d'Azil", STRIP)
    assert (row["town"], row["town_at"]) == ("", "")


def test_the_same_town_is_not_named_again_on_the_next_row() -> None:
    items, rows = [], []
    for town in ("Foix", "Foix", None, "Pau", "Foix"):  # short names: room is not what this is about
        it, row = _stop_row("🚰")
        assert it.stop is not None
        it.stop.town = town
        items.append(it)
        rows.append(row)
    _add_towns(rows, Roadbook("t", 100, 0, 0, items, [], [], 0, 0), STRIP)
    assert [r["town"] for r in rows] == ["Foix", "", "", "Pau", "Foix"]


# --- end to end, on a sample, with a fake Nominatim


def _named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answer: Any = None) -> FakeNominatim:  # noqa: ANN401
    http = FakeNominatim(answer)
    fake = partial(lookup, cache_path=tmp_path / "towns.json", http=http, sleep=lambda _: None)
    monkeypatch.setattr(build_module, "name_towns", fake)
    return http


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_on_the_sample_the_big_stops_are_named_and_the_rows_keep_their_height(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    http = _named(monkeypatch, tmp_path)
    cfg = load_config()
    plain = render_html(build(HILLY, cfg), cfg)
    cfg["towns"]["enabled"] = True
    book = build(HILLY, cfg)
    named = {(s.km, s.km_end) for s in book.stops if s.town}
    # Saint-Girons and Auterive (and Le Mas-d'Azil, 10 POIs); fountains, cemeteries and lone bakeries are not asked
    assert {(60.0, 61.3), (178.4, 179.7)} <= {(round(a, 1), round(b, 1)) for a, b in named}
    assert len(http.asked) == len(named) == 3
    assert all(s.town is None for s in book.stops if len(s.pois) < CFG["min_pois"])
    html = render_html(book, cfg)
    assert "<b>km 60.0 → 61.3 · Saint-Girons</b>" in html
    assert '<span class="town">Saint-Girons</span>' in html
    assert "town names © OpenStreetMap" in html.replace("Town", "town")
    # names only use room rows already had: the strips break at the same rows
    strips = re.compile(r'<div class="hdr"><span>([^<]*)</span>')
    assert strips.findall(html) == strips.findall(plain)


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_says_how_many_towns_it_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _named(monkeypatch, tmp_path)
    out = tmp_path / "out.html"
    result = CliRunner().invoke(app, [str(HILLY), "--towns", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "Towns: 3 of 3 busy stops named" in result.output
    _named(monkeypatch, tmp_path / "other").down = True
    result = CliRunner().invoke(app, [str(HILLY), "--towns", "--no-details", "-o", str(out)])
    assert result.exit_code == 0
    assert "did not answer" in result.output


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_towns_are_not_looked_up_unless_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = _named(monkeypatch, tmp_path)
    cfg = load_config()
    assert cfg["towns"]["enabled"] is False
    assert build(HILLY, cfg).towns is None
    # a ribbon of tokens has no room for them, and without the reference sheet they would show nowhere
    cfg["towns"]["enabled"] = True
    cfg["render"]["layout"], cfg["render"]["details"] = "line", False
    assert build(HILLY, cfg).towns is None
    assert http.asked == []
