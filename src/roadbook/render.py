from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .kinds import KINDS, climb_of
from .model import Glyph
from .svg import gutter_svg

if TYPE_CHECKING:
    from .model import Item, Poi, Roadbook, Stop

# Row geometry (mm). Must match the CSS variables in the template.
MAIN_H, SUB_H, LEG_H, HDR_H, PAD, EMO_H = 4.8, 3.2, 3.4, 4.4, 1.0, 4.4
KM_COL, EMOJI_W, COUNT_W, MORE_W = 9.5, 4.9, 1.3, 2.0
LAYOUT_WIDTH = {"strip": 35.0, "line": 16.0}
DIST_W = 6.0  # an inline "↓10.6" distance to the next row, at the end of the main line
LABEL_W = 8.0  # room kept for a short label ("Cat HC", "1240 m") next to a climb's own emoji
GUTTER_MIN_SPAN_M = 300.0  # a strip's profile spans at least this much elevation, so rolling ground stays flat


def _fit(emojis: list[Glyph], avail: float, max_emojis: int) -> tuple[list[Glyph], bool, float]:
    """Keep as many emojis (in priority order) as fit in `avail` mm. Returns (kept, truncated, width used)."""
    kept: list[Glyph] = []
    used = 0.0
    for g in emojis:
        # one COUNT_W per superscript, whatever its length: charging per character would widen 10+ POI tokens
        w = EMOJI_W + (COUNT_W if g.sup else 0)
        more_w = MORE_W if len(kept) + 1 < len(emojis) else 0
        if len(kept) >= max_emojis or used + w + more_w > avail:
            break
        kept.append(g)
        used += w
    truncated = len(kept) < len(emojis)
    return kept, truncated, used + (MORE_W if truncated else 0)


def _wrap(emojis: list[Glyph], widths: list[float], max_emojis: int) -> tuple[list[list[Glyph]], bool, float]:
    """Pour emojis (in priority order) into lines of the given widths, mm. Returns (lines, truncated, first width).

    Only the last line keeps room for a "+"; the ones before it overflow onto the next instead. A line may stay
    empty when not even one emoji fits there, which lets a crowded climb row send its stop down a line. With a
    single width this is exactly _fit.
    """
    lines: list[list[Glyph]] = []
    first_w = 0.0
    i = 0
    for n, avail in enumerate(widths):
        last = n == len(widths) - 1
        line: list[Glyph] = []
        used = 0.0
        while i < len(emojis) and i < max_emojis:
            w = EMOJI_W + (COUNT_W if emojis[i].sup else 0)  # one COUNT_W per superscript, as in _fit
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
    """For when _fit keeps nothing: the first emoji alone, shedding its count, then the "+", until it fits.

    The count goes first: that more kinds are there matters more than how many of the first. If even the bare
    emoji does not fit, it is shown anyway, a hair over its room rather than gone.
    """
    bare = Glyph(emojis[0].emoji)
    if len(emojis) > 1 and avail >= EMOJI_W + MORE_W:
        return [bare], True, EMOJI_W + MORE_W
    return [bare], False, EMOJI_W


def _category(p: Poi) -> str:
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
    sep: str  # between the figures of a stats or leg line
    leg_elevation: bool  # ↗️/↘️ metres on a leg line of its own, or only a bare distance on the main line
    range_m: float  # a stop stretching at least this far shows where it ends
    emoji_lines: int = 1  # lines a row's emojis may fill; more than 1 wraps a crowded stop below its main line
    wrap_avail: float = 0.0  # mm for emojis on those extra lines, which carry no ↓ distance


