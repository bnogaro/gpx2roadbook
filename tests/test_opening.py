import datetime as dt
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from roadbook.build import build
from roadbook.cli import app
from roadbook.config import load_config
from roadbook.hours import Report
from roadbook.opening import DayHours, Ride, Verdict, Window, on_day, verdict
from roadbook.render import render_html

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
LAT, LON = 42.912725, 0.6478569  # Cierp-Gaud, France: French public holidays apply
SUNDAY, WEDNESDAY, ARMISTICE = dt.date(2026, 10, 11), dt.date(2026, 10, 14), dt.date(2026, 11, 11)  # 11/11 is a Wed
EN = "\u2013"


@pytest.mark.parametrize(
    ("value", "day", "shown"),
    [
        ("Mo-Sa 09:00-19:30", WEDNESDAY, DayHours(f"09:00{EN}19:30")),
        ("Mo-Sa 09:00-19:30", SUNDAY, DayHours("closed", closed=True)),
        ("Mo-Fr 07:00-12:30,15:30-19:00; Su 07:00-12:00", WEDNESDAY, DayHours(f"07:00{EN}12:30, 15:30{EN}19:00")),
        ("Mo-Fr 07:00-12:30,15:30-19:00; Su 07:00-12:00", SUNDAY, DayHours(f"07:00{EN}12:00")),
        ("24/7", SUNDAY, DayHours("open 24 h")),
        ("Mo-Su 18:00-02:00", WEDNESDAY, DayHours(f"00:00{EN}02:00, 18:00{EN}24:00")),
        # public holidays follow the shop's country
        ("Mo-Sa 08:00-19:00; PH off", ARMISTICE, DayHours("closed", closed=True)),
        ("Mo-Sa 08:00-19:00; PH 08:00-12:00", ARMISTICE, DayHours(f"08:00{EN}12:00")),
        # what OSM leaves open is shown as such, comments included
        ("Mo-Fr 08:00-12:00 unknown", WEDNESDAY, DayHours(f"08:00{EN}12:00?")),
        ('Mo-Fr 08:00-12:00 "sur rendez-vous"', WEDNESDAY, DayHours(f'08:00{EN}12:00 "sur rendez-vous"')),
    ],
)
def test_on_day(value: str, day: dt.date, shown: DayHours) -> None:
    assert on_day(value, LAT, LON, day) == shown


def test_a_value_that_is_not_opening_hours_syntax_is_left_to_the_caller() -> None:
    assert on_day("Mo-Sa 9h-19h", LAT, LON, SUNDAY) is None


def _t(hhmm: str, day: dt.date = WEDNESDAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.fromisoformat(hhmm))


def _ride(**cfg: object) -> Ride:
    return Ride.from_cfg({**load_config()["ride"], **cfg})


def test_ride_settings() -> None:
    assert _ride() == Ride(climb_min_per_100m=5)  # nothing set: no date, no estimate
    assert _ride(date="2026-10-11").date == SUNDAY
    assert _ride(date=SUNDAY).date == SUNDAY  # a bare TOML date in a --config file
    assert _ride(date=SUNDAY, start="06:00").start is None  # no speed: no estimate
    assert _ride(date=SUNDAY, start="06:00", speed_kmh=25).start == _t("06:00", SUNDAY)
    assert _ride(date=SUNDAY, start=dt.time(6), speed_kmh=25).start == _t("06:00", SUNDAY)


def test_the_arrival_window_widens_with_the_time_ridden() -> None:
    ride = _ride(date=WEDNESDAY, start="06:00", speed_kmh=25)  # margin: 15 %, at least 20 min
    assert ride.window(0, 0) == Window(_t("06:00"), _t("06:20"))  # never before the start
    assert ride.window(25, 25) == Window(_t("06:40"), _t("07:20"))  # 1 h ridden: the 20 min floor
    assert ride.window(200, 200) == Window(_t("12:48"), _t("15:12"))  # 8 h ridden: +-15 % = 72 min
    # a stop stretching over several km: from its first POI at the earliest to its last at the latest
    assert ride.window(25, 50) == Window(_t("06:40"), _t("08:20"))
    assert _ride(date=WEDNESDAY).window(25, 25) is None


def _hilly(a: float, b: float) -> float:
    """A profile climbing 10 m per km, all the way."""
    return 10 * (b - a)


def test_climbing_and_breaks_delay_the_estimate() -> None:
    flat = _ride(date=WEDNESDAY, start="06:00", speed_kmh=25, climb_min_per_100m=0)
    assert flat.window(100, 100, _hilly) == Window(_t("09:24"), _t("10:36"))  # 4 h, +-36 min
    climbing = _ride(date=WEDNESDAY, start="06:00", speed_kmh=25, climb_min_per_100m=6)
    # 1000 m climbed on the way: 4 h + 60 min, and the margin grows with it (15 % of 5 h = 45 min)
    assert climbing.window(100, 100, _hilly) == Window(_t("10:15"), _t("11:45"))
    assert climbing.window(100, 100) == flat.window(100, 100)  # without a profile, the route counts as flat
    rests = _ride(date=WEDNESDAY, start="06:00", speed_kmh=25, climb_min_per_100m=0, breaks=[[80, 45], [50, 15]])
    # both breaks are before km 100: an hour later, with the same margin (breaks are planned, not guessed)
    assert rests.window(100, 100) == Window(_t("10:24"), _t("11:36"))
    assert rests.window(50, 50) == flat.window(50, 50)  # a break at km 50 only delays what comes after it
    assert rests.window(60, 60) == Window(_t("08:17"), _t("09:01"))  # 08:24 +15 min break, +-22 min


