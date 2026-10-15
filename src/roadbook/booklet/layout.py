"""The booklet's pages: its blocks laid out on them, and what else they hold, in reading order.

Its pages are laid out here, not by the browser: each line is drawn its set height (imposition.Format), so what fits
on a page is known, each page can say which km it covers and start at a stop, and the pages can be printed imposed,
in the order that folds into a booklet (imposition.py). In reading order:

- the cover: the ride at a glance, its checkpoints, climbs and warnings, the keys to the emojis and grades;
- the stops, two facing pages at a time, each page with its stretch's profile, the left one of a spread with a
  mini-map of it when a stop is off the road;
- the map across the centre spread, Notes pages to fill the last sheet, and the credits on the back.

Each page is a small dataclass of its kind, which the template draws.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from importlib.metadata import version
from typing import TYPE_CHECKING, ClassVar

from roadbook.i18n import _, date_text
from roadbook.imposition import centre, page_count
from roadbook.render import HOME, TOOL, credit, grade_key, poi_category, ride_title
from roadbook.routemap import svg as map_svg
from roadbook.svg import FOOD, WATER, profile_svg
from roadbook.tiles import style as tile_style

from .content import Block, Content, Row, Town, rows_h, wrap

if TYPE_CHECKING:
    from roadbook.config import Settings
    from roadbook.imposition import Format
    from roadbook.model import Roadbook

log = logging.getLogger(__name__)

COVER_PROFILE = 2.4  # the cover's profile, this many times a page's


@dataclass
class Spread:
    """Two facing pages of stops, left and right, and the stretch of road they cover; the left one has a mini-map of
    it, if `mapped`."""

    pages: list[list[Block]]  # the last spread may have but its left page
    mapped: bool
    km: tuple[float, float] = (0.0, 0.0)


def category_name(kind: str) -> str:
    """A POI category's name, as the cover's key shows it: one of the defaults' in the road book's language, one of
    the rider's own as named."""
    names = {
        "water": _("water"),
        "toilets": _("toilets"),
        "bakery": _("bakery"),
        "cafe": _("cafe"),
        "food": _("food"),
        "fastfood": _("fastfood"),
        "grocery": _("grocery"),
        "fuel": _("fuel"),
        "vending": _("vending"),
        "icecream": _("icecream"),
        "lodging": _("lodging"),
    }
    return names.get(kind, kind)


# ------------------------------------------------------------ the pages, by kind


@dataclass(kw_only=True)
class Page:
    """A page: its number, the booklet's count of them, and whether it is a right-hand one."""

    kind: ClassVar[str]  # how the template draws it
    n: int = 0
    of: int = 0
    recto: bool = False


@dataclass(frozen=True)
class CoverList:
    """A list on the cover: its title, and its items, as many as there is room for."""

    title: str
    items: list[str]
    key: tuple[tuple[str, str], ...] = ()  # after the title: (colour, what it means), the profile's bands


@dataclass
class CoverPage(Page):
    """The ride at a glance."""

    kind: ClassVar[str] = "cover"
    title: str
    total: str  # its km, climbing and stops
    ride: str  # its day, and the arrival estimate's assumptions
    profile: str
    lists: list[CoverList]
    key: list[str]  # the emojis', on as many lines as they need
    grades: list[tuple[str, str]]  # the climbs' colours: label, colour


@dataclass
class StopsPage(Page):
    """A page of stops: its km and day for the header, its stretch's profile, its blocks."""

    kind: ClassVar[str] = "stops"
    blocks: list[Block]
    km: str
    day: str
    profile: str
    starts: float | None = None  # a spread's left page: where the spread starts, marked on the overview
    minimap: str = ""  # a spread's left page: its mini-map, if it has one


@dataclass
class MapPage(Page):
    """The overview, across the centre spread: drawn on its left page, which carries both."""

    kind: ClassVar[str] = "map"
    svg: str = ""


@dataclass
class MapRightPage(Page):
    """The overview's right half."""

    kind: ClassVar[str] = "map-right"


@dataclass
class NotesPage(Page):
    """A page ruled to write on."""

    kind: ClassVar[str] = "notes"


@dataclass
class BackPage(Page):
    """The credits."""

    kind: ClassVar[str] = "back"
    maps: str
    credit: str
    check: str  # a word on the hours, when they were looked up
    tool: str
    version: str
    home: str


# ------------------------------------------------------------ the booklet


