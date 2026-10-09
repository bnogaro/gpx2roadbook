"""What every OpenStreetMap lookup shares: the HTTP calls, the services' usage rules, an on-disk answer cache, and the
report of what was found."""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

from platformdirs import user_cache_path

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

type Http = Callable[[str, dict[str, str] | None], Any]  # http() below, or a fake in tests

log = logging.getLogger(__name__)
wire = logging.getLogger("roadbook.http")  # every request and answer: -vvv

USER_AGENT = f"gpx2roadbook/{version('gpx2roadbook')} (+https://github.com/bnogaro/gpx2roadbook)"
TIMEOUT_S = 60  # per request
NOMINATIM = "https://nominatim.openstreetmap.org"  # its /search and /reverse
NOMINATIM_EVERY_S = 1.1  # its usage policy: at most one request per second
NOMINATIM_GIVE_UP = 3  # consecutive failures
# the main Overpass server, then a mirror: the public servers are often overloaded
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
M_PER_DEG = 111_320  # metres in a degree of latitude, or of longitude on the equator
# what reading an answer raises when it is not what was asked for: an error page, a list for an object
_UNREADABLE = (ValueError, KeyError, TypeError, AttributeError)


@dataclass
class Report:
    """What a lookup did, for one line of output."""

    asked: int = 0  # shops, busy stops or climbs to look up
    found: int = 0  # of `asked`, those OSM has an answer for
    cached: int = 0  # of `asked`, answered from the cache
    sources: list[str] = field(default_factory=list)  # the services that answered the others
    failed: bool = False  # some could not be looked up at all

    def answered_by(self, source: str) -> None:
        if source not in self.sources:
            self.sources.append(source)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Metres between two nearby points, as if the Earth were flat there: close enough over a few kilometres."""
    dy = (lat2 - lat1) * M_PER_DEG
    dx = (lon2 - lon1) * M_PER_DEG * math.cos(math.radians(lat1))
    return math.hypot(dx, dy)


def http(url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401  parsed JSON
    """GET `url`, or POST `data` as a form; raises OSError (URLError, timeouts) or ValueError (bad JSON)."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})  # noqa: S310  fixed https URLs
    wire.debug("%s %s%s", "POST" if body else "GET", url, f", {len(body)} bytes" if body else "")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            raw = resp.read()
    except OSError as exc:
        wire.debug("failed after %.1f s: %s", time.perf_counter() - start, exc)
        raise
    wire.debug("%s, %.1f kB in %.1f s", resp.status, len(raw) / 1000, time.perf_counter() - start)
    return json.loads(raw)


def overpass[T](query: str, http: Http, parse: Callable[[dict[str, Any]], T | None]) -> list[T]:
    """What `parse` makes of the elements Overpass finds for `query`, leaving out the ones it makes nothing of.

    The first server that answers does; raises OSError if none does, or none answers what `parse` can read.
    """
    wire.debug("Overpass query: %s", query)
    error: Exception | None = None
    for url in OVERPASS:
        try:
            return [found for e in http(url, {"data": query})["elements"] if (found := parse(e)) is not None]
        except (OSError, *_UNREADABLE) as exc:
            log.info("Overpass: %s did not answer: %s", urllib.parse.urlsplit(url).hostname, exc)
            error = exc
    raise OSError(error) from error


def nominatim[T](
    endpoint: str, params: dict[str, str], http: Http, sleep: Callable[[float], None], parse: Callable[[Any], T]
) -> T:
    """What `parse` makes of Nominatim's answer to `params` at `endpoint` ("search" or "reverse").

    Waits before every request, the first too, as another lookup may just have asked. Raises OSError if Nominatim
    does not answer, or answers what `parse` can't read.
    """
    wire.debug("waiting %g s: Nominatim takes one request a second", NOMINATIM_EVERY_S)
    sleep(NOMINATIM_EVERY_S)
    try:
        return parse(http(f"{NOMINATIM}/{endpoint}?{urllib.parse.urlencode(params)}", None))
    except _UNREADABLE as exc:
        raise OSError(exc) from exc


def one_by_one[T, R](items: list[T], ask: Callable[[T], R], answer: Callable[[T, R], None]) -> bool:
    """`answer` each item with what `ask` gets for it, in turn, until NOMINATIM_GIVE_UP of them fail in a row: the
    service is down, or we are offline. Returns whether some were left unanswered."""
    failed = False
    failures = 0  # in a row
    for n, item in enumerate(items):
        if failures >= NOMINATIM_GIVE_UP:
            log.info("%d failures in a row: giving up on the %d left", failures, len(items) - n)
            return True
        try:
            result = ask(item)
        except OSError:
            failed, failures = True, failures + 1
            continue
        failures = 0
        answer(item, result)
    return failed


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
        self.changed = False  # nothing to write back until an answer is put
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries: dict[str, Any] = data["entries"] if data.get("version") == version else {}
        except (OSError, ValueError, KeyError):
            self.entries = {}
        log.debug("Cache %s: %d answers", path, len(self.entries))

    def get(self, key: str) -> tuple[bool, Any]:
        """(known, answer): known is False when `key` was never looked up, or too long ago."""
        entry = self.entries.get(key)
        if entry is None or dt.datetime.fromisoformat(entry["at"]) < self.oldest:
            return False, None
        return True, entry[self.field]

    def put(self, key: str, answer: Any) -> None:  # noqa: ANN401  any JSON value
        self.entries[key] = {"at": dt.datetime.now(dt.UTC).isoformat(), self.field: answer}
        self.changed = True

    def save(self) -> None:
        if not self.changed:
            return  # all from the cache, or nothing answered: the file has nothing new to keep
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"version": self.version, "entries": self.entries}), encoding="utf-8")
        except OSError as exc:  # a cache that cannot be written only costs a slower next run
            log.info("Cache %s not saved: %s", self.path, exc)
        else:
            log.debug("Cache %s: %d answers saved", self.path, len(self.entries))
