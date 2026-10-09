"""See the road book while setting it: built again a moment after the last change, offline, from the cache only."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import run, ui

from roadbook import settings as st
from roadbook.api import GpxError, preview
from roadbook.gui.components.run_panel import LOOKUPS

if TYPE_CHECKING:
    from nicegui.element import Element
    from nicegui.events import ValueChangeEventArguments

    from roadbook.api import Preview
    from roadbook.gui.models.form import Form

WATCH_S = 0.25  # how often the settings are looked at
DEBOUNCE_S = 0.6  # how long they must stand still before the preview is built again
ZOOMS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
HIDE = {"strips": ".details", "sheet": ".sheet"}  # each tab hides the other one's part of the road book


def framed(html: str, tab: str, zoom: float) -> str:
    """The road book as the preview shows it: one tab's part, zoomed; at 1, its millimetres are the screen's."""
    style = f"<style>{HIDE[tab]} {{ display: none; }} html {{ zoom: {zoom:g}; }}</style>"
    return html.replace("</head>", f"{style}</head>", 1)


def notes(shot: Preview) -> list[str]:
    """What the preview leaves out, and why: the lookups not cached yet, then what the build warned of."""
    said = [
        f"{what}: {r.unasked} {items} not looked up yet. The preview only uses names and hours already found;"
        " making the road book looks them up."
        for what, attr, _, items in LOOKUPS
        if (r := getattr(shot.book, attr)) is not None and r.unasked
    ]
    return said + shot.warnings


class PreviewPanel:
    """Tabs for the strips and the reference sheet, the zoom, and the road book at its true size."""

    def __init__(self, form: Form) -> None:
        self.form = form
        self.shot: Preview | None = None
        self.error = ""  # why there is no preview, when the GPX can't make one
        self.seen = ""  # the settings last looked at...
        self.since = 0.0  # ...since when
        self.shown: str | None = None  # the settings the preview was built from
        self.tab = "strips"
        self.zoom = 1.0
        self.frame: Element | None = None
        with ui.row().classes("w-full items-center no-wrap gap-1"):
            with ui.tabs(on_change=self.pick).classes("grow") as tabs:
                ui.tab("strips", "Strips", icon="view_week").mark("preview-strips")
                ui.tab("sheet", "Reference sheet", icon="list_alt").mark("preview-sheet")
            tabs.value = self.tab
            ui.button(icon="zoom_out", on_click=lambda: self.zoom_by(-1)).props("flat dense round").tooltip(
                "Zoom out"
            ).mark("zoom-out")
            ui.label().bind_text_from(self, "zoom", backward=lambda z: f"{z:.0%}").classes("w-12 text-center").mark(
                "zoom"
            )
            ui.button(icon="zoom_in", on_click=lambda: self.zoom_by(1)).props("flat dense round").tooltip(
                "Zoom in"
            ).mark("zoom-in")
        self.view()
        ui.timer(WATCH_S, self.watch)

    @ui.refreshable_method
    def view(self) -> None:
        """The preview itself, built again with each new one."""
        self.frame = None
        if self.shot is None:
            text = self.error or "Pick a GPX file to see its road book here."
            ui.label(text).classes("text-grey-7 text-sm").mark("preview-empty")
            return
        for note in notes(self.shot):
            ui.label(note).classes("text-orange-800 text-xs").mark("preview-note")
        # no sandbox, though the road book runs no script: Chromium then leaves a zoomed reference sheet blank
        self.frame = ui.element("iframe").classes("w-full h-[75vh] border").mark("preview-frame")
        self.reframe()

    def reframe(self) -> None:
        """Show the tab and zoom picked, without building again."""
        if self.frame is None or self.shot is None:
            return
        if self.tab == "sheet" and '<div class="details">' not in self.shot.html:
            html = f"<p style='font-family: sans-serif'>No reference sheet: turn on “{st.DETAILS.text(self.form.cfg)}”"
            html += " in the advanced settings.</p>"
        else:
            html = framed(self.shot.html, self.tab, self.zoom)
        self.frame.props["srcdoc"] = html

    def pick(self, e: ValueChangeEventArguments) -> None:
        self.tab = str(e.value)
        self.reframe()

    def zoom_by(self, step: int) -> None:
        i = min(max(ZOOMS.index(self.zoom) + step, 0), len(ZOOMS) - 1)
        self.zoom = ZOOMS[i]
        self.reframe()

    async def watch(self) -> None:
        """Note when the settings change, and build the preview again once they have stood still for DEBOUNCE_S."""
        cfg = self.form.settings()
        key = json.dumps([self.form.gpx, cfg], sort_keys=True, default=str)
        now = time.monotonic()
        if key != self.seen:
            self.seen, self.since = key, now
        elif key != self.shown and now - self.since >= DEBOUNCE_S:
            self.shown = key
            await self.build(cfg)

    async def build(self, cfg: dict[str, Any]) -> None:
        """Build and render on a worker thread (run.io_bound): a long route takes a moment."""
        gpx = Path(self.form.gpx)
        shot, error = None, ""
        if self.form.gpx and await run.io_bound(gpx.is_file):
            try:
                shot = await run.io_bound(preview, gpx, cfg)  # None: the app is shutting down
            except GpxError as exc:
                error = str(exc)
            except (ValueError, TypeError, KeyError) as exc:  # a setting half typed: wait for the next
                error = f"No preview with these settings: {exc}"
        self.shot, self.error = shot, error
        self.view.refresh()
