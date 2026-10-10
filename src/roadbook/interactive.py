"""`roadbook -i`: ask for the common settings, then offer the advanced ones, and say which command does the same."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import questionary

from . import settings as st
from .api import default_out as default_out_for

if TYPE_CHECKING:
    from prompt_toolkit.input import Input
    from prompt_toolkit.output import Output


# what the prompts ask, in their order; the layout, categories, breaks, PDF and output come with their own prompts
COMMON = [st.PAGE, st.CHECKPOINT_EVERY, st.TOWNS, st.HOURS, st.DATE, st.START, st.SPEED]  # then the breaks: _breaks()
ADVANCED = [
    st.WIDTH,
    st.LENGTH,
    st.GAP,
    st.MAX_SPAN,
    st.MIN_CLIMB,
    st.EMOJI_LINES,
    st.DETAILS,
    st.MARGIN,
    st.CLIMB,
    st.SUMMIT_ROWS,
    st.CLIMB_NAMES,
    st.CLIMB_PAGES,
    st.CLIMB_PAGES_FROM,
    st.MAP,
    st.MAP_STYLE,
    st.MAP_CONTOURS,
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


def _shown(s: st.Setting, cfg: dict[str, Any]) -> str:
    """A setting's value as listed in the advanced menu."""
    value = s.get(cfg)
    if s.kind is bool:
        return "yes" if value else "no"
    if s.shown and not value:
        return f"{_plain(s.shown(cfg))}, auto"
    return str(_plain(value))


def _ask(s: st.Setting, cfg: dict[str, Any], ask: _Prompts) -> None:
    current = s.get(cfg)
    question = s.text(cfg)
    if s.kind is bool:
        s.set(cfg, ask(questionary.confirm(question, default=current, **ask.io)))
    elif s.choices:
        options = [*s.choices, *([current] if current not in s.choices else []), OTHER]
        answer = ask(questionary.select(question, choices=options, default=current, **ask.io))
        if answer == OTHER:
            answer = ask(questionary.text(question, validate=lambda t: bool(t.strip()) or "required", **ask.io))
        s.set(cfg, answer.strip())
    elif s.kind is str:
        validate = s.check or (lambda _: True)
        s.set(cfg, ask(questionary.text(question, default=str(current), validate=validate, **ask.io)).strip())
    else:
        # a 0 that means "automatic" is offered as the value it stands for, and kept as 0 if accepted unchanged
        auto = s.shown(cfg) if s.shown and not current else None
        default = str(_plain(auto if auto is not None else current))
        validate = s.check or st.is_number(s.kind)
        answer = s.kind(ask(questionary.text(question, default=default, validate=validate, **ask.io)))
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
        questionary.select(st.LAYOUT.text(cfg), choices=choices, default=cfg["render"]["layout"], **ask.io)
    )


def _categories(cfg: dict[str, Any], ask: _Prompts) -> None:
    enabled = cfg["pois"]["enabled"]
    choices = [
        questionary.Choice(f"{spec["emoji"]} {name}", value=name, checked=name in enabled)
        for name, spec in cfg["categories"].items()
    ]
    cfg["pois"]["enabled"] = ask(
        questionary.checkbox(
            st.CATEGORIES.text(cfg),
            choices=choices,
            validate=lambda picked: bool(picked) or "pick at least one",
            **ask.io,
        )
    )


def _breaks(cfg: dict[str, Any], ask: _Prompts) -> None:
    """The planned breaks, each delaying every arrival time after it: a list, so not a Setting."""
    current = " ".join(f"{_plain(km)}:{_plain(minutes)}" for km, minutes in cfg["ride"]["breaks"])
    answer = ask(questionary.text(st.BREAKS.text(cfg), default=current, validate=st.are_breaks, **ask.io))
    cfg["ride"]["breaks"] = st.parse_breaks(answer)


def _advanced(cfg: dict[str, Any], ask: _Prompts) -> None:
    choices = [questionary.Choice(f"{s.text(cfg)} ({_shown(s, cfg)})", value=s) for s in ADVANCED]
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
        if s.when is None or s.when(cfg):
            _ask(s, cfg, prompts)
    if st.timed(cfg):
        _breaks(cfg, prompts)
    _categories(cfg, prompts)
    pdf = prompts(questionary.confirm(st.PDF.text(cfg), default=pdf, **prompts.io))
    default_out = str(out or default_out_for(gpx))
    out = Path(prompts(questionary.path(st.OUT.text(cfg), default=default_out, **prompts.io)))
    _advanced(cfg, prompts)
    return gpx, out, pdf


def _added(cfg: dict[str, Any], base: dict[str, Any]) -> list[str]:
    """The repeatable options: one per break or checkpoint that `base` doesn't have.

    A break can also be taken out at the prompt, which no option can say for one that a --config file plans.
    """
    args = []
    for km, minutes in cfg["ride"]["breaks"]:
        if [km, minutes] not in base["ride"]["breaks"]:
            args += ["--break", f"{_plain(km)}:{_plain(minutes)}"]
    for km, label in cfg["checkpoints"]["extra"][len(base["checkpoints"]["extra"]) :]:
        args += ["--checkpoint", f"{_plain(km)}:{label}" if label else str(_plain(km))]
    return args


def command(gpx: Path, out: Path, *, pdf: bool, cfg: dict[str, Any], base: dict[str, Any], config: Path | None) -> str:
    """The plain `roadbook …` command that does what the prompts were answered with: only what differs from `base`."""
    args = ["roadbook", str(gpx)]
    if out != default_out_for(gpx):
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
    args += _added(cfg, base)
    if cfg["pois"]["enabled"] != base["pois"]["enabled"]:
        args += ["--categories", ",".join(cfg["pois"]["enabled"])]
    if pdf:
        args.append("--pdf")
    # quoted for the shell it will be pasted into: cmd and PowerShell take double quotes, POSIX shells single ones
    return subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
