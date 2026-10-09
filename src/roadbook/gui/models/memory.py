"""The settings the window remembers between runs: a --config file in the user's config folder, so the CLI reads it
too (`roadbook --config <it>`)."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

from platformdirs import user_config_path

from roadbook.config import check, load_config, merge

if TYPE_CHECKING:
    from pathlib import Path


def path() -> Path:
    return user_config_path("gpx2roadbook", appauthor=False) / "gui.toml"


def read(file: Path) -> dict[str, Any] | None:
    """The defaults, with what `file` sets over them; None if there is no file.

    A broken or outdated file raises ValueError (tomllib's TOMLDecodeError is one), an unreadable one OSError.
    """
    if not file.is_file():
        return None
    cfg = load_config()
    over = tomllib.loads(file.read_text(encoding="utf-8"))
    check(over, cfg)
    return merge(cfg, over)


def write(file: Path, text: str) -> None:
    """Write through a temporary file, so a window closed halfway leaves the last file whole."""
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(file)
