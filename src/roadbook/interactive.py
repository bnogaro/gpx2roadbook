"""`roadbook -i`: ask for the common settings, then offer the advanced ones, and say which command does the same."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from .render import LAYOUT_WIDTH

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class Setting:
    """One cfg value the prompts can change, and the CLI flag that sets it."""

    section: str
    key: str
    flag: str
    question: str
    kind: type  # float, int, bool or str
    shown: Callable[[dict[str, Any]], Any] | None = None  # the value 0 stands for, when 0 means "automatic"

    def get(self, cfg: dict[str, Any]) -> Any:  # noqa: ANN401  cfg values are plain TOML scalars
        return cfg[self.section][self.key]

    def set(self, cfg: dict[str, Any], value: Any) -> None:  # noqa: ANN401
        cfg[self.section][self.key] = value


def _strip_width(cfg: dict[str, Any]) -> float:
    return LAYOUT_WIDTH[cfg["render"]["layout"]]


def _span(cfg: dict[str, Any]) -> float:
    return 3 * cfg["stops"]["gap_m"]


COMMON = [
    Setting("render", "page", "--page", "Paper size (A4, A5, Letter…)", str),
    Setting("checkpoints", "every_km", "--checkpoint-every", "Checkpoint every N km (0 = none)", float),
]
ADVANCED = [
    Setting("render", "width_mm", "--width", "Strip width, mm", float, shown=_strip_width),
    Setting("render", "length_mm", "--length", "Strip length, mm", float),
    Setting("stops", "gap_m", "--gap", "Merge POIs closer than, m", float),
    Setting("stops", "max_span_m", "--max-span", "Longest stop, m", float, shown=_span),
    Setting("pois", "max_offset_m", "--max-offset", "Ignore POIs further from the route than, m", float),
    Setting("climbs", "min_gain_m", "--min-climb", "Smallest climb, m of gain", float),
    Setting("render", "emoji_lines", "--emoji-lines", "Lines of emojis a busy stop may fill", int),
    Setting("render", "leg_elevation", "--leg-elevation", "Climbing/descent on each leg", bool),
    Setting("render", "details", "--details", "Reference sheet with POI names", bool),
]


def _number(kind: type) -> Callable[[str], Any]:
    def parse(text: str) -> Any:  # noqa: ANN401
        try:
            value = kind(text)
        except ValueError:
            msg = f"{text!r} is not a number"
            raise typer.BadParameter(msg) from None
        if value < 0:
            msg = "must be 0 or more"
            raise typer.BadParameter(msg)
        return value

    return parse


def _plain(value: Any) -> Any:  # noqa: ANN401
    """30.0 reads better as 30 in a prompt and a command line."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _ask(s: Setting, cfg: dict[str, Any]) -> None:
    current = s.get(cfg)
    if s.kind is bool:
        s.set(cfg, typer.confirm(s.question, default=current))
        return
    if s.kind is str:
        s.set(cfg, typer.prompt(s.question, default=current).strip())
        return
    # a 0 that means "automatic" is offered as the value it stands for, and kept as 0 if accepted unchanged
    auto = s.shown(cfg) if s.shown and not current else None
    answer = typer.prompt(s.question, default=_plain(auto if auto is not None else current), value_proc=_number(s.kind))
    s.set(cfg, 0 if auto is not None and answer == auto else answer)


def _gpx(given: Path | None) -> Path:
    def existing(text: str) -> Path:
        path = Path(text.strip().strip('"')).expanduser()
        if not path.is_file():
            msg = f"no file at {path}"
            raise typer.BadParameter(msg)
        return path

    return given or typer.prompt("GPX file", value_proc=existing)


def _layout(cfg: dict[str, Any]) -> None:
    def choice(text: str) -> str:
        if text not in LAYOUT_WIDTH:
            msg = f"choose one of: {', '.join(LAYOUT_WIDTH)}"
            raise typer.BadParameter(msg)
        return text

    cfg["render"]["layout"] = typer.prompt(
        f"Layout ({', '.join(LAYOUT_WIDTH)})", default=cfg["render"]["layout"], value_proc=choice
    )


