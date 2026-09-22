from __future__ import annotations

import re
import unicodedata
from typing import Any

from .model import Poi, Stop


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _compile(categories: dict[str, Any]) -> list[tuple[str, re.Pattern]]:
    out = []
    for name, spec in categories.items():
        terms = [re.escape(_norm(m)) for m in spec["match"]]
        # short terms must match a whole word ("bar", "wc"); longer ones a word prefix ("toilet" -> "toilettes")
        parts = [rf"\b{t}\b" if len(t) <= 3 else rf"\b{t}" for t in terms]
        out.append((name, re.compile("|".join(parts))))
    return out


def classify(pois: list[Poi], categories: dict[str, Any]) -> None:
    rules = _compile(categories)
    for p in pois:
        for text in (_norm(p.type), _norm(p.name)):
            hit = next((name for name, rx in rules if text and rx.search(text)), None)
            if hit:
                p.category = hit
                break


def filter_pois(pois: list[Poi], enabled: list[str], max_offset_m: float) -> list[Poi]:
    return sorted(
        (p for p in pois if p.category in enabled and p.offset_m <= max_offset_m),
        key=lambda p: p.km,
    )


def cluster(pois: list[Poi], gap_m: float, max_span_m: float) -> list[Stop]:
    """Group consecutive POIs into stops (small gaps chain together, up to a maximum span)."""
    stops: list[Stop] = []
    current: list[Poi] = []
    for p in pois:
        if current and (p.km - current[-1].km) * 1000 <= gap_m and (p.km - current[0].km) * 1000 <= max_span_m:
            current.append(p)
        else:
            if current:
                stops.append(Stop(current))
            current = [p]
    if current:
        stops.append(Stop(current))
    return stops


def emoji_counts(stop: Stop, categories: dict[str, Any]) -> list[tuple[str, int]]:
    """Distinct emojis of a stop in category priority order, with how many POIs share each."""
    counts: dict[str, int] = {}
    for p in stop.pois:
        counts[p.category] = counts.get(p.category, 0) + 1
    return [(categories[name]["emoji"], counts[name]) for name in categories if name in counts]
