"""The climb pages: a card for each climb worth one, its profile a kilometre at a time, as the big races print them.

A climb is cut into kilometres from its foot, each with its own grade; the stops on the way come from the strips,
and the towns, villages and hamlets the road goes through from OpenStreetMap (places.py).
"""

from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, Any

from .model import ClimbPage, Section

if TYPE_CHECKING:
    from .model import Climb, Stop
    from .profile import Profile

log = logging.getLogger(__name__)

SHORT_KM = 0.3  # a last stretch shorter than this joins the kilometre before it: a grade over a few metres is noise


def sections(climb: Climb, profile: Profile) -> list[Section]:
    """The climb in kilometres from its foot; the last one runs to the summit, a short remainder included."""
    edges = [climb.start_km + k for k in range(int(climb.length_km) + 1)]
    if climb.end_km - edges[-1] >= SHORT_KM or len(edges) == 1:
        edges.append(climb.end_km)
    else:
        edges[-1] = climb.end_km
    return [Section(a, b, profile.ele_at(a), profile.ele_at(b)) for a, b in itertools.pairwise(edges)]


def chosen(climbs: list[Climb], from_label: str, categories: list[list]) -> list[Climb]:
    """The climbs of category `from_label` or harder; the categories go from the hardest ("HC") to the easiest."""
    order = [str(name) for name, _ in categories]
    if from_label not in order:
        log.warning("Climb pages: no category %r (%s); every categorised climb gets one", from_label, ", ".join(order))
        from_label = order[-1]
    hardest = order[: order.index(from_label) + 1]
    return [c for c in climbs if c.label in hardest]


def pages(
    climbs: list[Climb], stops: list[Stop], profile: Profile, cfg: dict[str, Any], categories: list[list], snap_m: float
) -> list[ClimbPage]:
    """A page for each climb of `cfg["from_category"]` or harder, with the stops on its way: those within `snap_m` of
    its foot or summit too, as the strips show them on its rows."""
    out = []
    for c in chosen(climbs, str(cfg["from_category"]), categories):
        lo, hi = c.start_km - snap_m / 1000, c.end_km + snap_m / 1000
        on_it = [s for s in stops if s.km_end >= lo and s.km <= hi]
        out.append(ClimbPage(c, sections(c, profile), on_it))
    log.info("Climb pages: %d climbs of category %s or harder", len(out), cfg["from_category"])
    return out
