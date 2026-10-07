import datetime as dt
from pathlib import Path

import pytest

from roadbook.build import build
from roadbook.config import load_config
from roadbook.hours import Report
from roadbook.opening import DayHours, on_day, ride_date
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


def test_ride_date() -> None:
    assert ride_date({"date": ""}) is None
    assert ride_date({"date": "2026-10-11"}) == SUNDAY
    assert ride_date({"date": SUNDAY}) == SUNDAY  # a bare TOML date in a --config file


def test_the_reference_sheet_shows_the_ride_day(monkeypatch: pytest.MonkeyPatch) -> None:
    hours = {"Intermarché Super": "Mo-Sa 09:00-19:30", "La Tablée": "Tu-Sa 09:00-21:30", "Intermarché": "9h-19h"}

    def fake_lookup(pois: list, cfg: dict) -> Report:  # sets hours as the OSM lookup would, without network
        for p in pois:
            if p.category in cfg["categories"]:
                p.opening_hours = hours.get(p.name)
        return Report(asked=1, found=1)

    monkeypatch.setattr("roadbook.build.lookup", fake_lookup)
    cfg = load_config()
    cfg["hours"].update(enabled=True, date="2026-10-14")
    html = render_html(build(HILLY, cfg), cfg)
    assert "opening hours on Wed 14 Oct 2026" in html
    assert f'Intermarché Super <span class="off">(265 m)</span> <span class="oh">09:00{EN}19:30</span>' in html
    assert '<span class="off">9h-19h</span>' in html  # not OSM syntax: shown as written, muted
    assert '<span class="off">hours unknown</span>' in html

    cfg["hours"]["date"] = "2026-10-11"  # Sunday
    html = render_html(build(HILLY, cfg), cfg)
    assert 'Intermarché Super <span class="off">(265 m)</span> <span class="closed">closed</span>' in html
