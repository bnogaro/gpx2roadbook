"""The road book as HTML: the strips, and without a booklet, the reference sheet, the climb pages and the map; and
its printing to PDF.

The strips are made in strips.py, the reference sheet in sheet.py, the climb pages' cards in climbs.py; here, the
page they go on, as HTML.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, select_autoescape

from roadbook.i18n import _, current
from roadbook.imposition import chosen as booklet_format
from roadbook.routemap import svg as map_svg

from .climbs import ClimbCard, _climb_cards, climb_card
from .sheet import _details, credit, eta_text, grade_key, poi_category, pretty_hours, ride_title, shop_hours
from .strips import (
    DIST_W,
    HDR_H,
    LAYOUT_WIDTH,
    PAD,
    Geometry,
    Row,
    RowLayout,
    Strip,
    _add_heads,
    _gutters,
    _paginate,
    _row,
    km_col,
)

if TYPE_CHECKING:
    from roadbook.config import Settings
    from roadbook.model import Roadbook

__all__ = [
    "HOME",
    "LAYOUT_WIDTH",
    "TOOL",
    "ClimbCard",
    "climb_card",
    "credit",
    "eta_text",
    "grade_key",
    "html_to_pdf",
    "poi_category",
    "pretty_hours",
    "printable_area",
    "render_html",
    "ride_title",
    "shop_hours",
]

log = logging.getLogger(__name__)

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


def _printable_width(page: str, orientation: str) -> float:
    """The width a page leaves inside its margins, mm."""
    w, h = PAGE_MM.get(page.upper(), PAGE_MM["A4"])
    return (h if orientation == "landscape" else w) - 2 * PAGE_MARGIN


def printable_area(page: str) -> tuple[float, float]:
    """The width and height a page leaves inside its margins, upright, mm."""
    w, h = PAGE_MM.get(page.upper(), PAGE_MM["A4"])
    return w - 2 * PAGE_MARGIN, h - 2 * PAGE_MARGIN


def render_html(book: Roadbook, settings: Settings) -> str:
    r = settings.render
    layout = r.layout
    grades = settings.climbs.grade_colours
    width = r.width_mm or LAYOUT_WIDTH[layout]
    length = r.length_mm

    # strip: what is left of the row once the km column is taken.
    # line: tokens may grow; cap so a single stop can't eat the whole ribbon.
    col = km_col(book.length_km)
    avail = width - col - 3 * PAD if layout == "strip" else 40.0
    gutter = r.gutter_mm if layout == "strip" and book.profile is not None else 0
    avail -= gutter
    if layout == "strip":  # the ↓ distance to the next row takes the end of the main line
        avail -= DIST_W
    row_layout = RowLayout(
        avail=avail,
        max_emojis=r.max_emojis or 99,
        sep=" " if gutter else " · ",  # the gutter eats ~5 mm; drop the dots so stats still fit beside it
        range_m=r.stop_range_m,
        # tokens of the line layout grow sideways instead
        emoji_lines=r.emoji_lines if layout == "strip" else 1,
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

    sheet = (
        booklet_format(settings.booklet.format) is None
    )  # else the reference sheet, the climb pages and the map go in the booklet
    details = (
        _details(book, settings.categories, r.stop_range_m, settings.hours.categories, grades)
        if r.details and sheet
        else []
    )
    climb_cards = (
        _climb_cards(book, settings.categories, grades, _printable_width(r.page, orientation)) if sheet else []
    )
    route_map = (
        map_svg(book.route_map, book, settings.categories, settings.map.km_every) if book.route_map and sheet else ""
    )
    env = Environment(loader=PackageLoader("roadbook", "templates"), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("roadbook.html.j2").render(
        _=_,
        lang=current(),
        book=book,
        layout=layout,
        strips=strips,
        width=width,
        length=length,
        page=r.page,
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
        g=Geometry(gutter, col),
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
