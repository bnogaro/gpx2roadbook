from __future__ import annotations

import logging
import sys
from datetime import datetime, time
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path  # noqa: TC003  Typer resolves the annotations of main() at runtime
from time import perf_counter
from typing import TYPE_CHECKING, Annotated, cast, override

import typer

from . import settings as st
from .api import GpxError, lookup_line, run
from .config import load_config
from .interactive import ask, command
from .opening import parse_break

if TYPE_CHECKING:
    from io import TextIOWrapper

    from .model import Roadbook
    from .osm import Report

app = typer.Typer(add_completion=False, help="Turn a GPX file with POIs into a compact printable road book.")
log = logging.getLogger(__name__)


class Layout(StrEnum):
    strip = "strip"
    line = "line"


def _start(value: str | None) -> tuple[str | None, str | None]:
    """--start as (date, time) config values: "HH:MM" gives a time; "YYYY-MM-DD HH:MM" the date too."""
    if not value:
        return None, None
    text = value.strip()
    try:
        if len(text) > len("HH:MM"):
            when = datetime.fromisoformat(text)
            return when.date().isoformat(), f"{when:%H:%M}"
        return None, f"{time.fromisoformat(text):%H:%M}"
    except ValueError as exc:
        msg = f'{value!r}: give a time, 06:00, or a date and time, "2026-10-12 06:00"'
        raise typer.BadParameter(msg, param_hint="--start") from exc


def _break(value: str) -> list:
    """--break KM:MINUTES as a [km, minutes] config entry."""
    try:
        return parse_break(value)
    except ValueError as exc:
        msg = f"{value!r}: give the km and the minutes, e.g. 95:45"
        raise typer.BadParameter(msg, param_hint="--break") from exc


def _extra_checkpoint(value: str) -> list:
    """--checkpoint KM or KM:LABEL as a [km, label] config entry."""
    km, _, label = value.partition(":")
    try:
        return [float(km), label]
    except ValueError as exc:
        msg = f"{value!r}: give the km, and a label if you like, e.g. 87.5:Lunch"
        raise typer.BadParameter(msg, param_hint="--checkpoint") from exc


def _show_version(wanted: bool) -> None:  # noqa: FBT001  Typer's callback for a flag
    if wanted:
        typer.echo(f"gpx2roadbook {version('gpx2roadbook')}")
        raise typer.Exit


def _utf8_console() -> None:
    """Emojis in the summary and accents in the log, on a Windows console too."""
    for stream in (sys.stdout, sys.stderr):
        cast("TextIOWrapper", stream).reconfigure(encoding="utf-8")


