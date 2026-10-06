from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

from .climbs import find_climbs
from .kinds import KINDS
from .model import Climb, Item, Roadbook, Stop
from .parse import read_gpx
from .pois import classify, cluster, emoji_counts, filter_pois
from .profile import Profile
from .snap import snap_pois

if TYPE_CHECKING:
    from pathlib import Path


def _checkpoints(length_km: float, cfg: dict[str, Any]) -> list[tuple[float, str]]:
    cps: list[tuple[float, str]] = []
    every = cfg["every_km"]
    if every and every > 0:
        k = every
        while k < length_km - every / 4:  # skip one that would sit right on the finish
            cps.append((float(k), ""))
            k += every
    cps += [(float(km), str(label)) for km, label in cfg["extra"]]
    return sorted(cps)


def _snap_to_climbs(stops: list[Stop], climbs: list[Climb], snap_m: float) -> dict[tuple[int, str], Stop]:
    """Attach each stop lying within `snap_m` of a climb's foot or summit to that edge, keyed by (climb index, edge)."""
    snapped: dict[tuple[int, str], Stop] = {}
    for s in stops:
        edges = [
            (abs(km - s.km), (i, edge))
            for i, c in enumerate(climbs)
            for edge, km in (("foot", c.start_km), ("summit", c.end_km))
        ]
        gap, key = min(edges, default=(float("inf"), None))
        if key is not None and gap * 1000 <= snap_m and key not in snapped:
            snapped[key] = s
    return snapped


def build(gpx_path: Path, cfg: dict[str, Any]) -> Roadbook:
    track, pois = read_gpx(gpx_path)
    profile = Profile(track, **cfg["elevation"])
    length = track.length_km

    snap_pois(track, pois)
    classify(pois, cfg["categories"])
    kept = filter_pois(pois, cfg["pois"]["enabled"], cfg["pois"]["max_offset_m"])
    stops = cluster(kept, cfg["stops"]["gap_m"], cfg["stops"]["max_span_m"])
    climbs = find_climbs(profile, cfg["climbs"])

    def item(kind: str, km: float, **kw: Any) -> Item:  # noqa: ANN401  forwards Item's own keyword fields
        return Item(kind=kind, km=km, ele=profile.ele_at(km), **kw)

    items = [item("start", 0.0, label="START")]
    for i, (km, label) in enumerate(_checkpoints(length, cfg["checkpoints"]), 1):
        items.append(item("checkpoint", km, label=label or f"CP{i}"))
    snapped = _snap_to_climbs(stops, climbs, cfg["climbs"]["snap_m"])
    absorbed = {id(s) for s in snapped.values()}

    def stop_kw(s: Stop | None) -> dict[str, Any]:
        return {"stop": s, "emojis": emoji_counts(s, cfg["categories"])} if s else {}

    items.extend(item("stop", s.km, **stop_kw(s)) for s in stops if id(s) not in absorbed)
    for i, c in enumerate(climbs):
        items.append(item("climb", c.start_km, climb=c, label=c.label, **stop_kw(snapped.get((i, "foot")))))
        # the gutter already shows every summit; a row is only worth its space when a stop sits there
        if top := snapped.get((i, "summit")):
            items.append(item("summit", c.end_km, climb=c, **stop_kw(top)))
    items.append(item("finish", length, label="FINISH"))
    items.sort(key=lambda it: (it.km, KINDS[it.kind].rank))

    for a, b in itertools.pairwise(items):
        a.dist_to_next = b.km - a.km
        a.gain_to_next = profile.gain(a.km, b.km)
        a.loss_to_next = profile.loss(a.km, b.km)

    return Roadbook(
        title=track.name,
        length_km=length,
        gain_m=profile.total_gain,
        loss_m=profile.total_loss,
        items=items,
        stops=stops,
        climbs=climbs,
        poi_total=len(pois),
        poi_kept=len(kept),
        profile=profile,
    )
