from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


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


@dataclass
class Poi:
    name: str
    type: str
    lat: float
    lon: float
    hint_km: float | None = None    # route km stated by the exporter, if any
    km: float = 0.0                 # route km after snapping onto our own track
    offset_m: float = 0.0           # distance from the route
    category: str | None = None


@dataclass
class Stop:
    """A cluster of nearby POIs, shown as one row/token."""

    pois: list[Poi]

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
    avg_grade: float   # percent
    max_grade: float   # percent over ~200 m
    label: str         # "HC", "1".."4" or "" when below category 4

    @property
    def length_km(self) -> float:
        return self.end_km - self.start_km


@dataclass
class Item:
    kind: str                       # start | stop | climb | checkpoint | finish
    km: float
    ele: float
    label: str = ""
    emojis: list[tuple[str, int]] = field(default_factory=list)
    climb: Climb | None = None
    stop: Stop | None = None
    dist_to_next: float | None = None   # km
    gain_to_next: float | None = None   # m
    loss_to_next: float | None = None   # m


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