class _Indent(logging.Formatter):
    """A detail (-vv) indented under the step it belongs to, a request to OpenStreetMap (-vvv) twice."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        depth = 2 if record.name == "roadbook.http" else 1 if record.levelno < logging.INFO else 0
        return "  " * depth + super().format(record)


class _Stderr(logging.StreamHandler):
    """The handler the log goes through, told apart from any other so that each run replaces the one before."""


def _log_to_stderr(verbosity: int) -> None:
    """Say what is being done on stderr, the summary staying alone on stdout: warnings only by default; -v the
    steps, -vv the details, -vvv every request to OpenStreetMap too."""
    roadbook = logging.getLogger("roadbook")
    for h in [h for h in roadbook.handlers if isinstance(h, _Stderr)]:
        roadbook.removeHandler(h)
    handler = _Stderr(sys.stderr)
    handler.setFormatter(_Indent("%(message)s"))
    roadbook.addHandler(handler)
    roadbook.setLevel({0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG))
    logging.getLogger("roadbook.http").setLevel(logging.DEBUG if verbosity >= 3 else logging.INFO)  # noqa: PLR2004


def _echo_lookup(what: str, r: Report | None, found: str, items: str) -> None:
    """One line on what an OpenStreetMap lookup found, e.g. "Towns: 5 of 7 busy stops named (Nominatim)"."""
    if said := lookup_line(what, r, found, items):
        line, failure = said
        typer.echo(line)
        if failure:
            typer.echo(failure, err=True)


@app.command()
def main(  # noqa: PLR0913, PLR0917  one parameter per CLI option, as Typer expects
    gpx: Annotated[
        Path | None,
        typer.Argument(exists=True, dir_okay=False, help="GPX file (route/track + waypoints); asked for with -i."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(*st.OUT.decls, "-o", help=st.OUT.help),
    ] = None,
    layout: Annotated[
        Layout,
        typer.Option(*st.LAYOUT.decls, help=st.LAYOUT.help),
    ] = Layout.strip,
    width: Annotated[
        float | None,
        typer.Option(*st.WIDTH.decls, help=st.WIDTH.help),
    ] = None,
    length: Annotated[
        float | None,
        typer.Option(*st.LENGTH.decls, help=st.LENGTH.help),
    ] = None,
    page: Annotated[
        str | None,
        typer.Option(*st.PAGE.decls, help=st.PAGE.help),
    ] = None,
    checkpoint_every: Annotated[
        float | None,
        typer.Option(*st.CHECKPOINT_EVERY.decls, help=st.CHECKPOINT_EVERY.help),
    ] = None,
    checkpoint: Annotated[
        list[str] | None,
        typer.Option(*st.CHECKPOINTS.decls, help=st.CHECKPOINTS.help),
    ] = None,
    gap: Annotated[
        float | None,
        typer.Option(*st.GAP.decls, help=st.GAP.help),
    ] = None,
    min_climb: Annotated[
        float | None,
        typer.Option(*st.MIN_CLIMB.decls, help=st.MIN_CLIMB.help),
    ] = None,
    summit_rows: Annotated[
        bool | None,
        typer.Option(*st.SUMMIT_ROWS.decls, help=st.SUMMIT_ROWS.help),
    ] = None,
    max_span: Annotated[
        float | None,
        typer.Option(*st.MAX_SPAN.decls, help=st.MAX_SPAN.help),
    ] = None,
    categories: Annotated[
        str | None,
        typer.Option(*st.CATEGORIES.decls, help=st.CATEGORIES.help),
    ] = None,
    details: Annotated[
        bool | None,
        typer.Option(*st.DETAILS.decls, help=st.DETAILS.help),
    ] = None,
    emoji_lines: Annotated[
        int | None,
        typer.Option(*st.EMOJI_LINES.decls, help=st.EMOJI_LINES.help),
    ] = None,
    hours: Annotated[
        bool | None,
        typer.Option(*st.HOURS.decls, help=st.HOURS.help),
    ] = None,
    towns: Annotated[
        bool | None,
        typer.Option(*st.TOWNS.decls, help=st.TOWNS.help),
    ] = None,
    climb_names: Annotated[
        bool | None,
        typer.Option(*st.CLIMB_NAMES.decls, help=st.CLIMB_NAMES.help),
    ] = None,
    climb_pages: Annotated[
        bool | None,
        typer.Option(*st.CLIMB_PAGES.decls, help=st.CLIMB_PAGES.help),
    ] = None,
    climb_pages_from: Annotated[
        str | None,
        typer.Option(*st.CLIMB_PAGES_FROM.decls, help=st.CLIMB_PAGES_FROM.help),
    ] = None,
    map_: Annotated[
        bool | None,
        typer.Option(*st.MAP.decls, help=st.MAP.help),
    ] = None,
    map_style: Annotated[
        str | None,
        typer.Option(*st.MAP_STYLE.decls, help=st.MAP_STYLE.help),
    ] = None,
    date: Annotated[
        datetime | None,
        typer.Option(*st.DATE.decls, formats=["%Y-%m-%d"], help=st.DATE.help),
    ] = None,
    start: Annotated[
        str | None,
        typer.Option(*st.START.decls, help=st.START.help),
    ] = None,
    speed: Annotated[
        float | None,
        typer.Option(*st.SPEED.decls, help=st.SPEED.help),
    ] = None,
    margin: Annotated[
        float | None,
        typer.Option(*st.MARGIN.decls, help=st.MARGIN.help),
    ] = None,
    climb: Annotated[
        float | None,
        typer.Option(*st.CLIMB.decls, help=st.CLIMB.help),
    ] = None,
    break_: Annotated[
        list[str] | None,
        typer.Option(*st.BREAKS.decls, help=st.BREAKS.help),
    ] = None,
    refresh: Annotated[  # noqa: FBT002  a --refresh flag
        bool,
        typer.Option(*st.REFRESH.decls, help=st.REFRESH.help),
    ] = False,
    pdf: Annotated[  # noqa: FBT002  a --pdf flag
        bool,
        typer.Option(*st.PDF.decls, help=st.PDF.help),
    ] = False,
    config: Annotated[Path | None, typer.Option(help="TOML file overriding default.toml.")] = None,
    interactive: Annotated[  # noqa: FBT002  a -i flag
        bool,
        typer.Option(
            "--interactive", "-i", help="Ask for the settings step by step, starting from the ones given as options."
        ),
    ] = False,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Say what it does: -v each step, -vv each stop, climb and name found, -vvv each request online.",
        ),
    ] = 0,
    _version: Annotated[  # noqa: FBT002  a --version flag
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Print the version and exit."),
    ] = False,
) -> None:
    started = perf_counter()
    _utf8_console()
    _log_to_stderr(verbose)
    cfg = load_config(config)
    if config:
        log.info("Settings: the defaults, then %s", config)
    st.LAYOUT.set(cfg, layout.value)
    start_date, start_time = _start(start)
    for setting, value in (
        (st.WIDTH, width),
        (st.LENGTH, length),
        (st.PAGE, page),
        (st.DETAILS, details),
        (st.EMOJI_LINES, emoji_lines),
        (st.HOURS, hours),
        (st.TOWNS, towns),
        (st.CLIMB_NAMES, climb_names),
        (st.CLIMB_PAGES, climb_pages),
        (st.CLIMB_PAGES_FROM, climb_pages_from),
        (st.MAP, map_),
        (st.MAP_STYLE, map_style),
        (st.DATE, start_date or (date and date.date().isoformat())),
        (st.START, start_time),
        (st.SPEED, speed),
        (st.MARGIN, margin),
        (st.CLIMB, climb),
        (st.BREAKS, [*cfg["ride"]["breaks"], *map(_break, break_ or [])] or None),
        (st.CHECKPOINT_EVERY, checkpoint_every),
        (st.MIN_CLIMB, min_climb),
        (st.SUMMIT_ROWS, summit_rows),
        (st.GAP, gap),
        (st.MAX_SPAN, max_span),
    ):
        if value is not None:
            setting.set(cfg, value)
    if refresh:
        st.refresh(cfg)
    if checkpoint:
        st.CHECKPOINTS.set(cfg, [*st.CHECKPOINTS.get(cfg), *map(_extra_checkpoint, checkpoint)])
    if categories:
        st.CATEGORIES.set(cfg, [c.strip() for c in categories.split(",")])

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

    _make(gpx, cfg, out, pdf=pdf)
    log.info("Done in %.1f s", perf_counter() - started)


def _make(gpx: Path, cfg: dict, out: Path | None, *, pdf: bool) -> None:
    """Run, then say what was made; exit 1 for a GPX that can't be read, or a PDF that wasn't written."""
    try:
        result = run(gpx, cfg, out=out, pdf=pdf)
    except GpxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    _summary(result.book)
    typer.echo(f"Wrote {result.html}")
    if result.pdf_error:
        typer.echo(f"PDF not written: {result.pdf_error}", err=True)
        raise typer.Exit(1)
    if result.pdf:
        typer.echo(f"Wrote {result.pdf}")


def _summary(book: Roadbook) -> None:
    typer.echo(f"{book.title}: {book.length_km:.1f} km, +{book.gain_m:.0f} m / -{book.loss_m:.0f} m")
    typer.echo(
        f"POIs: {book.poi_total} in file -> {book.poi_kept} kept -> {len(book.stops)} stops; {len(book.climbs)} climbs"
    )
    _echo_lookup("Opening hours", book.hours, "shops", "shops")
    _echo_lookup("Towns", book.towns, "busy stops named", "stops")
    _echo_lookup("Climb names", book.climb_names, "climbs named", "climbs")
    _echo_lookup("Places on climbs", book.places, "climbs with places found", "climbs")
    _echo_lookup("Map", book.tiles, "tiles", "tiles")
