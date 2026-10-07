"""What a shop's OSM `opening_hours` mean on a given day: when it is open, or that it is closed.

The value is evaluated where the shop is, so public holidays (`PH`) follow its country's calendar. Times are the
shop's local times, as written in OSM: the ride's date and times are local too, so no time zone is involved.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import opening_hours
from opening_hours import OpeningHours, State

_DAY = dt.timedelta(days=1)
# what OpeningHours() raises for a value it can't use; its type stubs leave these out (they promise SyntaxError)
_INVALID: tuple[type[Exception], ...] = tuple(
    getattr(opening_hours, name) for name in ("ParserError", "UnknownCountryError", "InvalidCoordinatesError")
)


@dataclass(frozen=True)
class DayHours:
    """A shop's hours on one date, as the reference sheet shows them."""

    text: str  # "07:00-12:30, 15:30-19:00" (with en dashes), "open 24 h", "closed"
    closed: bool = False  # closed all day


def ride_date(cfg: dict[str, object]) -> dt.date | None:
    """`hours.date` from the config: "YYYY-MM-DD", a TOML date, or empty for none."""
    value = cfg.get("date")
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value) if isinstance(value, str) and value else None


def parse(value: str, lat: float, lon: float) -> OpeningHours | None:
    """The OSM value, ready to evaluate at the shop's place; None when it is not valid `opening_hours` syntax."""
    try:
        return OpeningHours(value, coords=(lat, lon), auto_timezone=False, auto_country=True)
    except _INVALID:
        return None


def _hhmm(t: dt.datetime, day: dt.date) -> str:
    return "24:00" if t.date() > day else t.strftime("%H:%M")  # an interval running to midnight ends at 24:00


def on_day(value: str, lat: float, lon: float, day: dt.date) -> DayHours | None:
    """The shop's hours on `day`; None when the value can't be read, so the caller shows it as given."""
    oh = parse(value, lat, lon)
    if oh is None:
        return None
    start = dt.datetime.combine(day, dt.time())
    spans = list(oh.intervals(start, start + _DAY))
    if all(s == State.CLOSED for _, _, s, _ in spans):
        return DayHours("closed", closed=True)
    parts = []
    for a, b, state, comment in spans:
        if state == State.CLOSED:
            continue
        when = "open 24 h" if (a, b) == (start, start + _DAY) else f"{_hhmm(a, day)}\u2013{_hhmm(b, day)}"
        maybe = "?" if state == State.UNKNOWN else ""  # OSM says it may be open, without saying for sure
        note = f' "{comment}"' if comment else ""
        parts.append(f"{when}{maybe}{note}")
    return DayHours(", ".join(parts))
