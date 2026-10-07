"""What every OpenStreetMap lookup shares: the HTTP call, the services' usage rules, and an on-disk answer cache."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from platformdirs import user_cache_path

if TYPE_CHECKING:
    from pathlib import Path

USER_AGENT = f"gpx2roadbook/{version('gpx2roadbook')} (+https://github.com/bnogaro/gpx2roadbook)"
TIMEOUT_S = 60  # per request
NOMINATIM_EVERY_S = 1.1  # its usage policy: at most one request per second
NOMINATIM_GIVE_UP = 3  # consecutive failures
# the main Overpass server, then a mirror: the public servers are often overloaded
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")


def http(url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401  parsed JSON
    """GET `url`, or POST `data` as a form; raises OSError (URLError, timeouts) or ValueError (bad JSON)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})  # noqa: S310  fixed https URLs
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
        return json.load(resp)


def cache_path(name: str) -> Path:
    """Where a cache file lives: the user's cache folder, one file per kind of answer."""
    return user_cache_path("gpx2roadbook", appauthor=False) / name


class JsonCache:
    """Answers already looked up, by key, each stamped with when; a file of another `version` starts empty.

    An answer may be None ("OSM has nothing"), which is still an answer: only a failed lookup is left out.
    """

    def __init__(self, path: Path, version: int, max_age_days: float, field: str = "value") -> None:
        self.path = path
        self.version = version
        self.field = field  # the entry key the answer is stored under
        self.oldest = dt.datetime.now(dt.UTC) - dt.timedelta(days=max_age_days)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries: dict[str, Any] = data["entries"] if data.get("version") == version else {}
        except (OSError, ValueError, KeyError):
            self.entries = {}

    def get(self, key: str) -> tuple[bool, Any]:
        """(known, answer): known is False when `key` was never looked up, or too long ago."""
        entry = self.entries.get(key)
        if entry is None or dt.datetime.fromisoformat(entry["at"]) < self.oldest:
            return False, None
        return True, entry[self.field]

    def put(self, key: str, answer: Any) -> None:  # noqa: ANN401  any JSON value
        self.entries[key] = {"at": dt.datetime.now(dt.UTC).isoformat(), self.field: answer}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"version": self.version, "entries": self.entries}), encoding="utf-8")
        except OSError:
            pass  # a cache that cannot be written only costs a slower next run
