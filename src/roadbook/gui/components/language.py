"""The EN / FR switch in the header: the window's language, remembered between runs, apart from the road book's."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import run, ui

from roadbook import settings as st
from roadbook.gui.components.words import tooltip
from roadbook.gui.models import memory
from roadbook.i18n import LANGUAGES, translate

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.events import ValueChangeEventArguments

    from roadbook.gui.models.form import Form
    from roadbook.gui.models.language import Language


class LanguageSwitch:
    """Switch the window's language: its words follow at once, through their bindings, and the form keeps its values.

    The road book's language is a setting of its own (--lang), but on a first launch, with nothing remembered yet,
    switching the window to French makes the road book French too.
    """

    def __init__(self, language: Language, form: Form, *, first: Callable[[], bool]) -> None:
        self.form = form
        self.first = first  # whether this is a first launch: no window language, no settings remembered
        toggle = ui.toggle({lang: lang.upper() for lang in LANGUAGES}, value=language.lang, on_change=self.switch)
        toggle.bind_value(language, "lang").props(
            "unelevated dense color=primary toggle-color=white toggle-text-color=primary"
        ).mark("language")
        tooltip(toggle, language, lambda lang: translate("The window's language", lang))

    async def switch(self, e: ValueChangeEventArguments) -> None:
        if e.value == "fr" and self.first():
            st.LANGUAGE.set(self.form.cfg, "fr")
        try:
            await run.io_bound(memory.write, memory.language_path(), memory.language_toml(e.value))
        except OSError as exc:
            ui.notify(translate("Language not remembered: {error}", e.value, error=exc), type="warning")