class Booklet:
    """The booklet of a road book, in a format: its content on pages, two facing at a time, and its other pages."""

    def __init__(self, book: Roadbook, settings: Settings, f: Format) -> None:
        self.book, self.f = book, f.fitted(book.length_km, climbs=bool(book.climbs))
        self.km_every = settings.map.km_every  # a km marker on the maps every so many km; 0, to suit their scale
        self.content = Content(book, settings, self.f)
        self.mapped = book.route_map is not None  # the spreads get mini-maps, the middle the overview

    def spreads(self) -> list[Spread]:
        """The booklet's blocks on its spreads."""
        return self.paginate(self.content.blocks())

    def paginate(self, blocks: list[Block]) -> list[Spread]:
        """The blocks on pages, two facing pages at a time, in order: a heading with what follows it, a warning with
        the stop before it; what doesn't fit on a page's rest starts the next; a stop too tall for any page split
        across pages. A spread with a stop off the road gets a mini-map on its left page, which then holds less."""
        cap = self.f.blocks_h
        small = cap - self.f.minimap - self.f.gap if self.mapped else cap
        groups: list[list[Block]] = []
        for b in blocks:
            if groups and (groups[-1][-1].keep == "next" or b.keep == "prev"):
                groups[-1].append(b)
            else:
                groups.append([b])
        units: list[list[Block]] = []
        for g in groups:
            if sum(b.h for b in g) <= small:
                units.append(g)
            else:  # each on its own, the too tall ones split
                units += [[part] for b in g for part in self.split(b, small)]
        spreads: list[Spread] = []
        i = 0
        while i < len(units):
            pages, after = self._fill(units, i, (cap, cap))
            mapped = self.mapped and any(b.far for page in pages for b in page)
            if mapped:  # room for its map: some of it may go on to the next spread
                pages, after = self._fill(units, i, (small, cap))
            spreads.append(Spread(pages, mapped))
            i = after
        for n, sp in enumerate(spreads):  # from its first block to the next spread's, or on to its last stop's end
            blocks = [b for page in sp.pages for b in page]
            until = spreads[n + 1].pages[0][0].km if n + 1 < len(spreads) else self.book.length_km
            sp.km = (blocks[0].km, max([until, *(b.end for b in blocks if b.end is not None)]))
        return spreads

    @staticmethod
    def _fill(units: list[list[Block]], i: int, caps: tuple[float, ...]) -> tuple[list[list[Block]], int]:
        """Pages of `caps` mm filled with the units from the `i`th on, each unit whole; and the first unit left."""
        pages = []
        for cap in caps:
            if i == len(units):
                break
            page: list[Block] = []
            used = 0.0
            while i < len(units) and (not page or used + sum(b.h for b in units[i]) <= cap):
                page += units[i]
                used += sum(b.h for b in units[i])
                i += 1
            pages.append(page)
        return pages, i

    def split(self, b: Block, cap: float) -> list[Block]:
        """A block cut into parts that each fit a page: a stop's rows go on under its km and town, "continued"."""
        if b.h <= cap or not b.rows:
            return [b]
        fit = max(2, int((cap - self.f.head - self.f.gap) / self.f.line) + 1)  # rows a part holds: its head, and more
        head = b.rows[0]
        town = head.right.town if isinstance(head.right, Town) else ""
        parts, rows = [], b.rows
        while rows:
            take, rows = rows[:fit], rows[fit:]
            parts.append(replace(b, h=rows_h(self.f, len(take)), rows=take))
            if rows:
                rows = [Row(head.left, Town(_("{town} (continued)", town=town).strip())), *rows]
        return parts

    def pages(self) -> list[Page]:
        """Every page in reading order: the cover, the stops, the map across the centre spread, Notes, the back.

        The stops start on page 2, a left-hand page, and the map takes two: each spread of stops faces its own.
        """
        spreads = self.spreads()
        flat = [(sp, k, blocks) for sp in spreads for k, blocks in enumerate(sp.pages)]
        stops = []
        for i, (sp, k, blocks) in enumerate(flat):
            page = self.stops_page(blocks, flat[i + 1][2][0].km if i + 1 < len(flat) else None)
            if k == 0:
                page.starts = sp.km[0]  # marked on the overview
                page.minimap = self.minimap_svg(sp.km) if sp.mapped else ""
            stops.append(page)
        n = page_count(1 + len(stops) + 2 * self.mapped + 1, self.f)
        out: list[Page] = [self.cover()]
        mid = centre(n)
        overview = MapPage()
        while len(out) < n - 1:
            if self.mapped and len(out) == mid:
                out += [overview, MapRightPage()]
            elif stops:
                out.append(stops.pop(0))
            else:
                out.append(NotesPage())
        out.append(self.back())
        for i, p in enumerate(out):
            p.n, p.of, p.recto = i + 1, n, i % 2 == 0
        if self.mapped:
            overview.svg = self.map_svg(
                [(p.starts, f"p. {p.n}") for p in out if isinstance(p, StopsPage) and p.starts is not None]
            )
        log.info(
            "Booklet: %s, %d pages: %d of stops, %d mini-maps%s",
            self.f.name,
            n,
            len(flat),
            sum(sp.mapped for sp in spreads),
            ", the map in the middle" * self.mapped,
        )
        return out

    def stops_page(self, blocks: list[Block], until: float | None) -> StopsPage:
        """A page of stops: its km and day for the header, its stretch's profile, its blocks."""
        a = blocks[0].km
        ends = [x.end for x in blocks if x.end is not None]  # the next page's start, or further: its last stop's end
        b = max(until if until is not None else self.book.length_km, *ends, a + 0.1)
        t = self.content.clock.at(a)
        profile = (
            profile_svg(
                self.book.profile,
                self.book.climbs,
                (a, b),
                (self.f.body[0], self.f.profile),
                marks=[x.km for x in blocks if x.kind == "stop"],
                grades=self.content.grades,
            )
            if self.book.profile
            else ""
        )
        return StopsPage(
            blocks=blocks, km=f"km {a:.1f} → {b:.1f}", day=date_text(t.date(), year=False) if t else "", profile=profile
        )

    def cover(self) -> CoverPage:
        """The ride at a glance: its totals and timing, its profile, its checkpoints, climbs and warnings, as many as
        the page holds, and the keys."""
        book, f, content = self.book, self.f, self.content
        checkpoints = [
            f"km {it.km:.1f} · {it.label}{content.at_text(it.km)}" for it in book.items if it.kind == "checkpoint"
        ]
        climbs = [
            f"km {c.start_km:.1f} · {c.name or _('Climb')}{f' · Cat {c.label}' if c.label else ''} · "
            + _(
                "{length} km at {grade} % · ↗{gain} m",
                length=f"{c.length_km:.1f}",
                grade=f"{c.avg_grade:.1f}",
                gain=f"{c.gain_m:.0f}",
            )
            for c in book.climbs
            if c.name or c.label
        ]
        warnings = [content.stretch_line(g, f.body[0], f.font) for g in content.stretches]
        present = {poi_category(p) for s in book.stops for p in s.pois}
        emojis = [f"{v.emoji} {category_name(k)}" for k, v in content.categories.items() if k in present]
        key = wrap(emojis, " · ", f.body[0], f.font - 1) if emojis else []
        grades = grade_key(content.grades) if book.climbs and content.grades else []
        # the lines left under the title, totals, timing and profile, and over the keys: the lists share them
        fixed = 3 * f.line + 8 + COVER_PROFILE * f.profile + 3 + len(key) * f.line + (f.line if grades else 0)
        room = int((f.body[1] - fixed) / f.line)
        lists = []
        # a band over the profile for each thing some stretch is short of, water above food
        bands = [
            (colour, [(g.start, g.end) for g in content.stretches if lack in g.lacks], words)
            for lack, colour, words in (("water", WATER, _("no water")), ("food", FOOD, _("no food")))
            if any(lack in g.lacks for g in content.stretches)
        ]
        warned, banded = _("Warnings"), ()
        if book.profile and bands:
            warned, banded = _("Warnings · on the profile:"), tuple((colour, words) for colour, _s, words in bands)
        for title, items, swatches in (
            (_("Checkpoints"), checkpoints, ()),
            (warned, warnings, banded),
            (_("Climbs"), climbs, ()),
        ):
            if not items or room < 2:  # noqa: PLR2004  a title and one line at least
                continue
            fits = (
                items
                if len(items) <= room - 1
                else [*items[: room - 2], _("… and {n} more inside", n=len(items) - room + 2)]
            )
            lists.append(CoverList(title, fits, swatches))
            room -= len(fits) + 1
        stops = _("{n} stops", n=len(book.stops))
        total = f"{book.length_km:.0f} km · ↗{book.gain_m:.0f} m ↘{book.loss_m:.0f} m · {stops}"
        profile = (
            profile_svg(
                book.profile,
                book.climbs,
                (0.0, book.length_km),
                (f.body[0], COVER_PROFILE * f.profile),
                marks=[it.km for it in book.items if it.kind == "checkpoint"],
                bands=[(colour, spans) for colour, spans, _words in bands],
                grades=content.grades,
            )
            if book.profile
            else ""
        )
        return CoverPage(
            title=book.title,
            total=total,
            ride=ride_title(book),
            profile=profile,
            lists=lists,
            key=key,
            grades=grades,
        )

    def back(self) -> BackPage:
        owed = credit(self.book)
        found_hours = self.book.hours is not None and self.book.hours.found
        m = self.book.route_map  # its mini-maps carry a short credit: the full one is here, and on the overview
        return BackPage(
            maps=_("Maps: {credit}.", credit=tile_style(m.style)[1].credit) if m else "",
            credit=_("{owed} © OpenStreetMap contributors (ODbL).", owed=owed) if owed else "",
            check=_("Hours change: check the ones you rely on.") if found_hours else "",
            tool=TOOL,
            version=version(TOOL),
            home=HOME,
        )

    def map_svg(self, pages: list[tuple[float, str]]) -> str:
        """The overview across the centre spread: the route, its towns and climbs, no POIs, but where each spread
        starts, "p. 4", the way to the page with the stops there."""
        m = self.book.route_map
        every = self.km_every
        return map_svg(m, self.book, self.content.categories, every, with_pois=False, pages=pages) if m else ""

    def minimap_svg(self, km: tuple[float, float]) -> str:
        """A spread's mini-map: its stretch of road, with its stops' POIs; "" when the book has none made for it."""
        m = self.book.minimaps.get(km)
        return map_svg(m, self.book, self.content.categories, self.km_every, span=km) if m else ""
