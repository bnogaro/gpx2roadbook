from __future__ import annotations

import itertools
import logging
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .imposition import chosen as booklet_format
from .kinds import KINDS, climb_of
from .model import Glyph
from .opening import Ride, on_day
from .routemap import svg as map_svg
from .svg import climb_svg, grade_colour, gutter_svg

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable

    from .model import Climb, ClimbPage, Item, Poi, Roadbook, Stop
    from .opening import Window

log = logging.getLogger(__name__)

# Row geometry (mm). Must match the CSS variables in the template.
MAIN_H, SUB_H, HDR_H, PAD, EMO_H = 4.8, 3.2, 4.4, 1.0, 4.4
KM_COL, EMOJI_W, COUNT_W, MORE_W = 9.5, 4.9, 1.3, 2.0
LAYOUT_WIDTH = {"strip": 35.0, "line": 16.0}
WHOLE_KM = 10.0  # a climb this long shows its length in whole km ("21km"): the decimal is noise
DIST_W = 6.0  # an inline "↓10.6" distance to the next row, at the end of the main line
LABEL_W = 8.0  # room kept for a short label ("Cat HC", "1240 m") next to a climb's own emoji
GUTTER_MIN_SPAN_M = 300.0  # a strip's profile spans at least this much elevation, so rolling ground stays flat
HEAD_H = 3.2  # a heading line above a row: a busy stop's town, a climb's name
FRAME_PAD = 0.8  # under a town's row, so its frame's bottom line clears the emojis
OVERRUN = 6.0  # mm the route's last row (the finish) may run a strip past its length, rather than start one alone

PAGE_MM = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
}  # width, height, portrait; another size gets A4's
PAGE_MARGIN = 8.0  # the template's @page margin

PDF_TIMEOUT_S = 120  # a few seconds is usual; a browser stuck on a dialog would otherwise never return

# Watermark: the name on every strip; name, version and home on the reference sheet
TOOL = "gpx2roadbook"
HOME = "github.com/bnogaro/gpx2roadbook"


def _wrap(emojis: list[Glyph], widths: list[float], max_emojis: int) -> tuple[list[list[Glyph]], bool, float]:
    """Pour emojis (in priority order) into lines of the given widths, mm. Returns (lines, truncated, first width).

    Only the last line keeps room for a "+"; the ones before it overflow onto the next instead. A line may stay
    empty when not even one emoji fits there, which lets a crowded climb row send its stop down a line.
    """
    lines: list[list[Glyph]] = []
    first_w = 0.0
    i = 0
    for n, avail in enumerate(widths):
        last = n == len(widths) - 1
        line: list[Glyph] = []
        used = 0.0
        while i < len(emojis) and i < max_emojis:
            # one COUNT_W per superscript, whatever its length: charging per character would widen 10+ POI tokens
            w = EMOJI_W + (COUNT_W if emojis[i].sup else 0)
            more_w = MORE_W if last and i + 1 < len(emojis) else 0
            if used + w + more_w > avail:
                break
            line.append(emojis[i])
            used += w
            i += 1
        lines.append(line)
        if n == 0:
            first_w = used
        if i >= min(len(emojis), max_emojis):
            break
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    truncated = i < len(emojis)
    return lines, truncated, first_w + (MORE_W if truncated and len(lines) == 1 else 0)


def _first_only(emojis: list[Glyph], avail: float) -> tuple[list[Glyph], bool, float]:
    """For when _wrap keeps nothing: the first emoji alone, shedding its count, then the "+", until it fits.

    The count goes first: that more kinds are there matters more than how many of the first. If even the bare
    emoji does not fit, it is shown anyway, a hair over its room rather than gone.
    """
    bare = Glyph(emojis[0].emoji, dim=emojis[0].dim)
    if len(emojis) > 1 and avail >= EMOJI_W + MORE_W:
        return [bare], True, EMOJI_W + MORE_W
    return [bare], False, EMOJI_W


def poi_category(p: Poi) -> str:
    """The category of a POI inside a stop; filter_pois() only keeps classified ones."""
    if p.category is None:
        msg = f"POI {p.name!r} at km {p.km:.1f} has no category"
        raise ValueError(msg)
    return p.category