def _row(it: Item, book: Roadbook, layout: RowLayout) -> dict[str, Any]:
    avail, max_emojis, sep = layout.avail, layout.max_emojis, layout.sep
    kind = KINDS[it.kind]
    row: dict[str, Any] = {
        "kind": it.kind,
        "km": f"{it.km:.1f}",
        "label": kind.label(it, book),
        "sub": "",
        "leg": None,
        "dist": None,
    }
    if kind.emoji:
        # the row's own emoji leads; a stop snapped onto a climb's foot or summit follows in the space left.
        # The row is too narrow for both a stop and a full label, so with a stop on board the category
        # shrinks to a superscript on ⛰️, and the summit's elevation only shows if room is left.
        sup = kind.sup(it)
        room = avail - EMOJI_W - COUNT_W * len(sup)
        # at least one stop emoji, even when max_emojis is 1 or the room is too tight for its count and a "+":
        # a snapped stop must not vanish behind its climb
        stop_emojis, extra, row["more"], stop_w = _emoji_lines(it.emojis, room, max(1, max_emojis - 1), layout)
        if it.emojis and not stop_emojis and not extra:
            stop_emojis, row["more"], stop_w = _first_only(it.emojis, room)
        row["emojis"] = [Glyph(kind.emoji, sup), *stop_emojis]
        row["emoji_lines"] = extra
        emoji_w = EMOJI_W + stop_w
        if kind.label_if_room and stop_w + LABEL_W > room:
            row["label"] = ""
    else:
        row["emojis"], row["emoji_lines"], row["more"], emoji_w = _emoji_lines(it.emojis, avail, max_emojis, layout)
    if kind.stats:
        c = climb_of(it)
        row["sub"] = f"{c.length_km:.1f}km{sep}{c.avg_grade:.1f}%{sep}↗️{c.gain_m:.0f}"
        row["sub_short"] = f"{c.length_km:.1f}km {c.avg_grade:.0f}%"
    elif it.stop and _spans(it.stop, layout.range_m):
        # the row sits at the stop's first POI; a long stop says where its last one is, so nothing hides past it
        row["sub"] = row["sub_short"] = f"→{it.stop.km_end:.1f}"
    if it.dist_to_next is not None:
        if layout.leg_elevation:
            row["leg"] = f"{it.dist_to_next:.1f}{sep}↗️{it.gain_to_next:.0f} ↘️{it.loss_to_next:.0f}"
        else:  # a bare distance needs no line of its own
            row["dist"] = f"↓{it.dist_to_next:.1f}"
    row["h"] = MAIN_H + EMO_H * len(row["emoji_lines"]) + (SUB_H if row["sub"] else 0) + (LEG_H if row["leg"] else 0)
    row["w"] = max(11.0, emoji_w + 2 * PAD + 0.6, 15.0 if row["sub"] else 0.0)
    if it.dist_to_next is not None:
        row["leg_short"] = f"{it.dist_to_next:.1f}" + (f" ↗️{it.gain_to_next:.0f}" if layout.leg_elevation else "")
    return row


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


def _paginate(rows: list[dict[str, Any]], capacity: float, key: str) -> list[list[dict[str, Any]]]:
    pages: list[list[dict[str, Any]]] = [[]]
    used = 0.0
    for r in rows:
        if pages[-1] and used + r[key] > capacity:
            pages.append([])
            used = 0.0
        pages[-1].append(r)
        used += r[key]
    return pages


_TIME_RANGE = re.compile(r"(\d\d:\d\d)-(\d\d:\d\d)")


def pretty_hours(value: str) -> str:
    """An OSM `opening_hours` value, easier to read on paper: one rule after another, en dashes between times."""
    rules = [" ".join(r.split()) for r in value.split(";") if r.strip()]
    return " · ".join(_TIME_RANGE.sub("\\1\u2013\\2", r).replace(",", ", ") for r in rules)


def _details(book: Roadbook, categories: dict[str, Any], range_m: float, hours_for: list[str]) -> list[dict[str, Any]]:
    """One entry per stop: its POIs grouped under their emoji; with hours, a shop that gets them has its own line."""
    out = []
    order = list(categories)
    for s in book.stops:
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        shared: dict[str, list[dict[str, Any]]] = {}
        for p in sorted(s.pois, key=lambda p: (order.index(_category(p)), p.km)):
            emoji = categories[_category(p)]["emoji"]
            poi = {"name": p.name or p.type, "off": round(p.offset_m), "hours": None}
            if book.hours is not None and p.category in hours_for:
                poi["hours"] = pretty_hours(p.opening_hours) if p.opening_hours else ""  # "" = unknown
                groups.append((emoji, [poi]))
            elif emoji in shared:
                shared[emoji].append(poi)
            else:
                shared[emoji] = [poi]
                groups.append((emoji, shared[emoji]))
        km = f"{s.km:.1f} → {s.km_end:.1f}" if _spans(s, range_m) else f"{s.km:.1f}"
        out.append({"km": km, "groups": groups})
    return out


