"""The map's tiles: who draws each style, and fetching them politely, each kept on disk for the next road book.

Only providers whose terms allow a map that a desktop tool prints. Not openstreetmap.org's own tile servers: their
policy forbids offline use, and a road book's map is kept to print. OpenStreetMap France's are free to use "pour des
impressions sur papier ou des copies d'écran", for a free, non-profit app that names itself in its User-Agent: its
humanitarian style (osm) and CyclOSM. OpenTopoMap's terms allow apps that don't strain its server, and printing
(topo). EOX's Sentinel-2 cloudless of 2017 is under CC BY 4.0 (satellite): the later years are non-commercial only.
Each asks for its credit on the map, and none allows bulk downloading: a road book takes the few dozen tiles its one
map shows, one at a time, with the tool's User-Agent. A tile is kept `max_age_days`, longer than the servers ask;
after that it is asked again with its ETag, or its date, so an unchanged one is not downloaded again.

The contour lines' heights come the same way, from Mapzen's Terrain Tiles on AWS Open Data (TERRAIN): free to use,
with the credit each of the surveys they are made from asks for. Each tile names those surveys in a header of its
own, kept beside it (a style's `note`), so the map credits the ones it shows.
"""

from __future__ import annotations

import email.utils
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING

from .osm import USER_AGENT, Report, cache_path, one_by_one, wire

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# a tile's URL, and the copy we have: its ETag, else when it was fetched; get_tile() below, or a fake in tests
type Get = Callable[[str, str | None, float | None], tuple[bytes | None, dict[str, str]]]
type Key = tuple[int, int, int]  # zoom, x, y

log = logging.getLogger(__name__)

OSM = "© OpenStreetMap contributors, openstreetmap.org/copyright"
TIMEOUT_S = 20  # a tile takes a second at most; a server that doesn't answer by then is down


@dataclass(frozen=True)
class Style:
    """A map style: where its tiles come from, and the credit the map must show."""

    url: str  # with {z}, {x}, {y}, and {s} for a subdomain
    source: str  # who serves them
    credit: str  # printed on the map
    max_zoom: int
    subdomains: str = ""  # {s}: the same tile always from the same one, as a browser's cache would have it
    note: str = ""  # a header saying what each tile was made from, kept beside it, for the credit

    def tile_url(self, key: Key) -> str:
        z, x, y = key
        s = self.subdomains[(x + y) % len(self.subdomains)] if self.subdomains else ""
        return self.url.format(s=s, z=z, x=x, y=y)

    @property
    def suffix(self) -> str:
        return self.url.rsplit(".", 1)[-1]  # png, jpg


STYLES = {
    "osm": Style(
        "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
        "OpenStreetMap France",
        f"{OSM} · Humanitarian style (HOT), tiles by OpenStreetMap France",
        19,
        "abc",
    ),
    "cyclosm": Style(
        "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        "OpenStreetMap France",
        f"{OSM} · CyclOSM, tiles by OpenStreetMap France",
        19,
        "abc",
    ),
    "topo": Style(
        "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "OpenTopoMap",
        f"Map data {OSM}, SRTM · Map style © OpenTopoMap (CC-BY-SA)",
        17,
        "abc",
    ),
    "satellite": Style(
        "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2017_3857/default/g/{z}/{y}/{x}.jpg",  # WMTS: row first
        "EOX",
        "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH"
        " (Contains modified Copernicus Sentinel data 2017), CC BY 4.0",
        14,  # 10 m a pixel: closer is only blurrier
    ),
}
DEFAULT = "osm"
TERRAIN = "terrain"  # not a style to pick: the heights the contour lines are drawn from (contours.py)
SOURCES = STYLES | {
    TERRAIN: Style(
        "https://s3.amazonaws.com/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif",
        "AWS Terrain Tiles",
        "Contours: Mapzen Terrain Tiles",  # and the surveys the tiles were made from: contours.credit()
        14,
        note="x-amz-meta-x-imagery-sources",  # "srtm/N45E005.tif, gmted/30N000E_20101117_gmted_mea075.tif"
    ),
}


def style(name: str) -> tuple[str, Style]:
    """The style called `name`, or the default one, with a warning, for a name no style has."""
    if name not in STYLES:
        log.warning("Map: no style %r (%s); using %s", name, ", ".join(STYLES), DEFAULT)
        name = DEFAULT
    return name, STYLES[name]