@dataclass(frozen=True)
class RowLayout:
    """What every row of one render shares, worked out once by render_html() from cfg["render"]."""

    avail: float  # mm left for emojis on a row's main line
    max_emojis: int
    sep: str  # between the figures of a climb's stats line
    range_m: float  # a stop stretching at least this far shows where it ends
    emoji_lines: int = 1  # lines a row's emojis may fill; more than 1 wraps a crowded stop below its main line
    wrap_avail: float = 0.0  # mm for emojis on those extra lines, which carry no ↓ distance
    grades: tuple[tuple[float, str], ...] = ()  # a climb's colour by its average grade: see svg.grade_colour()


@dataclass(frozen=True)
class Head:
    """A heading line above a row: a busy stop's town ("town"), or a climb's name ("col"). See _add_heads()."""

    cls: str  # its CSS class: "town" | "col"
    text: str


@dataclass(frozen=True)
class Row:
    """An item as a strip shows it in a row, or a ribbon in a token: its texts, emojis and size."""

    kind: str
    km: str
    label: str
    emojis: list[Glyph]  # on the main line: the row's own emoji first, if it has one, then its stop's
    emoji_lines: list[list[Glyph]]  # a crowded stop's emojis carried on below the main line
    more: bool  # some emojis are still left out: a "+" after the last
    w: float  # mm along a ribbon
    sub: str = ""  # a climb's length and grade, under the main line
    sub_short: str = ""  # a ribbon's: a climb's length and grade, or where a long stop ends
    km_end: str = ""  # a long stop's last km, at the foot of the km column
    gain: str = ""  # a climb's ↗ metres, at the end of its sub line
    dist: str = ""  # "↓4.3" to the next row, at the end of the main line
    leg_short: str = ""  # the same at the foot of a ribbon's token
    grade: str = ""  # a climb or summit row's colour, by the climb's average grade
    heads: tuple[Head, ...] = ()  # lines above the row: see _add_heads()
    town: bool = False  # framed with its town's name

    @property
    def h(self) -> float:
        """Its height down a strip, mm: its lines, and the room under a town's frame."""
        # the last km takes the km column of the row's last emoji line; with none, a short line of its own
        end_h = SUB_H if self.km_end and not self.emoji_lines else 0
        h = MAIN_H + EMO_H * len(self.emoji_lines) + (SUB_H if self.sub else 0) + end_h
        return h + (FRAME_PAD if self.town else 0) + HEAD_H * len(self.heads)


def _row(it: Item, book: Roadbook, layout: RowLayout) -> Row:
    avail, max_emojis, sep = layout.avail, layout.max_emojis, layout.sep
    kind = KINDS[it.kind]
    label = kind.label(it, book)
    if kind.emoji:
        # the row's own emoji leads; a stop snapped onto a climb's foot or summit follows in the space left.
        # The row is too narrow for both a stop and a full label, so with a stop on board the category
        # shrinks to a superscript on ⛰️, and the summit's elevation only shows if room is left.
        sup = kind.sup(it)
        room = avail - EMOJI_W - COUNT_W * len(sup)
        # at least one stop emoji, even when max_emojis is 1 or the room is too tight for its count and a "+":
        # a snapped stop must not vanish behind its climb
        stop_emojis, extra, more, stop_w = _emoji_lines(it.emojis, room, max(1, max_emojis - 1), layout)
        if it.emojis and not stop_emojis and not extra:
            stop_emojis, more, stop_w = _first_only(it.emojis, room)
        emojis = [Glyph(kind.emoji, sup), *stop_emojis]
        emoji_w = EMOJI_W + stop_w
        if kind.label_if_room and stop_emojis and stop_w + LABEL_W > room:  # alone, the label clips at worst
            label = ""
    else:
        emojis, extra, more, emoji_w = _emoji_lines(it.emojis, avail, max_emojis, layout)
    sub = sub_short = km_end = gain = ""
    if kind.stats:
        c = climb_of(it)
        length = f"{c.length_km:.0f}" if c.length_km >= WHOLE_KM else f"{c.length_km:.1f}"
        sub = f"{length}km{sep}{c.avg_grade:.1f}%"
        # the climb's gain is what a rider plans by: never cut, while length and grade may be, should the line be short
        gain = f"↗︎{c.gain_m:.0f}"  # the arrow as text (VS15): narrower than its emoji
        sub_short = f"{length}km {c.avg_grade:.0f}%"
    elif it.stop and _spans(it.stop, layout.range_m):
        # the row sits at the stop's first POI; a long stop says where its last one is, so nothing hides past it: on a
        # strip down the km column, under the first; a ribbon's token has no column for it
        km_end = f"{it.stop.km_end:.1f}"
        sub_short = f"→{km_end}"
    # the distance to the next row: at the end of the main line on a strip, at the foot of a ribbon's token
    leg = f"{it.dist_to_next:.1f}" if it.dist_to_next is not None else ""
    return Row(
        kind=it.kind,
        km=f"{it.km:.1f}",
        label=label,
        emojis=emojis,
        emoji_lines=extra,
        more=more,
        w=max(11.0, emoji_w + 2 * PAD + 0.6, 15.0 if sub_short else 0.0),
        sub=sub,
        sub_short=sub_short,
        km_end=km_end,
        gain=gain,
        dist=f"↓{leg}" if leg else "",
        leg_short=leg,
        grade=grade_colour(it.climb.avg_grade, layout.grades) if it.climb and layout.grades else "",
    )


