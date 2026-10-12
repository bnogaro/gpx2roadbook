"""The settings that are lists: POI categories as chips, planned breaks and extra checkpoints as rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from roadbook import settings as st
from roadbook.gui.components.words import tooltip
from roadbook.gui.models.form import Row
from roadbook.i18n import translate

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.events import ValueChangeEventArguments

    from roadbook.gui.models.form import Form
    from roadbook.gui.models.language import Language


def categories(form: Form, language: Language) -> None:
    """One chip per category, its emoji first, selected when it is shown; the order stays the config's."""
    order = list(form.cfg["categories"])

    def toggle(name: str, e: ValueChangeEventArguments) -> None:
        picked = set(st.CATEGORIES.get(form.cfg))
        if e.value:
            picked.add(name)
        else:
            picked.discard(name)
        st.CATEGORIES.set(form.cfg, [c for c in order if c in picked])

    ui.label().bind_text_from(
        language, "lang", backward=lambda lang: translate(st.CATEGORIES.text(form.cfg), lang)
    ).classes("text-sm text-grey-8")
    with ui.row().classes("gap-1").mark(st.CATEGORIES.flag.removeprefix("--")) as chips:
        for name, spec in form.cfg["categories"].items():
            ui.chip(
                selectable=True,
                selected=name in st.CATEGORIES.get(form.cfg),
                color="primary",
                text_color="white",
                on_selection_change=lambda e, n=name: toggle(n, e),
            ).bind_text_from(
                language, "lang", backward=lambda lang, n=name, emoji=spec["emoji"]: f"{emoji} {translate(n, lang)}"
            ).bind_selected_from(form.cfg["pois"], "enabled", backward=lambda enabled, n=name: n in enabled).props(
                "outline dense"
            ).mark(f"category-{name}")
    tooltip(chips, language, lambda lang: translate(st.CATEGORIES.help, lang))


class Rows:
    """Editable rows for a list setting: km and minutes for breaks, km and a label for checkpoints."""

    def __init__(
        self,
        rows: list[Row],
        setting: st.Setting,
        value_label: Callable[[str], str],
        language: Language,
        *,
        minutes: bool,
    ) -> None:
        self.rows = rows
        self.setting = setting
        self.value_label = value_label  # the value field's label, in a language
        self.language = language
        self.minutes = minutes  # the value is a number of minutes, else a label
        self.name = setting.flag.removeprefix("--")
        heading = ui.label().bind_text_from(language, "lang", backward=lambda lang: translate(setting.text({}), lang))
        tooltip(heading.classes("text-sm text-grey-8"), language, lambda lang: translate(setting.help, lang))
        self.show()
        ui.button(icon="add", on_click=self.add).bind_text_from(
            language, "lang", backward=lambda lang: translate("Add", lang)
        ).props("flat dense").mark(f"{self.name}-add")

    @ui.refreshable_method
    def show(self) -> None:
        for n, row in enumerate(self.rows):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.number("km", min=0, format="%.1f").bind_value(row, "km", forward=lambda v: v or 0.0).classes(
                    "w-24"
                ).mark(f"{self.name}-km-{n}")
                if self.minutes:
                    value = ui.number(min=0).bind_value(row, "value", forward=lambda v: v or 0.0).classes("w-24")
                else:
                    value = ui.input().bind_value(row, "value").classes("w-32")
                value.bind_label_from(self.language, "lang", backward=self.value_label).mark(f"{self.name}-value-{n}")
                remove = ui.button(icon="delete", on_click=lambda r=row: self.remove(r)).props("flat dense round")
                tooltip(remove, self.language, lambda lang: translate("Remove", lang)).mark(f"{self.name}-remove-{n}")

    def add(self) -> None:
        self.rows.append(Row(0.0, 0.0 if self.minutes else ""))
        self.show.refresh()

    def remove(self, row: Row) -> None:
        self.rows.remove(row)
        self.show.refresh()
