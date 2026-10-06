from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .kinds import KINDS, climb_of
from .svg import gutter_svg

if TYPE_CHECKING:
    from .model import Item, Poi, Roadbook

# Row geometry (mm). Must match the CSS variables in the template.
MAIN_H, SUB_H, LEG_H, HDR_H, PAD = 4.8, 3.2, 3.4, 4.4, 1.0
KM_COL, EMOJI_W, COUNT_W, MORE_W = 9.5, 4.9, 1.3, 2.0
LAYOUT_WIDTH = {"strip": 35.0, "line": 16.0}
DIST_W = 6.0  # an inline "↓10.6" distance to the next row, at the end of the main line
LABEL_W = 8.0  # room kept for a short label ("Cat HC", "1240 m") next to a climb's own emoji
GUTTER_MIN_SPAN_M = 300.0  # a strip's profile spans at least this much elevation, so rolling ground stays flat


def _fit(emojis: list[tuple[str, int]], avail: float, max_emojis: int) -> tuple[list[tuple[str, int]], bool, float]:
    """Keep as many emojis (in priority order) as fit in `avail` mm. Returns (kept, truncated, width used)."""
    kept: list[tuple[str, int]] = []
    used = 0.0
    for e, n in emojis:
        w = EMOJI_W + (COUNT_W if n > 1 else 0)
        more_w = MORE_W if len(kept) + 1 < len(emojis) else 0
        if len(kept) >= max_emojis or used + w + more_w > avail:
            break
        kept.append((e, n))
        used += w
    truncated = len(kept) < len(emojis)
    return kept, truncated, used + (MORE_W if truncated else 0)


def _category(p: Poi) -> str:
    """The category of a POI inside a stop; filter_pois() only keeps classified ones."""
    if p.category is None:
        msg = f"POI {p.name!r} at km {p.km:.1f} has no category"
        raise ValueError(msg)
    return p.category


def _row(
    it: Item, book: Roadbook, avail: float, max_emojis: int, *, sep: str = " · ", leg_elevation: bool = True
) -> dict[str, Any]:
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
        # at least one stop emoji even when max_emojis is 1: a snapped stop must not vanish behind its climb
        stop_emojis, row["more"], stop_w = _fit(it.emojis, room, max(1, max_emojis - 1))
        row["emojis"] = [(kind.emoji, sup or 0), *stop_emojis]
        emoji_w = EMOJI_W + stop_w
        if kind.label_if_room and stop_w + LABEL_W > room:
            row["label"] = ""
    else:
        row["emojis"], row["more"], emoji_w = _fit(it.emojis, avail, max_emojis)
    if kind.stats:
        c = climb_of(it)
        row["sub"] = f"{c.length_km:.1f}km{sep}{c.avg_grade:.1f}%{sep}↗️{c.gain_m:.0f}"
        row["sub_short"] = f"{c.length_km:.1f}km {c.avg_grade:.0f}%"
    if it.dist_to_next is not None:
        if leg_elevation:
            row["leg"] = f"{it.dist_to_next:.1f}{sep}↗️{it.gain_to_next:.0f} ↘️{it.loss_to_next:.0f}"
        else:  # a bare distance needs no line of its own
            row["dist"] = f"↓{it.dist_to_next:.1f}"
    row["h"] = MAIN_H + (SUB_H if row["sub"] else 0) + (LEG_H if row["leg"] else 0)
    row["w"] = max(11.0, emoji_w + 2 * PAD + 0.6, 15.0 if row["sub"] else 0.0)
    if it.dist_to_next is not None:
        row["leg_short"] = f"{it.dist_to_next:.1f}" + (f" ↗️{it.gain_to_next:.0f}" if leg_elevation else "")
    return row


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


def _details(book: Roadbook, categories: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    order = list(categories)
    for s in book.stops:
        groups: dict[str, list[tuple[str, int]]] = {}
        for p in sorted(s.pois, key=lambda p: (order.index(_category(p)), p.km)):
            groups.setdefault(categories[_category(p)]["emoji"], []).append((p.name or p.type, round(p.offset_m)))
        out.append({"km": f"{s.km:.1f}", "groups": list(groups.items())})
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
        seg = p.e[(p.x >= anchors[0][0] * 1000) & (p.x <= anchors[-1][0] * 1000)]
        lo = float(seg.min()) if seg.size else p.ele_at(anchors[0][0])
        hi = max(float(seg.max()) if seg.size else lo, lo + GUTTER_MIN_SPAN_M)
        s["gutter"] = gutter_svg(p, book.climbs, anchors, width, height, (lo, hi))


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
    sep = " " if gutter else " · "  # the gutter eats ~5 mm; drop the dots so stats still fit beside it
    rows = [
        _row(it, book, avail, r["max_emojis"] or 99, sep=sep, leg_elevation=r["leg_elevation"]) for it in book.items
    ]

    if layout == "strip":
        pages = _paginate(rows, length - HDR_H - 2 * PAD, "h")
        orientation = "portrait"
    else:
        pages = _paginate(rows, length - 2 * PAD, "w")
        orientation = "landscape"
    strips = [{"rows": p, "first": p[0]["km"], "last": p[-1]["km"]} for p in pages]
    if gutter:
        _add_gutters(strips, book, gutter, length - HDR_H - 2 * PAD)

    env = Environment(loader=PackageLoader("roadbook", "templates"), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("roadbook.html.j2").render(
        book=book,
        layout=layout,
        strips=strips,
        width=width,
        length=length,
        page=r["page"],
        orientation=orientation,
        details=_details(book, cfg["categories"]) if r["details"] else [],
        g={
            "MAIN_H": MAIN_H,
            "SUB_H": SUB_H,
            "LEG_H": LEG_H,
            "HDR_H": HDR_H,
            "KM_COL": KM_COL,
            "PAD": PAD,
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
