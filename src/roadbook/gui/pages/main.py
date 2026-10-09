"""The window: the GPX and the output on top, the common settings, the advanced ones folded away; the run, and a
preview of the road book. Two columns on a wide screen, one on a phone or a narrow window, in that order.

A device on the network (roadbook-gui --lan) gets no path on this computer, to read or to write: it uploads the GPX,
and downloads the road book.
"""

from __future__ import annotations

import ipaddress
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


def page(*, lan: bool = False) -> None:
    """The page, built once per window: everything after that goes through bindings and handlers."""
    form = Form()  # local to this page: each window its own
    remote = lan and not is_here(ui.context.client.ip)
    ui.page_title("gpx2roadbook")
    with ui.header().classes("items-center") as header:
        ui.label("gpx2roadbook").classes("text-lg font-bold")
        ui.label(version("gpx2roadbook")).classes("text-xs opacity-70")
    # a grid, not a row: one column below Tailwind's lg (1024 px), two above; min-w-0 lets a long path shrink
    with ui.element("div").classes("w-full grid grid-cols-1 lg:grid-cols-2 items-start gap-6 p-2"):
        with ui.column().classes("w-full min-w-0 gap-2"):
            _files(form, remote=remote)
            lists = _settings(form)
        with ui.column().classes("w-full min-w-0 gap-2"):
            RunPanel(form, remote=remote)
            PreviewPanel(form)
    with header:
        ui.space()
        Memory(form, on_apply=lambda: [rows.show.refresh() for rows in lists])


def is_here(ip: str) -> bool:
    """Whether a browser tab runs on this computer: its address is a loopback one."""
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:  # no address, or a name
        return ip == "localhost"


def _files(form: Form, *, remote: bool) -> None:
    """The GPX, picked with the system's own dialog in a window, or uploaded in a browser tab; then the output."""
    if remote:  # the upload alone, and its name
        ui.label().bind_text_from(form, "gpx", backward=lambda gpx: Path(gpx).name or "No GPX yet").classes(
            "text-sm"
        ).mark("gpx-name")
    else:
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            ui.input("GPX file", on_change=lambda e: _gpx_typed(form, e.value)).bind_value_from(form, "gpx").classes(
                "grow min-w-0"
            ).tooltip("The route, with its POIs: see the README for getting one from onroutemap.de").mark("gpx")
            if app.native.main_window is not None:
                ui.button("Browse", icon="folder_open", on_click=lambda: _browse(form)).props("outline").mark("browse")
    if app.native.main_window is None:  # a browser tab can't hand over a path: upload the file
        ui.upload(
            label="Drop a GPX here" if remote else "or drop a GPX here",
            auto_upload=True,
            max_files=1,
            on_upload=lambda e: _uploaded(form, e, remote=remote),
        ).props('accept=".gpx" flat bordered').classes("w-full").mark("upload")
    if not remote:
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


async def _uploaded(form: Form, e: UploadEventArguments, *, remote: bool) -> None:
    """An upload has no folder of its own: it is kept in a temporary one, and the road book goes to Downloads; for a
    device on the network, next to it, to be downloaded from there."""
    path = Path(tempfile.mkdtemp(prefix="roadbook-")) / Path(e.file.name).name
    await e.file.save(path)
    form.choose(path, None if remote else user_downloads_path() / f"{path.stem}.roadbook.html")