def _emoji_lines(
    emojis: list[Glyph], avail: float, max_emojis: int, layout: RowLayout
) -> tuple[list[Glyph], list[list[Glyph]], bool, float]:
    """A row's emojis: those on its main line, the lines wrapped below it, whether some are still left out, and the
    main line's width."""
    widths = [avail] + [layout.wrap_avail] * (layout.emoji_lines - 1)
    lines, truncated, first_w = _wrap(emojis, widths, max_emojis)
    return lines[0], lines[1:], truncated, first_w


def _spans(stop: Stop, range_m: float) -> bool:
    return (stop.km_end - stop.km) * 1000 >= range_m


def _add_heads(rows: list[Row], book: Roadbook) -> list[Row]:
    """The rows with their heading lines, each HEAD_H mm tall: a named stop's town, then a named climb's name.

    A town is not named again on the next row in the same town: its stops make one (towns.group), but two too far
    apart to join may still follow each other, and naming it once is enough on a narrow strip. A climb's name goes
    above its foot's row, the one that always shows (its summit only gets a row when a stop is there), so it
    announces the climb before it starts and sits right above the length and grade it goes with. A climb row whose
    foot is in a named town gets both lines: where you are, then what comes.
    """
    out: list[Row] = []
    last = None  # the last town shown
    for it, row in zip(book.items, rows, strict=True):
        heads: list[Head] = []
        town = it.stop.town if it.stop else None
        if town and town != last:
            heads.append(Head("town", town))
        framed = bool(heads)  # a named town gets a frame round its name and row
        last = town or last
        if it.kind == "climb" and it.climb and it.climb.name:
            heads.append(Head("col", it.climb.name))
        out.append(replace(row, heads=tuple(heads), town=framed))
    return out


def _paginate[T](rows: list[T], capacity: float, size: Callable[[T], float]) -> list[list[T]]:
    """Fill strips with rows up to `capacity` mm; only the very last row may overrun a strip, by up to OVERRUN."""
    pages: list[list[T]] = [[]]
    used = 0.0
    for i, r in enumerate(rows):
        room = capacity + (OVERRUN if i == len(rows) - 1 else 0)
        if pages[-1] and used + size(r) > room:
            pages.append([])
            used = 0.0
        pages[-1].append(r)
        used += size(r)
    return pages


_TIME_RANGE = re.compile(r"(\d\d:\d\d)-(\d\d:\d\d)")


def pretty_hours(value: str) -> str:
    """An OSM `opening_hours` value, easier to read on paper: one rule after another, en dashes between times."""
    rules = [" ".join(r.split()) for r in value.split(";") if r.strip()]
    return " · ".join(_TIME_RANGE.sub("\\1\u2013\\2", r).replace(",", ", ") for r in rules)


_VERDICT = {"closed": "closed", "unknown": "open?"}  # "open" needs no word; "tight" says what changes


@dataclass(frozen=True)
class ShopHours:
    """How the reference sheet shows a shop's hours: a verdict word if judged, then its hours, and their CSS class."""

    verdict: str
    hours: str
    hours_class: str  # "oh" | "off" | "closed" | "write-in"


def _shop_hours(p: Poi, day: dt.date | None) -> ShopHours:
    """A shop's hours on the reference sheet: those of `day` if given and readable, else the value as OSM gives it."""
    if not p.opening_hours:
        # a blank to write them in by hand, once checked elsewhere (the shop's site, a map app) before the ride
        return ShopHours("", "", "write-in")
    v = p.verdict
    word = (f"⚠ {v.note}" if v.state == "tight" else _VERDICT.get(v.state, "")) if v else ""
    found = on_day(p.opening_hours, p.lat, p.lon, day) if day else None
    if found is None:  # no day asked, or a value that isn't OSM syntax (then shown as written, muted)
        return ShopHours(word, pretty_hours(p.opening_hours), "off" if day else "oh")
    if found.closed:  # closed all day: the verdict, if any, says it already
        return ShopHours("", found.text, "closed")
    # judged closed or tight: the day's hours are only there to say when instead, so they step back
    return ShopHours(word, found.text, "off" if word else "oh")


