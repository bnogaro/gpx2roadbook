"""What the booklet says, block by block in route order, each block its height on the page.

- a stop: its km large on the left, with the distance from the last stop; then what it has, water and toilets
  counted with the nearest named, each shop with its side of the road, its distance off it and its hours, those
  closed when the rider passes last;
- a climb where it starts, with its profile when it has a climb page;
- a warning before each long stretch with nowhere to drink or eat;
- a heading: the start, the checkpoints, the finish, and on a timed ride, each new day, sunset and sunrise.

Each line right of a stop's or a climb's km is a small dataclass of its kind, which the template draws.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

import numpy as np

from roadbook.i18n import _, date_text
from roadbook.imposition import PT
from roadbook.opening import Ride
from roadbook.render import climb_card, eta_text, poi_category, shop_hours
from roadbook.sun import local, sun_times
from roadbook.svg import grade_colour

if TYPE_CHECKING:
    import datetime as dt

    from roadbook.config import Settings
    from roadbook.imposition import Format
    from roadbook.model import Climb, Poi, Roadbook, Stop
    from roadbook.render import ClimbCard

OFF_ROUTE_M = 30  # a place further than this from the route says how far
SIDE = {-1: "←", 1: "→"}  # the side of the road, as the rider goes
SUN_STEP_KM = 1.0  # sunset and sunrise are placed to the km
HOURS_SHARE = 0.55  # hours wider than this share of a line go on the lines below the shop's
WIDE_HOURS_INDENT = 4.0  # mm, those lines' indent
RANK = {"start": 0, "day": 1, "sun": 2, "checkpoint": 3, "stop": 4, "gap": 5, "climb": 6, "finish": 9}  # at one km

# about how wide a character is, in ems: on the wide side, so what is cut to fit does fit
NARROW = set(" \u00a0.,:;'|!iljft()[]/·")  # the no-break space: French's, before its units and colons
WIDE = set("mwMW%@")
UNSEEN = {0x200D, *range(0xFE00, 0xFE10)}  # joiners and variation selectors

type Kind = Literal["start", "day", "sun", "checkpoint", "stop", "gap", "climb", "finish"]
type Keep = Literal["", "next", "prev"]


def text_mm(text: str, pt: float) -> float:
    """About how wide `text` runs at `pt`, mm."""
    return sum(map(_ems, text)) * pt * PT


def _ems(c: str) -> float:
    """A character's width, in ems: an emoji or an arrow about two letters'."""
    if ord(c) in UNSEEN:
        return 0.0
    if ord(c) >= 0x2190:  # noqa: PLR2004  arrows on: emojis and their like
        return 1.2
    if c in NARROW:
        return 0.3
    if c in WIDE:
        return 0.82
    return 0.68 if c.isupper() else 0.56


def wrap(parts: list[str], sep: str, width: float, pt: float) -> list[str]:
    """The parts joined by `sep`, on as many lines `width` mm wide as they need; a part is never cut."""
    lines = [""]
    for part in parts:
        joined = f"{lines[-1]}{sep}{part}" if lines[-1] else part
        if lines[-1] and text_mm(joined, pt) > width:
            lines.append(part)
        else:
            lines[-1] = joined
    return lines


def svg_height(svg: str) -> float:
    """An SVG's height, mm, as written on it."""
    found = re.search(r'height="([\d.]+)mm"', svg)
    return float(found.group(1)) if found else 0.0


def where(p: Poi) -> str:
    """Which side of the road a place is on, and how far off it, once that is more than a few steps: "← 120 m"."""
    far = f"{round(p.offset_m)} m" if p.offset_m > OFF_ROUTE_M else ""
    return " ".join(s for s in (SIDE.get(p.side, ""), far) if s)