def _categories(cfg: dict[str, Any]) -> None:
    known = cfg["categories"]
    typer.echo("POI categories: " + "  ".join(f"{spec['emoji']} {name}" for name, spec in known.items()))

    def names(text: str) -> list[str]:
        chosen = list(known) if text.strip() == "all" else [n.strip() for n in text.split(",") if n.strip()]
        if unknown := [n for n in chosen if n not in known]:
            msg = f"unknown: {', '.join(unknown)}"
            raise typer.BadParameter(msg)
        return chosen

    cfg["pois"]["enabled"] = typer.prompt(
        "Categories to show (comma-separated, or all)", default=",".join(cfg["pois"]["enabled"]), value_proc=names
    )


def _advanced(cfg: dict[str, Any]) -> None:
    typer.echo("\nAdvanced options:")
    for n, s in enumerate(ADVANCED, 1):
        value = s.get(cfg)
        if s.kind is bool:
            value = "yes" if value else "no"
        elif s.shown and not value:
            value = f"{_plain(s.shown(cfg))} (auto)"
        typer.echo(f"  {n:>2}. {s.question} [{_plain(value)}]")

    def numbers(text: str) -> list[int]:
        try:
            picked = [int(n) for n in text.replace(" ", ",").split(",") if n]
        except ValueError:
            msg = "give numbers from the list, e.g. 1,6"
            raise typer.BadParameter(msg) from None
        if bad := [n for n in picked if not 1 <= n <= len(ADVANCED)]:
            msg = f"not in the list: {', '.join(map(str, bad))}"
            raise typer.BadParameter(msg)
        return picked

    for n in typer.prompt("Numbers to change, e.g. 1,6 (Enter = none)", default="", value_proc=numbers):
        _ask(ADVANCED[n - 1], cfg)


def ask(gpx: Path | None, out: Path | None, pdf: bool, cfg: dict[str, Any]) -> tuple[Path, Path, bool]:  # noqa: FBT001
    """Prompt for every setting, defaulting to what `cfg` (defaults, config file and flags) already says."""
    gpx = _gpx(gpx)
    _layout(cfg)
    for s in COMMON:
        _ask(s, cfg)
    _categories(cfg)
    pdf = typer.confirm("Also export a PDF?", default=pdf)
    out = typer.prompt("Output file", default=str(out or gpx.with_suffix(".roadbook.html")), value_proc=Path)
    _advanced(cfg)
    return gpx, out, pdf


def command(gpx: Path, out: Path, *, pdf: bool, cfg: dict[str, Any], base: dict[str, Any], config: Path | None) -> str:
    """The plain `roadbook …` command that does what the prompts were answered with: only what differs from `base`."""
    args = ["roadbook", str(gpx)]
    if out != gpx.with_suffix(".roadbook.html"):
        args += ["-o", str(out)]
    if config:
        args += ["--config", str(config)]
    if cfg["render"]["layout"] != base["render"]["layout"]:
        args += ["--layout", cfg["render"]["layout"]]
    for s in COMMON + ADVANCED:
        value = s.get(cfg)
        if value == s.get(base):
            continue
        if s.kind is bool:
            args.append(s.flag if value else s.flag.replace("--", "--no-", 1))
        else:
            args += [s.flag, str(_plain(value))]
    for km, label in cfg["checkpoints"]["extra"][len(base["checkpoints"]["extra"]) :]:
        args += ["--checkpoint", f"{_plain(km)}:{label}" if label else str(_plain(km))]
    if cfg["pois"]["enabled"] != base["pois"]["enabled"]:
        args += ["--categories", ",".join(cfg["pois"]["enabled"])]
    if pdf:
        args.append("--pdf")
    # quoted for the shell it will be pasted into: cmd and PowerShell take double quotes, POSIX shells single ones
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
