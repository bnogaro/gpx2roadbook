from __future__ import annotations

import itertools
from typing import TYPE_CHECKING, Any

from .climbs import find_climbs
from .hours import lookup
from .kinds import KINDS
from .model import Climb, Item, Roadbook, Stop
from .opening import Ride, verdict
from .parse import read_gpx
from .pois import classify, cluster, emoji_counts, filter_pois
from .profile import Profile
from .snap import snap_pois
from .summits import lookup as name_climbs
from .towns import lookup as name_towns

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
    """Attach each stop lying within `snap_m` of a climb's foot or summit to that edge, keyed by (climb index, edge).

    Closest pairs are matched first, so a stop whose nearest edge is taken falls back to its next-nearest free one.
    """
    # one stop per edge: a climb or summit row has little room left for a stop's emojis
    pairs = sorted(
        (abs(km - s.km), s.km, j, i, k, s)
        for j, s in enumerate(stops)
        for i, c in enumerate(climbs)
        for k, km in enumerate((c.start_km, c.end_km))
        if abs(km - s.km) * 1000 <= snap_m
    )  # ties go to the earlier stop, then the earlier edge; the stop index keeps Stop itself out of the comparison
    snapped: dict[tuple[int, str], Stop] = {}
    taken: set[int] = set()
    for *_, j, i, k, s in pairs:
        key = (i, ("foot", "summit")[k])
        if j not in taken and key not in snapped:
            snapped[key] = s
            taken.add(j)
    return snapped


def _judge(stops: list[Stop], ride: Ride, profile: Profile) -> None:
    """Give each stop the window when the rider may be there, and each shop with hours whether it is open then."""
    for s in stops:
        s.window = ride.window(s.km, s.km_end, profile.gain)
        if s.window is None:
            continue
        for p in s.pois:
            if p.opening_hours:
                p.verdict = verdict(p.opening_hours, p.lat, p.lon, s.window)


def build(gpx_path: Path, cfg: dict[str, Any]) -> Roadbook:
    track, pois = read_gpx(gpx_path)
    profile = Profile(track, **cfg["elevation"])
    length = track.length_km

    snap_pois(track, pois)
    classify(pois, cfg["categories"])
    kept = filter_pois(pois, cfg["pois"]["enabled"], cfg["pois"]["max_offset_m"])
    gap_m = cfg["stops"]["gap_m"]
    stops = cluster(kept, gap_m, cfg["stops"]["max_span_m"] or 3 * gap_m)
    climbs = find_climbs(profile, cfg["climbs"])
    ride = Ride.from_cfg(cfg["ride"])
    # hours show on the reference sheet, and on the strip with an arrival estimate: else don't spend minutes on them
    wanted = cfg["hours"]["enabled"] and (cfg["render"]["details"] or ride.start is not None)
    hours = lookup(kept, cfg["hours"]) if wanted else None
    _judge(stops, ride, profile)
    # names show on the strip's rows and on the reference sheet; a ribbon of tokens has no room for them
    wanted = cfg["towns"]["enabled"] and (cfg["render"]["layout"] == "strip" or cfg["render"]["details"])
    towns = name_towns(stops, cfg["towns"]) if wanted else None
    # likewise: a climb's name goes above its row on the strip, and with the climb on the reference sheet
    wanted = cfg["climb_names"]["enabled"] and (cfg["render"]["layout"] == "strip" or cfg["render"]["details"])
    climb_names = name_climbs(climbs, track, cfg["climb_names"]) if wanted else None

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
        hours=hours,
        towns=towns,
        climb_names=climb_names,
        ride=ride,
    )
