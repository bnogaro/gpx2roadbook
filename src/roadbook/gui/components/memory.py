"""The last settings: restored as the window opens, saved as they change, reset, or saved as a file to share."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import app, run, ui

from roadbook.gui.components.words import tooltip
from roadbook.gui.models import memory
from roadbook.i18n import translate

if TYPE_CHECKING:
    from collections.abc import Callable

    from roadbook.gui.models.form import Form
    from roadbook.gui.models.language import Language

SHARED = "roadbook.toml"  # the name offered for a file to share


class Memory:
    """The settings menu, and the timers that restore the last settings once, then save them whenever they change."""

    def __init__(self, form: Form, language: Language, on_apply: Callable[[], Any]) -> None:
        self.form = form
        self.language = language
        self.on_apply = on_apply  # the parts the bindings don't reach, rebuilt: the breaks and checkpoints
        self.file = memory.path()
        self.saved: str | None = None  # what the file holds; None until restored, so the defaults never overwrite it
        self.fresh = False  # whether there were no settings to restore: no file yet, as on a first launch
        button = ui.button(icon="settings").props("flat round color=white").mark("settings-menu")
        tooltip(button, language, lambda lang: translate("Settings", lang))
        with button, ui.menu():
            with ui.menu_item(on_click=self.reset).mark("reset"):  # its text in a section, as ui.menu_item("…") does
                ui.item_section().bind_text_from(
                    language, "lang", backward=lambda lang: translate("Reset to defaults", lang)
                )
            with ui.menu_item(on_click=self.save_as).mark("save-as"):
                ui.item_section().bind_text_from(
                    language, "lang", backward=lambda lang: translate("Save as config file…", lang)
                )
        ui.timer(0, self.restore, once=True)  # once the page is up, so a notice can show
        ui.timer(1.0, self.save)

    async def restore(self) -> None:
        try:
            cfg = await run.io_bound(memory.read, self.file)
            self.fresh = cfg is None
        except (ValueError, OSError) as exc:
            message = translate(
                "Your last settings are left out, as {file} can't be read: {error}",
                self.language.lang,
                file=self.file,
                error=exc,
            )
            ui.notify(message, type="warning")
            cfg = None
        if cfg is not None:
            self.form.apply(cfg)
            self.on_apply()
        self.saved = self.form.toml()

    async def save(self) -> None:
        """Write the settings, only when they changed, off the event loop."""
        text = self.form.toml()
        if self.saved is None or text == self.saved:
            return
        self.saved = text  # not again if it fails: one notice is enough
        try:
            await run.io_bound(memory.write, self.file, text)
        except OSError as exc:
            ui.notify(translate("Settings not remembered: {error}", self.language.lang, error=exc), type="warning")

    def reset(self) -> None:
        self.form.apply(self.form.defaults)
        self.on_apply()
        ui.notify(translate("Settings back to the defaults", self.language.lang))

    async def save_as(self) -> None:
        """The settings as a --config file to share: saved where the system's dialog says in a window, else
        downloaded."""
        text = self.form.toml()
        window: Any = app.native.main_window  # Any: nicegui defines its WindowProxy twice, which confuses ty
        if window is None:
            ui.download.content(text.encode(), SHARED, "application/toml")
            return
        import webview  # noqa: PLC0415  there in a window only, with nicegui's native extra

        lang = self.language.lang
        picked = await window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=SHARED,
            file_types=(translate("TOML files (*.toml)", lang), translate("All files (*.*)", lang)),
        )
        if not picked:
            return
        file = Path(picked if isinstance(picked, str) else picked[0])  # a path, or a tuple of one, by platform
        try:
            await run.io_bound(memory.write, file, text)
        except OSError as exc:
            ui.notify(translate("Not saved: {error}", self.language.lang, error=exc), type="negative")
            return
        ui.notify(
            translate(
                "Saved {file}: roadbook --config {name} takes the same settings",
                self.language.lang,
                file=file,
                name=file.name,
            )
        )
