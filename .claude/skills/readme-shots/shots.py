"""Regenerate the README's images in docs/img/ from the working tree.

    uv run --no-sync --with pillow python .claude/skills/readme-shots/shots.py [strip] [map] [booklet]
        [--pages FIRST LAST]

With no name, all three. Each road book is made by `api.run`, with the lookups, as a user would get it; the shots are
taken in the headless Chrome or Edge the road book prints with, and stored as palette PNGs.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

from roadbook import api
from roadbook.config import load_config
from roadbook.render import _find_browser

ROOT = Path(__file__).resolve().parents[3]
IMG = ROOT / "docs" / "img"
SAMPLE = ROOT / "samples" / "entrainement_ubf.gpx"
PX = 96 / 25.4  # CSS px a mm


def shoot(html: str, out: Path, w_mm: float, h_mm: float, dpi: float) -> None:
    """Screenshot `html` in a window `w_mm` by `h_mm` at `dpi`."""
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "shot.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run(  # noqa: S603  the installed Chrome or Edge, on a page written here
            [
                _find_browser(),
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--force-device-scale-factor={dpi / 96}",
                f"--window-size={round(w_mm * PX)},{round(h_mm * PX)}",
                f"--screenshot={out.resolve()}",
                page.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )


def palette(png: Path, method: Image.Quantize) -> None:
    """Store `png` as 256 colours: a fifth of the browser's size. Check the result by eye."""
    Image.open(png).convert("RGB").quantize(256, method=method).save(png, optimize=True)
    print(f"{png.relative_to(ROOT)}: {Image.open(png).size}, {png.stat().st_size // 1024} kB")


def made(cfg: dict, out: Path) -> api.Result:
    return api.run(SAMPLE, cfg, out=out / "book.html")


def strip(out: Path) -> None:
    """The first strip of the defaults, without the reference sheet: 35 by 200 mm."""
    cfg = load_config()
    cfg["render"]["details"] = False
    html = made(cfg, out).html.read_text(encoding="utf-8")
    style = """<style>
  body { background: #fff !important; padding: 0 !important; margin: 0 !important; }
  .sheet > .strip:not(:nth-of-type(1)) { display: none; }
</style>"""
    shoot(html.replace("</head>", style + "</head>", 1), IMG / "strip-example.png", 35, 200, 192)
    palette(IMG / "strip-example.png", Image.Quantize.MAXCOVERAGE)


def route_map(out: Path) -> None:
    """The booklet's overview map, the largest route map (the others are mini-maps): full width, 110 mm from y = 74."""
    result = made(load_config(), out)
    html = api.booklet_out(result.html).read_text(encoding="utf-8")
    maps = re.findall(r'<svg class="route-map".*?</svg>', html, re.DOTALL)
    svg = max(maps, key=lambda m: float(re.search(r'viewBox="0 0 ([\d.]+)', m).group(1)))
    width = float(re.search(r'viewBox="0 0 ([\d.]+)', svg).group(1))
    x, y, w, h = 0.0, 74.0, width, 110.0
    page = f"""<!doctype html><meta charset="utf-8"><style>
html, body {{ margin: 0; overflow: hidden; background: #fff; font-family: system-ui, sans-serif; }}
.view {{ width: {w}mm; height: {h}mm; overflow: hidden; position: relative; }}
.view svg {{ position: absolute; left: {-x}mm; top: {-y}mm; }}
</style><div class="view">{svg}</div>"""
    shoot(page, IMG / "map.png", w, h, 130)
    palette(IMG / "map.png", Image.Quantize.MAXCOVERAGE)  # MEDIANCUT turns the green start disc grey


def booklet(out: Path, first: int, last: int) -> None:
    """Pages `first` to `last` of the A5 booklet of a timed ride, side by side as on screen."""
    cfg = load_config()
    cfg["hours"]["enabled"] = True
    cfg["ride"].update(date="2026-10-11", start="07:30", speed_kmh=24)
    html = api.booklet_out(made(cfg, out).html).read_text(encoding="utf-8")
    page_w = float(re.search(r"--pw: ([\d.]+)mm", html).group(1))
    page_h = float(re.search(r"--ph: ([\d.]+)mm", html).group(1))
    shown = ", ".join(f'.page[style$="order: {n}"]' for n in range(first, last + 1))
    gap = 4
    style = f"""<style>
  .note {{ display: none !important; }}
  body {{ background: #fff !important; padding: 0 !important; }}
  .booklet {{ gap: {gap}mm !important; flex-wrap: nowrap !important; }}
  .page, .spread {{ display: none; }}
  {shown} {{ display: block; box-shadow: 0 0 0 0.3mm #bbb !important; }}
</style>"""
    count = last - first + 1
    shoot(
        html.replace("</head>", style + "</head>", 1),
        IMG / "booklet.png",
        count * page_w + (count - 1) * gap,
        page_h,
        150,
    )
    palette(IMG / "booklet.png", Image.Quantize.FASTOCTREE)  # MAXCOVERAGE tints white pages' greys pink


def main() -> None:
    args = sys.argv[1:]
    first, last = 2, 3
    if "--pages" in args:
        i = args.index("--pages")
        first, last = int(args[i + 1]), int(args[i + 2])
        del args[i : i + 3]
    wanted = args or ["strip", "map", "booklet"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for name in wanted:
            {"strip": strip, "map": route_map, "booklet": lambda o: booklet(o, first, last)}[name](out)


if __name__ == "__main__":
    main()
