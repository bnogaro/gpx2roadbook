"""The booklet: the ride's stops, climbs and map as small pages to print, fold and take along, in a file of its own.

Its pages are laid out here, not by the browser: each line is drawn its set height (imposition.Format), so what fits
on a page is known, each page can say which km it covers and start at a stop, and the pages can be printed imposed,
in the order that folds into a booklet (imposition.py). In reading order:

- the cover: the ride at a glance, its checkpoints, climbs and warnings, the keys to the emojis and grades;
- the stops, a block each: its km large on the left, with the distance from the last stop; then what it has, water
  and toilets counted with the nearest named, each shop with its side of the road, its distance off it and its
  hours, those closed when the rider passes last. Between them, the climbs where they come, with their profile when
  they have a climb page; a warning before each long stretch with nowhere to drink or eat; the checkpoints; and on a
  timed ride, each new day, sunset and sunrise;
- the map across the centre spread, Notes pages to fill the last sheet, and the credits on the back.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import numpy as np
from jinja2 import Environment, PackageLoader, select_autoescape

from .imposition import centre, chosen, page_count, sides
from .opening import Ride
from .render import HOME, TOOL, climb_card, credit, eta_text, grade_key, poi_category, ride_title, shop_hours
from .routemap import svg as map_svg
from .sun import local, sun_times
from .svg import grade_colour, profile_svg

if TYPE_CHECKING:
    import datetime as dt

    from .imposition import Format
    from .model import Climb, Poi, Roadbook, Stop

log = logging.getLogger(__name__)

PT = 25.4 / 72  # mm a point
OFF_ROUTE_M = 30  # a place further than this from the route says how far
SIDE = {-1: "←", 1: "→"}  # the side of the road, as the rider goes
SUN_STEP_KM = 1.0  # sunset and sunrise are placed to the km
HOURS_SHARE = 0.55  # hours wider than this share of a line go on the lines below the shop's
WIDE_HOURS_INDENT = 4.0  # mm, those lines' indent
COVER_PROFILE = 2.4  # the cover's profile, this many times a page's
RANK = {"start": 0, "day": 1, "sun": 2, "checkpoint": 3, "stop": 4, "gap": 5, "climb": 6, "finish": 9}  # at one km

# about how wide a character is, in ems: on the wide side, so what is cut to fit does fit
NARROW = set(" .,:;'|!iljft()[]/·")
WIDE = set("mwMW%@")
UNSEEN = {0x200D, *range(0xFE00, 0xFE10)}  # joiners and variation selectors


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


def where(p: Poi) -> str:
    """Which side of the road a place is on, and how far off it, once that is more than a few steps: "← 120 m"."""
    far = f"{round(p.offset_m)} m" if p.offset_m > OFF_ROUTE_M else ""
    return " ".join(s for s in (SIDE.get(p.side, ""), far) if s)


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


@dataclass
class Block:
    """What goes on a page as one, `h` mm high: a stop, a climb, a warning or a heading, drawn by its kind."""

    kind: str
    km: float
    h: float
    rows: list[dict[str, Any]] = field(default_factory=list)  # a stop's or a climb's lines: what is left, what right
    data: dict[str, Any] = field(default_factory=dict)
    keep: str = ""  # "next": on the page of the block after it; "prev": on the page of the one before
    end: float | None = None  # where a stop or a climb ends, when it spans some road


class Booklet:
    """The booklet of a road book, in a format: its blocks, its pages, its sides to print."""

    def __init__(self, book: Roadbook, cfg: dict[str, Any], f: Format) -> None:
        self.book, self.cfg, self.f = book, cfg, f
        self.b = cfg["booklet"]
        self.categories: dict[str, Any] = cfg["categories"]
        self.grades = tuple((float(s), str(c)) for s, c in cfg["climbs"]["grade_colours"])
        self.ride = book.ride or Ride()
        self.clock = Clock(book)
        self.width = f.body[0] - f.km_col  # the lines right of the km column
        self.hours_for = set(cfg["hours"]["categories"]) if book.hours is not None else set()
        self.warnings = self.gaps()  # on their pages, and on the cover

    # ------------------------------------------------------------ the blocks

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

    def band(self, kind: str, km: float, text: str, *, keep: str = "next") -> Block:
        """A heading across the page: the start, a checkpoint, a new day, sunset, sunrise, the finish."""
        return Block(kind, km, self.f.line + 1.2 + self.f.gap, data={"text": text}, keep=keep)

    def at_text(self, km: float) -> str:
        t = self.clock.at(km)
        return f" · ~{t:%H:%M}" if t else ""

    def start_text(self) -> str:
        t = self.clock.at(0.0)
        return "🟢 Start" + (f" · {t:%a %d %b %H:%M}" if t else "")

    def finish_text(self) -> str:
        return f"🏁 Finish · km {self.book.length_km:.1f}{self.at_text(self.book.length_km)}"

    def stop(self, s: Stop, after: float | None) -> Block:
        """A stop: its km, the distance from the stop before and where it ends on the left; its town, arrival time
        and places on the right."""
        eta = eta_text(s.window, self.ride.date) if s.window else ""
        head = (
            {"kind": "town", "town": s.town or "", "eta": eta, "dark": self.clock.dark(s.km)} if s.town or eta else None
        )
        spans = s.km_end - s.km >= self.cfg["render"]["stop_range_m"] / 1000
        left = [
            f"{s.km:.1f}",
            f"+{s.km - after:.1f}" if after is not None else "",
            f"→ {s.km_end:.1f}" if spans else "",
        ]
        while left[-1] == "":
            left.pop()
        right = ([head] if head else []) + self.lines(s)
        rows = [
            {"left": left[i] if i < len(left) else "", "right": right[i] if i < len(right) else None}
            for i in range(max(len(left), len(right)))
        ]
        return Block("stop", s.km, self.rows_h(len(rows)), rows, end=s.km_end)

    def rows_h(self, n: int) -> float:
        return self.f.head + (n - 1) * self.f.line + self.f.gap

    def lines(self, s: Stop) -> list[dict[str, Any]]:
        """A stop's places, kind by kind in the categories' order: water and toilets counted, the nearest named;
        each shop on its line, the nearest first, at most `max_per_kind` of a kind; those closed when the rider
        passes after all the others."""
        counted, most = set(self.b["counted"]), int(self.b["max_per_kind"])
        day = s.window.early.date() if s.window else self.ride.date
        by_kind: dict[str, list[Poi]] = {}
        for p in s.pois:
            by_kind.setdefault(poi_category(p), []).append(p)
        shown: list[dict[str, Any]] = []
        closed: list[dict[str, Any]] = []
        for kind in self.categories:
            pois = sorted(by_kind.get(kind, []), key=lambda p: p.offset_m)
            if not pois:
                continue
            emoji = self.categories[kind]["emoji"]
            if kind in counted:
                near = pois[0]
                shown.append(
                    {
                        "kind": "count",
                        "emoji": emoji,
                        "n": len(pois),
                        "name": near.name or near.type,
                        "where": where(near),
                    }
                )
                continue
            shops = sorted((self.shop(p, emoji, day) for p in pois), key=lambda line: line["closed"])  # stable
            kept = shops[:most] if most else shops
            for line in kept:
                (closed if line["closed"] else shown).extend([line, *line.pop("under")])
            if len(shops) > len(kept):
                shown.append({"kind": "more", "emoji": emoji, "n": len(shops) - len(kept)})
        return shown + closed

    def shop(self, p: Poi, emoji: str, day: dt.date | None) -> dict[str, Any]:
        """A shop's line, and the lines under it for hours too long to share it."""
        line: dict[str, Any] = {
            "kind": "shop",
            "emoji": emoji,
            "name": p.name or p.type,
            "where": where(p),
            "verdict": "",
            "hours": "",
            "cls": "",
            "closed": False,
            "under": [],
        }
        if p.category not in self.hours_for:
            return line
        h = shop_hours(p, day)
        line |= {"verdict": h["verdict"], "hours": h["hours"], "cls": h["hours_class"]}
        line["closed"] = h["hours_class"] == "closed" or (p.verdict is not None and p.verdict.state == "closed")
        if text_mm(h["hours"], self.f.font) > HOURS_SHARE * self.width:
            parts = h["hours"].split(" · ")
            line["under"] = [
                {"kind": "hours", "text": t, "cls": h["hours_class"]}
                for t in wrap(parts, " · ", self.width - WIDE_HOURS_INDENT, self.f.font)
            ]
            line["hours"] = ""
        return line

    def climb(self, c: Climb, card: dict[str, Any] | None) -> Block:
        """A climb where it starts: its name and category, where it ends, its figures, and its profile if it has a
        climb page."""
        stats = f"{c.length_km:.1f} km at {c.avg_grade:.1f} % · max {c.max_grade:.0f} % · ↗{c.gain_m:.0f} m"
        rows = [
            {"left": f"{c.start_km:.1f}", "right": {"kind": "col", "name": c.name or "Climb", "cat": c.label}},
            {"left": f"→ {c.end_km:.1f}", "right": {"kind": "text", "text": stats}},
        ]
        svg = card["svg"] if card else ""
        grade = grade_colour(c.avg_grade, self.grades) if self.grades else ""
        h = self.rows_h(len(rows)) + (svg_height(svg) + 1 if svg else 0)
        return Block("climb", c.start_km, h, rows, {"grade": grade, "svg": svg}, end=c.end_km)

    def supplies(self, s: Stop, kinds: set[str]) -> bool:
        """Whether a stop has a place of `kinds` the rider may use: not one known to be closed when passing, nor at
        night a shop not known to be open (water and toilets, the counted kinds, are there at night too)."""
        dark = self.clock.dark(s.km)
        counted = set(self.b["counted"])
        for p in s.pois:
            state = p.verdict.state if p.verdict else None
            if p.category not in kinds or state == "closed":
                continue
            if dark and p.category not in counted and state != "open":
                continue
            return True
        return False

    def gaps(self) -> list[Block]:
        """A warning before each long stretch with nowhere to drink, or nothing to eat: after the last stop with some,
        at the start if there is none before. Long as a rider drinks and eats: with a speed, in hours of riding, its
        climbing included (`water_gap_h`, `food_gap_h`); without one, in km (`water_gap_km`, `food_gap_km`)."""
        paced = self.clock.riding(0.0, 0.0) is not None

        def long(a: float, b: float) -> float:
            """How long a stretch is: hours of riding with a speed, else km."""
            hours = self.clock.riding(a, b)
            return b - a if hours is None else hours

        found: dict[tuple[float, float], list[str]] = {}
        for kind in ("water", "food"):
            limit = float(self.b[f"{kind}_gap_{'h' if paced else 'km'}"])
            if limit <= 0:
                continue
            kinds = set(self.b[kind])
            at = [0.0, *(s.km for s in self.book.stops if self.supplies(s, kinds)), self.book.length_km]
            for a, b in itertools.pairwise(at):
                if long(a, b) >= limit:
                    found.setdefault((a, b), []).append(kind)
        by_km = {s.km: s for s in self.book.stops}
        out = []
        for (a, b), kinds in sorted(found.items()):
            what = " or ".join(kinds)
            if b >= self.book.length_km:
                then = "to the finish"
            else:
                stop = by_km[b]
                eta = f" ({eta_text(stop.window, self.ride.date)})" if stop.window else ""
                then = f"next at km {b:.1f}{eta}"
            span = f"{duration(long(a, b))} ({b - a:.0f} km)" if paced else f"{b - a:.0f} km"
            out.append(self.band("gap", a, f"⚠ {span} without {what}: {then}", keep="prev"))
        return out

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
                out.append(self.band("day", km, f"{t:%A %d %B}"))
            day = t.date()
            sun = self.clock.sun(km, day)
            now_dark = sun is not None and not sun[0] <= t < sun[1]
            if dark is not None and sun is not None and now_dark != dark:
                out.append(
                    self.band("sun", km, f"🌇 Sunset ~{sun[1]:%H:%M}" if now_dark else f"🌅 Sunrise ~{sun[0]:%H:%M}")
                )
            dark = now_dark
        return out

    # ------------------------------------------------------------ the pages

    def paginate(self, blocks: list[Block]) -> list[list[Block]]:
        """The blocks on pages, in order: a heading with what follows it, a warning with the stop before it; what
        doesn't fit on a page's rest starts the next; a stop too tall for any page split across pages."""
        cap = self.f.blocks_h
        groups: list[list[Block]] = []
        for b in blocks:
            if groups and (groups[-1][-1].keep == "next" or b.keep == "prev"):
                groups[-1].append(b)
            else:
                groups.append([b])
        units: list[list[Block]] = []
        for g in groups:
            if sum(b.h for b in g) <= cap:
                units.append(g)
            else:  # each on its own, the too tall ones split
                units += [[part] for b in g for part in self.split(b, cap)]
        pages: list[list[Block]] = [[]]
        used = 0.0
        for u in units:
            h = sum(b.h for b in u)
            if pages[-1] and used + h > cap:
                pages.append([])
                used = 0.0
            pages[-1] += u
            used += h
        return pages

    def split(self, b: Block, cap: float) -> list[Block]:
        """A block cut into parts that each fit a page: a stop's rows go on under its km and town, "continued"."""
        if b.h <= cap or not b.rows:
            return [b]
        fit = max(2, int((cap - self.f.head - self.f.gap) / self.f.line) + 1)  # rows a part holds: its head, and more
        head = b.rows[0]
        town = head["right"]["town"] if head["right"] and head["right"]["kind"] == "town" else ""
        parts, rows = [], b.rows
        while rows:
            take, rows = rows[:fit], rows[fit:]
            parts.append(Block(b.kind, b.km, self.rows_h(len(take)), take, b.data, end=b.end))
            if rows:
                more = {"kind": "town", "town": f"{town} (continued)".strip(), "eta": "", "dark": False}
                rows = [{"left": head["left"], "right": more}, *rows]
        return parts

    def pages(self) -> list[dict[str, Any]]:
        """Every page in reading order: the cover, the stops, the map across the centre spread, Notes, the back."""
        stops = self.paginate(self.blocks())
        content = [
            self.stops_page(blocks, stops[i + 1][0].km if i + 1 < len(stops) else None)
            for i, blocks in enumerate(stops)
        ]
        spread = self.book.route_map is not None
        n = page_count(1 + len(content) + 2 * spread + 1, self.f)
        out: list[dict[str, Any]] = [{"kind": "cover", **self.cover()}]
        mid = centre(n)
        while len(out) < n - 1:
            if spread and len(out) == mid:
                out += [{"kind": "map", "svg": self.map_svg()}, {"kind": "map-right"}]
            elif content:
                out.append(content.pop(0))
            else:
                out.append({"kind": "notes"})
        out.append({"kind": "back", **self.back()})
        for i, p in enumerate(out):
            p |= {"n": i + 1, "of": n, "recto": i % 2 == 0}
        log.info("Booklet: %s, %d pages: %d of stops%s", self.f.name, n, len(stops), ", the map in the middle" * spread)
        return out

    def stops_page(self, blocks: list[Block], until: float | None) -> dict[str, Any]:
        """A page of stops: its km and day for the header, its stretch's profile, its blocks."""
        a = blocks[0].km
        ends = [x.end for x in blocks if x.end is not None]  # the next page's start, or further: its last stop's end
        b = max(until if until is not None else self.book.length_km, *ends, a + 0.1)
        t = self.clock.at(a)
        profile = (
            profile_svg(
                self.book.profile,
                self.book.climbs,
                (a, b),
                (self.f.body[0], self.f.profile),
                marks=[x.km for x in blocks if x.kind == "stop"],
                grades=self.grades,
            )
            if self.book.profile
            else ""
        )
        return {
            "kind": "stops",
            "blocks": blocks,
            "km": f"km {a:.1f} → {b:.1f}",
            "day": f"{t:%a %d %b}" if t else "",
            "profile": profile,
        }

    def cover(self) -> dict[str, Any]:
        """The ride at a glance: its totals and timing, its profile, its checkpoints, climbs and warnings, as many as
        the page holds, and the keys."""
        book, f = self.book, self.f
        checkpoints = [
            f"km {it.km:.1f} · {it.label}{self.at_text(it.km)}" for it in book.items if it.kind == "checkpoint"
        ]
        climbs = [
            f"km {c.start_km:.1f} · {c.name or 'Climb'}{f' · Cat {c.label}' if c.label else ''} · "
            f"{c.length_km:.1f} km at {c.avg_grade:.1f} % · ↗{c.gain_m:.0f} m"
            for c in book.climbs
            if c.name or c.label
        ]
        warnings = [b.data["text"].removeprefix("⚠ ") for b in self.warnings]
        present = {poi_category(p) for s in book.stops for p in s.pois}
        emojis = [f"{v['emoji']} {k}" for k, v in self.categories.items() if k in present]
        key = wrap(emojis, " · ", f.body[0], f.font - 1) if emojis else []
        grades = grade_key(self.grades) if book.climbs and self.grades else []
        # the lines left under the title, totals, timing and profile, and over the keys: the lists share them
        fixed = 3 * f.line + 8 + COVER_PROFILE * f.profile + 3 + len(key) * f.line + (f.line if grades else 0)
        room = int((f.body[1] - fixed) / f.line)
        lists = []
        for title, items in (("Checkpoints", checkpoints), ("Warnings", warnings), ("Climbs", climbs)):
            if not items or room < 2:  # noqa: PLR2004  a title and one line at least
                continue
            fits = (
                items if len(items) <= room - 1 else [*items[: room - 2], f"… and {len(items) - room + 2} more inside"]
            )
            lists.append({"title": title, "items": fits})
            room -= len(fits) + 1
        total = f"{book.length_km:.0f} km · ↗{book.gain_m:.0f} m ↘{book.loss_m:.0f} m · {len(book.stops)} stops"
        profile = (
            profile_svg(
                book.profile,
                book.climbs,
                (0.0, book.length_km),
                (f.body[0], COVER_PROFILE * f.profile),
                marks=[it.km for it in book.items if it.kind == "checkpoint"],
                grades=self.grades,
            )
            if book.profile
            else ""
        )
        return {
            "title": book.title,
            "total": total,
            "ride": ride_title(book),
            "profile": profile,
            "lists": lists,
            "key": key,
            "grades": grades,
        }

    def back(self) -> dict[str, Any]:
        owed = credit(self.book)
        found_hours = self.book.hours is not None and self.book.hours.found
        return {
            "credit": f"{owed} © OpenStreetMap contributors (ODbL)." if owed else "",
            "check": "Hours change: check the ones you rely on." if found_hours else "",
            "tool": TOOL,
            "version": version(TOOL),
            "home": HOME,
        }

    def map_svg(self) -> str:
        m = self.book.route_map
        return map_svg(m, self.book, self.categories, self.cfg["map"]["km_every"]) if m else ""


def duration(hours: float) -> str:
    """Hours as a rider says them: "3h20"."""
    minutes = round(hours * 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def svg_height(svg: str) -> float:
    """An SVG's height, mm, as written on it."""
    found = re.search(r'height="([\d.]+)mm"', svg)
    return float(found.group(1)) if found else 0.0


def render_booklet(book: Roadbook, cfg: dict[str, Any]) -> str | None:
    """The booklet's HTML: its pages imposed on A4 sides to print, in reading order on screen; None without one."""
    f = chosen(cfg)
    if f is None:
        return None
    pages = Booklet(book, cfg, f).pages()
    printed = [
        [
            {"x": slot.x, "y": slot.y, "page": pages[slot.page]}
            for slot in side
            if pages[slot.page]["kind"] != "map-right"  # the map's left half carries both
        ]
        for side in sides(len(pages), f)
    ]
    env = Environment(loader=PackageLoader("roadbook", "templates"), autoescape=select_autoescape(["html", "j2"]))
    notes_lines = int((f.body[1] - f.header) / (2 * f.line))
    return env.get_template("booklet.html.j2").render(
        book=book, f=f, sides=printed, pages=len(pages), notes_lines=notes_lines
    )
