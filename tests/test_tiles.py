import email.message
import io
import logging
import os
import time
import urllib.error
from pathlib import Path
from typing import Self

import pytest

from roadbook.api import lookup_line
from roadbook.osm import USER_AGENT
from roadbook.tiles import DEFAULT, SOURCES, STYLES, TERRAIN, fetch, get_tile, style

PNG = b"\x89PNG\r\n\x1a\n tile"
KEYS = [(10, 514, 376), (10, 515, 376), (10, 514, 377)]


class FakeServer:
    """Answers each tile with an image, an ETag and what it was made from, or "unchanged" to the copy it gave, or
    fails; records the asks."""

    def __init__(self, *, down: bool = False, etag: str | None = '"v1"', made_from: str | None = None) -> None:
        self.down = down
        self.etag = etag
        self.made_from = made_from
        self.asked: list[tuple[str, str | None, float | None]] = []

    def __call__(self, url: str, etag: str | None, since: float | None) -> tuple[bytes | None, dict[str, str]]:
        self.asked.append((url, etag, since))
        if self.down:
            msg = "down"
            raise OSError(msg)
        if (etag and etag == self.etag) or (since and not self.etag):
            return None, {}
        headers = {"etag": self.etag} if self.etag else {}
        return PNG + url.encode(), headers | ({SOURCES[TERRAIN].note: self.made_from} if self.made_from else {})


def test_tiles_are_fetched_once_then_come_from_the_cache(tmp_path: Path) -> None:
    server = FakeServer()
    found, report = fetch("osm", KEYS, 30, folder=tmp_path, get=server)
    assert set(found) == set(KEYS)
    assert found[KEYS[0]] == PNG + b"https://c.tile.openstreetmap.fr/hot/10/514/376.png"
    assert (report.asked, report.found, report.cached, report.sources) == (3, 3, 0, ["OpenStreetMap France"])
    assert (tmp_path / "10" / "514" / "376.etag").read_text(encoding="utf-8") == '"v1"'
    again, report = fetch("osm", KEYS, 30, folder=tmp_path, get=server)
    assert again == found
    assert (report.found, report.cached, report.sources) == (3, 3, [])
    assert len(server.asked) == 3  # nothing asked the second time
    assert lookup_line("Map", report, "tiles", "tiles") == ("Map: 3 of 3 tiles (3 from cache)", None)


def test_an_old_tile_is_asked_again_with_its_etag_and_kept_when_unchanged(tmp_path: Path) -> None:
    server = FakeServer()
    fetch("osm", KEYS[:1], 30, folder=tmp_path, get=server)
    tile = tmp_path / "10" / "514" / "376.png"
    month_ago = time.time() - 31 * 86400
    os.utime(tile, (month_ago, month_ago))
    found, report = fetch("osm", KEYS[:1], 30, folder=tmp_path, get=server)
    assert server.asked[-1][1] == '"v1"'  # if changed since: unchanged, nothing downloaded
    assert found[KEYS[0]] == tile.read_bytes()
    assert (report.found, report.cached, report.sources) == (1, 0, ["OpenStreetMap France"])
    assert tile.stat().st_mtime > month_ago  # as good as new: not asked again for another 30 days


def test_a_tile_without_etag_is_asked_again_by_its_date(tmp_path: Path) -> None:
    server = FakeServer(etag=None)
    fetch("satellite", KEYS[:1], 30, folder=tmp_path, get=server)
    url, _, since = server.asked[0]
    assert url == "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2017_3857/default/g/10/376/514.jpg"  # row first
    assert since is None
    found, _ = fetch("satellite", KEYS[:1], 0, folder=tmp_path, get=server)  # 0 days, as --refresh asks
    assert server.asked[-1][2] is not None
    assert found[KEYS[0]].startswith(PNG)
    assert not (tmp_path / "10" / "514" / "376.etag").exists()


def test_offline_only_the_cache_answers_whatever_its_age(tmp_path: Path) -> None:
    server = FakeServer()
    fetch("topo", KEYS[:1], 30, folder=tmp_path, get=server)
    old = time.time() - 400 * 86400
    os.utime(tmp_path / "10" / "514" / "376.png", (old, old))
    found, report = fetch("topo", KEYS, 30, folder=tmp_path, get=server, offline=True)
    assert list(found) == KEYS[:1]
    assert (report.asked, report.found, report.cached, report.unasked) == (3, 1, 1, 2)
    assert len(server.asked) == 1  # only the first fetch went online