def shop_hours(p: Poi, day: dt.date | None) -> dict[str, str]:
    """_shop_hours() as a dict, with keys verdict, hours and hours_class: as the booklet reads it."""
    return asdict(_shop_hours(p, day))


def ride_title(book: Roadbook) -> str:
    """What the reference sheet's title says about the ride: its day, and the arrival estimate's assumptions."""
    ride = book.ride
    if ride is None or ride.date is None:
        return ""
    if ride.start is None:
        return f"opening hours on {ride.date:%a %d %b %Y}" if book.hours is not None else ""
    pace = f"{ride.speed_kmh:g} km/h"
    if ride.climb_min_per_100m:
        pace += f" + {ride.climb_min_per_100m:g} min/100 m climbed"
    margin = f"±{ride.margin_pct:g} %, ≥{ride.margin_min:g} min"
    breaks = ", ".join(f"km {km:g} ({minutes:g} min)" for km, minutes in ride.breaks)
    return f"start {ride.start:%a %d %b %Y %H:%M} at {pace} ({margin})" + (f" · breaks: {breaks}" if breaks else "")


def eta_text(w: Window, ride_day: dt.date | None) -> str:
    """ "~09:40-10:50" (en dash), with the weekday when the rider gets there on another day than the start."""

    def at(t: dt.datetime) -> str:
        return f"{t:%a %H:%M}" if t.date() != ride_day else f"{t:%H:%M}"

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
    categories: dict[str, Any],
    range_m: float,
    hours_for: list[str],
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
            emoji = categories[poi_category(p)]["emoji"]
            poi = PoiLine(p.name or p.type, round(p.offset_m))
            if book.hours is not None and p.category in hours_for:
                h = _shop_hours(p, day)
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
    cat = f"Cat {c.label} · " if c.label else ""
    stats = PoiLine(f"{cat}{c.length_km:.1f} km at {c.avg_grade:.1f} % · ↗️{c.gain_m:.0f} m", 0)
    return SheetEntry(
        c.start_km,
        f"{c.start_km:.1f} → {c.end_km:.1f}",
        [("⛰️", [stats])],
        col=c.name or "",
        grade=grade_colour(c.avg_grade, grades) if grades else "",  # a swatch before the name, as on the strip
    )


def grade_key(grades: tuple[tuple[float, str], ...]) -> list[tuple[str, str]]:
    """The reference sheet's key to the climb colours: ("under 3 %", colour), ("3 to 6 %", colour), …"""
    steps = [f"{start:g}" for start, _ in grades]
    labels = [f"under {steps[1]} %" if len(steps) > 1 else "climbs"]
    labels += [f"{a}\u2013{b} %" for a, b in itertools.pairwise(steps[1:])]
    labels += [f"{steps[-1]} % and more"] if len(steps) > 1 else []
    return list(zip(labels, [c for _, c in grades], strict=True))


def _printable_width(page: str, orientation: str) -> float:
    """The width a page leaves inside its margins, mm."""
    w, h = PAGE_MM.get(page.upper(), PAGE_MM["A4"])
    return (h if orientation == "landscape" else w) - 2 * PAGE_MARGIN


def printable_area(page: str) -> tuple[float, float]:
    """The width and height a page leaves inside its margins, upright, mm."""
    w, h = PAGE_MM.get(page.upper(), PAGE_MM["A4"])
    return w - 2 * PAGE_MARGIN, h - 2 * PAGE_MARGIN


@dataclass(frozen=True)
class ClimbCard:
    """A climb page's card: its title's parts, and its profile."""

    name: str
    stats: str  # "Cat 2 · 4.3 km at 9.6 % · max 12.0 % · ↗️408 m"
    km: str
    grade: str  # a swatch before the name, by the climb's average grade
    svg: str


def _climb_cards(
    book: Roadbook, categories: dict[str, Any], grades: tuple[tuple[float, str], ...], width: float
) -> list[ClimbCard]:
    """One card per climb page: its title, and its profile with the places and stops on the way marked."""
    return [_climb_card(page, categories, grades, width) for page in book.climb_pages]


