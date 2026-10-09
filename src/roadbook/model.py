from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .opening import Ride, Verdict, Window
    from .osm import Report
    from .profile import Profile


@dataclass
class Track:
    name: str
    lat: np.ndarray
    lon: np.ndarray
    ele: np.ndarray
    dist: np.ndarray  # cumulative distance in metres

    @property
    def length_km(self) -> float:
        return float(self.dist[-1]) / 1000

    def at(self, km: float) -> tuple[float, float]:
        """(lat, lon) of the route `km` from the start, between the two track points around it."""
        d = km * 1000
        return float(np.interp(d, self.dist, self.lat)), float(np.interp(d, self.dist, self.lon))


@dataclass
class Poi:
    name: str
    type: str
    lat: float
    lon: float
    hint_km: float | None = None  # route km stated by the exporter, if any
    km: float = 0.0  # route km after snapping onto our own track
    offset_m: float = 0.0  # distance from the route
    category: str | None = None
    opening_hours: str | None = None  # OSM `opening_hours` value, when looked up and known
    osm_id: str | None = None  # the OSM element it came from, "node/123"
    verdict: Verdict | None = None  # open or not while the rider may pass; needs hours and an arrival estimate


@dataclass
class Stop:
    """A cluster of nearby POIs, shown as one row/token."""

    pois: list[Poi]
    window: Window | None = None  # when the rider may be there, with an arrival estimate
    town: str | None = None  # the town it is in, looked up for busy stops only

    @property
    def km(self) -> float:
        return self.pois[0].km

    @property
    def km_end(self) -> float:
        return self.pois[-1].km


@dataclass
class Climb:
    start_km: float
    end_km: float
    gain_m: float
    avg_grade: float  # percent
    max_grade: float  # percent over ~200 m
    label: str  # "HC", "1".."4" or "" when below category 4
    name: str | None = None  # the col, pass or peak at its summit, when looked up and OSM has one

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km


@dataclass
class Section:
    """A stretch of a climb as its page shows it: a kilometre from the foot, the last one up to the summit."""

    start_km: float  # on the route
    end_km: float
    ele_start: float
    ele_end: float

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km

    @property
    def grade(self) -> float:
        """Its average grade, percent."""
        return (self.ele_end - self.ele_start) / max(self.length_km * 1000, 1e-9) * 100


@dataclass
class ClimbPage:
    """A climb's card on the climb pages: its profile by sections, and what the road passes on the way up."""

    climb: Climb
    sections: list[Section]
    stops: list[Stop]  # the stops on the way, the foot's and the summit's too
    places: list[tuple[float, str]] = field(default_factory=list)  # (route km, name): the towns, villages, hamlets


@dataclass(frozen=True)
class Glyph:
    """An emoji as a row shows it, with its superscript already formatted: a POI count ("2") or a category ("HC")."""

    emoji: str
    sup: str = ""
    dim: bool = False  # every shop it stands for is known to be closed when the rider passes


@dataclass
class Item:
    kind: str  # a key of kinds.KINDS: start | stop | climb | summit | checkpoint | finish
    km: float
    ele: float
    label: str = ""
    emojis: list[Glyph] = field(default_factory=list)
    climb: Climb | None = None
    stop: Stop | None = None
    dist_to_next: float | None = None  # km


@dataclass
class Roadbook:
    title: str
    length_km: float
    gain_m: float
    loss_m: float
    items: list[Item]
    stops: list[Stop]
    climbs: list[Climb]
    poi_total: int
    poi_kept: int
    profile: Profile | None = None
    hours: Report | None = None  # None when opening hours were not looked up
    towns: Report | None = None  # None when town names were not looked up
    climb_names: Report | None = None  # None when climb names were not looked up
    ride: Ride | None = None
    climb_pages: list[ClimbPage] = field(default_factory=list)  # empty unless asked for
    places: Report | None = None  # None when the places on the climbs were not looked up
