"""What a shop's OSM `opening_hours` mean for the ride: its hours on the ride day, or whether it is open on arrival.

The value is evaluated where the shop is, so public holidays (`PH`) follow its country's calendar. Times are the
shop's local times, as written in OSM: the ride's date and times are local too, so no time zone is involved.
"""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

import opening_hours
from opening_hours import OpeningHours, State

from .i18n import _

if TYPE_CHECKING:
    from .config import RideSettings

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


class Terrain(Protocol):
    """The route's profile, as the arrival times need it: `profile.Profile`."""

    def gain(self, km_a: float, km_b: float, /) -> float:
        """Metres climbed between two km."""
        ...

    def descent_km(self, km_a: float, km_b: float, /) -> float:
        """Km going down between two km, steep enough to ride at the descent speed."""
        ...


class _Flat:
    """A route without a profile: flat all along."""

    def gain(self, _a: float, _b: float) -> float:
        return 0.0

    def descent_km(self, _a: float, _b: float) -> float:
        return 0.0


_flat = _Flat()


@dataclass(frozen=True)
class Window:
    """When the rider may reach a stop: from its first POI at the earliest to its last at the latest."""

    early: dt.datetime
    late: dt.datetime


def parse_break(text: str) -> list[float]:
    """A planned break as typed, "KM:MINUTES" ("95:45"), as a `[ride] breaks` entry; raises ValueError if it isn't."""
    km, sep, minutes = text.partition(":")
    if not sep:
        msg = f"{text!r} has no ':'"
        raise ValueError(msg)
    return [float(km), float(minutes)]


@dataclass(frozen=True)
class Ride:
    """The ride the `[ride]` settings plan: its date and, given a start time and a speed, when it reaches each km.

    The time to a km is its distance at `speed_kmh`, its descents at `descent_kmh` if given, plus `climb_min_per_100m`
    for every 100 m climbed on the way, plus the planned breaks before it.
    """

    date: dt.date | None = None
    start: dt.datetime | None = None  # set only when the date, start time and speed are all known
    speed_kmh: float = 0.0  # on the flat, short stops included
    margin_pct: float = 15.0
    margin_min: float = 20.0
    climb_min_per_100m: float = 0.0
    breaks: tuple[tuple[float, float], ...] = ()  # (km, minutes): a break there delays every stop after it
    descent_kmh: float = 0.0  # down a descent; 0: at speed_kmh, as on the flat

    @classmethod
    def of(cls, settings: RideSettings) -> Ride:
        """From the `[ride]` settings: it has a start only given a date, a start time and a speed."""
        date, time, speed = settings.date, settings.start, settings.speed_kmh
        start = dt.datetime.combine(date, time) if date and time and speed > 0 else None
        breaks = tuple(sorted(settings.breaks))
        margins = settings.margin_pct, settings.margin_min
        return cls(date, start, speed, *margins, settings.climb_min_per_100m, breaks, settings.descent_kmh)

    def riding_h(self, km_from: float, km_to: float, terrain: Terrain = _flat) -> float | None:
        """Hours riding from one km to another at the ride's speeds, its climbing included, the breaks not; None without
        a speed."""
        if self.speed_kmh <= 0:
            return None
        down = terrain.descent_km(km_from, km_to) if self.descent_kmh > 0 else 0.0
        hours = (km_to - km_from - down) / self.speed_kmh + (down / self.descent_kmh if down else 0.0)
        return hours + terrain.gain(km_from, km_to) / 100 * self.climb_min_per_100m / 60

    def _at(self, start: dt.datetime, km: float, sign: int, terrain: Terrain) -> dt.datetime:
        """The estimate for `km`, moved by its margin: earlier (sign -1) or later (+1)."""
        riding = self.riding_h(0.0, km, terrain) or 0.0  # a start time comes with a speed
        resting = sum(minutes for at_km, minutes in self.breaks if at_km < km) / 60
        # the error grows with the time ridden: a little faster or slower adds up over a long day; breaks are planned
        spread = max(riding * self.margin_pct / 100, self.margin_min / 60)
        at = start + dt.timedelta(hours=riding + resting + sign * spread)
        # to the minute, rounding outwards: a window shown as "06:00-06:21" never ends before what it says
        minute = at.replace(second=0, microsecond=0)
        return minute + dt.timedelta(minutes=1) if sign > 0 and at > minute else minute

    def at(self, km: float, terrain: Terrain = _flat) -> dt.datetime | None:
        """When the rider is expected at `km`, without the margin; None without a start time and speed."""
        return None if self.start is None else self._at(self.start, km, 0, terrain)

    def window(self, km_from: float, km_to: float, terrain: Terrain = _flat) -> Window | None:
        """When the rider may be between `km_from` and `km_to`; None without a start time and speed.

        `terrain` is the route's profile, its climbing and descents; without one, the route counts as flat.
        """
        if self.start is None:
            return None
        early, late = self._at(self.start, km_from, -1, terrain), self._at(self.start, km_to, +1, terrain)
        return Window(max(self.start, early), late)


@dataclass(frozen=True)
class Verdict:
    """Is a shop open while the rider may pass?"""

    state: str  # "open" or "closed" (all along the window), "tight" (it changes within it), "unknown"
    note: str = ""  # for "tight": what changes and when, "closes 12:30", "opens 07:00", in the road book's language


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
        return DayHours(_("closed"), closed=True)
    parts = []
    for a, b, state, comment in spans:
        if state == State.CLOSED:
            continue
        when = _("open 24 h") if (a, b) == (start, start + _DAY) else f"{_hhmm(a, day)}\u2013{_hhmm(b, day)}"
        maybe = "?" if state == State.UNKNOWN else ""  # OSM says it may be open, without saying for sure
        note = f' "{comment}"' if comment else ""
        parts.append(f"{when}{maybe}{note}")
    return DayHours(", ".join(parts))


def _change(before: State, after: State, at: str) -> str:
    """What a shop does at `at`, when its state changes from `before` to `after`: "closes 12:30", "opens 07:00"…"""
    if after == State.CLOSED:
        return _("closes {time}", time=at)
    if after == State.OPEN:
        return _("opens {time}", time=at)
    return _("may close {time}", time=at) if before == State.OPEN else _("may open {time}", time=at)


def verdict(value: str, lat: float, lon: float, window: Window) -> Verdict | None:
    """Whether the shop is open all along `window`, closed all along, or changes within it; None if unreadable."""
    oh = parse(value, lat, lon)
    if oh is None:
        return None
    spans = list(oh.intervals(window.early, window.late))
    states = {s for _, _, s, _ in spans}
    if len(states) == 1:
        return Verdict({State.OPEN: "open", State.CLOSED: "closed"}.get(states.pop(), "unknown"))
    notes = [_change(a[2], b[2], f"{b[0]:%H:%M}") for a, b in itertools.pairwise(spans) if a[2] != b[2]]
    return Verdict("tight", ", ".join(notes))
