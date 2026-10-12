"""The words a reader sees, in their language: English as written in the code, the others from catalogs.

A message is its English text, with `str.format` fields: `_("Climb at km {km}", km="12.5")`. Its French is in a
`locales/fr/<area>.toml` catalog, keyed by that English text; one file per area (the road book, the booklet, the GUI,
the settings), merged as they are read. A message a catalog lacks reads in English.

The road book's language is set once per run, by `api.run` and `api.preview` (`with language(...)`), and `_()` reads
it there, so the code that words the road book takes no language argument. A context variable, so a preview on one
thread and a run on another keep their own. A GUI, which has a language of its own, calls `translate()` with it.
"""

from __future__ import annotations

import tomllib
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Iterator

LANGUAGES = ("en", "fr")  # English first: the code's own

_language: ContextVar[str] = ContextVar("language", default="en")

# Day and month names, short, as a road book prints dates: "Mon 12 Oct 2026", "lun. 12 oct. 2026". Not strftime's
# %a and %b, which follow the computer's locale.
_DAYS = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "fr": ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."),
}
_MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
}


@cache
def catalog(lang: str) -> dict[str, str]:
    """Every message of `lang` with a translation, by its English text: its catalogs merged. English has none."""
    folder = files("roadbook") / "locales" / lang
    if lang == "en" or not folder.is_dir():
        return {}
    merged: dict[str, str] = {}
    for area in sorted(folder.iterdir(), key=lambda f: f.name):
        if area.name.endswith(".toml"):
            merged |= tomllib.loads(area.read_text(encoding="utf-8"))
    return merged


def translate(text: str, lang: str, **fields: object) -> str:
    """`text` in `lang`, its fields filled in; in English when `lang`'s catalogs lack it."""
    words = catalog(lang).get(text, text)
    return words.format(**fields) if fields else words


def _(text: str, **fields: object) -> str:
    """`text` in the road book's language: see `language()`."""
    return translate(text, _language.get(), **fields)


def current() -> str:
    """The road book's language, as set by `language()`; English outside a run."""
    return _language.get()


@contextmanager
def language(lang: str) -> Iterator[None]:
    """Word the road book in `lang` within."""
    if lang not in LANGUAGES:
        msg = f"language {lang!r}: not one of {', '.join(LANGUAGES)}"
        raise ValueError(msg)
    token = _language.set(lang)
    try:
        yield
    finally:
        _language.reset(token)


def date_text(day: dt.date, *, year: bool = True) -> str:
    """A date as a road book prints it, in its language: "Mon 12 Oct 2026", "lun. 12 oct. 2026"."""
    lang = _language.get()
    text = f"{_DAYS[lang][day.weekday()]} {day.day:02d} {_MONTHS[lang][day.month - 1]}"
    return f"{text} {day.year}" if year else text