def climb_card(
    page: ClimbPage, categories: dict[str, Any], grades: tuple[tuple[float, str], ...], width: float
) -> dict[str, Any]:
    """_climb_card() as a dict, with keys name, stats, km, grade and svg: as the booklet reads it."""
    return asdict(_climb_card(page, categories, grades, width))


def _climb_card(
    page: ClimbPage, categories: dict[str, Any], grades: tuple[tuple[float, str], ...], width: float
) -> ClimbCard:
    c, order = page.climb, list(categories)

    def emojis(s: Stop) -> str:
        kinds = sorted({poi_category(p) for p in s.pois}, key=order.index)
        return "".join(categories[k]["emoji"] for k in kinds)

    on_it = lambda km: min(max(km, c.start_km), c.end_km)  # noqa: E731  a stop at the foot or top, on its edge
    marks = [*page.places, *((on_it(s.km), emojis(s)) for s in page.stops)]
    sections = [(s.start_km, s.end_km, s.ele_start, s.ele_end) for s in page.sections]
    stats = [f"Cat {c.label}"] if c.label else []
    stats += [f"{c.length_km:.1f} km at {c.avg_grade:.1f} %", f"max {c.max_grade:.1f} %", f"↗️{c.gain_m:.0f} m"]
    return ClimbCard(
        name=c.name or f"Climb at km {c.start_km:.1f}",
        stats=" · ".join(stats),
        km=f"km {c.start_km:.1f} → {c.end_km:.1f}",
        grade=grade_colour(c.avg_grade, grades) if grades else "",
        svg=climb_svg(sections, marks, width, grades=grades),
    )


def credit(book: Roadbook) -> str:
    """What the reference sheet owes OpenStreetMap: "Opening hours, town names and climb names", or "".

    Only what it shows: a lookup that found nothing, such as climb names on a flat route, has nothing to credit.
    """
    used = [
        what
        for what, done in (
            ("opening hours", book.hours),
            ("town names", book.towns),
            ("climb names", book.climb_names),
            ("place names", book.places),
        )
        if done is not None and done.found
    ]
    if not used:
        return ""
    listed = used[0] if len(used) == 1 else f"{', '.join(used[:-1])} and {used[-1]}"
    return listed[0].upper() + listed[1:]


@dataclass(frozen=True)
class Strip:
    """A strip of rows, or a ribbon of tokens, as printed."""

    rows: list[Row]
    first: str  # its first row's km
    last: str
    over: float  # how far the last strip runs past its length to keep the finish, 0 for the others
    gutter: str = ""  # the profile down its right edge, an SVG: see _gutters()


@dataclass(frozen=True)
class Geometry:
    """The row geometry the template's CSS variables take, mm."""

    gutter: float  # the profile's width, 0 without one
    main_h: float = MAIN_H
    head_h: float = HEAD_H
    frame_pad: float = FRAME_PAD
    sub_h: float = SUB_H
    hdr_h: float = HDR_H
    km_col: float = KM_COL
    pad: float = PAD
    emo_h: float = EMO_H


def _gutters(
    strips: list[Strip], book: Roadbook, width: float, height: float, grades: tuple[tuple[float, str], ...]
) -> list[str]:
    """Each strip's profile, whose km scale follows its rows: one anchor at each row's main line."""
    p = book.profile
    if p is None:  # render_html() only asks for gutters when there is a profile
        return [""] * len(strips)
    out: list[str] = []
    first = 0  # index in book.items of the strip's first row
    for s in strips:
        items = book.items[first : first + len(s.rows)]
        anchors, y = [], 0.0
        for it, row in zip(items, s.rows, strict=True):
            anchors.append((it.km, y + HEAD_H * len(row.heads) + MAIN_H / 2))  # under its heading lines
            y += row.h
        first += len(items)
        if first < len(book.items):  # carry the line to the bottom edge, towards the next strip's first row
            anchors.append((book.items[first].km, height + s.over))
        # each strip gets its own scale: one shared with a 1500 m pass would flatten every other strip
        lo, hi = p.ele_range(anchors[0][0], anchors[-1][0])
        span = (lo, max(hi, lo + GUTTER_MIN_SPAN_M))
        out.append(gutter_svg(p, book.climbs, anchors, width, height + s.over, span, grades=grades))
    return out


