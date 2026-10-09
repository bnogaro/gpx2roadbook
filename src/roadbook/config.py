from __future__ import annotations

import re
import tomllib
from importlib import resources
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

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
