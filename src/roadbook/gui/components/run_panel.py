"""Make the road book: run it off the event loop, show its log as it goes, then what it made and how to reach it."""

from __future__ import annotations

import logging
import subprocess
import webbrowser
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, override

from nicegui import run, ui

from roadbook.api import GpxError, Result, lookup_line
from roadbook.api import run as make_road_book
from roadbook.render import html_to_pdf

if TYPE_CHECKING:
    from roadbook.gui.models.form import Form

LOOKUPS = (  # what the summary says of each OpenStreetMap lookup, as the CLI does
    ("Opening hours", "hours", "shops", "shops"),
    ("Towns", "towns", "busy stops named", "stops"),
    ("Climb names", "climb_names", "climbs named", "climbs"),
)


class _Queue(logging.Handler):
    """The run's log, kept for the page: the run logs from a worker thread, and the page's timer shows it."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: deque[str] = deque()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        indent = "    " if record.name == "roadbook.http" else "  " if record.levelno < logging.INFO else ""
        self.lines.append(indent + record.getMessage())


class RunPanel:
    """The Run button, the log, and what the run made."""

    def __init__(self, form: Form) -> None:
        self.form = form
        self.result: Result | None = None
        self.queue = _Queue()
        self.button = (
            ui.button("Make the road book", icon="directions_bike", on_click=self.make)
            .props("unelevated size=lg")
            .classes("w-full")
            .mark("make")
        )
        self.log = ui.log(max_lines=400).classes("w-full h-48 text-xs").mark("log")
        ui.timer(0.2, self.show_log)
        self.summary = ui.column().classes("w-full gap-0").mark("summary")
        with ui.row().classes("gap-2") as self.actions:
            ui.button("Open", icon="open_in_new", on_click=self.open_html).props("outline").mark("open")
            ui.button("PDF", icon="picture_as_pdf", on_click=self.make_pdf).props("outline").mark("pdf")
            ui.button("Folder", icon="folder_open", on_click=self.open_folder).props("outline").mark("folder")
        self.actions.visible = False
        with ui.row().classes("w-full items-center no-wrap gap-2") as self.same:
            self.command = ui.label().classes("font-mono text-xs break-all").mark("command")
            ui.button(icon="content_copy", on_click=self.copy).props("flat dense round").tooltip(
                "Copy the same command for the terminal"
            ).mark("copy")
        self.same.visible = False

    def show_log(self) -> None:
        while self.queue.lines:
            self.log.push(self.queue.lines.popleft())

    async def make(self) -> None:
        """Run on a worker thread (run.io_bound), so the window stays alive through the online lookups."""
        gpx = Path(self.form.gpx)
        if not self.form.gpx or not await run.io_bound(gpx.is_file):
            ui.notify("Pick a GPX file first", type="warning")
            return
        self.button.disable()
        self.log.clear()
        logger = logging.getLogger("roadbook")
        level = logger.level
        logger.setLevel(logging.INFO)  # the steps, as roadbook -v shows them
        logger.addHandler(self.queue)
        try:
            out = Path(self.form.out) if self.form.out else None
            result = await run.io_bound(make_road_book, gpx, self.form.settings(), out=out, pdf=self.form.pdf)
        except GpxError as exc:
            ui.notify(str(exc), type="negative", multi_line=True)
            return
        finally:
            logger.removeHandler(self.queue)
            logger.setLevel(level)
            self.button.enable()
            self.show_log()
        if result is not None:  # None: the app is shutting down
            self.done(result)

    def done(self, result: Result) -> None:
        self.result = result
        book = result.book
        self.summary.clear()
        with self.summary:
            ui.label(f"{book.title}: {book.length_km:.1f} km, +{book.gain_m:.0f} m / -{book.loss_m:.0f} m")
            ui.label(f"{book.poi_kept} of {book.poi_total} POIs, in {len(book.stops)} stops; {len(book.climbs)} climbs")
            for what, attr, found, items in LOOKUPS:
                if said := lookup_line(what, getattr(book, attr), found, items):
                    ui.label(said[0])
                    if said[1]:
                        ui.notify(said[1], type="warning", multi_line=True)
            ui.label(f"Wrote {result.html}").classes("text-positive").mark("wrote")
        for warning in result.warnings:
            ui.notify(warning, type="warning", multi_line=True)
        if result.pdf_error:
            ui.notify(f"PDF not written: {result.pdf_error}", type="negative", multi_line=True)
        self.actions.visible = True
        self.command.set_text(self.form.command())
        self.same.visible = True

    async def open_html(self) -> None:
        if self.result:
            await run.io_bound(webbrowser.open, self.result.html.as_uri())

    async def make_pdf(self) -> None:
        """Print the road book to a PDF next to it, with Edge or Chrome, and open it."""
        if not self.result:
            return
        pdf = self.result.html.with_suffix(".pdf")
        try:
            await run.io_bound(html_to_pdf, self.result.html, pdf)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:  # no browser, or it failed
            ui.notify(f"PDF not written: {exc}", type="negative", multi_line=True)
            return
        await run.io_bound(webbrowser.open, pdf.as_uri())

    async def open_folder(self) -> None:
        if self.result:
            await run.io_bound(webbrowser.open, self.result.html.parent.as_uri())

    def copy(self) -> None:
        ui.clipboard.write(self.command.text)  # not awaited: a plain function in nicegui 3.18
        ui.notify("Command copied")
