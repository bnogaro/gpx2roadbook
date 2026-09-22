from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .model import Item, Roadbook

# Row geometry (mm). Must match the CSS variables in the template.
MAIN_H, SUB_H, LEG_H, HDR_H, PAD = 4.8, 3.2, 3.4, 4.4, 1.0
KM_COL, EMOJI_W, COUNT_W, MORE_W = 9.5, 4.9, 1.3, 2.0
LAYOUT_WIDTH = {"strip": 35.0, "line": 16.0}


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


def _row(it: Item, book: Roadbook, avail: float, max_emojis: int) -> dict[str, Any]:
    emojis, truncated, emoji_w = _fit(it.emojis, avail, max_emojis)
    row: dict[str, Any] = {
        "kind": it.kind,
        "km": f"{it.km:.1f}",
        "emojis": emojis,
        "more": truncated,
        "label": it.label,
        "sub": "",
        "leg": None,
    }
    if it.kind == "start":
        row["emojis"] = [("🟢", 0)]
    elif it.kind == "finish":
        row["emojis"] = [("🏁", 0)]
    elif it.kind == "checkpoint":
        row["emojis"] = [("🚩", 0)]
        if it.label.startswith("CP"):
            row["label"] = f"{it.label} · {book.length_km - it.km:.0f} to go"
    elif it.kind == "climb":
        c = it.climb
        row["emojis"] = [("⛰️", 0)]
        row["label"] = f"Cat {c.label}" if c.label else ""
        row["sub"] = f"{c.length_km:.1f}km · {c.avg_grade:.1f}% · ↗️{c.gain_m:.0f}"
        row["sub_short"] = f"{c.length_km:.1f}km {c.avg_grade:.0f}%"
        emoji_w = EMOJI_W
    if it.dist_to_next is not None:
        row["leg"] = f"{it.dist_to_next:.1f} · ↗️{it.gain_to_next:.0f} ↘️{it.loss_to_next:.0f}"
    row["h"] = MAIN_H + (SUB_H if row["sub"] else 0) + (LEG_H if row["leg"] else 0)
    if it.kind in ("start", "finish", "checkpoint"):
        emoji_w = EMOJI_W
    row["w"] = max(11.0, emoji_w + 2 * PAD + 0.6, 15.0 if row["sub"] else 0.0)
    if it.dist_to_next is not None:
        row["leg_short"] = f"{it.dist_to_next:.1f} ↗️{it.gain_to_next:.0f}"
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
        for p in sorted(s.pois, key=lambda p: (order.index(p.category), p.km)):
            groups.setdefault(categories[p.category]["emoji"], []).append((p.name or p.type, round(p.offset_m)))
        out.append({"km": f"{s.km:.1f}", "groups": list(groups.items())})
    return out


def render_html(book: Roadbook, cfg: dict[str, Any]) -> str:
    r = cfg["render"]
    layout = r["layout"]
    width = r["width_mm"] or LAYOUT_WIDTH[layout]
    length = r["length_mm"]

    if layout == "strip":
        avail = width - KM_COL - 3 * PAD  # what is left of the row once the km column is taken
    else:
        avail = 40.0  # tokens may grow; cap so a single stop can't eat the whole ribbon
    rows = [_row(it, book, avail, r["max_emojis"] or 99) for it in book.items]

    if layout == "strip":
        pages = _paginate(rows, length - HDR_H - 2 * PAD, "h")
        orientation = "portrait"
    else:
        pages = _paginate(rows, length - 2 * PAD, "w")
        orientation = "landscape"
    strips = [{"rows": p, "first": p[0]["km"], "last": p[-1]["km"]} for p in pages]

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
        g=dict(MAIN_H=MAIN_H, SUB_H=SUB_H, LEG_H=LEG_H, HDR_H=HDR_H, KM_COL=KM_COL, PAD=PAD),
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
        raise RuntimeError("No Edge/Chrome found for PDF export; open the HTML and print it instead.")
    subprocess.run(
        [exe, "--headless", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf.resolve()}", html.resolve().as_uri()],
        check=True,
        capture_output=True,
    )
