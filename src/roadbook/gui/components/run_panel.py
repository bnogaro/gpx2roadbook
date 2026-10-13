"""Make the road book: run it off the event loop, show its log as it goes, then what it made and how to reach it."""

from __future__ import annotations

import logging
import subprocess
import threading
import webbrowser
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

from nicegui import run, ui

from roadbook.api import GpxError, Result, lookup_line
from roadbook.api import run as make_road_book
from roadbook.gui.components.words import tooltip
from roadbook.i18n import translate
from roadbook.render import html_to_pdf

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.elements.mixins.text_element import TextElement

    from roadbook.gui.models.form import Form
    from roadbook.gui.models.language import Language
    from roadbook.model import Roadbook


def lookups(lang: str) -> tuple[tuple[str, str, str, str], ...]:
    """What the summary says of each OpenStreetMap lookup, as the CLI does, in `lang`: the lookup, its report in the
    book, what it found, what it looks up."""
    return (
        (translate("Opening hours", lang), "hours", translate("shops", lang), translate("shops", lang)),
        (translate("Towns", lang), "towns", translate("busy stops named", lang), translate("stops", lang)),
        (translate("Climb names", lang), "climb_names", translate("climbs named", lang), translate("climbs", lang)),
        (
            translate("Places on climbs", lang),
            "places",
            translate("climbs with places found", lang),
            translate("climbs", lang),
        ),
        (translate("Map", lang), "tiles", translate("tiles", lang), translate("tiles", lang)),
        (translate("Contours", lang), "terrain", translate("terrain tiles", lang), translate("terrain tiles", lang)),
    )


def lookup_said(book: Roadbook, n: int, lang: str) -> tuple[str, str | None] | None:
    """What the summary says of the `n`th lookup, in `lang`: see api.lookup_line."""
    what, attr, found, items = lookups(lang)[n]
    return lookup_line(what, getattr(book, attr), found, items, lang)


class _Queue(logging.Handler):
    """The run's log, kept for the page: the run logs from a worker thread, and the page's timer shows it.

    Only the run's own: a preview built meanwhile, on a thread of its own, logs its steps too.
    """

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: deque[str] = deque()
        self.thread: int | None = None  # the run's, once it starts

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread:
            return
        indent = "    " if record.name == "roadbook.http" else "  " if record.levelno < logging.INFO else ""
        self.lines.append(indent + record.getMessage())