@pytest.mark.parametrize(
    ("value", "early", "late", "shown"),
    [
        ("Mo-Sa 07:00-19:00", "08:00", "09:00", Verdict("open")),
        ("Mo-Sa 07:00-19:00", "19:00", "20:00", Verdict("closed")),  # closes right as the window starts
        ("Mo-Sa 07:00-12:30", "12:00", "13:00", Verdict("tight", "closes 12:30")),
        ("Mo-Sa 07:00-12:30,14:30-19:00", "12:00", "15:00", Verdict("tight", "closes 12:30, opens 14:30")),
        ("Mo-Sa 07:00-19:00", "06:30", "07:30", Verdict("tight", "opens 07:00")),
        ("Mo-Fr 08:00-12:00 unknown", "09:00", "10:00", Verdict("unknown")),
        ("Mo-Fr 08:00-12:00 unknown", "11:00", "13:00", Verdict("tight", "closes 12:00")),
    ],
)
def test_verdict(value: str, early: str, late: str, shown: Verdict) -> None:
    assert verdict(value, LAT, LON, Window(_t(early), _t(late))) == shown


def test_a_window_across_midnight_is_judged_on_both_days() -> None:
    night = Window(_t("23:30", SUNDAY), _t("00:30", dt.date(2026, 10, 12)))
    assert verdict("Mo 00:00-06:00; Su 20:00-24:00", LAT, LON, night) == Verdict("open")
    assert verdict("Su 20:00-24:00", LAT, LON, night) == Verdict("tight", "closes 00:00")
    assert verdict("9h-19h", LAT, LON, night) is None


HOURS = {"Intermarché Super": "Mo-Sa 09:00-19:30", "La Tablée": "Tu-Sa 09:00-21:30", "Intermarché": "9h-19h"}


@pytest.fixture
def fake_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sets hours on the shops at km 0.2 of HILLY as the OSM lookup would, without network."""

    def lookup(pois: list, cfg: dict) -> Report:
        for p in pois:
            if p.category in cfg["categories"]:
                p.opening_hours = HOURS.get(p.name)
        return Report(asked=1, found=1)

    monkeypatch.setattr("roadbook.build.lookup", lookup)


@pytest.mark.usefixtures("fake_lookup")
def test_the_reference_sheet_shows_the_ride_day() -> None:
    cfg = load_config()
    cfg["hours"]["enabled"] = True
    cfg["ride"]["date"] = "2026-10-14"
    html = render_html(build(HILLY, cfg), cfg)
    assert "opening hours on Wed 14 Oct 2026" in html
    assert f'Intermarché Super <span class="off">(265 m)</span> <span class="oh">09:00{EN}19:30</span>' in html
    assert '<span class="off">9h-19h</span>' in html  # not OSM syntax: shown as written, muted
    assert '<span class="off">hours unknown</span>' in html
    # the watermark follows the OpenStreetMap credit
    assert re.search(r'<p class="credit">[^<]*</p>\s*<p class="colophon">Made with gpx2roadbook ', html)

    cfg["ride"]["date"] = "2026-10-11"  # Sunday
    html = render_html(build(HILLY, cfg), cfg)
    assert 'Intermarché Super <span class="off">(265 m)</span> <span class="closed">closed</span>' in html


@pytest.mark.usefixtures("fake_lookup")
def test_with_a_start_and_speed_shops_are_judged_on_arrival() -> None:
    cfg = load_config()
    cfg["hours"]["enabled"] = True
    cfg["ride"].update(date="2026-10-14", start="06:00", speed_kmh=25)
    book = build(HILLY, cfg)
    first = book.stops[0]  # km 0.2, reached between 06:00 and 06:21: before the shops open at 09:00
    assert first.window == Window(_t("06:00"), _t("06:21"))
    html = render_html(book, cfg)
    assert "start Wed 14 Oct 2026 06:00 at 25 km/h" in html
    assert f'<b>km 0.2</b> <span class="eta">~06:00{EN}06:21</span>' in html
    closed = f'<b class="verdict">closed</b> <span class="off">09:00{EN}19:30</span>'  # and when it opens, muted
    assert f'Intermarché Super <span class="off">(265 m)</span> {closed}' in html
    # on the strip, the grocery and fast food emojis are dimmed; the fuel station's hours can't be read, so not it
    row = next(i for i in book.items if i.stop is first)
    assert {g.emoji: g.dim for g in row.emojis} == {"🍔": True, "🛒": True, "⛽": False}
    assert '<span class="dim">🛒</span>' in html

    cfg["ride"]["start"] = "08:50"  # there between 08:50 and 09:11: the supermarket opens meanwhile
    html = render_html(build(HILLY, cfg), cfg)
    assert '<b class="verdict">⚠ opens 09:00</b>' in html


def test_the_sheet_title_states_the_estimate_s_assumptions() -> None:
    cfg = load_config()
    cfg["ride"].update(date="2026-10-17", start="07:00", speed_kmh=26, breaks=[[95, 45]])
    html = render_html(build(HILLY, cfg), cfg)
    assert "start Sat 17 Oct 2026 07:00 at 26 km/h + 5 min/100 m climbed" in html
    assert "breaks: km 95 (45 min)" in html


@pytest.mark.parametrize("bad", ["95", "95:lunch", "km95:45"])
def test_a_break_needs_a_km_and_minutes(bad: str) -> None:
    result = CliRunner().invoke(app, [str(HILLY), "--break", bad])
    assert result.exit_code == 2
    assert "95:45" in result.output
