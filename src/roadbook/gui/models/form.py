"""What the window's form holds: one per window, never shared (see nicegui/llms.md, "module-level state")."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from roadbook import settings as st
from roadbook.api import default_out
from roadbook.config import load_config
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

    def __post_init__(self) -> None:
        self.breaks = [Row(float(km), float(minutes)) for km, minutes in st.BREAKS.get(self.cfg)]
        self.checkpoints = [Row(float(km), str(label)) for km, label in st.CHECKPOINTS.get(self.cfg)]

    def choose(self, gpx: Path, out: Path | None = None) -> None:
        """A GPX picked: the road book goes next to it, unless told otherwise."""
        self.gpx = str(gpx)
        self.out = str(out or default_out(gpx))

    def settings(self) -> dict[str, Any]:
        """The settings a run takes: the form's, with its rows, and --refresh."""
        cfg = copy.deepcopy(self.cfg)
        st.BREAKS.set(cfg, sorted([r.km, r.value] for r in self.breaks))
        st.CHECKPOINTS.set(cfg, sorted([r.km, r.value or ""] for r in self.checkpoints))
        if self.refresh:
            st.refresh(cfg)
        return cfg

    def command(self) -> str:
        """The `roadbook …` command that makes the same road book."""
        gpx = Path(self.gpx)
        cfg = self.settings()
        same = command(gpx, Path(self.out), pdf=self.pdf, cfg=cfg, base=load_config(), config=None)
        return f"{same} --refresh" if self.refresh else same
