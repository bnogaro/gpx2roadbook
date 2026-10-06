from __future__ import annotations

from pathlib import Path
from typing import Any

from .climbs import find_climbs
from .model import Climb, Item, Roadbook, Stop
from .parse import read_gpx
from .pois import classify, cluster, emoji_counts, filter_pois
from .profile import Profile
from .snap import snap_pois

# at equal km, a summit closes its climb before anything else opens
_RANK = {"start": 0, "summit": 1, "checkpoint": 2, "stop": 3, "climb": 4, "finish": 9}


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
        edges = [(abs(km - s.km), (i, edge)) for i, c in enumerate(climbs) for edge, km in (("foot", c.start_km), ("summit", c.end_km))]
        gap, key = min(edges, default=(float("inf"), None))
        if gap * 1000 <= snap_m and key not in snapped:
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

    def item(kind: str, km: float, **kw) -> Item:
        return Item(kind=kind, km=km, ele=profile.ele_at(km), **kw)

    items = [item("start", 0.0, label="START")]
    for i, (km, label) in enumerate(_checkpoints(length, cfg["checkpoints"]), 1):
        items.append(item("checkpoint", km, label=label or f"CP{i}"))
    snapped = _snap_to_climbs(stops, climbs, cfg["climbs"]["snap_m"])
    absorbed = {id(s) for s in snapped.values()}

    def stop_kw(s: Stop | None) -> dict[str, Any]:
        return dict(stop=s, emojis=emoji_counts(s, cfg["categories"])) if s else {}

    for s in stops:
        if id(s) not in absorbed:
            items.append(item("stop", s.km, **stop_kw(s)))
    for i, c in enumerate(climbs):
        items.append(item("climb", c.start_km, climb=c, label=c.label, **stop_kw(snapped.get((i, "foot")))))
        # the gutter already shows every summit; a row is only worth its space when a stop sits there
        if top := snapped.get((i, "summit")):
            items.append(item("summit", c.end_km, climb=c, **stop_kw(top)))
    items.append(item("finish", length, label="FINISH"))
    items.sort(key=lambda it: (it.km, _RANK[it.kind]))

    for a, b in zip(items, items[1:]):
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
