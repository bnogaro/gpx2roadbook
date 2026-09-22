from __future__ import annotations

from pathlib import Path
from typing import Any

from .climbs import find_climbs
from .model import Item, Roadbook
from .parse import read_gpx
from .pois import classify, cluster, emoji_counts, filter_pois
from .profile import Profile
from .snap import snap_pois

_RANK = {"start": 0, "checkpoint": 1, "stop": 2, "climb": 3, "finish": 9}


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
    for s in stops:
        items.append(item("stop", s.km, stop=s, emojis=emoji_counts(s, cfg["categories"])))
    for c in climbs:
        items.append(item("climb", c.start_km, climb=c, label=c.label))
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
    )
