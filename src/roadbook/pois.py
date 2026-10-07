from __future__ import annotations

import itertools
import re
import unicodedata
from typing import Any

from .model import Glyph, Poi, Stop

_SHORT_TERM = 3  # longest match term (in characters) that must match a whole word


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _compile(categories: dict[str, Any]) -> list[tuple[str, re.Pattern]]:
    out = []
    for name, spec in categories.items():
        terms = [re.escape(_norm(m)) for m in spec["match"]]
        # short terms must match a whole word ("bar", "wc"); longer ones a word prefix ("toilet" -> "toilettes")
        parts = [rf"\b{t}\b" if len(t) <= _SHORT_TERM else rf"\b{t}" for t in terms]
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


SAME_SPOT_M = 100  # POIs closer than this are one place: a stop never splits between them


def _split(pois: list[Poi], max_span_m: float) -> list[list[Poi]]:
    """Cut a run of POIs into pieces spanning at most `max_span_m`.

    Each piece fills up to the span, so there are as few stops as the span allows; but when the next POI overflows
    it, the cut moves back to the last real gap (over `SAME_SPOT_M`) instead of falling between POIs at one spot,
    which would show one place as two rows. With no real gap in the piece, it falls at the widest one.
    """
    pieces: list[list[Poi]] = []
    current: list[Poi] = []
    for p in pois:
        while current and (p.km - current[0].km) * 1000 > max_span_m:
            gaps = [(b.km - a.km) * 1000 for a, b in itertools.pairwise([*current, p])]
            real = [i for i, g in enumerate(gaps) if g > SAME_SPOT_M]
            cut = (real[-1] if real else max(range(len(gaps)), key=gaps.__getitem__)) + 1
            pieces.append(current[:cut])
            current = current[cut:]
        current.append(p)
    return [*pieces, current] if current else pieces


def cluster(pois: list[Poi], gap_m: float, max_span_m: float) -> list[Stop]:
    """Group consecutive POIs into stops: gaps up to `gap_m` chain together, then long runs are cut to `max_span_m`."""
    runs: list[list[Poi]] = []
    for p in pois:
        if runs and (p.km - runs[-1][-1].km) * 1000 <= gap_m:
            runs[-1].append(p)
        else:
            runs.append([p])
    return [Stop(piece) for run in runs for piece in _split(run, max_span_m)]


def emoji_counts(stop: Stop, categories: dict[str, Any]) -> list[Glyph]:
    """Distinct emojis of a stop in category priority order, topped by how many POIs share each, if several.

    An emoji is dimmed when every POI it stands for is a shop known to be closed while the rider may pass.
    """
    counts: dict[str, int] = {}
    closed: dict[str, int] = {}
    for p in stop.pois:
        if p.category is not None:  # stops only hold classified POIs; this narrows the type
            counts[p.category] = counts.get(p.category, 0) + 1
            if p.verdict is not None and p.verdict.state == "closed":
                closed[p.category] = closed.get(p.category, 0) + 1
    return [
        Glyph(categories[name]["emoji"], str(n) if (n := counts[name]) > 1 else "", dim=closed.get(name) == n)
        for name in categories
        if name in counts
    ]