class RunPanel:
    """The Run button, the log, and what the run made: opened here, or downloaded by a device on the network.

    The log is the road book's, in English, as roadbook -v shows it; the rest is in the window's language.
    """

    def __init__(self, form: Form, language: Language, *, remote: bool = False) -> None:
        self.form = form
        self.language = language
        self.remote = remote
        self.result: Result | None = None
        self.queue = _Queue()
        self.button = ui.button(icon="directions_bike", on_click=self.make).props("unelevated size=lg")
        self.say(self.button, lambda lang: translate("Make the road book", lang)).classes("w-full").mark("make")
        self.log = ui.log(max_lines=400).classes("w-full h-48 text-xs").mark("log")
        self.log.visible = False  # until a run: an empty box is room lost, on a phone most
        ui.timer(0.2, self.show_log)
        self.summary = ui.column().classes("w-full gap-0").mark("summary")
        with ui.row().classes("gap-2") as self.actions:
            if remote:  # opening a file here would open it on this computer, not on the device
                self.html_button = ui.button(icon="view_week", on_click=self.download_html).props("outline")
                self.html_button.mark("download")
                self.pdf_button = ui.button(icon="picture_as_pdf", on_click=self.download_pdf).props("outline")
                self.pdf_button.mark("pdf")
            else:
                self.html_button = ui.button(icon="view_week", on_click=self.open_html).props("outline").mark("open")
                self.pdf_button = ui.button(icon="picture_as_pdf", on_click=self.make_pdf).props("outline").mark("pdf")
            # named once a run says what the road book holds: with a booklet, the strips alone; without, the
            # reference sheet and the map are in it too
            for button, words in ((self.html_button, self.html_name), (self.pdf_button, self.pdf_name)):
                button.bind_text_from(self, "result", backward=lambda _r, words=words: words(language.lang))
                button.bind_text_from(language, "lang", backward=words)
            with ui.row().classes("gap-2") as self.booklet:  # only when a booklet was made
                booklet, booklet_pdf = (
                    (self.download_booklet, self.download_booklet_pdf)
                    if remote
                    else (self.open_booklet, self.make_booklet_pdf)
                )
                self.say(ui.button(icon="menu_book", on_click=booklet), lambda lang: translate("Booklet", lang)).props(
                    "outline"
                ).mark("download-booklet" if remote else "open-booklet")
                self.say(
                    ui.button(icon="picture_as_pdf", on_click=booklet_pdf), lambda lang: translate("Booklet PDF", lang)
                ).props("outline").mark("booklet-pdf")
            if not remote:
                folder = ui.button(icon="folder_open", on_click=self.open_folder).props("outline").mark("folder")
                self.say(folder, lambda lang: translate("Folder", lang))
        self.actions.visible = False
        with ui.row().classes("w-full items-center no-wrap gap-2") as self.same:
            self.command = ui.label().classes("font-mono text-xs break-all").mark("command")
            copy = ui.button(icon="content_copy", on_click=self.copy).props("flat dense round")
            tooltip(copy, language, lambda lang: translate("Copy the same command for the terminal", lang)).mark("copy")
        self.same.visible = False

    def say[T: TextElement](self, element: T, words: Callable[[str], str]) -> T:
        """`element`'s text, in the window's language."""
        return element.bind_text_from(self.language, "lang", backward=words)

    def html_name(self, lang: str) -> str:
        if self.result is not None and self.result.booklet is None:
            return translate("Road book", lang)
        return translate("Strips", lang)

    def pdf_name(self, lang: str) -> str:
        if self.result is not None and self.result.booklet is None:
            return translate("Road book PDF", lang)
        return translate("Strips PDF", lang)

    def show_log(self) -> None:
        while self.queue.lines:
            self.log.push(self.queue.lines.popleft())

    async def make(self) -> None:
        """Run on a worker thread (run.io_bound), so the window stays alive through the online lookups."""
        gpx = Path(self.form.gpx)
        if not self.form.gpx or not await run.io_bound(gpx.is_file):
            ui.notify(translate("Pick a GPX file first", self.language.lang), type="warning")
            return
        self.button.disable()
        self.log.clear()
        self.log.visible = True
        logger = logging.getLogger("roadbook")
        level = logger.level
        logger.setLevel(logging.INFO)  # the steps, as roadbook -v shows them
        logger.addHandler(self.queue)
        try:
            out = Path(self.form.out) if self.form.out else None
            result = await run.io_bound(self._run, gpx, self.form.settings(), out, pdf=self.form.pdf)
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

    def _run(self, gpx: Path, cfg: dict[str, Any], out: Path | None, *, pdf: bool) -> Result:
        """The run, on its worker thread: its log is this thread's."""
        self.queue.thread = threading.get_ident()
        return make_road_book(gpx, cfg, out=out, pdf=pdf)

    def done(self, result: Result) -> None:
        self.result = result
        book = result.book
        lang = self.language.lang
        self.summary.clear()
        with self.summary:
            self.say(
                ui.label(),
                lambda lang: translate(
                    "{title}: {km} km, +{gain} m / -{loss} m",
                    lang,
                    title=book.title,
                    km=f"{book.length_km:.1f}",
                    gain=f"{book.gain_m:.0f}",
                    loss=f"{book.loss_m:.0f}",
                ),
            )
            self.say(
                ui.label(),
                lambda lang: translate(
                    "{kept} of {total} POIs, in {stops} stops; {climbs} climbs",
                    lang,
                    kept=book.poi_kept,
                    total=book.poi_total,
                    stops=len(book.stops),
                    climbs=len(book.climbs),
                ),
            )
            for n in range(len(lookups(lang))):
                if said := lookup_said(book, n, lang):  # in any language, as the report is the same
                    self.say(ui.label(), lambda lang, n=n: (lookup_said(book, n, lang) or ("",))[0])
                    if said[1]:
                        ui.notify(said[1], type="warning", multi_line=True)
            self.say(ui.label(), lambda lang: translate("Wrote {file}", lang, file=result.html)).classes(
                "text-positive"
            ).mark("wrote")
            if booklet := result.booklet:
                self.say(ui.label(), lambda lang: translate("Wrote {file}", lang, file=booklet)).classes(
                    "text-positive"
                ).mark("wrote-booklet")
        for warning in result.warnings:
            ui.notify(warning, type="warning", multi_line=True)
        if result.pdf_error:
            ui.notify(
                translate("PDF not written: {error}", lang, error=result.pdf_error), type="negative", multi_line=True
            )
        self.actions.visible = True
        self.booklet.visible = result.booklet is not None
        self.command.set_text(self.form.command())
        self.same.visible = True

    async def open_html(self) -> None:
        if self.result:
            await run.io_bound(webbrowser.open, self.result.html.as_uri())

    async def open_booklet(self) -> None:
        if self.result and self.result.booklet:
            await run.io_bound(webbrowser.open, self.result.booklet.as_uri())

    async def make_pdf(self) -> None:
        """Print the road book to a PDF next to it, with Edge or Chrome, and open it."""
        if pdf := await self.pdf():
            await run.io_bound(webbrowser.open, pdf.as_uri())

    async def make_booklet_pdf(self) -> None:
        """Print the booklet to a PDF next to it, imposed to fold, and open it."""
        if pdf := await self.pdf(booklet=True):
            await run.io_bound(webbrowser.open, pdf.as_uri())

    async def pdf(self, *, booklet: bool = False) -> Path | None:
        """The PDF next to the road book, or its booklet, printed with Edge or Chrome unless the run already made it."""
        if not self.result:
            return None
        html, made = (self.result.booklet, self.result.booklet_pdf) if booklet else (self.result.html, self.result.pdf)
        if html is None:
            return None
        if made is not None:
            return made
        pdf = html.with_suffix(".pdf")
        try:
            await run.io_bound(html_to_pdf, html, pdf)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:  # no browser, or it failed
            message = translate("PDF not written: {error}", self.language.lang, error=exc)
            ui.notify(message, type="negative", multi_line=True)
            return None
        return pdf

    async def download_html(self) -> None:
        if self.result:
            await _download(self.result.html, "text/html")

    async def download_pdf(self) -> None:
        if pdf := await self.pdf():
            await _download(pdf, "application/pdf")

    async def download_booklet(self) -> None:
        if self.result and self.result.booklet:
            await _download(self.result.booklet, "text/html")

    async def download_booklet_pdf(self) -> None:
        if pdf := await self.pdf(booklet=True):
            await _download(pdf, "application/pdf")

    async def open_folder(self) -> None:
        if self.result:
            await run.io_bound(webbrowser.open, self.result.html.parent.as_uri())

    def copy(self) -> None:
        ui.clipboard.write(self.command.text)  # not awaited: a plain function in nicegui 3.18
        ui.notify(translate("Command copied", self.language.lang))


async def _download(file: Path, media_type: str) -> None:
    """Hand a file to the device: read off the event loop, then sent as the download's content."""
    data = await run.io_bound(file.read_bytes)
    if data is not None:  # None: the app is shutting down
        ui.download.content(data, file.name, media_type)
