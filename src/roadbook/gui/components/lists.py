"""The settings that are lists: POI categories as chips, planned breaks and extra checkpoints as rows."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from roadbook import settings as st
from roadbook.gui.models.form import Row

if TYPE_CHECKING:
    from nicegui.events import ValueChangeEventArguments

    from roadbook.gui.models.form import Form


def categories(form: Form) -> None:
    """One chip per category, its emoji first, selected when it is shown; the order stays the config's."""
    order = list(form.cfg["categories"])

    def toggle(name: str, e: ValueChangeEventArguments) -> None:
        picked = set(st.CATEGORIES.get(form.cfg))
        if e.value:
            picked.add(name)
        else:
            picked.discard(name)
        st.CATEGORIES.set(form.cfg, [c for c in order if c in picked])

    ui.label(st.CATEGORIES.text(form.cfg)).classes("text-sm text-grey-8")
    with ui.row().classes("gap-1").tooltip(st.CATEGORIES.help).mark(st.CATEGORIES.flag.removeprefix("--")):
        for name, spec in form.cfg["categories"].items():
            ui.chip(
                f"{spec['emoji']} {name}",
                selectable=True,
                selected=name in st.CATEGORIES.get(form.cfg),
                color="primary",
                text_color="white",
                on_selection_change=lambda e, n=name: toggle(n, e),
            ).bind_selected_from(form.cfg["pois"], "enabled", backward=lambda enabled, n=name: n in enabled).props(
                "outline dense"
            ).mark(f"category-{name}")


class Rows:
    """Editable rows for a list setting: km and minutes for breaks, km and a label for checkpoints."""

    def __init__(self, rows: list[Row], setting: st.Setting, value_label: str, *, minutes: bool) -> None:
        self.rows = rows
        self.setting = setting
        self.value_label = value_label
        self.minutes = minutes  # the value is a number of minutes, else a label
        self.name = setting.flag.removeprefix("--")
        ui.label(setting.text({})).classes("text-sm text-grey-8").tooltip(setting.help)
        self.show()
        ui.button("Add", icon="add", on_click=self.add).props("flat dense").mark(f"{self.name}-add")

    @ui.refreshable_method
    def show(self) -> None:
        for n, row in enumerate(self.rows):
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.number("km", min=0, format="%.1f").bind_value(row, "km", forward=lambda v: v or 0.0).classes(
                    "w-24"
                ).mark(f"{self.name}-km-{n}")
                if self.minutes:
                    ui.number(self.value_label, min=0).bind_value(row, "value", forward=lambda v: v or 0.0).classes(
                        "w-24"
                    ).mark(f"{self.name}-value-{n}")
                else:
                    ui.input(self.value_label).bind_value(row, "value").classes("w-32").mark(f"{self.name}-value-{n}")
                ui.button(icon="delete", on_click=lambda r=row: self.remove(r)).props("flat dense round").tooltip(
                    "Remove"
                ).mark(f"{self.name}-remove-{n}")

    def add(self) -> None:
        self.rows.append(Row(0.0, 0.0 if self.minutes else ""))
        self.show.refresh()

    def remove(self, row: Row) -> None:
        self.rows.remove(row)
        self.show.refresh()