def _add_gutters(strips: list[dict[str, Any]], book: Roadbook, width: float, height: float) -> None:
    """Give each strip a profile whose km scale follows its rows: one anchor at each row's main line."""
    p = book.profile
    if p is None:  # render_html() only asks for gutters when there is a profile
        return
    first = 0  # index in book.items of the strip's first row
    for s in strips:
        items = book.items[first : first + len(s["rows"])]
        anchors, y = [], 0.0
        for it, row in zip(items, s["rows"], strict=True):
            anchors.append((it.km, y + MAIN_H / 2))
            y += row["h"]
        first += len(items)
        if first < len(book.items):  # carry the line to the bottom edge, towards the next strip's first row
            anchors.append((book.items[first].km, height))
        # each strip gets its own scale: one shared with a 1500 m pass would flatten every other strip
        lo, hi = p.ele_range(anchors[0][0], anchors[-1][0])
        s["gutter"] = gutter_svg(p, book.climbs, anchors, width, height, (lo, max(hi, lo + GUTTER_MIN_SPAN_M)))


def render_html(book: Roadbook, cfg: dict[str, Any]) -> str:
    r = cfg["render"]
    layout = r["layout"]
    width = r["width_mm"] or LAYOUT_WIDTH[layout]
    length = r["length_mm"]

    # strip: what is left of the row once the km column is taken.
    # line: tokens may grow; cap so a single stop can't eat the whole ribbon.
    avail = width - KM_COL - 3 * PAD if layout == "strip" else 40.0
    gutter = r["gutter_mm"] if layout == "strip" and book.profile is not None else 0
    avail -= gutter
    if layout == "strip" and not r["leg_elevation"]:
        avail -= DIST_W
    row_layout = RowLayout(
        avail=avail,
        max_emojis=r["max_emojis"] or 99,
        sep=" " if gutter else " · ",  # the gutter eats ~5 mm; drop the dots so stats still fit beside it
        leg_elevation=r["leg_elevation"],
        range_m=r["stop_range_m"],
        # tokens of the line layout grow sideways instead
        emoji_lines=r["emoji_lines"] if layout == "strip" else 1,
        wrap_avail=avail + (DIST_W if layout == "strip" and not r["leg_elevation"] else 0),
    )
    rows = [_row(it, book, row_layout) for it in book.items]

    if layout == "strip":
        pages = _paginate(rows, length - HDR_H - 2 * PAD, "h")
        orientation = "portrait"
    else:
        pages = _paginate(rows, length - 2 * PAD, "w")
        orientation = "landscape"
    strips = [{"rows": p, "first": p[0]["km"], "last": p[-1]["km"]} for p in pages]
    if gutter:
        _add_gutters(strips, book, gutter, length - HDR_H - 2 * PAD)

    details = _details(book, cfg["categories"], r["stop_range_m"], cfg["hours"]["categories"]) if r["details"] else []
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
        g={
            "MAIN_H": MAIN_H,
            "SUB_H": SUB_H,
            "LEG_H": LEG_H,
            "HDR_H": HDR_H,
            "KM_COL": KM_COL,
            "PAD": PAD,
            "EMO_H": EMO_H,
            "GUTTER": gutter,
        },
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
    """Print via a headless Chromium-based browser (Edge/Chrome), which renders colour emoji reliably."""
    exe = _find_browser()
    if exe is None:
        msg = "No Edge/Chrome found for PDF export; open the HTML and print it instead."
        raise RuntimeError(msg)
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
    )
