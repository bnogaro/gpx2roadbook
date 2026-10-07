"""`roadbook -i`: ask for the common settings, then offer the advanced ones, and say which command does the same."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import questionary

from .render import LAYOUT_WIDTH

if TYPE_CHECKING:
    from collections.abc import Callable

    from prompt_toolkit.input import Input
    from prompt_toolkit.output import Output


@dataclass(frozen=True)
class Setting:
    """One cfg value the prompts can change, and the CLI flag that sets it."""

    section: str
    key: str
    flag: str
    question: str
    kind: type  # float, int, bool or str
    shown: Callable[[dict[str, Any]], Any] | None = None  # the value 0 stands for, when 0 means "automatic"
    choices: tuple[str, ...] = ()  # offered as a menu, with "Other…" for anything else

    def get(self, cfg: dict[str, Any]) -> Any:  # noqa: ANN401  cfg values are plain TOML scalars
        return cfg[self.section][self.key]

    def set(self, cfg: dict[str, Any], value: Any) -> None:  # noqa: ANN401
        cfg[self.section][self.key] = value


def _strip_width(cfg: dict[str, Any]) -> float:
    return LAYOUT_WIDTH[cfg["render"]["layout"]]


def _span(cfg: dict[str, Any]) -> float:
    return 3 * cfg["stops"]["gap_m"]


COMMON = [
    Setting("render", "page", "--page", "Paper size", str, choices=("A4", "A5", "A3", "Letter", "Legal")),
    Setting("checkpoints", "every_km", "--checkpoint-every", "Checkpoint every N km (0 = none)", float),
    Setting("hours", "enabled", "--hours", "Look up shops' opening hours (OpenStreetMap, needs internet)", bool),
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


OTHER = "Other…"


@dataclass(frozen=True)
class _Prompts:
    """questionary, wired to a terminal, or to a pipe and a dummy screen in tests."""

    input: Input | None = None
    output: Output | None = None

    def __call__(self, question: questionary.Question) -> Any:  # noqa: ANN401
        # unsafe_ask lets Ctrl+C through as KeyboardInterrupt, which Typer turns into "Aborted!"
        return question.unsafe_ask()

    @property
    def io(self) -> dict[str, Any]:
        return {"input": self.input, "output": self.output}


def _plain(value: Any) -> Any:  # noqa: ANN401
    """30.0 reads better as 30 in a prompt and a command line."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _is_number(kind: type) -> Callable[[str], bool | str]:
    def check(text: str) -> bool | str:
        try:
            return kind(text) >= 0 or "must be 0 or more"
        except ValueError:
            return f"{text!r} is not a number"

    return check


def _shown(s: Setting, cfg: dict[str, Any]) -> str:
    """A setting's value as listed in the advanced menu."""
    value = s.get(cfg)
    if s.kind is bool:
        return "yes" if value else "no"
    if s.shown and not value:
        return f"{_plain(s.shown(cfg))}, auto"
    return str(_plain(value))


def _ask(s: Setting, cfg: dict[str, Any], ask: _Prompts) -> None:
    current = s.get(cfg)
    if s.kind is bool:
        s.set(cfg, ask(questionary.confirm(s.question, default=current, **ask.io)))
    elif s.choices:
        options = [*s.choices, *([current] if current not in s.choices else []), OTHER]
        answer = ask(questionary.select(s.question, choices=options, default=current, **ask.io))
        if answer == OTHER:
            answer = ask(questionary.text(s.question, validate=lambda t: bool(t.strip()) or "required", **ask.io))
        s.set(cfg, answer.strip())
    elif s.kind is str:
        s.set(cfg, ask(questionary.text(s.question, default=current, **ask.io)).strip())
    else:
        # a 0 that means "automatic" is offered as the value it stands for, and kept as 0 if accepted unchanged
        auto = s.shown(cfg) if s.shown and not current else None
        default = str(_plain(auto if auto is not None else current))
        answer = s.kind(ask(questionary.text(s.question, default=default, validate=_is_number(s.kind), **ask.io)))
        s.set(cfg, 0 if auto is not None and answer == auto else answer)


def _gpx(given: Path | None, ask: _Prompts) -> Path:
    if given:
        return given

    def is_file(text: str) -> bool | str:
        return Path(text.strip().strip('"')).expanduser().is_file() or "no such file"

    answer = ask(questionary.path("GPX file", validate=is_file, file_filter=_gpx_or_dir, **ask.io))
    return Path(answer.strip().strip('"')).expanduser()


def _gpx_or_dir(path: str) -> bool:
    """Tab completion offers folders to walk through and .gpx files to pick."""
    return Path(path).is_dir() or path.lower().endswith(".gpx")


def _layout(cfg: dict[str, Any], ask: _Prompts) -> None:
    choices = [
        questionary.Choice("strip  vertical, for the top tube", value="strip"),
        questionary.Choice("line   one horizontal ribbon", value="line"),
    ]
    cfg["render"]["layout"] = ask(
        questionary.select("Layout", choices=choices, default=cfg["render"]["layout"], **ask.io)
    )


def _categories(cfg: dict[str, Any], ask: _Prompts) -> None:
    enabled = cfg["pois"]["enabled"]
    choices = [
        questionary.Choice(f"{spec['emoji']} {name}", value=name, checked=name in enabled)
        for name, spec in cfg["categories"].items()
    ]
    cfg["pois"]["enabled"] = ask(
        questionary.checkbox(
            "POI categories to show",
            choices=choices,
            validate=lambda picked: bool(picked) or "pick at least one",
            **ask.io,
        )
    )


def _advanced(cfg: dict[str, Any], ask: _Prompts) -> None:
    choices = [questionary.Choice(f"{s.question} ({_shown(s, cfg)})", value=s) for s in ADVANCED]
    for s in ask(questionary.checkbox("Advanced options to change (Enter = none)", choices=choices, **ask.io)):
        _ask(s, cfg, ask)


def ask(
    gpx: Path | None,
    out: Path | None,
    pdf: bool,  # noqa: FBT001
    cfg: dict[str, Any],
    *,
    input: Input | None = None,  # noqa: A002  prompt_toolkit's own name for it
    output: Output | None = None,
) -> tuple[Path, Path, bool]:
    """Prompt for every setting, defaulting to what `cfg` (defaults, config file and flags) already says."""
    prompts = _Prompts(input, output)
    gpx = _gpx(gpx, prompts)
    _layout(cfg, prompts)
    for s in COMMON:
        _ask(s, cfg, prompts)
    _categories(cfg, prompts)
    pdf = prompts(questionary.confirm("Also export a PDF?", default=pdf, **prompts.io))
    default_out = str(out or gpx.with_suffix(".roadbook.html"))
    out = Path(prompts(questionary.path("Output file", default=default_out, **prompts.io)))
    _advanced(cfg, prompts)
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
