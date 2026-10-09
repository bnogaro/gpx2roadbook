from __future__ import annotations

import itertools
import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

from .climb_pages import pages as climb_pages
from .climbs import find_climbs
from .hours import lookup as find_hours
from .kinds import KINDS
from .model import Climb, Item, Poi, Roadbook, Stop
from .opening import Ride, verdict
from .parse import read_gpx
from .places import lookup as find_places
from .pois import classify, cluster, emoji_counts, filter_pois
from .profile import Profile
from .snap import snap_pois
from .summits import lookup as name_climbs
from .towns import group as group_by_town
from .towns import lookup as name_towns

if TYPE_CHECKING:
    from pathlib import Path

    from .model import ClimbPage, Track
    from .osm import Report

log = logging.getLogger(__name__)


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
    if ride.start is None:
        return  # no arrival times
    climbing = f", +{ride.climb_min_per_100m:g} min per 100 m climbed" if ride.climb_min_per_100m else ""
    log.info("Arrival times: from %s at %g km/h%s", f"{ride.start:%a %d %b %H:%M}", ride.speed_kmh, climbing)
    for s in stops:
        s.window = ride.window(s.km, s.km_end, profile.gain)
        if s.window is None:
            continue
        for p in s.pois:
            if p.opening_hours:
                p.verdict = verdict(p.opening_hours, p.lat, p.lon, s.window)
            if p.verdict:
                when = f"{s.window.early:%a %H:%M}-{s.window.late:%H:%M}"
                v = p.verdict.state + (f" ({p.verdict.note})" if p.verdict.note else "")
                log.debug("km %.1f %s, there %s: %s", s.km, p.name or p.type, when, v)


def _explain_left_out(pois: list[Poi], cfg: dict[str, Any]) -> None:
    """Why the file's POIs that are not in the road book were left out: -v counts them, -vv names each."""
    left: list[tuple[str, str, Poi]] = []  # why, in a count of them; the detail for this one; the POI
    for p in pois:
        if p.category is None:
            left.append(("match no category", f"no category for type {p.type!r}", p))
        elif p.category not in cfg["enabled"]:
            left.append(("are in categories not shown", f"category {p.category} not shown", p))
    if left:
        counts = Counter(why for why, _, _ in left)
        log.info("POIs left out: %s", ", ".join(f"{n} {why}" for why, n in counts.items()))
    for _, detail, p in left:
        log.debug("%r at km %.1f: %s", p.name, p.km, detail)


def _log_stops(stops: list[Stop], kept: int, gap_m: float, span_m: float) -> None:
    log.info(
        "Stops: %d from %d POIs, each within %g m of the next, spanning %g m at most", len(stops), kept, gap_m, span_m
    )
    for s in stops:
        kinds = Counter(p.category for p in s.pois)
        what = ", ".join(f"{c} x{n}" if n > 1 else str(c) for c, n in kinds.items())
        end = f"{s.km_end:.1f}"
        log.debug("km %.1f%s: %s", s.km, f"-{end}" if end != f"{s.km:.1f}" else "", what)


def _log_climbs(climbs: list[Climb], min_gain_m: float) -> None:
    log.info("Climbs: %d gaining %g m or more", len(climbs), min_gain_m)
    for c in climbs:
        log.debug(
            "km %.1f-%.1f: %.1f km at %.1f %%, steepest %.1f %%, +%.0f m%s",
            *(c.start_km, c.end_km, c.length_km, c.avg_grade, c.max_grade, c.gain_m),
            f", Cat {c.label}" if c.label else "",
        )


def _climb_pages(
    climbs: list[Climb], stops: list[Stop], profile: Profile, track: Track, cfg: dict[str, Any], *, offline: bool
) -> tuple[list[ClimbPage], Report | None]:
    """The climb pages, if asked for, and the places on their climbs' roads when town names are on."""
    if not cfg["climb_pages"]["enabled"]:
        return [], None
    c = cfg["climbs"]
    made = climb_pages(climbs, stops, profile, cfg["climb_pages"], c["categories"], c["snap_m"])
    if not (made and cfg["towns"]["enabled"]):
        return made, None
    return made, find_places(made, track, cfg["climb_pages"], cfg["towns"]["max_age_days"], offline=offline)


def build(gpx_path: Path, cfg: dict[str, Any], *, offline: bool = False) -> Roadbook:
    """The road book of `gpx_path` with the settings `cfg`. Offline, the OpenStreetMap lookups only read their cache:
    a preview, quick and quiet on the network; their reports count what they left unasked."""
    track, pois = read_gpx(gpx_path)
    log.info("Read %s: %d track points, %d waypoints", gpx_path.name, len(track.dist), len(pois))
    profile = Profile(track, **cfg["elevation"])
    length = track.length_km

    snap_pois(track, pois)
    classify(pois, cfg["categories"])
    kept = filter_pois(pois, cfg["pois"]["enabled"])
    _explain_left_out(pois, cfg["pois"])
    gap_m = cfg["stops"]["gap_m"]
    span_m = cfg["stops"]["max_span_m"] or 3 * gap_m
    stops = cluster(kept, gap_m, span_m)
    _log_stops(stops, len(kept), gap_m, span_m)
    climbs = find_climbs(profile, cfg["climbs"])
    _log_climbs(climbs, cfg["climbs"]["min_gain_m"])
    ride = Ride.from_cfg(cfg["ride"])
    if ride.start is None and (cfg["ride"]["start"] or cfg["ride"]["speed_kmh"]):  # a slip that would go unnoticed
        log.warning("No arrival times: they need a ride date, a start time and a speed (--date, --start, --speed).")
    # hours show on the reference sheet, and on the strip with an arrival estimate: else don't spend minutes on them
    wanted = cfg["hours"]["enabled"] and (cfg["render"]["details"] or ride.start is not None)
    hours = find_hours(kept, cfg["hours"], offline=offline) if wanted else None
    # names go above rows on the strip, and on the reference sheet; a ribbon of tokens has no room for them
    named = cfg["render"]["layout"] == "strip" or cfg["render"]["details"]
    towns = name_towns(stops, cfg["towns"], offline=offline) if named and cfg["towns"]["enabled"] else None
    if towns is not None:  # a town's stops make one, so judged on arrival as one
        stops = group_by_town(stops, cfg["towns"]["group_km"])
    _judge(stops, ride, profile)
    climb_names = (
        name_climbs(climbs, track, cfg["climb_names"], offline=offline)
        if (named or cfg["climb_pages"]["enabled"]) and cfg["climb_names"]["enabled"]  # each page is titled by it
        else None
    )
    pages, places = _climb_pages(climbs, stops, profile, track, cfg, offline=offline)

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
        # the gutter already shows every summit: by default a row is only worth its space when a stop sits there
        top = snapped.get((i, "summit"))
        if top or cfg["climbs"]["summit_rows"]:
            items.append(item("summit", c.end_km, climb=c, **stop_kw(top)))
    items.append(item("finish", length, label="FINISH"))
    items.sort(key=lambda it: (it.km, KINDS[it.kind].rank))

    for a, b in itertools.pairwise(items):
        a.dist_to_next = b.km - a.km

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
        climb_pages=pages,
        places=places,
    )
