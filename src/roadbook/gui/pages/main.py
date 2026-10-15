"""The window: the GPX and the output on top, the common settings, the advanced ones folded away; the run, and a
preview of the road book. Two columns on a wide screen, one on a phone or a narrow window, in that order.

A device on the network (roadbook-gui --lan) gets no path on this computer, to read or to write: it uploads the GPX,
and downloads the road book.

Its words are in the window's language, switched in the header (components.language), bound to it.
"""

from __future__ import annotations

import ipaddress
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import app, run, ui
from platformdirs import user_downloads_path

from roadbook import settings as st
from roadbook.gui.components.fields import Condition, label, setting_field
from roadbook.gui.components.language import LanguageSwitch
from roadbook.gui.components.lists import Rows, categories
from roadbook.gui.components.memory import Memory
from roadbook.gui.components.preview import PreviewPanel
from roadbook.gui.components.run_panel import RunPanel
from roadbook.gui.components.words import tooltip
from roadbook.gui.models import memory
from roadbook.gui.models.form import Form
from roadbook.gui.models.language import Language
from roadbook.i18n import translate

if TYPE_CHECKING:
    from nicegui.events import UploadEventArguments

COMMON = (
    st.LAYOUT,
    st.PAGE,
    st.LANGUAGE,
    st.BOOKLET,
    st.CHECKPOINT_EVERY,
    st.TOWNS,
    st.HOURS,
    st.DATE,
    st.START,
    st.SPEED,
)
ADVANCED = (
    st.WIDTH,
    st.LENGTH,
    st.GAP,
    st.MAX_SPAN,
    st.MIN_CLIMB,
    st.EMOJI_LINES,
    st.DETAILS,
    st.MARGIN,
    st.DESCENT,
    st.CLIMB,
    st.SUMMIT_ROWS,
    st.CLIMB_NAMES,
    st.CLIMB_PAGES,
    st.CLIMB_PAGES_FROM,
    st.MAP,
    st.MAP_STYLE,
    st.MAP_CONTOURS,
    st.WATER_GAP_H,
    st.WATER_GAP_KM,
    st.FOOD_GAP_H,
    st.FOOD_GAP_KM,
)


async def page(*, lan: bool = False) -> None:
    """The page, built once per window: everything after that goes through bindings and handlers."""
    form = Form()  # local to this page: each window its own
    remote = lan and not is_here(ui.context.client.ip)
    try:
        remembered = await run.io_bound(memory.read_language, memory.language_path())
    except (ValueError, OSError):  # a broken file: English, until a switch writes it whole again
        remembered = ""
    language = Language(remembered or "en")
    ui.page_title("gpx2roadbook")
    with ui.header().classes("items-center") as header:
        ui.label("gpx2roadbook").classes("text-lg font-bold")
        ui.label(version("gpx2roadbook")).classes("text-xs opacity-70")
    # a grid, not a row: one column below Tailwind's lg (1024 px), two above; min-w-0 lets a long path shrink
    with ui.element("div").classes("w-full grid grid-cols-1 lg:grid-cols-2 items-start gap-6 p-2"):
        with ui.column().classes("w-full min-w-0 gap-2"):
            _files(form, language, remote=remote)
            lists = _settings(form, language)
        with ui.column().classes("w-full min-w-0 gap-2"):
            RunPanel(form, language, remote=remote)
            PreviewPanel(form, language)
    with header:
        ui.space()
        LanguageSwitch(language, form, first=lambda: remembered is None and settings.fresh)
        settings = Memory(form, language, on_apply=lambda: [rows.show.refresh() for rows in lists])


def is_here(ip: str) -> bool:
    """Whether a browser tab runs on this computer: its address is a loopback one."""
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:  # no address, or a name
        return ip == "localhost"


