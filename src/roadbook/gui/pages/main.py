"""The window: the GPX and the output on top, the common settings, the advanced ones folded away; the run, and a
preview of the road book."""

from __future__ import annotations

import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import app, ui
from platformdirs import user_downloads_path

from roadbook import settings as st
from roadbook.gui.components.fields import Condition, setting_field
from roadbook.gui.components.lists import Rows, categories
from roadbook.gui.components.memory import Memory
from roadbook.gui.components.preview import PreviewPanel
from roadbook.gui.components.run_panel import RunPanel
from roadbook.gui.models.form import Form

if TYPE_CHECKING:
    from nicegui.events import UploadEventArguments

COMMON = (st.LAYOUT, st.PAGE, st.CHECKPOINT_EVERY, st.TOWNS, st.HOURS, st.DATE, st.START, st.SPEED)
ADVANCED = (
    st.WIDTH,
    st.LENGTH,
    st.GAP,
    st.MAX_SPAN,
    st.MIN_CLIMB,
    st.EMOJI_LINES,
    st.DETAILS,
    st.MARGIN,
    st.CLIMB,
    st.SUMMIT_ROWS,
    st.CLIMB_NAMES,
)


def page() -> None:
    """The page, built once per window: everything after that goes through bindings and handlers."""
    form = Form()  # local to this page: each window its own
    ui.page_title("gpx2roadbook")
    with ui.header().classes("items-center") as header:
        ui.label("gpx2roadbook").classes("text-lg font-bold")
        ui.label(version("gpx2roadbook")).classes("text-xs opacity-70")
    with ui.row().classes("w-full no-wrap items-start gap-6 p-2"):
        with ui.column().classes("w-1/2 gap-2"):
            _files(form)
            lists = _settings(form)
        with ui.column().classes("w-1/2 gap-2"):
            RunPanel(form)
            PreviewPanel(form)
    with header:
        ui.space()
        Memory(form, on_apply=lambda: [rows.show.refresh() for rows in lists])


def _files(form: Form) -> None:
    """The GPX, picked with the system's own dialog in a window, or uploaded in a browser tab; then the output."""
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        ui.input("GPX file", on_change=lambda e: _gpx_typed(form, e.value)).bind_value_from(form, "gpx").classes(
            "grow"
        ).tooltip("The route, with its POIs: see the README for getting one from onroutemap.de").mark("gpx")
        if app.native.main_window is not None:
            ui.button("Browse", icon="folder_open", on_click=lambda: _browse(form)).props("outline").mark("browse")
    if app.native.main_window is None:  # a browser tab can't hand over a path: upload the file
        ui.upload(
            label="or drop a GPX here", auto_upload=True, max_files=1, on_upload=lambda e: _uploaded(form, e)
        ).props('accept=".gpx" flat bordered').classes("w-full").mark("upload")
    ui.input(st.OUT.text(form.cfg)).bind_value(form, "out").classes("w-full").tooltip(st.OUT.help).mark("out")
    with ui.row().classes("gap-4"):
        ui.switch(st.PDF.text(form.cfg)).bind_value(form, "pdf").tooltip(st.PDF.help).mark("pdf-switch")
        ui.switch(st.REFRESH.text(form.cfg)).bind_value(form, "refresh").tooltip(st.REFRESH.help).mark("refresh")


def _settings(form: Form) -> list[Rows]:
    """The common settings, then the lists, then the advanced settings folded away; the rows, to rebuild."""
    with ui.card().classes("w-full"):
        for s in COMMON:
            setting_field(s, form)
        with ui.column().classes("w-full gap-0") as breaks:
            breaks_rows = Rows(form.breaks, st.BREAKS, "minutes", minutes=True)
        if st.BREAKS.when is not None:  # planned breaks only count with arrival times
            breaks.bind_visibility_from(Condition(form.cfg, st.BREAKS.when), "holds")
        categories(form)
    with ui.expansion("Advanced settings", icon="tune").classes("w-full").mark("advanced"):
        for s in ADVANCED:
            setting_field(s, form)
        checkpoints_rows = Rows(form.checkpoints, st.CHECKPOINTS, "label", minutes=False)
    return [breaks_rows, checkpoints_rows]


def _gpx_typed(form: Form, text: str | None) -> None:
    path = Path((text or "").strip().strip('"')).expanduser()
    if text and path.suffix.lower() == ".gpx" and str(path) != form.gpx:
        form.choose(path)


async def _browse(form: Form) -> None:
    window: Any = app.native.main_window  # Any: nicegui defines its WindowProxy twice, which confuses ty
    if window is None:
        return
    picked = await window.create_file_dialog(file_types=("GPX files (*.gpx)", "All files (*.*)"))
    if picked:
        form.choose(Path(picked[0]))


async def _uploaded(form: Form, e: UploadEventArguments) -> None:
    """An upload has no folder of its own: it is kept in a temporary one, and the road book goes to Downloads."""
    path = Path(tempfile.mkdtemp(prefix="roadbook-")) / Path(e.file.name).name
    await e.file.save(path)
    form.choose(path, user_downloads_path() / f"{path.stem}.roadbook.html")
