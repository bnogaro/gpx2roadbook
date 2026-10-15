"""The reference sheet: each stop's POIs, a shop's hours on the day, the named climbs between them, and its title,
credits and key to the climb colours."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from roadbook.i18n import _, date_text
from roadbook.opening import Ride, on_day
from roadbook.svg import grade_colour

from .strips import _spans

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Collection

    from roadbook.config import Category
    from roadbook.model import Climb, Poi, Roadbook
    from roadbook.opening import Window


def poi_category(p: Poi) -> str:
    """The category of a POI inside a stop; filter_pois() only keeps classified ones."""
    if p.category is None:
        msg = f"POI {p.name!r} at km {p.km:.1f} has no category"
        raise ValueError(msg)
    return p.category


_TIME_RANGE = re.compile(r"(\d\d:\d\d)-(\d\d:\d\d)")


def pretty_hours(value: str) -> str:
    """An OSM `opening_hours` value, easier to read on paper: one rule after another, en dashes between times."""
    rules = [" ".join(r.split()) for r in value.split(";") if r.strip()]
    return " · ".join(_TIME_RANGE.sub("\\1\u2013\\2", r).replace(",", ", ") for r in rules)


@dataclass(frozen=True)
class ShopHours:
    """How the reference sheet shows a shop's hours: a verdict word if judged, then its hours, and their CSS class."""

    verdict: str
    hours: str
    hours_class: str  # "oh" | "off" | "closed" | "write-in"


def shop_hours(p: Poi, day: dt.date | None) -> ShopHours:
    """A shop's hours on the reference sheet: those of `day` if given and readable, else the value as OSM gives it."""
    if not p.opening_hours:
        # a blank to write them in by hand, once checked elsewhere (the shop's site, a map app) before the ride
        return ShopHours("", "", "write-in")
    v = p.verdict
    verdicts = {"closed": _("closed"), "unknown": _("open?")}  # "open" needs no word; "tight" says what changes
    word = (f"⚠ {v.note}" if v.state == "tight" else verdicts.get(v.state, "")) if v else ""
    found = on_day(p.opening_hours, p.lat, p.lon, day) if day else None
    if found is None:  # no day asked, or a value that isn't OSM syntax (then shown as written, muted)
        return ShopHours(word, pretty_hours(p.opening_hours), "off" if day else "oh")
    if found.closed:  # closed all day: the verdict, if any, says it already
        return ShopHours("", found.text, "closed")
    # judged closed or tight: the day's hours are only there to say when instead, so they step back
    return ShopHours(word, found.text, "off" if word else "oh")


def ride_title(book: Roadbook) -> str:
    """What the reference sheet's title says about the ride: its day, and the arrival estimate's assumptions."""
    ride = book.ride
    if ride is None or ride.date is None:
        return ""
    if ride.start is None:
        return _("opening hours on {date}", date=date_text(ride.date)) if book.hours is not None else ""
    pace = _("{speed} km/h", speed=f"{ride.speed_kmh:g}")
    if ride.descent_kmh:
        pace = _("{pace}, {speed} km/h downhill", pace=pace, speed=f"{ride.descent_kmh:g}")
    if ride.climb_min_per_100m:
        pace = _("{pace} + {minutes} min/100 m climbed", pace=pace, minutes=f"{ride.climb_min_per_100m:g}")
    margin = _("±{pct} %, ≥{minutes} min", pct=f"{ride.margin_pct:g}", minutes=f"{ride.margin_min:g}")
    breaks = ", ".join(_("km {km} ({minutes} min)", km=f"{km:g}", minutes=f"{m:g}") for km, m in ride.breaks)
    when = f"{date_text(ride.start.date())} {ride.start:%H:%M}"
    title = _("start {when} at {pace} ({margin})", when=when, pace=pace, margin=margin)
    return title + " · " + _("breaks: {breaks}", breaks=breaks) if breaks else title


def eta_text(w: Window, ride_day: dt.date | None) -> str:
    """ "~09:40-10:50" (en dash), with the weekday when the rider gets there on another day than the start."""

    def at(t: dt.datetime) -> str:
        day = date_text(t.date(), year=False).split(" ", 1)[0]  # its first word: the weekday's name
        return f"{day} {t:%H:%M}" if t.date() != ride_day else f"{t:%H:%M}"

    return f"~{at(w.early)}\u2013{at(w.late)}"


