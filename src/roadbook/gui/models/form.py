"""What the window's form holds: one per window, never shared (see nicegui/llms.md, "module-level state")."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roadbook import settings as st
from roadbook.api import default_out
from roadbook.config import differences, dumps, load_config
from roadbook.interactive import command


@dataclass
class Row:
    """A planned break (km, minutes) or an extra checkpoint (km, label), as a row the form edits."""

    km: float = 0.0
    value: Any = None  # the break's minutes, or the checkpoint's label


@dataclass
class Form:
    """The settings being edited, and what the run makes of them."""

    cfg: dict[str, Any] = field(default_factory=load_config)
    gpx: str = ""
    out: str = ""
    pdf: bool = False
    refresh: bool = False
    breaks: list[Row] = field(default_factory=list)
    checkpoints: list[Row] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=load_config, repr=False)

    def __post_init__(self) -> None:
        self.apply(self.cfg)

    def apply(self, cfg: dict[str, Any]) -> None:
        """Take `cfg`'s settings, in place: the fields stay bound to the same dicts and lists."""
        _assign(self.cfg, copy.deepcopy(cfg))
        self.breaks[:] = [Row(float(km), float(minutes)) for km, minutes in st.BREAKS.get(self.cfg)]
        self.checkpoints[:] = [Row(float(km), str(label)) for km, label in st.CHECKPOINTS.get(self.cfg)]

    def choose(self, gpx: Path, out: Path | None = None) -> None:
        """A GPX picked: the road book goes next to it, unless told otherwise."""
        self.gpx = str(gpx)
        self.out = str(out or default_out(gpx))

    def settings(self) -> dict[str, Any]:
        """The settings a run takes: the form's, with its rows, and --refresh."""
        cfg = self._with_rows()
        if self.refresh:
            st.refresh(cfg)
        return cfg

    def toml(self) -> str:
        """The settings that differ from the defaults, as a --config file: what the window remembers."""
        changed = differences(self._with_rows(), self.defaults)
        return f"# gpx2roadbook settings: roadbook --config <this file> takes them too\n\n{dumps(changed)}\n"

    def _with_rows(self) -> dict[str, Any]:
        cfg = copy.deepcopy(self.cfg)
        st.BREAKS.set(cfg, sorted([r.km, r.value] for r in self.breaks))
        st.CHECKPOINTS.set(cfg, sorted([r.km, r.value or ""] for r in self.checkpoints))
        return cfg

    def command(self) -> str:
        """The `roadbook …` command that makes the same road book."""
        gpx = Path(self.gpx)
        cfg = self.settings()
        same = command(gpx, Path(self.out), pdf=self.pdf, cfg=cfg, base=load_config(), config=None)
        return f"{same} --refresh" if self.refresh else same


def _assign(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Make `target` equal `source` without replacing it, nor any dict in it: bindings hold on to those."""
    for key in [k for k in target if k not in source]:
        del target[key]
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _assign(target[key], value)
        else:
            target[key] = value
