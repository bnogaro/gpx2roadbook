"""The settings: default.toml merged with a --config file, as a dict to edit and save, and typed for a run.

The dict is what the CLI's flags, `roadbook -i` and the GUI change, and what a --config file is written from. A run
reads it once, through `Settings.of`: one frozen dataclass per table, its values the types the run uses.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass, fields
from importlib import resources
from typing import TYPE_CHECKING, Any, Self

from .i18n import LANGUAGES

if TYPE_CHECKING:
    from pathlib import Path

    from _typeshed import DataclassInstance

BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
ESCAPES = {'"': '\\"', "\\": "\\\\"}
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge `over` into `base`, as --config does with the defaults."""
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: Path | None = None) -> dict[str, Any]:
    default = resources.files("roadbook").joinpath("default.toml")
    cfg = tomllib.loads(default.read_text(encoding="utf-8"))
    if path is not None:
        merge(cfg, tomllib.loads(path.read_text(encoding="utf-8")))
    return cfg


def differences(cfg: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """What `cfg` sets otherwise than `base`: the smallest --config file that turns `base` into `cfg`."""
    out: dict[str, Any] = {}
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            if sub := differences(value, base[key]):
                out[key] = sub
        elif key not in base or value != base[key]:
            out[key] = value
    return out


def check(over: dict[str, Any], base: dict[str, Any], where: str = "") -> None:
    """Raise ValueError if `over` sets a key `base` doesn't have, or a value of another kind: an outdated file."""
    for key, value in over.items():
        name = f"{where}{key}"
        if key not in base:
            msg = f"no setting {name}"
            raise ValueError(msg)
        if _kind(value) != _kind(base[key]):
            msg = f"{name} should be a {_kind(base[key])}"
            raise ValueError(msg)
        if isinstance(value, dict):
            check(value, base[key], f"{name}.")


def _kind(value: Any) -> str:  # noqa: ANN401  a TOML value
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    return {dict: "table", list: "list", str: "text"}.get(type(value), type(value).__name__)


def dumps(cfg: dict[str, Any]) -> str:
    """`cfg` as a TOML file --config reads: its plain values first, then a [table] for each of its dicts."""
    return "\n".join(_table(cfg, ""))


def _table(table: dict[str, Any], name: str) -> list[str]:
    lines = [f"[{name}]"] if name and any(not isinstance(v, dict) for v in table.values()) else []
    lines += [f"{_key(k)} = {_value(v)}" for k, v in table.items() if not isinstance(v, dict)]
    for k, v in table.items():
        if isinstance(v, dict):
            lines += ["", *_table(v, f"{name}.{_key(k)}" if name else _key(k))]
    return lines


def _key(key: str) -> str:
    return key if BARE_KEY.fullmatch(key) else _string(key)


def _value(value: Any) -> str:  # noqa: ANN401  a TOML value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    msg = f"no TOML for {value!r}"
    raise TypeError(msg)


def _string(text: str) -> str:
    # a basic string: the quote and backslash escaped, the control characters as \uXXXX, the rest as it is
    return '"' + "".join(ESCAPES.get(c) or (f"\\u{ord(c):04x}" if CONTROL.match(c) else c) for c in text) + '"'


# ---------------------------------------------------------------- the settings of a run, typed
# Each table of default.toml as a frozen dataclass, its fields its keys: what each means is said there. A number is
# kept as TOML gives it, an int or a float, unless the run converts it anyway: a strip 200 mm long stays "200".


def _with(table: dict[str, Any], **converted: object) -> dict[str, Any]:
    """A table's settings, some of them converted."""
    return {**table, **converted}


@dataclass(frozen=True)
class ElevationSettings:
    """[elevation]: how the profile is smoothed, and its climbing summed."""

    step_m: float
    smooth_m: float
    swing_m: float


@dataclass(frozen=True)
class ClimbsSettings:
    """[climbs]: what counts as a climb, its category and its colours."""

    min_gain_m: float
    min_length_m: float
    min_grade_pct: float
    merge_gap_m: float
    merge_dip_m: float
    merge_dip_ratio: float
    snap_m: float
    summit_rows: bool
    categories: tuple[tuple[str, float], ...]  # (name, from this score), the hardest first
    grade_colours: tuple[tuple[float, str], ...]  # (from this grade, colour), the gentlest first

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(
            **_with(
                table,
                categories=tuple((str(name), float(score)) for name, score in table["categories"]),
                grade_colours=tuple((float(grade), str(colour)) for grade, colour in table["grade_colours"]),
            )
        )


@dataclass(frozen=True)
class PoisSettings:
    """[pois]: the categories shown."""

    enabled: tuple[str, ...]

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(tuple(table["enabled"]))


@dataclass(frozen=True)
class StopsSettings:
    """[stops]: how the POIs make stops."""

    gap_m: float
    max_span_m: float


@dataclass(frozen=True)
class HoursSettings:
    """[hours]: looking up the shops' opening hours."""

    enabled: bool
    categories: tuple[str, ...]
    match_m: float
    max_age_days: float
    kinds: dict[str, tuple[str, ...]]  # by category, the OSM main tags its place may have; any, for one not here

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(
            **_with(
                table,
                categories=tuple(table["categories"]),
                kinds={name: tuple(tags) for name, tags in table["kinds"].items()},
            )
        )


@dataclass(frozen=True)
class TownsSettings:
    """[towns]: naming the towns at busy stops."""

    enabled: bool
    min_pois: int
    shop_min_pois: int
    shops: tuple[str, ...]
    max_age_days: float
    group_km: float
    edge_km: float

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(**_with(table, shops=tuple(table["shops"])))


@dataclass(frozen=True)
class ClimbNamesSettings:
    """[climb_names]: naming the climbs."""

    enabled: bool
    pass_m: float
    peak_m: float
    max_age_days: float


@dataclass(frozen=True)
class ClimbPagesSettings:
    """[climb_pages]: a page of profiles for the hardest climbs."""

    enabled: bool
    from_category: str
    min_sections: float
    place_m: float

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(**_with(table, from_category=str(table["from_category"]), place_m=float(table["place_m"])))


@dataclass(frozen=True)
class MapSettings:
    """[map]: the map of the route, and the booklet's mini-maps."""

    enabled: bool
    style: str
    contours: bool
    km_every: float
    max_age_days: float

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(**_with(table, style=str(table["style"])))


@dataclass(frozen=True)
class BookletSettings:
    """[booklet]: the booklet, and the stretches it warns of."""

    format: str  # as given: imposition.chosen() reads it, whatever its case
    water_gap_h: float
    water_gap_km: float
    food_gap_h: float
    food_gap_km: float
    water: tuple[str, ...]
    food: tuple[str, ...]
    counted: tuple[str, ...]
    max_per_kind: int

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(
            format=str(table["format"]),
            water_gap_h=float(table["water_gap_h"]),
            water_gap_km=float(table["water_gap_km"]),
            food_gap_h=float(table["food_gap_h"]),
            food_gap_km=float(table["food_gap_km"]),
            water=tuple(table["water"]),
            food=tuple(table["food"]),
            counted=tuple(table["counted"]),
            max_per_kind=int(table["max_per_kind"]),
        )


@dataclass(frozen=True)
class RideSettings:
    """[ride]: the ride's date, and how to estimate its times; opening.Ride is the ride, its start worked out."""

    date: dt.date | None
    start: dt.time | None
    speed_kmh: float
    climb_min_per_100m: float
    breaks: tuple[tuple[float, float], ...]  # (km, minutes), as given
    margin_pct: float
    margin_min: float

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(
            date=_date(table["date"]),
            start=_time(table["start"]),
            speed_kmh=float(table["speed_kmh"] or 0),
            climb_min_per_100m=float(table["climb_min_per_100m"] or 0),
            breaks=tuple((float(km), float(minutes)) for km, minutes in table["breaks"]),
            margin_pct=float(table["margin_pct"]),
            margin_min=float(table["margin_min"]),
        )


def _date(value: object) -> dt.date | None:
    """ "YYYY-MM-DD", a bare TOML date (from a --config file), or empty for none."""
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(value) if isinstance(value, str) and value else None


def _time(value: object) -> dt.time | None:
    """ "HH:MM", a bare TOML time, or empty for none."""
    if isinstance(value, dt.time):
        return value
    return dt.time.fromisoformat(value) if isinstance(value, str) and value else None


@dataclass(frozen=True)
class CheckpointsSettings:
    """[checkpoints]: one every so many km, and the others asked for."""

    every_km: float
    extra: tuple[tuple[float, str], ...]  # (km, label)

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(table["every_km"], tuple((float(km), str(label)) for km, label in table["extra"]))


@dataclass(frozen=True)
class RenderSettings:
    """[render]: the strips or the ribbon, and the paper."""

    layout: str
    width_mm: float
    length_mm: float
    page: str
    details: bool
    stop_range_m: float
    emoji_lines: int
    max_emojis: int
    gutter_mm: float
    language: str

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        if table["language"] not in LANGUAGES:
            msg = f"render.language {table['language']!r}: not one of {', '.join(LANGUAGES)}"
            raise ValueError(msg)
        return cls(**table)


@dataclass(frozen=True)
class Category:
    """A [categories.<name>] table: its emoji, and the words a POI's type or name is matched on."""

    emoji: str
    match: tuple[str, ...]

    @classmethod
    def of(cls, table: dict[str, Any]) -> Self:
        return cls(str(table["emoji"]), tuple(table["match"]))


@dataclass(frozen=True)
class Settings:
    """Every setting a run reads, typed: what `of` makes of the settings' dict, once, as the run starts."""

    elevation: ElevationSettings
    climbs: ClimbsSettings
    pois: PoisSettings
    stops: StopsSettings
    hours: HoursSettings
    towns: TownsSettings
    climb_names: ClimbNamesSettings
    climb_pages: ClimbPagesSettings
    map: MapSettings
    booklet: BookletSettings
    ride: RideSettings
    checkpoints: CheckpointsSettings
    render: RenderSettings
    categories: dict[str, Category]  # in the order they are shown in, which is the order they are matched in

    @classmethod
    def of(cls, cfg: dict[str, Any]) -> Settings:
        """The settings `cfg` holds, typed. Raises ValueError, naming it, for a table or a setting it lacks, or a value
        that is not of its kind; as --config files are merged, not checked, it ignores what no run reads."""

        def table(name: str, of: type[DataclassInstance]) -> dict[str, Any]:
            return _section(cfg.get(name), name, of)

        try:
            return cls(
                elevation=ElevationSettings(**table("elevation", ElevationSettings)),
                climbs=ClimbsSettings.of(table("climbs", ClimbsSettings)),
                pois=PoisSettings.of(table("pois", PoisSettings)),
                stops=StopsSettings(**table("stops", StopsSettings)),
                hours=HoursSettings.of(table("hours", HoursSettings)),
                towns=TownsSettings.of(table("towns", TownsSettings)),
                climb_names=ClimbNamesSettings(**table("climb_names", ClimbNamesSettings)),
                climb_pages=ClimbPagesSettings.of(table("climb_pages", ClimbPagesSettings)),
                map=MapSettings.of(table("map", MapSettings)),
                booklet=BookletSettings.of(table("booklet", BookletSettings)),
                ride=RideSettings.of(table("ride", RideSettings)),
                checkpoints=CheckpointsSettings.of(table("checkpoints", CheckpointsSettings)),
                render=RenderSettings.of(table("render", RenderSettings)),
                categories={
                    name: Category.of(_section(each, f"categories.{name}", Category))
                    for name, each in _section(cfg.get("categories"), "categories").items()
                },
            )
        except (TypeError, ValueError) as exc:  # a number that is text, a list that is no list of pairs…
            msg = f"settings: {exc}"
            raise ValueError(msg) from exc


def _section(table: object, name: str, of: type[DataclassInstance] | None = None) -> dict[str, Any]:
    """`table`, the settings' [name], checked to be a table; with a dataclass `of`, its keys only, each there."""
    if table is None:
        msg = f"no [{name}]"
        raise ValueError(msg)
    if not isinstance(table, dict):
        msg = f"[{name}] should be a table"
        raise TypeError(msg)
    if of is None:
        return table
    keys = [f.name for f in fields(of)]
    if missing := [key for key in keys if key not in table]:
        msg = f"no {', '.join(f'{name}.{key}' for key in missing)}"
        raise ValueError(msg)
    return {key: table[key] for key in keys}
