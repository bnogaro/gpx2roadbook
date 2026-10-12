"""The climb pages: a card per climb, its title and its profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from roadbook.svg import climb_svg, grade_colour

from .sheet import poi_category

if TYPE_CHECKING:
    from roadbook.config import Category
    from roadbook.model import ClimbPage, Roadbook, Stop


@dataclass(frozen=True)
class ClimbCard:
    """A climb page's card: its title's parts, and its profile."""

    name: str
    stats: str  # "Cat 2 · 4.3 km at 9.6 % · max 12.0 % · ↗️408 m"
    km: str
    grade: str  # a swatch before the name, by the climb's average grade
    svg: str


def _climb_cards(
    book: Roadbook, categories: dict[str, Category], grades: tuple[tuple[float, str], ...], width: float
) -> list[ClimbCard]:
    """One card per climb page: its title, and its profile with the places and stops on the way marked."""
    return [climb_card(page, categories, grades, width) for page in book.climb_pages]


def climb_card(
    page: ClimbPage, categories: dict[str, Category], grades: tuple[tuple[float, str], ...], width: float
) -> ClimbCard:
    c, order = page.climb, list(categories)

    def emojis(s: Stop) -> str:
        kinds = sorted({poi_category(p) for p in s.pois}, key=order.index)
        return "".join(categories[k].emoji for k in kinds)

    on_it = lambda km: min(max(km, c.start_km), c.end_km)  # noqa: E731  a stop at the foot or top, on its edge
    marks = [*page.places, *((on_it(s.km), emojis(s)) for s in page.stops)]
    sections = [(s.start_km, s.end_km, s.ele_start, s.ele_end) for s in page.sections]
    stats = [f"Cat {c.label}"] if c.label else []
    stats += [f"{c.length_km:.1f} km at {c.avg_grade:.1f} %", f"max {c.max_grade:.1f} %", f"↗️{c.gain_m:.0f} m"]
    return ClimbCard(
        name=c.name or f"Climb at km {c.start_km:.1f}",
        stats=" · ".join(stats),
        km=f"km {c.start_km:.1f} → {c.end_km:.1f}",
        grade=grade_colour(c.avg_grade, grades) if grades else "",
        svg=climb_svg(sections, marks, width, grades=grades),
    )