@dataclass(frozen=True)
class PoiLine:
    """A POI on the reference sheet: its name, how far off the route, and a shop's hours if it gets them."""

    name: str
    off: int  # m from the route
    verdict: str = ""
    hours: str = ""
    hours_class: str = ""  # see ShopHours


@dataclass(frozen=True)
class SheetEntry:
    """A stop on the reference sheet, or a named climb at its foot."""

    at: float  # the km it is sorted by
    km: str
    groups: list[tuple[str, list[PoiLine]]]  # (emoji, its POIs)
    town: str = ""
    eta: str = ""  # when the rider gets there: see eta_text()
    col: str = ""  # a climb's name
    grade: str = ""  # a climb's colour, by its average grade


def _details(
    book: Roadbook,
    categories: dict[str, Category],
    range_m: float,
    hours_for: Collection[str],
    grades: tuple[tuple[float, str], ...] = (),
) -> list[SheetEntry]:
    """One entry per stop: its POIs grouped under their emoji; with hours, a shop that gets them has its own line.

    A named climb gets an entry too, at its foot, between the stops: its name, category, length and grade.
    """
    ride = book.ride or Ride()
    out: list[SheetEntry] = []
    order = list(categories)
    for s in book.stops:
        day = s.window.early.date() if s.window else ride.date  # the day the rider gets there
        groups: list[tuple[str, list[PoiLine]]] = []
        shared: dict[str, list[PoiLine]] = {}
        for p in sorted(s.pois, key=lambda p: (order.index(poi_category(p)), p.km)):
            emoji = categories[poi_category(p)].emoji
            poi = PoiLine(p.name or p.type, round(p.offset_m))
            if book.hours is not None and p.category in hours_for:
                h = shop_hours(p, day)
                groups.append((emoji, [replace(poi, verdict=h.verdict, hours=h.hours, hours_class=h.hours_class)]))
            elif emoji in shared:
                shared[emoji].append(poi)
            else:
                shared[emoji] = [poi]
                groups.append((emoji, shared[emoji]))
        km = f"{s.km:.1f} → {s.km_end:.1f}" if _spans(s, range_m) else f"{s.km:.1f}"
        eta = eta_text(s.window, ride.date) if s.window else ""
        out.append(SheetEntry(s.km, km, groups, town=s.town or "", eta=eta))
    out += [_climb_entry(c, grades) for c in book.climbs if c.name]
    return sorted(out, key=lambda e: e.at)  # stable: a stop at a climb's foot comes first, as on the strip


def _climb_entry(c: Climb, grades: tuple[tuple[float, str], ...]) -> SheetEntry:
    stats = [_("Cat {label}", label=c.label)] if c.label else []
    stats += [
        _("{length} km at {grade} %", length=f"{c.length_km:.1f}", grade=f"{c.avg_grade:.1f}"),
        f"↗️{c.gain_m:.0f} m",
    ]
    return SheetEntry(
        c.start_km,
        f"{c.start_km:.1f} → {c.end_km:.1f}",
        [("⛰️", [PoiLine(" · ".join(stats), 0)])],
        col=c.name or "",
        grade=grade_colour(c.avg_grade, grades) if grades else "",  # a swatch before the name, as on the strip
    )


def grade_key(grades: tuple[tuple[float, str], ...]) -> list[tuple[str, str]]:
    """The reference sheet's key to the climb colours: ("under 3 %", colour), ("3 to 6 %", colour), …"""
    steps = [f"{start:g}" for start, _colour in grades]
    labels = [_("under {grade} %", grade=steps[1]) if len(steps) > 1 else _("climbs")]
    labels += [_("{low}\u2013{high} %", low=a, high=b) for a, b in itertools.pairwise(steps[1:])]
    labels += [_("{grade} % and more", grade=steps[-1])] if len(steps) > 1 else []
    return list(zip(labels, [c for _start, c in grades], strict=True))


def credit(book: Roadbook) -> str:
    """What the reference sheet owes OpenStreetMap: "Opening hours, town names and climb names", or "".

    Only what it shows: a lookup that found nothing, such as climb names on a flat route, has nothing to credit.
    """
    used = [
        what
        for what, done in (
            (_("opening hours"), book.hours),
            (_("town names"), book.towns),
            (_("climb names"), book.climb_names),
            (_("place names"), book.places),
        )
        if done is not None and done.found
    ]
    if not used:
        return ""
    listed = used[0] if len(used) == 1 else _("{items} and {last}", items=", ".join(used[:-1]), last=used[-1])
    return listed[0].upper() + listed[1:]