def render_html(book: Roadbook, cfg: dict[str, Any]) -> str:
    r = cfg["render"]
    layout = r["layout"]
    grades = tuple((float(start), str(colour)) for start, colour in cfg["climbs"]["grade_colours"])
    width = r["width_mm"] or LAYOUT_WIDTH[layout]
    length = r["length_mm"]

    # strip: what is left of the row once the km column is taken.
    # line: tokens may grow; cap so a single stop can't eat the whole ribbon.
    avail = width - KM_COL - 3 * PAD if layout == "strip" else 40.0
    gutter = r["gutter_mm"] if layout == "strip" and book.profile is not None else 0
    avail -= gutter
    if layout == "strip":  # the ↓ distance to the next row takes the end of the main line
        avail -= DIST_W
    row_layout = RowLayout(
        avail=avail,
        max_emojis=r["max_emojis"] or 99,
        sep=" " if gutter else " · ",  # the gutter eats ~5 mm; drop the dots so stats still fit beside it
        range_m=r["stop_range_m"],
        # tokens of the line layout grow sideways instead
        emoji_lines=r["emoji_lines"] if layout == "strip" else 1,
        wrap_avail=avail + (DIST_W if layout == "strip" else 0),
        grades=grades,
    )
    rows = [_row(it, book, row_layout) for it in book.items]
    if layout == "strip":  # a token of the line layout grows sideways: no room is left over in it
        rows = _add_heads(rows, book)

    def size(row: Row) -> float:  # down a strip, along a ribbon
        return row.h if layout == "strip" else row.w

    capacity = length - HDR_H - 2 * PAD if layout == "strip" else length - 2 * PAD
    orientation = "portrait" if layout == "strip" else "landscape"
    strips = [
        Strip(p, p[0].km, p[-1].km, max(0.0, sum(size(r) for r in p) - capacity))
        for p in _paginate(rows, capacity, size)
    ]
    if gutter:
        gutters = _gutters(strips, book, gutter, capacity, grades)
        strips = [replace(s, gutter=g) for s, g in zip(strips, gutters, strict=True)]
    noun = "strip" if layout == "strip" else "ribbon"
    log.info("Layout: %d %ss of %g x %g mm", len(strips), noun, width, length)
    for n, s in enumerate(strips, 1):
        used = sum(size(r) for r in s.rows)
        log.debug("%s %d: km %s to %s, %d rows, %.1f of %.1f mm", noun, n, s.first, s.last, len(s.rows), used, capacity)

    sheet = booklet_format(cfg) is None  # else the reference sheet, the climb pages and the map go in the booklet
    details = (
        _details(book, cfg["categories"], r["stop_range_m"], cfg["hours"]["categories"], grades)
        if r["details"] and sheet
        else []
    )
    climb_cards = (
        _climb_cards(book, cfg["categories"], grades, _printable_width(r["page"], orientation)) if sheet else []
    )
    route_map = (
        map_svg(book.route_map, book, cfg["categories"], cfg["map"]["km_every"]) if book.route_map and sheet else ""
    )
    env = Environment(loader=PackageLoader("roadbook", "templates"), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("roadbook.html.j2").render(
        book=book,
        layout=layout,
        strips=strips,
        width=width,
        length=length,
        page=r["page"],
        orientation=orientation,
        details=details,
        climb_cards=climb_cards,
        route_map=route_map,
        map_orientation="landscape" if book.route_map and book.route_map.landscape else "portrait",
        ride=ride_title(book),
        credit=credit(book),
        grade_key=grade_key(grades) if book.climbs and grades else [],
        tool=TOOL,
        version=version(TOOL),
        home=HOME,
        g=Geometry(gutter),
    )


def _find_browser() -> str | None:
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        if found := shutil.which(name):
            return found
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).exists():
            return p
    return None


def html_to_pdf(html: Path, pdf: Path) -> None:
    """Print via a headless Chromium-based browser (Edge/Chrome), which renders colour emoji reliably.

    Raises RuntimeError without one, subprocess.CalledProcessError if it fails, TimeoutExpired if it hangs.
    """
    exe = _find_browser()
    if exe is None:
        msg = "No Edge/Chrome found for PDF export; open the HTML and print it instead."
        raise RuntimeError(msg)
    log.info("PDF: printing with %s", exe)
    start = time.perf_counter()
    subprocess.run(  # noqa: S603  fixed argv, no shell; exe comes from PATH or a known install path
        [
            exe,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf.resolve()}",
            html.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=PDF_TIMEOUT_S,
    )
    log.info("PDF: printed in %.1f s", time.perf_counter() - start)
