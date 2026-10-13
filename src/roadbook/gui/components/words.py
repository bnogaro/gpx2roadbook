"""The window's words in its language: tooltips, which NiceGUI's `.tooltip()` leaves with no binding to re-word them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from collections.abc import Callable

    from nicegui.element import Element

    from roadbook.gui.models.language import Language


def tooltip[E: Element](element: E, language: Language, words: Callable[[str], str]) -> E:
    """`element`, with a tooltip that reads `words(lang)` in the window's language, again at each switch."""
    tip = ui.tooltip().bind_text_from(language, "lang", backward=words)
    tip.props["target"] = f"#{element.html_id}"  # as Element.tooltip() does
    return element
