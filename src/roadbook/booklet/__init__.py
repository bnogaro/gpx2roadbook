"""The booklet: the ride's stops, climbs and map as small pages to print, fold and take along, in a file of its own.

In reading order:

- the cover: the ride at a glance, its checkpoints, climbs and warnings, the keys to the emojis and grades;
- the stops, a block each: its km large on the left, with the distance from the last stop; then what it has, water
  and toilets counted with the nearest named, each shop with its side of the road, its distance off it and its
  hours, those closed when the rider passes last. Between them, the climbs where they come, with their profile when
  they have a climb page; a warning before each long stretch with nowhere to drink or eat; the checkpoints; and on a
  timed ride, each new day, sunset and sunrise;
- the map across the centre spread, Notes pages to fill the last sheet, and the credits on the back.

What it says, block by block, is in content.py; how the blocks go on pages, and what else the pages hold, in
layout.py; here, the pages imposed on the sides to print, as HTML, and the spreads' mini-maps, which the build makes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

from roadbook.imposition import chosen, sides
from roadbook.osm import Report
from roadbook.routemap import section

from .content import Block, Clock, Content, duration, svg_height, text_mm, where, wrap
from .layout import Booklet, MapRightPage, Page, Spread

if TYPE_CHECKING:
    from roadbook.model import Roadbook
    from roadbook.routemap import RouteMap

__all__ = [
    "Block",
    "Booklet",
    "Clock",
    "Content",
    "Spread",
    "duration",
    "minimaps",
    "render_booklet",
    "svg_height",
    "text_mm",
    "where",
    "wrap",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Placed:
    """A page where it is printed on a side: its top left corner, mm."""

    x: float
    y: float
    page: Page


def render_booklet(book: Roadbook, cfg: dict[str, Any]) -> str | None:
    """The booklet's HTML: its pages imposed on A4 sides to print, in reading order on screen; None without one."""
    f = chosen(cfg)
    if f is None:
        return None
    pages = Booklet(book, cfg, f).pages()
    printed = [
        [
            Placed(slot.x, slot.y, pages[slot.page])
            for slot in side
            if not isinstance(pages[slot.page], MapRightPage)  # the map's left half carries both
        ]
        for side in sides(len(pages), f)
    ]
    env = Environment(loader=PackageLoader("roadbook", "templates"), autoescape=select_autoescape(["html", "j2"]))
    notes_lines = int((f.body[1] - f.header) / (2 * f.line))
    return env.get_template("booklet.html.j2").render(
        book=book, f=f, sides=printed, pages=len(pages), notes_lines=notes_lines
    )


def minimaps(
    book: Roadbook, cfg: dict[str, Any], *, offline: bool = False
) -> tuple[dict[tuple[float, float], RouteMap], Report | None]:
    """The mini-maps of the booklet's spreads that get one, by their stretch, with their tiles, and how those were
    had; none without a booklet or a map."""
    f = chosen(cfg)
    if f is None or book.route_map is None or book.track is None:
        return {}, None
    made: dict[tuple[float, float], RouteMap] = {}
    report = Report()
    for sp in Booklet(book, cfg, f).spreads():
        if sp.mapped:
            made[sp.km], got = section(book.track, cfg["map"], (f.body[0], f.minimap), sp.km, offline=offline)
            report.add(got)
    log.info("Booklet: %d mini-maps, %d tiles", len(made), report.asked)
    return made, report
