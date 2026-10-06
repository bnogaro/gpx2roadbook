"""One definition per row kind: sorting and rendering read it from here instead of branching on `Item.kind`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from .model import Climb, Item, Roadbook


def climb_of(it: Item) -> Climb:
    """The climb behind a climb or summit row; build() always attaches one."""
    if it.climb is None:
        msg = f"{it.kind} row at km {it.km:.1f} has no climb"
        raise ValueError(msg)
    return it.climb


def _given(it: Item, _book: Roadbook) -> str:
    return it.label


def _to_go(it: Item, book: Roadbook) -> str:
    # numbered checkpoints count down to the finish; a named one ("Lunch") keeps its name alone
    return f"{it.label} · {book.length_km - it.km:.0f} to go" if it.label.startswith("CP") else it.label


def _category(it: Item, _book: Roadbook) -> str:
    c = climb_of(it)
    return f"Cat {c.label}" if c.label and not it.emojis else ""  # with a stop on board it moves to the superscript


def _elevation(it: Item, _book: Roadbook) -> str:
    return f"{it.ele:.0f} m"


def _no_sup(_it: Item) -> str:
    return ""


def _category_sup(it: Item) -> str:
    return climb_of(it).label if it.emojis else ""


@dataclass(frozen=True)
class Kind:
    rank: int  # sort order at equal km
    emoji: str = ""  # the row's own emoji, ahead of any stop riding on it; a plain stop row has none
    label: Callable[[Item, Roadbook], str] = _given
    sup: Callable[[Item], str] = _no_sup  # superscript on the row's own emoji
    label_if_room: bool = False  # the label gives way when the stops riding on the row leave no room for it
    stats: bool = False  # a sub line with the climb's length, grade and gain


# at equal km, a summit closes its climb before anything else opens
KINDS = {
    "start": Kind(0, "🟢"),
    "summit": Kind(1, "🔝", label=_elevation, label_if_room=True),
    "checkpoint": Kind(2, "🚩", label=_to_go),
    "stop": Kind(3),
    "climb": Kind(4, "⛰️", label=_category, sup=_category_sup, stats=True),
    "finish": Kind(9, "🏁"),
}
