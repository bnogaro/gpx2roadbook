from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path
from typing import Any


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: Path | None = None) -> dict[str, Any]:
    default = resources.files("roadbook").joinpath("default.toml")
    cfg = tomllib.loads(default.read_text(encoding="utf-8"))
    if path is not None:
        _merge(cfg, tomllib.loads(path.read_text(encoding="utf-8")))
    return cfg
