"""Every road book setting, described once: its place in the config, its CLI flag, its help text and its question.

The CLI takes its options' flags and help from here, `roadbook -i` its questions and checks, and a GUI its fields
and tooltips. Not here: the CLI's own switches (--config, -i, -v, --version) and the GPX file itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .opening import parse_break
from .render import LAYOUT_WIDTH

if TYPE_CHECKING:
    from collections.abc import Callable

type Config = dict[str, Any]

COMMON, ADVANCED, RUN = "common", "advanced", "run"  # RUN: how a run goes, not what the road book looks like


@dataclass(frozen=True)
class Setting:
    """One setting: where it lives in the config, the CLI flag that sets it, and how to ask for it."""

    flag: str  # "--speed"
    help: str  # the CLI's --help line, and a GUI's tooltip
    label: str | Callable[[Config], str] = ""  # the question -i asks, or a GUI field's label; may depend on the answers
    section: str = ""  # its config table; "" for a run setting, not in the config (--pdf, --refresh)
    key: str = ""
    kind: type = str  # float, int, bool, str, or list for the repeatable ones (breaks, checkpoints, categories)
    group: str = ADVANCED
    shown: Callable[[Config], Any] | None = None  # the value 0 stands for, when 0 means "automatic"
    choices: tuple[str, ...] = ()  # offered as a menu, with room for anything else
    when: Callable[[Config], bool] | None = None  # asked only if this holds, given the answers so far
    check: Callable[[str], bool | str] | None = None  # validates a text answer: True, or why it is refused

    def text(self, cfg: Config) -> str:
        """The label, as it reads given the answers so far."""
        return self.label if isinstance(self.label, str) else self.label(cfg)

    def get(self, cfg: Config) -> Any:  # noqa: ANN401  cfg values are plain TOML values
        return cfg[self.section][self.key]

    def set(self, cfg: Config, value: Any) -> None:  # noqa: ANN401
        cfg[self.section][self.key] = value

    @property
    def decls(self) -> tuple[str, ...]:
        """The flag as Typer declares it: a bool gets its --no- form too."""
        return (f"{self.flag}/--no-{self.flag.removeprefix('--')}",) if self.kind is bool else (self.flag,)


# ---------------------------------------------------------------- checks and conditions, shared by -i and a GUI


def strip_width(cfg: Config) -> float:
    return LAYOUT_WIDTH[cfg["render"]["layout"]]


def span(cfg: Config) -> float:
    return 3 * cfg["stops"]["gap_m"]


def is_date(text: str) -> bool | str:
    if not text.strip():
        return True  # no date
    try:
        dt.date.fromisoformat(text.strip())
    except ValueError:
        return "a date like 2026-10-12, or empty"
    return True


def is_time(text: str) -> bool | str:
    if not text.strip():
        return True  # no start time
    try:
        dt.time.fromisoformat(text.strip())
    except ValueError:
        return "a time like 06:00, or empty"
    return True


def is_speed(text: str) -> bool | str:
    try:
        return float(text) > 0 or "a speed above 0, for the arrival times"
    except ValueError:
        return f"{text!r} is not a number"


def is_number(kind: type) -> Callable[[str], bool | str]:
    def check(text: str) -> bool | str:
        try:
            return kind(text) >= 0 or "must be 0 or more"
        except ValueError:
            return f"{text!r} is not a number"

    return check


def parse_breaks(text: str) -> list[list[float]]:
    return [parse_break(b) for b in text.split()]


def are_breaks(text: str) -> bool | str:
    try:
        parse_breaks(text)
    except ValueError:
        return "KM:MINUTES, e.g. 95:45, separated by spaces"
    return True


def dated(cfg: Config) -> bool:
    return bool(cfg["ride"]["date"])


def timed(cfg: Config) -> bool:
    return dated(cfg) and bool(cfg["ride"]["start"])


def _date_label(cfg: Config) -> str:
    # the date gives the arrival times their day; with opening hours, it also picks each shop's hours that day
    if cfg["hours"]["enabled"]:
        return "Ride date, YYYY-MM-DD (empty: the whole week's hours, no arrival times)"
    return "Ride date, YYYY-MM-DD, for arrival times (empty: none)"


# ---------------------------------------------------------------- the settings, in the order a form would show them

LAYOUT = Setting(
    "--layout",
    "strip: vertical, top tube. line: one horizontal ribbon of tokens.",
    "Layout",
    "render",
    "layout",
    choices=("strip", "line"),
    group=COMMON,
)
PAGE = Setting(
    "--page",
    "Paper size, e.g. A4, A5, Letter.",
    "Paper size",
    "render",
    "page",
    choices=("A4", "A5", "A3", "Letter", "Legal"),
    group=COMMON,
)
CHECKPOINT_EVERY = Setting(
    "--checkpoint-every",
    "Auto checkpoint every N km (0 = off).",
    "Checkpoint every N km (0 = none)",
    "checkpoints",
    "every_km",
    float,
    COMMON,
)
CHECKPOINTS = Setting(
    "--checkpoint",
    "Extra checkpoint, KM or KM:LABEL. Repeatable.",
    "Extra checkpoints, KM or KM:LABEL",
    "checkpoints",
    "extra",
    list,
)
TOWNS = Setting(
    "--towns",
    "Name the town at busy stops, from OpenStreetMap (default: on).",
    "Name the towns at busy stops (OpenStreetMap, needs internet)",
    "towns",
    "enabled",
    bool,
    COMMON,
)
HOURS = Setting(
    "--hours",
    "Look up shops' opening hours on OpenStreetMap for the details sheet (needs internet).",
    "Look up shops' opening hours (OpenStreetMap, needs internet)",
    "hours",
    "enabled",
    bool,
    COMMON,
)
DATE = Setting(
    "--date",
    "Ride date, YYYY-MM-DD: with --hours, each shop's hours that day.",
    _date_label,
    "ride",
    "date",
    group=COMMON,
    check=is_date,
)
START = Setting(
    "--start",
    'Start time, HH:MM (or "YYYY-MM-DD HH:MM"): with --speed, when you reach each stop.',
    "Start time, HH:MM (empty: no arrival times)",
    "ride",
    "start",
    group=COMMON,
    when=dated,
    check=is_time,
)
SPEED = Setting(
    "--speed",
    "Average speed, km/h, short stops included: with --start, arrival times.",
    "Average speed on the flat, km/h, short stops included",
    "ride",
    "speed_kmh",
    float,
    COMMON,
    when=timed,
    check=is_speed,
)
BREAKS = Setting(
    "--break",
    "Planned break, KM:MINUTES, delaying every later stop. Repeatable.",
    "Planned breaks, KM:MINUTES separated by spaces (empty: none)",
    "ride",
    "breaks",
    list,
    COMMON,
    when=timed,
    check=are_breaks,
)
CATEGORIES = Setting(
    "--categories",
    "Comma-separated POI categories to keep, e.g. water,bakery,lodging.",
    "POI categories to show",
    "pois",
    "enabled",
    list,
    COMMON,
)
WIDTH = Setting(
    "--width",
    "Short side of a strip, mm (default: 35 strip / 16 line).",
    "Strip width, mm",
    "render",
    "width_mm",
    float,
    shown=strip_width,
)
LENGTH = Setting("--length", "Long side of a strip, mm.", "Strip length, mm", "render", "length_mm", float)
GAP = Setting(
    "--gap",
    "Merge POIs closer than this many metres into one stop.",
    "Merge POIs closer than, m",
    "stops",
    "gap_m",
    float,
)
MAX_SPAN = Setting(
    "--max-span",
    "Longest a stop may stretch, metres; longer runs split (default 3 x gap).",
    "Longest stop, m",
    "stops",
    "max_span_m",
    float,
    shown=span,
)
MIN_CLIMB = Setting(
    "--min-climb",
    "Smallest gain, metres, for an ascent to count as a climb (default 80).",
    "Smallest climb, m of gain",
    "climbs",
    "min_gain_m",
    float,
)
EMOJI_LINES = Setting(
    "--emoji-lines",
    "Lines a crowded stop's emojis may fill on a strip (default 2; 1 = one line, with a +).",
    "Lines of emojis a busy stop may fill",
    "render",
    "emoji_lines",
    int,
)
DETAILS = Setting(
    "--details", "Append a POI names reference sheet.", "Reference sheet with POI names", "render", "details", bool
)
MARGIN = Setting(
    "--margin",
    "How far off arrival times may be, % of the time ridden (default 15; at least 20 min).",
    "Arrival times may be off by, % of the time ridden",
    "ride",
    "margin_pct",
    float,
)
CLIMB = Setting(
    "--climb",
    "Minutes added to arrival times per 100 m climbed (default 5).",
    "Arrival times: minutes added per 100 m climbed",
    "ride",
    "climb_min_per_100m",
    float,
)
SUMMIT_ROWS = Setting(
    "--summit-rows",
    "A summit row at every climb's top, not only where a stop is (default: off).",
    "A summit row at every climb's top",
    "climbs",
    "summit_rows",
    bool,
)
CLIMB_NAMES = Setting(
    "--climb-names",
    "Name the col, pass or peak at each climb's top, from OpenStreetMap (default: on).",
    "Name the cols and peaks (OpenStreetMap, needs internet)",
    "climb_names",
    "enabled",
    bool,
)
OUT = Setting("--out", "Output .html (default: next to the GPX).", "Output file", group=RUN)
PDF = Setting("--pdf", "Also export a PDF via headless Edge/Chrome.", "Also export a PDF?", kind=bool, group=RUN)
REFRESH = Setting(
    "--refresh",
    "Look up opening hours, towns and climb names again, instead of using the cached answers.",
    "Look everything up again online",
    kind=bool,
    group=RUN,
)

SETTINGS = (
    LAYOUT,
    PAGE,
    CHECKPOINT_EVERY,
    CHECKPOINTS,
    TOWNS,
    HOURS,
    DATE,
    START,
    SPEED,
    BREAKS,
    CATEGORIES,
    WIDTH,
    LENGTH,
    GAP,
    MAX_SPAN,
    MIN_CLIMB,
    EMOJI_LINES,
    DETAILS,
    MARGIN,
    CLIMB,
    SUMMIT_ROWS,
    CLIMB_NAMES,
    OUT,
    PDF,
    REFRESH,
)
