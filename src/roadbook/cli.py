from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import Annotated, Optional

import typer

from .build import build
from .config import load_config
from .render import html_to_pdf, render_html

app = typer.Typer(add_completion=False, help="Turn a GPX file with POIs into a compact printable road book.")


class Layout(str, Enum):
    strip = "strip"
    line = "line"


def _extra_checkpoint(value: str) -> list:
    km, _, label = value.partition(":")
    return [float(km), label]


@app.command()
def main(
    gpx: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="GPX file (route/track + waypoints).")],
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Output .html (default: next to the GPX).")] = None,
    layout: Annotated[Layout, typer.Option(help="strip: vertical, top tube. line: one horizontal ribbon of tokens.")] = Layout.strip,
    width: Annotated[Optional[float], typer.Option(help="Short side of a strip, mm (default: 35 strip / 16 line).")] = None,
    length: Annotated[Optional[float], typer.Option(help="Long side of a strip, mm.")] = None,
    page: Annotated[Optional[str], typer.Option(help="Paper size, e.g. A4, A5, Letter.")] = None,
    checkpoint_every: Annotated[Optional[float], typer.Option(help="Auto checkpoint every N km (0 = off).")] = None,
    checkpoint: Annotated[Optional[list[str]], typer.Option(help="Extra checkpoint, KM or KM:LABEL. Repeatable.")] = None,
    gap: Annotated[Optional[float], typer.Option(help="Merge POIs closer than this many metres into one stop.")] = None,
    max_offset: Annotated[Optional[float], typer.Option(help="Ignore POIs further than this from the route, metres.")] = None,
    categories: Annotated[Optional[str], typer.Option(help="Comma-separated POI categories to keep, e.g. water,bakery,lodging.")] = None,
    details: Annotated[Optional[bool], typer.Option(help="Append a POI names reference sheet.")] = None,
    pdf: Annotated[bool, typer.Option(help="Also export a PDF via headless Edge/Chrome.")] = False,
    config: Annotated[Optional[Path], typer.Option(help="TOML file overriding default.toml.")] = None,
) -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # emoji-safe on Windows consoles
    cfg = load_config(config)
    cfg["render"]["layout"] = layout.value
    for key, value in (("width_mm", width), ("length_mm", length), ("page", page), ("details", details)):
        if value is not None:
            cfg["render"][key] = value
    if checkpoint_every is not None:
        cfg["checkpoints"]["every_km"] = checkpoint_every
    if checkpoint:
        cfg["checkpoints"]["extra"] += [_extra_checkpoint(c) for c in checkpoint]
    if gap is not None:
        cfg["stops"]["gap_m"] = gap
    if max_offset is not None:
        cfg["pois"]["max_offset_m"] = max_offset
    if categories:
        cfg["pois"]["enabled"] = [c.strip() for c in categories.split(",")]

    book = build(gpx, cfg)
    html = render_html(book, cfg)
    out = out or gpx.with_suffix(".roadbook.html")
    out.write_text(html, encoding="utf-8")

    typer.echo(f"{book.title}: {book.length_km:.1f} km, +{book.gain_m:.0f} m / -{book.loss_m:.0f} m")
    typer.echo(f"POIs: {book.poi_total} in file -> {book.poi_kept} kept -> {len(book.stops)} stops; {len(book.climbs)} climbs")
    typer.echo(f"Wrote {out}")
    if pdf:
        pdf_path = out.with_suffix(".pdf")
        try:
            html_to_pdf(out, pdf_path)
        except (RuntimeError, OSError) as exc:
            typer.echo(f"PDF not written: {exc}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Wrote {pdf_path}")
