"""The settings the window remembers between runs: a --config file in the user's config folder, so the CLI reads it
too (`roadbook --config <it>`); and next to it, in a file of its own, the window's language, which is no road book
setting."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

from platformdirs import user_config_path

from roadbook.config import check, load_config, merge, upgrade
from roadbook.i18n import LANGUAGES

if TYPE_CHECKING:
    from pathlib import Path


def path() -> Path:
    return user_config_path("gpx2roadbook", appauthor=False) / "gui.toml"


def language_path() -> Path:
    return path().with_name("window.toml")


def read_language(file: Path) -> str | None:
    """The window's language `file` holds; None if there is no file. A broken one raises ValueError, as read() does."""
    if not file.is_file():
        return None
    lang = tomllib.loads(file.read_text(encoding="utf-8")).get("language")
    if lang not in LANGUAGES:
        msg = f"{file}: language {lang!r}, not one of {', '.join(LANGUAGES)}"
        raise ValueError(msg)
    return lang


def language_toml(lang: str) -> str:
    return f'# gpx2roadbook\'s window: its own language, not the road book\'s\n\nlanguage = "{lang}"\n'


def read(file: Path) -> dict[str, Any] | None:
    """The defaults, with what `file` sets over them; None if there is no file.

    A broken or outdated file raises ValueError (tomllib's TOMLDecodeError is one), an unreadable one OSError.
    """
    if not file.is_file():
        return None
    cfg = load_config()
    over = upgrade(tomllib.loads(file.read_text(encoding="utf-8")))
    check(over, cfg)
    return merge(cfg, over)


def write(file: Path, text: str) -> None:
    """Write through a temporary file, so a window closed halfway leaves the last file whole."""
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(file)