def get_tile(url: str, etag: str | None, since: float | None) -> tuple[bytes | None, dict[str, str]]:
    """GET a tile: (its image, its headers by lowercase name); (None, {}) when the copy we have, of ETag `etag` or
    fetched at `since` (seconds since the epoch), is still current. Raises OSError if there is no image to be had."""
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    elif since is not None:
        headers["If-Modified-Since"] = email.utils.formatdate(since, usegmt=True)
    wire.debug("GET %s%s", url, " if changed" if len(headers) > 1 else "")
    start = time.perf_counter()
    req = urllib.request.Request(url, headers=headers)  # noqa: S310  fixed https URLs
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310
            image, headers = resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        if exc.code == HTTPStatus.NOT_MODIFIED:
            wire.debug("unchanged, in %.1f s", time.perf_counter() - start)
            return None, {}
        wire.debug("failed after %.1f s: %s", time.perf_counter() - start, exc)
        raise
    except OSError as exc:
        wire.debug("failed after %.1f s: %s", time.perf_counter() - start, exc)
        raise
    kind = headers.get("content-type", "")
    wire.debug("%s, %.1f kB in %.1f s", kind, len(image) / 1000, time.perf_counter() - start)
    if not kind.startswith("image/"):  # an error page, or a captive portal's
        msg = f"{url}: {kind or 'no content type'}, not an image"
        raise OSError(msg)
    return image, headers


def default_folder(name: str) -> Path:
    return cache_path("tiles") / name


def fetch(
    name: str,
    keys: list[Key],
    max_age_days: float,
    *,
    folder: Path | None = None,
    get: Get = get_tile,
    offline: bool = False,
) -> tuple[dict[Key, bytes], Report]:
    """The tiles `keys` of style `name` (or TERRAIN) that can be had, and a report of how, with what they were made
    from when the style notes it (report.made_from).

    From the cache when fetched less than `max_age_days` ago, else from the server, one at a time, until a few fail
    in a row. Never raises for a network problem: a tile that can't be had is left out, and the report says so.
    Offline, only the cache answers, whatever the age of its tiles, and the report counts the ones it leaves unasked.
    """
    started = time.perf_counter()
    name, s = (name, SOURCES[name]) if name in SOURCES else style(name)
    folder = folder or default_folder(name)
    oldest = time.time() - max_age_days * 86400
    report = Report(asked=len(keys), service=s.source)
    found: dict[Key, bytes] = {}
    todo: list[Key] = []
    for key in keys:
        path = _path(folder, s, key)
        if (image := _cached(path, oldest, offline=offline)) is None:
            todo.append(key)
            continue
        found[key] = image
        report.cached += 1
        report.made_of(_text(path.with_suffix(".note")))
    if offline:
        report.unasked, todo = len(todo), []

    def ask(key: Key) -> tuple[bytes, str | None]:
        path = _path(folder, s, key)
        have = path.is_file()
        etag = _text(path.with_suffix(".etag")) if have else None
        image, headers = get(s.tile_url(key), etag, path.stat().st_mtime if have else None)
        if image is None:  # unchanged: as good as new
            os.utime(path)
            return path.read_bytes(), _text(path.with_suffix(".note"))
        note = headers.get(s.note) if s.note else None
        _keep(path, image, headers.get("etag"), note)
        return image, note

    def answer(key: Key, got: tuple[bytes, str | None]) -> None:
        found[key] = got[0]
        report.made_of(got[1])
        report.answered_by(s.source)

    if todo:
        log.info("Map: %d tiles cached; fetching the other %d from %s", report.cached, len(todo), s.source)
    report.failed = one_by_one(todo, ask, answer)
    report.found = len(found)
    if todo:
        log.info("Map: %d tiles fetched in %.1f s", report.found - report.cached, time.perf_counter() - started)
    return found, report


def _cached(path: Path, oldest: float, *, offline: bool) -> bytes | None:
    """The tile kept at `path` if fetched since `oldest`, or offline whenever; None if it never was."""
    try:
        if offline or path.stat().st_mtime >= oldest:
            return path.read_bytes()
    except OSError:  # never fetched
        pass
    return None


def _path(folder: Path, s: Style, key: Key) -> Path:
    z, x, y = key
    return folder / str(z) / str(x) / f"{y}.{s.suffix}"


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _keep(path: Path, image: bytes, etag: str | None, note: str | None) -> None:
    """Keep a tile for the next road book, with its ETag and what it was made from; one that can't be written only
    costs a fetch next time.

    Written whole, then put in place: a preview reading the cache meanwhile never gets half a tile.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        part.write_bytes(image)
        part.replace(path)
        for suffix, text in ((".etag", etag), (".note", note)):
            if text:
                path.with_suffix(suffix).write_text(text, encoding="utf-8")
            else:
                path.with_suffix(suffix).unlink(missing_ok=True)
    except OSError as exc:
        log.info("Map: tile not kept in %s: %s", path.parent, exc)
