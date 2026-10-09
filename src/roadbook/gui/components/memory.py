"""The last settings: restored as the window opens, saved as they change, reset, or saved as a file to share."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import app, run, ui

from roadbook.gui.models import memory

if TYPE_CHECKING:
    from collections.abc import Callable

    from roadbook.gui.models.form import Form

SHARED = "roadbook.toml"  # the name offered for a file to share


class Memory:
    """The settings menu, and the timers that restore the last settings once, then save them whenever they change."""

    def __init__(self, form: Form, on_apply: Callable[[], Any]) -> None:
        self.form = form
        self.on_apply = on_apply  # the parts the bindings don't reach, rebuilt: the breaks and checkpoints
        self.file = memory.path()
        self.saved: str | None = None  # what the file holds; None until restored, so the defaults never overwrite it
        button = ui.button(icon="settings").props("flat round color=white").tooltip("Settings").mark("settings-menu")
        with button, ui.menu():
            ui.menu_item("Reset to defaults", on_click=self.reset).mark("reset")
            ui.menu_item("Save as config file…", on_click=self.save_as).mark("save-as")
        ui.timer(0, self.restore, once=True)  # once the page is up, so a notice can show
        ui.timer(1.0, self.save)

    async def restore(self) -> None:
        try:
            cfg = await run.io_bound(memory.read, self.file)
        except (ValueError, OSError) as exc:
            ui.notify(f"Your last settings are left out, as {self.file} can't be read: {exc}", type="warning")
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
            ui.notify(f"Settings not remembered: {exc}", type="warning")

    def reset(self) -> None:
        self.form.apply(self.form.defaults)
        self.on_apply()
        ui.notify("Settings back to the defaults")

    async def save_as(self) -> None:
        """The settings as a --config file to share: saved where the system's dialog says in a window, else
        downloaded."""
        text = self.form.toml()
        window: Any = app.native.main_window  # Any: nicegui defines its WindowProxy twice, which confuses ty
        if window is None:
            ui.download.content(text.encode(), SHARED, "application/toml")
            return
        import webview  # noqa: PLC0415  there in a window only, with nicegui's native extra

        picked = await window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=SHARED, file_types=("TOML files (*.toml)", "All files (*.*)")
        )
        if not picked:
            return
        file = Path(picked if isinstance(picked, str) else picked[0])  # a path, or a tuple of one, by platform
        try:
            await run.io_bound(memory.write, file, text)
        except OSError as exc:
            ui.notify(f"Not saved: {exc}", type="negative")
            return
        ui.notify(f"Saved {file}: roadbook --config {file.name} takes the same settings")
