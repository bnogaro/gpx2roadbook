"""One field per setting, built from its description in roadbook.settings: label, tooltip, check, condition."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from roadbook import settings as st

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.element import Element

    from roadbook.gui.models.form import Form


class Condition:
    """Whether a setting applies, given the other answers: bound to a field's visibility, which the binding loop
    re-checks as the answers change (the start time once there is a date, the speed once there is a start…)."""

    def __init__(self, cfg: dict[str, Any], when: Callable[[dict[str, Any]], bool]) -> None:
        self.cfg = cfg
        self.when = when

    @property
    def holds(self) -> bool:
        return self.when(self.cfg)


def marker(s: st.Setting) -> str:
    """The test marker of a setting's field: its flag, without the dashes ("speed")."""
    return s.flag.removeprefix("--")


def _validation(s: st.Setting) -> Callable[[Any], str | None] | None:
    """The setting's check, the way NiceGUI takes it: None when the answer is fine, else why it isn't."""
    check = s.check
    if check is None:
        return None

    def validate(value: Any) -> str | None:  # noqa: ANN401  whatever the field holds
        verdict = check("" if value is None else str(value))
        return None if verdict is True else str(verdict)

    return validate


def _keep(default: Any, kind: type) -> Callable[[Any], Any]:  # noqa: ANN401
    """A number field emptied keeps the setting's default, rather than handing None to the run."""
    return lambda value: default if value is None else kind(value)


def setting_field(s: st.Setting, form: Form) -> Element:
    """The field for `s`, bound to the form's settings, with its tooltip and check."""
    section, label = form.cfg[s.section], s.text(form.cfg)
    if s.kind is bool:
        element: Element = ui.switch(label).bind_value(section, s.key)
    elif s.choices:
        element = ui.select(list(s.choices), label=label, new_value_mode="add-unique").bind_value(section, s.key)
    elif s is st.DATE:  # a picker cleared gives None, where the settings take "" for none
        element = ui.date_input(label).bind_value(section, s.key, forward=_blank)
    elif s is st.START:
        element = ui.time_input(label).bind_value(section, s.key, forward=_blank)
    elif s.kind in {int, float}:
        element = ui.number(label, min=0, step=1 if s.kind is int else None, validation=_validation(s))
        element.bind_value(section, s.key, forward=_keep(s.get(form.cfg), s.kind))
        if s.shown:  # 0 stands for a value worked out from the others
            element.props(f'hint="0 = automatic ({s.shown(form.cfg):g})"')
    else:
        element = ui.input(label).bind_value(section, s.key)
    if s.when is not None:
        element.bind_visibility_from(Condition(form.cfg, s.when), "holds")
    return element.classes("w-full").tooltip(s.help).mark(marker(s))


def _blank(value: str | None) -> str:
    return value or ""