def duration(hours: float) -> str:
    """Hours as a rider says them: "3h20"."""
    minutes = round(hours * 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def rows_h(f: Format, n: int) -> float:
    """How high a block of `n` rows is: its head row, the others, and the gap under it, mm."""
    return f.head + (n - 1) * f.line + f.gap


# ------------------------------------------------------------ the lines right of a km


@dataclass(frozen=True)
class Town:
    """A stop's head: its town, and when the rider gets there, with a moon if it is dark then."""

    kind: ClassVar[str] = "town"
    town: str
    eta: str = ""
    dark: bool = False


@dataclass(frozen=True)
class Count:
    """Water or toilets: how many, and the nearest, named, with where it is."""

    kind: ClassVar[str] = "count"
    emoji: str
    n: int
    name: str
    where: str


@dataclass(frozen=True)
class Shop:
    """A shop: its name and where it is; its verdict and hours, when looked up, and whether it is closed then."""

    kind: ClassVar[str] = "shop"
    emoji: str
    name: str
    where: str
    verdict: str = ""
    hours: str = ""  # "" when they go on the lines under it
    cls: str = ""  # the hours' CSS class: "open", "closed", "write-in"…
    closed: bool = False


@dataclass(frozen=True)
class Hours:
    """A line of a shop's hours, under it, when they are too long to share its line."""

    kind: ClassVar[str] = "hours"
    text: str
    cls: str


@dataclass(frozen=True)
class More:
    """How many more shops of a kind there are than the stop shows."""

    kind: ClassVar[str] = "more"
    emoji: str
    n: int


@dataclass(frozen=True)
class Col:
    """A climb's head: its name and category."""

    kind: ClassVar[str] = "col"
    name: str
    cat: str


@dataclass(frozen=True)
class Text:
    """A line of plain text: a climb's figures."""

    kind: ClassVar[str] = "text"
    text: str


type Line = Town | Count | Shop | Hours | More | Col | Text


@dataclass(frozen=True)
class Row:
    """A row of a stop or a climb: its km column, and its line right of it."""

    left: str
    right: Line | None


# ------------------------------------------------------------ the blocks


@dataclass
class Clock:
    """The ride's time along the route: when the rider is at a km, and whether it is dark then; None untimed."""

    book: Roadbook

    def at(self, km: float) -> dt.datetime | None:
        ride, profile = self.book.ride, self.book.profile
        if ride is None:
            return None
        return ride.at(km, profile.gain) if profile else ride.at(km)

    def sun(self, km: float, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
        """Sunrise and sunset at `km` on `day`, in the computer's time zone, as the ride's times are."""
        if self.book.track is None:
            return None
        times = sun_times(*self.book.track.at(km), day)
        return None if times is None else (local(times[0]), local(times[1]))

    def riding(self, km_from: float, km_to: float) -> float | None:
        """Hours riding from one km to another, climbing included; None without a speed."""
        ride, profile = self.book.ride, self.book.profile
        if ride is None:
            return None
        return ride.riding_h(km_from, km_to, profile.gain) if profile else ride.riding_h(km_from, km_to)

    def dark(self, km: float) -> bool:
        t = self.at(km)
        if t is None:
            return False
        sun = self.sun(km, t.date())
        return sun is not None and not sun[0] <= t < sun[1]


@dataclass(frozen=True)
class Block:
    """What goes on a page as one, `h` mm high: a stop, a climb, a warning or a heading, drawn by its kind."""

    kind: Kind
    km: float
    h: float
    rows: list[Row] = field(default_factory=list)  # a stop's or a climb's lines: what is left, what right
    text: str = ""  # a warning's or a heading's
    far: bool = False  # a stop's: somewhere to find off the road, that a map helps with
    grade: str = ""  # a climb's colour
    svg: str = ""  # a climb's profile, when it has a climb page
    keep: Keep = ""  # "next": on the page of the block after it; "prev": on the page of the one before
    end: float | None = None  # where a stop or a climb ends, when it spans some road


@dataclass(frozen=True)
class Stretch:
    """A long stretch with nowhere to drink, or nothing to eat, from one stop (or the start) to the next with some (or
    the finish)."""

    start: float
    end: float
    lacks: tuple[str, ...]  # what it is short of: ("water",), ("food",) or ("water", "food")
    span: str  # how long: "3h25 (45 km)" with a speed, else "45 km"
    after: Stop | None = None  # the stop it starts at, None at the start
    then: Stop | None = None  # the stop it ends at, None at the finish


class Content:
    """What the booklet of a road book says, in a format: its blocks, and the warnings its cover lists too."""

    def __init__(self, book: Roadbook, settings: Settings, f: Format) -> None:
        self.book, self.f = book, f
        self.b = settings.booklet
        self.range_m = settings.render.stop_range_m  # a stop stretching at least this far shows where it ends
        self.categories = settings.categories
        self.grades = settings.climbs.grade_colours
        self.ride = book.ride or Ride()
        self.clock = Clock(book)
        self.width = f.body[0] - f.km_col  # the lines right of the km column
        self.hours_for = set(settings.hours.categories) if book.hours is not None else set()
        self.stretches = self.long_stretches()  # warned of on their pages, and on the cover
        self.warnings = self.gaps()

    def blocks(self) -> list[Block]:
        """Every block in route order: the stops, climbs, warnings and headings."""
        out = [self.band("start", 0.0, self.start_text())]
        out += [
            self.band("checkpoint", it.km, f"📍 {it.label} · km {it.km:.1f}{self.at_text(it.km)}")
            for it in self.book.items
            if it.kind == "checkpoint"
        ]
        after = None
        for s in self.book.stops:
            out.append(self.stop(s, after))
            after = s.km
        cards = {
            id(p.climb): climb_card(p, self.categories, self.grades, self.f.body[0]) for p in self.book.climb_pages
        }
        out += [self.climb(c, cards.get(id(c))) for c in self.book.climbs]
        out += self.warnings + self.days_and_nights()
        out.append(self.band("finish", self.book.length_km, self.finish_text(), keep="prev"))
        return sorted(out, key=lambda b: (b.km, RANK[b.kind]))

    def band(self, kind: Kind, km: float, text: str, *, keep: Keep = "next") -> Block:
        """A heading across the page: the start, a checkpoint, a new day, sunset, sunrise, the finish."""
        return Block(kind, km, self.f.line + 1.2 + self.f.gap, text=text, keep=keep)

    def at_text(self, km: float) -> str:
        t = self.clock.at(km)
        return f" · ~{t:%H:%M}" if t else ""

    def start_text(self) -> str:
        t = self.clock.at(0.0)
        return f"🟢 {_('Start')}" + (f" · {date_text(t.date(), year=False)} {t:%H:%M}" if t else "")

    def finish_text(self) -> str:
        return f"🏁 {_('Finish')} · km {self.book.length_km:.1f}{self.at_text(self.book.length_km)}"

    # ------------------------------------------------------------ the stops

    def stop(self, s: Stop, after: float | None) -> Block:
        """A stop: its km, the distance from the stop before and where it ends on the left; its town, arrival time
        and places on the right."""
        eta = eta_text(s.window, self.ride.date) if s.window else ""
        head = Town(s.town or "", eta, self.clock.dark(s.km)) if s.town or eta else None
        spans = s.km_end - s.km >= self.range_m / 1000
        left = [
            f"{s.km:.1f}",
            f"+{s.km - after:.1f}" if after is not None else "",
            f"→ {s.km_end:.1f}" if spans else "",
        ]
        while left[-1] == "":
            left.pop()
        right = ([head] if head else []) + self.lines(s)
        rows = [
            Row(left[i] if i < len(left) else "", right[i] if i < len(right) else None)
            for i in range(max(len(left), len(right)))
        ]
        far = any(p.offset_m > OFF_ROUTE_M for p in s.pois)  # somewhere to find off the road: a map helps
        return Block("stop", s.km, rows_h(self.f, len(rows)), rows, far=far, end=s.km_end)

    def lines(self, s: Stop) -> list[Line]:
        """A stop's places, kind by kind in the categories' order: water and toilets counted, the nearest named;
        each shop on its line, the nearest first, at most `max_per_kind` of a kind; those closed when the rider
        passes after all the others."""
        counted, most = set(self.b.counted), self.b.max_per_kind
        day = s.window.early.date() if s.window else self.ride.date
        by_kind: dict[str, list[Poi]] = {}
        for p in s.pois:
            by_kind.setdefault(poi_category(p), []).append(p)
        shown: list[Line] = []
        closed: list[Line] = []
        for kind in self.categories:
            pois = sorted(by_kind.get(kind, []), key=lambda p: p.offset_m)
            if not pois:
                continue
            emoji = self.categories[kind].emoji
            if kind in counted:
                near = pois[0]
                shown.append(Count(emoji, len(pois), near.name or near.type, where(near)))
                continue
            shops = sorted((self.shop(p, emoji, day) for p in pois), key=lambda shop: shop[0].closed)  # stable
            kept = shops[:most] if most else shops
            for line, under in kept:
                (closed if line.closed else shown).extend([line, *under])
            if len(shops) > len(kept):
                shown.append(More(emoji, len(shops) - len(kept)))
        return shown + closed

    def shop(self, p: Poi, emoji: str, day: dt.date | None) -> tuple[Shop, list[Hours]]:
        """A shop's line, and the lines under it for hours too long to share it."""
        name = p.name or p.type
        if p.category not in self.hours_for:
            return Shop(emoji, name, where(p)), []
        h = shop_hours(p, day)
        closed = h.hours_class == "closed" or (p.verdict is not None and p.verdict.state == "closed")
        hours, under = h.hours, []
        if text_mm(h.hours, self.f.font) > HOURS_SHARE * self.width:
            parts = h.hours.split(" · ")
            under = [Hours(t, h.hours_class) for t in wrap(parts, " · ", self.width - WIDE_HOURS_INDENT, self.f.font)]
            hours = ""
        line = Shop(emoji, name, where(p), verdict=h.verdict, hours=hours, cls=h.hours_class, closed=closed)
        return line, under

    # ------------------------------------------------------------ the climbs

    def climb(self, c: Climb, card: ClimbCard | None) -> Block:
        """A climb where it starts: its name and category, where it ends, its figures, and its profile if it has a
        climb page."""
        stats = _(
            "{length} km at {grade} % · max {max} % · ↗{gain} m",
            length=f"{c.length_km:.1f}",
            grade=f"{c.avg_grade:.1f}",
            max=f"{c.max_grade:.0f}",
            gain=f"{c.gain_m:.0f}",
        )
        rows = [Row(f"{c.start_km:.1f}", Col(c.name or _("Climb"), c.label)), Row(f"→ {c.end_km:.1f}", Text(stats))]
        svg = card.svg if card else ""
        grade = grade_colour(c.avg_grade, self.grades) if self.grades else ""
        h = rows_h(self.f, len(rows)) + (svg_height(svg) + 1 if svg else 0)
        return Block("climb", c.start_km, h, rows, grade=grade, svg=svg, end=c.end_km)

    # ------------------------------------------------------------ the warnings

    def supplies(self, s: Stop, kinds: set[str]) -> bool:
        """Whether a stop has a place of `kinds` the rider may use: not one known to be closed when passing, nor at
        night a shop not known to be open (water and toilets, the counted kinds, are there at night too)."""
        dark = self.clock.dark(s.km)
        counted = set(self.b.counted)
        for p in s.pois:
            state = p.verdict.state if p.verdict else None
            if p.category not in kinds or state == "closed":
                continue
            if dark and p.category not in counted and state != "open":
                continue
            return True
        return False

    def long_stretches(self) -> list[Stretch]:
        """Each long stretch with nowhere to drink, or nothing to eat: after the last stop with some, from the start
        if there is none before. Long as a rider drinks and eats: with a speed, in hours of riding, its climbing
        included (`water_gap_h`, `food_gap_h`); without one, in km (`water_gap_km`, `food_gap_km`)."""
        paced = self.clock.riding(0.0, 0.0) is not None

        def long(a: float, b: float) -> float:
            """How long a stretch is: hours of riding with a speed, else km."""
            hours = self.clock.riding(a, b)
            return b - a if hours is None else hours

        found: dict[tuple[float, float], list[str]] = {}
        warn = self.b
        for kind, limit, supplied in (
            ("water", warn.water_gap_h if paced else warn.water_gap_km, warn.water),
            ("food", warn.food_gap_h if paced else warn.food_gap_km, warn.food),
        ):
            if limit <= 0:
                continue
            kinds = set(supplied)
            at = [0.0, *(s.km for s in self.book.stops if self.supplies(s, kinds)), self.book.length_km]
            for a, b in itertools.pairwise(at):
                if long(a, b) >= limit:
                    found.setdefault((a, b), []).append(kind)
        by_km = {s.km: s for s in self.book.stops}
        return [
            Stretch(
                a,
                b,
                tuple(kinds),
                f"{duration(long(a, b))} ({b - a:.0f} km)" if paced else f"{b - a:.0f} km",
                after=by_km.get(a),
                then=by_km[b] if b < self.book.length_km else None,
            )
            for (a, b), kinds in sorted(found.items())
        ]

    def gaps(self) -> list[Block]:
        """A warning before each long stretch, where it starts: how long it is, and when the next stop with some is
        reached."""
        out = []
        for g in self.stretches:
            if g.then is None:
                then = _("to the finish")
            else:
                eta = f" ({eta_text(g.then.window, self.ride.date)})" if g.then.window else ""
                then = _("next at km {km}", km=f"{g.end:.1f}") + eta
            if g.lacks == ("water",):  # a message for each, as a language words what is missing its own way
                text = _("⚠ {span} without water: {then}", span=g.span, then=then)
            elif g.lacks == ("food",):
                text = _("⚠ {span} without food: {then}", span=g.span, then=then)
            else:
                text = _("⚠ {span} without water or food: {then}", span=g.span, then=then)
            out.append(self.band("gap", g.start, text, keep="prev"))
        return out

    def stretch_line(self, g: Stretch, width: float, pt: float) -> str:
        """A stretch on the cover, from km to km, with the towns there when they fit in `width` mm at `pt`:
        "km 112.4 Sézanne → 170.2 Montmirail · no water for 2h40 (58 km)"."""
        end = _("finish") if g.then is None else f"{g.end:.1f}"
        if g.lacks == ("water",):
            lacks = _("no water for {span}", span=g.span)
        elif g.lacks == ("food",):
            lacks = _("no food for {span}", span=g.span)
        else:
            lacks = _("no water or food for {span}", span=g.span)
        tail = f" · {lacks}"
        plain = f"km {g.start:.1f} → {end}{tail}"
        towns = (g.after.town if g.after else None, g.then.town if g.then else None)
        if not any(towns):
            return plain
        named = f"km {g.start:.1f}{f' {towns[0]}' if towns[0] else ''} → {end}{f' {towns[1]}' if towns[1] else ''}"
        return f"{named}{tail}" if text_mm(f"{named}{tail}", pt) <= width else plain

    # ------------------------------------------------------------ days and nights

    def days_and_nights(self) -> list[Block]:
        """On a timed ride, a heading for each new day, and for sunset and sunrise where the rider is then."""
        if self.clock.at(0.0) is None:
            return []
        out = []
        day, dark = None, None
        for km in [*np.arange(0.0, self.book.length_km, SUN_STEP_KM).tolist(), self.book.length_km]:
            t = self.clock.at(km)
            if t is None:  # pragma: no cover  timed at its start, so all along
                break
            if day is not None and t.date() != day:
                out.append(self.band("day", km, date_text(t.date(), year=False, long=True)))
            day = t.date()
            sun = self.clock.sun(km, day)
            now_dark = sun is not None and not sun[0] <= t < sun[1]
            if dark is not None and sun is not None and now_dark != dark:
                text = (
                    f"🌇 {_('Sunset ~{time}', time=f'{sun[1]:%H:%M}')}"
                    if now_dark
                    else f"🌅 {_('Sunrise ~{time}', time=f'{sun[0]:%H:%M}')}"
                )
                out.append(self.band("sun", km, text))
            dark = now_dark
        return out
