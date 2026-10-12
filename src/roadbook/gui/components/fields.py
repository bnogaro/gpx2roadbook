"""One field per setting, built from its description in roadbook.settings: label, tooltip, check, condition.

settings.py words them in English, for the CLI too; the window translates them into its own language
(locales/fr/settings.toml), the choices' labels as well, never their values ("strip" reads "bande", and stays "strip").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import binding, ui

from roadbook import settings as st
from roadbook.gui.components.words import tooltip
from roadbook.i18n import translate

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.element import Element

    from roadbook.gui.models.form import Form
    from roadbook.gui.models.language import Language


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


def choices(s: st.Setting, value: Any, lang: str) -> dict[Any, str]:  # noqa: ANN401  whatever the config holds
    """The setting's choices, by value, as the window shows them; a value typed in besides, as it is."""
    shown = {choice: translate(choice, lang) for choice in s.choices}
    return shown if value in shown or value in {None, ""} else {**shown, value: str(value)}


def _validation(s: st.Setting, language: Language) -> Callable[[Any], str | None] | None:
    """The setting's check, the way NiceGUI takes it: None when the answer is fine, else why it isn't."""
    check = s.check
    if check is None:
        return None

    def validate(value: Any) -> str | None:  # noqa: ANN401  whatever the field holds
        verdict = check("" if value is None else str(value))
        return None if verdict is True else translate(str(verdict), language.lang)

    return validate


def _keep(default: Any, kind: type) -> Callable[[Any], Any]:  # noqa: ANN401
    """A number field emptied keeps the setting's default, rather than handing None to the run."""
    return lambda value: default if value is None else kind(value)


def setting_field(s: st.Setting, form: Form, language: Language) -> Element:
    """The field for `s`, bound to the form's settings, with its tooltip and check, in the window's language."""
    section = form.cfg[s.section]
    if s.kind is bool:
        element: Element = ui.switch().bind_value(section, s.key)
    elif s.choices:
        select = ui.select(choices(s, section[s.key], language.lang), new_value_mode="add-unique")
        element = select.bind_value(section, s.key)
        language.on_switch(lambda: select.set_options(choices(s, select.value, language.lang)))
    elif s is st.DATE:  # a picker cleared gives None, where the settings take "" for none
        element = ui.date_input().bind_value(section, s.key, forward=_blank)
    elif s is st.START:
        element = ui.time_input().bind_value(section, s.key, forward=_blank)
    elif s.kind in {int, float}:
        element = ui.number(min=0, step=1 if s.kind is int else None, validation=_validation(s, language))
        element.bind_value(section, s.key, forward=_keep(s.get(form.cfg), s.kind))
        if s.shown:  # 0 stands for a value worked out from the others
            value = f"{s.shown(form.cfg):g}"
            hint = lambda lang: translate("0 = automatic ({value})", lang, value=value)  # noqa: E731
            binding.bind_from(element.props, "hint", language, "lang", backward=hint)
    else:
        element = ui.input().bind_value(section, s.key)
    label(element, language, lambda lang: translate(s.text(form.cfg), lang))
    if s.when is not None:
        element.bind_visibility_from(Condition(form.cfg, s.when), "holds")
    return tooltip(element.classes("w-full"), language, lambda lang: translate(s.help, lang)).mark(marker(s))


def label[E: Element](element: E, language: Language, words: Callable[[str], str]) -> E:
    """`element`'s label, or a switch's text, bound to the window's language."""
    if isinstance(element, ui.switch):
        element.bind_text_from(language, "lang", backward=words)
    else:
        element.bind_label_from(language, "lang", backward=words)  # ty: ignore[unresolved-attribute]  # a field's
    return element


def _blank(value: str | None) -> str:
    return value or ""
