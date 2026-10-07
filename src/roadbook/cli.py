from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path  # noqa: TC003  Typer resolves the annotations of main() at runtime
from typing import TYPE_CHECKING, Annotated, cast

import typer

from .build import build
from .config import load_config
from .interactive import ask, command
from .render import html_to_pdf, render_html

if TYPE_CHECKING:
    from io import TextIOWrapper

    from .hours import Report
    from .model import Roadbook

app = typer.Typer(add_completion=False, help="Turn a GPX file with POIs into a compact printable road book.")


class Layout(StrEnum):
    strip = "strip"
    line = "line"


def _extra_checkpoint(value: str) -> list:
    km, _, label = value.partition(":")
    return [float(km), label]


def _echo_hours(r: Report) -> None:
    where = [*r.sources, *([f"{r.cached} from cache"] if r.cached else [])]
    typer.echo(f"Opening hours: {r.found} of {r.asked} shops" + (f" ({', '.join(where)})" if where else ""))
    if r.failed:
        typer.echo("Opening hours: OpenStreetMap services did not answer for some shops; run again later.", err=True)


@app.command()
def main(  # noqa: PLR0913, PLR0917  one parameter per CLI option, as Typer expects
    gpx: Annotated[
        Path | None,
        typer.Argument(exists=True, dir_okay=False, help="GPX file (route/track + waypoints); asked for with -i."),
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output .html (default: next to the GPX).")] = None,
    layout: Annotated[
        Layout, typer.Option(help="strip: vertical, top tube. line: one horizontal ribbon of tokens.")
    ] = Layout.strip,
    width: Annotated[
        float | None, typer.Option(help="Short side of a strip, mm (default: 35 strip / 16 line).")
    ] = None,
    length: Annotated[float | None, typer.Option(help="Long side of a strip, mm.")] = None,
    page: Annotated[str | None, typer.Option(help="Paper size, e.g. A4, A5, Letter.")] = None,
    checkpoint_every: Annotated[float | None, typer.Option(help="Auto checkpoint every N km (0 = off).")] = None,
    checkpoint: Annotated[list[str] | None, typer.Option(help="Extra checkpoint, KM or KM:LABEL. Repeatable.")] = None,
    gap: Annotated[float | None, typer.Option(help="Merge POIs closer than this many metres into one stop.")] = None,
    min_climb: Annotated[
        float | None, typer.Option(help="Smallest gain, metres, for an ascent to count as a climb (default 80).")
    ] = None,
    max_span: Annotated[
        float | None, typer.Option(help="Longest a stop may stretch, metres; longer runs split (default 3 x gap).")
    ] = None,
    max_offset: Annotated[
        float | None, typer.Option(help="Ignore POIs further than this from the route, metres.")
    ] = None,
    categories: Annotated[
        str | None, typer.Option(help="Comma-separated POI categories to keep, e.g. water,bakery,lodging.")
    ] = None,
    details: Annotated[bool | None, typer.Option(help="Append a POI names reference sheet.")] = None,
    leg_elevation: Annotated[
        bool | None, typer.Option(help="Show climbing/descent metres on each leg between rows.")
    ] = None,
    emoji_lines: Annotated[
        int | None,
        typer.Option(help="Lines a crowded stop's emojis may fill on a strip (default 2; 1 = one line, with a +)."),
    ] = None,
    hours: Annotated[
        bool | None,
        typer.Option(help="Look up shops' opening hours on OpenStreetMap for the details sheet (needs internet)."),
    ] = None,
    refresh_hours: Annotated[  # noqa: FBT002  a --refresh-hours flag
        bool, typer.Option(help="Look up opening hours again, instead of using the ones cached from earlier runs.")
    ] = False,
    pdf: Annotated[bool, typer.Option(help="Also export a PDF via headless Edge/Chrome.")] = False,  # noqa: FBT002  a --pdf flag
    config: Annotated[Path | None, typer.Option(help="TOML file overriding default.toml.")] = None,
    interactive: Annotated[  # noqa: FBT002  a -i flag
        bool,
        typer.Option(
            "--interactive", "-i", help="Ask for the settings step by step, starting from the ones given as options."
        ),
    ] = False,
) -> None:
    cast("TextIOWrapper", sys.stdout).reconfigure(encoding="utf-8")  # emoji-safe on Windows consoles
    cfg = load_config(config)
    cfg["render"]["layout"] = layout.value
    for section, key, value in (
        ("render", "width_mm", width),
        ("render", "length_mm", length),
        ("render", "page", page),
        ("render", "details", details),
        ("render", "leg_elevation", leg_elevation),
        ("render", "emoji_lines", emoji_lines),
        ("hours", "enabled", hours),
        ("checkpoints", "every_km", checkpoint_every),
        ("climbs", "min_gain_m", min_climb),
        ("stops", "gap_m", gap),
        ("stops", "max_span_m", max_span),
        ("pois", "max_offset_m", max_offset),
    ):
        if value is not None:
            cfg[section][key] = value
    if checkpoint:
        cfg["checkpoints"]["extra"] += [_extra_checkpoint(c) for c in checkpoint]
    if categories:
        cfg["pois"]["enabled"] = [c.strip() for c in categories.split(",")]
    if refresh_hours:
        cfg["hours"]["max_age_days"] = 0

    if interactive:
        if not sys.stdin.isatty():
            typer.echo("-i asks its questions in a terminal; give the settings as options instead.", err=True)
            raise typer.Exit(2)
        gpx, out, pdf = ask(gpx, out, pdf, cfg)
        same = command(gpx, out, pdf=pdf, cfg=cfg, base=load_config(config), config=config)
        typer.echo(f"\nSame as: {same}\n")
    elif gpx is None:
        typer.echo("Missing GPX file: give one, or use -i to be asked.", err=True)
        raise typer.Exit(2)

    book = build(gpx, cfg)
    html = render_html(book, cfg)
    out = out or gpx.with_suffix(".roadbook.html")
    out.write_text(html, encoding="utf-8")
    _summary(book)
    typer.echo(f"Wrote {out}")
    if pdf:
        _write_pdf(out)


def _summary(book: Roadbook) -> None:
    typer.echo(f"{book.title}: {book.length_km:.1f} km, +{book.gain_m:.0f} m / -{book.loss_m:.0f} m")
    typer.echo(
        f"POIs: {book.poi_total} in file -> {book.poi_kept} kept -> {len(book.stops)} stops; {len(book.climbs)} climbs"
    )
    if book.hours:
        _echo_hours(book.hours)


def _write_pdf(html: Path) -> None:
    pdf_path = html.with_suffix(".pdf")
    try:
        html_to_pdf(html, pdf_path)
    except (RuntimeError, OSError) as exc:
        typer.echo(f"PDF not written: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote {pdf_path}")