def test_a_server_that_does_not_answer_is_given_up_on(tmp_path: Path) -> None:
    server = FakeServer(down=True)
    keys = [(10, x, 376) for x in range(10)]
    found, report = fetch("satellite", keys, 30, folder=tmp_path, get=server)
    assert found == {}
    assert len(server.asked) == 3  # three failures in a row: down, or offline
    assert report.failed
    line = lookup_line("Map", report, "tiles", "tiles")
    assert line == ("Map: 0 of 10 tiles", "Map: EOX did not answer for some tiles; run again later.")


def test_a_tile_that_cannot_be_kept_is_used_all_the_same(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="roadbook")
    blocker = tmp_path / "file"
    blocker.write_text("not a folder", encoding="utf-8")
    found, report = fetch("osm", KEYS[:1], 30, folder=blocker, get=FakeServer())
    assert report.found == 1
    assert found[KEYS[0]].startswith(PNG)
    assert "tile not kept" in caplog.text


def test_a_terrain_tile_says_what_it_was_made_from_cached_or_not(tmp_path: Path) -> None:
    server = FakeServer(made_from="eudem/eudem_dem_5deg_n45e000.tif, srtm/N45E005.tif")
    keys = [(8, 131, 90), (8, 131, 91)]
    found, report = fetch(TERRAIN, keys, 30, folder=tmp_path, get=server)
    assert server.asked[0][0] == "https://s3.amazonaws.com/elevation-tiles-prod/geotiff/8/131/90.tif"
    assert set(found) == set(keys)
    assert report.sources == ["AWS Terrain Tiles"]
    assert report.made_from == ["eudem/eudem_dem_5deg_n45e000.tif, srtm/N45E005.tif"]  # once for both tiles
    assert (tmp_path / "8" / "131" / "90.note").is_file()
    _, report = fetch(TERRAIN, keys, 30, folder=tmp_path, get=server, offline=True)
    assert (report.cached, report.made_from) == (2, ["eudem/eudem_dem_5deg_n45e000.tif, srtm/N45E005.tif"])
    _, report = fetch("osm", KEYS[:1], 30, folder=tmp_path, get=FakeServer())
    assert report.made_from == []  # the map's own tiles don't say


def test_an_unknown_style_falls_back_to_the_default(caplog: pytest.LogCaptureFixture) -> None:
    assert style("watercolour") == (DEFAULT, STYLES[DEFAULT])
    assert "no style 'watercolour'" in caplog.text


def test_each_style_has_its_credit_and_spreads_over_its_subdomains() -> None:
    for s in STYLES.values():
        assert s.credit
        assert s.tile_url((5, 1, 2)).startswith("https://")
    urls = {STYLES["osm"].tile_url((10, x, 0)) for x in range(3)}
    assert {u.split(".")[0] for u in urls} == {"https://a", "https://b", "https://c"}


class _Answer:
    """What urlopen() gives back."""

    def __init__(self, body: bytes, kind: str) -> None:
        self.body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = kind
        self.headers["ETag"] = '"abc"'

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self) -> bytes:
        return self.body


def test_get_tile_names_the_tool_and_says_which_copy_it_has(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sent: list[dict[str, str]] = []

    def urlopen(req: object, **_kwargs: object) -> _Answer:
        sent.append(dict(req.header_items()))  # ty: ignore[unresolved-attribute]  # a Request
        return _Answer(PNG, "image/png")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    caplog.set_level(logging.DEBUG, logger="roadbook.http")
    for etag, since in ((None, None), ('"abc"', 0.0), (None, 0.0)):
        image, headers = get_tile("https://tiles.example/1/2/3.png", etag, since)
        assert (image, headers["etag"], headers["content-type"]) == (PNG, '"abc"', "image/png")
    assert sent[0] == {"User-agent": USER_AGENT}
    assert sent[1]["If-none-match"] == '"abc"'
    assert sent[2]["If-modified-since"] == "Thu, 01 Jan 1970 00:00:00 GMT"
    assert caplog.messages[0] == "GET https://tiles.example/1/2/3.png"


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        (urllib.error.HTTPError("u", 304, "Not Modified", email.message.Message(), io.BytesIO()), (None, {})),
        (urllib.error.HTTPError("u", 404, "Not Found", email.message.Message(), io.BytesIO()), OSError),
        (OSError("timed out"), OSError),
        (_Answer(b"<html>", "text/html"), OSError),  # an error page, or a captive portal's
    ],
)
def test_get_tile_tells_unchanged_from_no_tile(monkeypatch: pytest.MonkeyPatch, answer: object, result: object) -> None:
    def urlopen(*_args: object, **_kwargs: object) -> object:
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    if result is OSError:
        with pytest.raises(OSError, match=r"."):
            get_tile("https://tiles.example/1/2/3.png", '"abc"', None)
    else:
        assert get_tile("https://tiles.example/1/2/3.png", '"abc"', None) == result
