"""The window's language, apart from the road book's (render.language): the window's words are bound to it, so a
switch re-words the whole window at once, and the form keeps what was typed."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui.binding import BindableProperty

if TYPE_CHECKING:
    from collections.abc import Callable


class Language:
    """The window's language, "en" or "fr": its texts, labels and tooltips are bound to `lang`."""

    lang = BindableProperty(on_change=lambda language, _lang: language.switched())

    def __init__(self, lang: str = "en") -> None:
        self.rewords: list[Callable[[], Any]] = []
        self.lang = lang

    def on_switch(self, reword: Callable[[], Any]) -> None:
        """Run `reword` at each switch, for what no binding reaches: a menu's choices, the preview's own page."""
        self.rewords.append(reword)

    def switched(self) -> None:
        for reword in self.rewords:
            reword()
