"""The booklet's formats, and how its pages go onto A4 sheets to print double-sided, fold and staple.

A booklet is one signature, saddle-stitched: its sheets folded together in the middle, nested, stapled through the
fold. The outermost sheet carries the first two pages and the last two; the innermost one, on one side, the two
middle pages side by side: the centre spread, where the map goes, across the fold.

- A5: each A4 sheet, landscape, is a sheet of the signature, two pages a side. Printed double-sided, flipping on
  the short edge, the stack folds as it comes out.
- A6: each A5 half of an A4 sheet, portrait, is one. Printed double-sided, flipping on the long edge, the stack is
  cut across and the bottom pile put under the top one: A4 sheet j carries the signature's sheet j on top and sheet
  j + M below, M the A4 sheets, so the two piles fall in order. Then the lot is folded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Format:
    """A booklet's page, the A4 side it is printed on, and what a page holds: all in mm, but the type, in points.

    The template draws each line its set height, so what fits on a page is known here (roadbook.booklet.layout).
    """

    name: str
    page: tuple[float, float]  # width, height
    sheet: tuple[float, float]  # the A4 side the pages are printed on: width, height
    rows: int  # signature sheets on an A4 sheet, one above the other
    flip: str  # the edge to flip on, printing double-sided
    margin: float  # at the page's top, bottom and outer edge
    inner: float  # at its inner edge, along the fold: room for the staples
    font: float  # the text's size, pt
    km_font: float  # a stop's km, pt
    line: float  # a line
    head: float  # a stop's first line, with its km
    km_col: float  # the km column, on the left of each stop
    header: float  # the running header: km, day, page
    profile: float  # the profile under it
    gap: float  # between two stops
    minimap: float  # the mini-map of a spread's stretch, on its left-hand page

    @property
    def body(self) -> tuple[float, float]:
        """The room for the text: width and height inside the margins."""
        return self.page[0] - self.margin - self.inner, self.page[1] - 2 * self.margin

    @property
    def blocks_h(self) -> float:
        """The room for the stops, under the header and the profile."""
        return self.body[1] - self.header - self.profile - self.gap

    @property
    def spread(self) -> tuple[float, float]:
        """The room inside the centre spread's margins: two pages wide, across the fold."""
        return 2 * self.page[0] - 2 * self.margin, self.page[1] - 2 * self.margin


FORMATS = {
    "a5": Format(
        "A5",
        (148.5, 210.0),
        (297.0, 210.0),
        1,
        "short",
        margin=8.0,
        inner=11.0,
        font=9.0,
        km_font=13.0,
        line=4.2,
        head=5.6,
        km_col=15.0,
        header=6.0,
        profile=11.0,
        gap=1.8,
        minimap=60.0,
    ),
    "a6": Format(
        "A6",
        (105.0, 148.5),
        (210.0, 297.0),
        2,
        "long",
        margin=6.0,
        inner=9.0,
        font=8.0,
        km_font=11.5,
        line=3.7,
        head=5.0,
        km_col=12.5,
        header=5.0,
        profile=8.0,
        gap=1.5,
        minimap=42.0,
    ),
}
OFF = "off"


def chosen(cfg: dict[str, Any]) -> Format | None:
    """The booklet's format the settings ask for, whatever their case: "A5", "a6"; None for no booklet ("off"). A name
    no format has gets A5, with a warning."""
    given = str(cfg["booklet"]["format"])
    name = given.lower()
    if name == OFF:
        return None
    if name not in FORMATS:
        known = ", ".join(f.name for f in FORMATS.values())
        log.warning("Booklet: no format %r (%s or %s); making it A5", given, known, OFF)
        name = "a5"
    return FORMATS[name]


def page_count(content: int, f: Format) -> int:
    """The pages a booklet needs for `content` pages: four to a sheet of the signature, and as many of those as fill
    the A4 sheets, so cutting A6 ones leaves no blank half to fold in."""
    per_a4 = 4 * f.rows
    return max(per_a4, -(-content // per_a4) * per_a4)


@dataclass(frozen=True)
class Slot:
    """A page's place on a printed side: its number from 0, and its top left corner, mm."""

    page: int
    x: float
    y: float


def sides(n: int, f: Format) -> list[list[Slot]]:
    """Each printed side, in printing order (a sheet's front, then its back), with the pages on it, of a booklet of
    `n` pages (a multiple of 4)."""
    signature: list[tuple[int, int]] = []  # each side of each of its sheets, outermost first: (left, right)
    for s in range(n // 4):
        signature += [(n - 1 - 2 * s, 2 * s), (2 * s + 1, n - 2 - 2 * s)]
    sheets = n // 4
    a4 = -(-sheets // f.rows)  # the A4 sheets: one signature sheet on each of their rows, the piles cut apart
    w, h = f.page
    out = []
    for j in range(a4):
        for side in (0, 1):  # front, back
            slots = []
            for row in range(f.rows):
                s = j + row * a4
                if s < sheets:
                    left, right = signature[2 * s + side]
                    slots += [Slot(left, 0.0, row * h), Slot(right, w, row * h)]
            out.append(slots)
    return out


def centre(n: int) -> int:
    """The left page of a booklet's centre spread, of `n` pages, from 0: the two side by side on one printed side."""
    return n // 2 - 1