def _files(form: Form, language: Language, *, remote: bool) -> None:
    """The GPX, picked with the system's own dialog in a window, or uploaded in a browser tab; then the output."""
    if remote:  # the upload alone, and its name
        name = ui.label().classes("text-sm").mark("gpx-name")
        name.bind_text_from(form, "gpx", backward=lambda gpx: _gpx_name(gpx, language.lang))
        name.bind_text_from(language, "lang", backward=lambda lang: _gpx_name(form.gpx, lang))
    else:
        with ui.row().classes("w-full items-center no-wrap gap-2"):
            gpx = ui.input(on_change=lambda e: _gpx_typed(form, e.value)).bind_value_from(form, "gpx")
            label(gpx.classes("grow min-w-0"), language, lambda lang: translate("GPX file", lang)).mark("gpx")
            tooltip(
                gpx,
                language,
                lambda lang: translate(
                    "The route, with its POIs: see the README for getting one from onroutemap.de", lang
                ),
            )
            if app.native.main_window is not None:
                browse = ui.button(icon="folder_open", on_click=lambda: _browse(form, language)).props("outline")
                browse.bind_text_from(language, "lang", backward=lambda lang: translate("Browse", lang)).mark("browse")
    if app.native.main_window is None:  # a browser tab can't hand over a path: upload the file
        upload = ui.upload(
            auto_upload=True,
            max_files=1,
            on_upload=lambda e: _uploaded(form, e, remote=remote),
        ).props('accept=".gpx" flat bordered')
        if remote:
            upload.bind_label_from(language, "lang", backward=lambda lang: translate("Drop a GPX here", lang))
        else:
            upload.bind_label_from(language, "lang", backward=lambda lang: translate("or drop a GPX here", lang))
        upload.classes("w-full").mark("upload")
    if not remote:
        out = ui.input().bind_value(form, "out")
        label(out.classes("w-full"), language, lambda lang: translate(st.OUT.text(form.cfg), lang)).mark("out")
        tooltip(out, language, lambda lang: translate(st.OUT.help, lang))
    with ui.row().classes("gap-4"):
        for s, key in ((st.PDF, "pdf"), (st.REFRESH, "refresh")):
            switch = ui.switch().bind_value(form, key).mark("pdf-switch" if s is st.PDF else "refresh")
            label(switch, language, lambda lang, s=s: translate(s.text(form.cfg), lang))
            tooltip(switch, language, lambda lang, s=s: translate(s.help, lang))


def _gpx_name(gpx: str, lang: str) -> str:
    return Path(gpx).name or translate("No GPX yet", lang)


def _settings(form: Form, language: Language) -> list[Rows]:
    """The common settings, then the lists, then the advanced settings folded away; the rows, to rebuild."""
    with ui.card().classes("w-full"):
        for s in COMMON:
            setting_field(s, form, language)
        with ui.column().classes("w-full gap-0") as breaks:
            breaks_rows = Rows(form.breaks, st.BREAKS, lambda lang: translate("minutes", lang), language, minutes=True)
        if st.BREAKS.when is not None:  # planned breaks only count with arrival times
            breaks.bind_visibility_from(Condition(form.cfg, st.BREAKS.when), "holds")
        categories(form, language)
    with ui.expansion(icon="tune").classes("w-full").mark("advanced") as advanced:
        for s in ADVANCED:
            setting_field(s, form, language)
        checkpoints_rows = Rows(
            form.checkpoints, st.CHECKPOINTS, lambda lang: translate("label", lang), language, minutes=False
        )
    advanced.bind_text_from(language, "lang", backward=lambda lang: translate("Advanced settings", lang))
    return [breaks_rows, checkpoints_rows]


def _gpx_typed(form: Form, text: str | None) -> None:
    path = Path((text or "").strip().strip('"')).expanduser()
    if text and path.suffix.lower() == ".gpx" and str(path) != form.gpx:
        form.choose(path)


async def _browse(form: Form, language: Language) -> None:
    window: Any = app.native.main_window  # Any: nicegui defines its WindowProxy twice, which confuses ty
    if window is None:
        return
    lang = language.lang
    picked = await window.create_file_dialog(
        file_types=(translate("GPX files (*.gpx)", lang), translate("All files (*.*)", lang))
    )
    if picked:
        form.choose(Path(picked[0]))


async def _uploaded(form: Form, e: UploadEventArguments, *, remote: bool) -> None:
    """An upload has no folder of its own: it is kept in a temporary one, and the road book goes to Downloads; for a
    device on the network, next to it, to be downloaded from there."""
    path = Path(tempfile.mkdtemp(prefix="roadbook-")) / Path(e.file.name).name
    await e.file.save(path)
    form.choose(path, None if remote else user_downloads_path() / f"{path.stem}.roadbook.html")
