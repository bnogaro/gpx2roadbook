import logging
import math
import re
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from roadbook import settings as st
from roadbook.cli import app
from roadbook.config import load_config
from roadbook.contours import (
    LABEL_GAP,
    SIDE,
    STEPS,
    Grid,
    add,
    credit,
    grid,
    heights,
    interval,
    isolines,
    keys,
    lines,
)
from roadbook.model import Roadbook, Track
from roadbook.osm import M_PER_DEG, Report
from roadbook.routemap import RouteMap, frame, svg, world
from roadbook.tiles import TERRAIN

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
MADE_FROM = "srtm/N45E005.tif, gmted/30N000E_20101117_gmted_mea075.tif"


def _tif(z: np.ndarray, *, order: str = "<", predictor: int = 2, nodata: str = "-32768") -> bytes:
    """A terrain tile as Mapzen's are: 512 x 512 16-bit signed heights in 256 x 256 tiles, deflated, each row's
    differences kept when `predictor` is 2."""
    tile, data, offsets, sizes = 256, b"", [], []
    for r in range(0, SIDE, tile):
        for c in range(0, SIDE, tile):
            block = z[r : r + tile, c : c + tile].astype(np.int64)
            if predictor == 2:
                block = np.diff(block, axis=1, prepend=0)
            raw = zlib.compress(block.astype(np.int16).astype(order + "i2").tobytes())
            offsets.append(8 + len(data))
            sizes.append(len(raw))
            data += raw
    text = nodata.encode() + b"\0"
    arrays_at = 8 + len(data) + len(text)
    ifd_at = arrays_at + 32
    entries = [
        (256, 3, 1, SIDE),
        (257, 3, 1, SIDE),
        (258, 3, 1, 16),
        (259, 3, 1, 8),
        (277, 3, 1, 1),
        (317, 3, 1, predictor),
        (322, 3, 1, tile),
        (323, 3, 1, tile),
        (324, 4, 4, arrays_at),
        (325, 4, 4, arrays_at + 16),
        (339, 3, 1, 2),
        (42113, 2, len(text), 8 + len(data)),
    ]
    ifd = struct.pack(order + "H", len(entries)) + b"".join(
        struct.pack(order + "HHI", tag, kind, count)
        + (struct.pack(order + "H", value) + b"\0\0" if kind == 3 else struct.pack(order + "I", value))
        for tag, kind, count, value in entries
    )
    head = (b"II" if order == "<" else b"MM") + struct.pack(order + "HI", 42, ifd_at)
    return (
        head + data + text + struct.pack(f"{order}4I", *offsets) + struct.pack(f"{order}4I", *sizes) + ifd + b"\0" * 4
    )


# --- reading a terrain tile


@pytest.mark.parametrize(("order", "predictor"), [("<", 2), (">", 2), ("<", 1)])
def test_a_terrain_tile_reads_as_its_heights(order: str, predictor: int) -> None:
    z = np.add.outer(np.arange(SIDE) * 7, np.arange(SIDE) * 13) - 3000  # below the sea and up to 7 km: rows wrap
    z[100, 200] = -32768  # no height there
    got = heights(_tif(z, order=order, predictor=predictor))
    assert got.shape == (SIDE, SIDE)
    assert np.isnan(got[100, 200])
    got[100, 200] = z[100, 200]
    assert np.array_equal(got, z)


@pytest.mark.parametrize(
    "tif",
    [
        b"",
        b"<html>Not Found</html>",
        _tif(np.zeros((SIDE, SIDE)))[:600],  # cut short
        b"II+" + _tif(np.zeros((SIDE, SIDE)))[3:],  # no TIFF's mark
    ],
)
def test_a_tile_that_is_no_terrain_tile_is_refused(tif: bytes) -> None:
    with pytest.raises(ValueError, match=r"."):
        heights(tif)


def test_a_terrain_tile_of_another_size_is_refused() -> None:
    tif = _tif(np.zeros((SIDE, SIDE)))
    width = struct.pack("<HHIH", 256, 3, 1, SIDE)
    with pytest.raises(ValueError, match="512 x 512"):
        heights(tif.replace(width, struct.pack("<HHIH", 256, 3, 1, 256)))


# --- the lines


def _grid(z: np.ndarray, step: float = 1.0) -> Grid:
    return Grid(z.astype(float), 0.0, 0.0, step)


def test_a_hill_is_a_closed_line_round_it_at_its_height() -> None:
    r = np.hypot(*np.mgrid[-50:51, -50:51])
    ((x, y, closed),) = isolines(_grid(1000 - 20 * r), 500)  # 25 mm from its top
    assert closed
    assert (x[0], y[0]) == (x[-1], y[-1])
    assert np.hypot(x - 50, y - 50) == pytest.approx(25, abs=0.05)
    assert float(np.hypot(np.diff(x), np.diff(y)).sum()) == pytest.approx(2 * math.pi * 25, rel=0.01)


def test_a_slope_is_an_open_line_across_the_map() -> None:
    z = np.tile(np.arange(40) * 10.0, (30, 1))  # up to the east, 10 m a mm
    ((x, y, closed),) = isolines(_grid(z), 205)
    assert not closed
    assert np.allclose(x, 20.5)
    assert (y.min(), y.max()) == (0, 29)


def test_a_saddle_parts_the_corners_on_the_other_side_of_its_middle() -> None:
    z = np.array([[100.0, 0.0], [0.0, 100.0]])  # up top left and bottom right, a cell 10 mm across

    def cut_off(found: list[tuple[np.ndarray, np.ndarray, bool]]) -> set[tuple[int, int]]:
        return {(round(x.mean() / 10) * 10, round(y.mean() / 10) * 10) for x, y, _ in found}

    assert cut_off(isolines(_grid(z + np.array([[0, 30], [0, 0]]), step=10), 50)) == {
        (10, 0),
        (0, 10),
    }  # middle up: the lows
    assert cut_off(isolines(_grid(z - np.array([[0, 0], [30, 0]]), step=10), 50)) == {
        (0, 0),
        (10, 10),
    }  # down: the highs


def test_no_line_crosses_a_gap_in_the_terrain() -> None:
    z = np.tile(np.arange(40) * 10.0, (30, 1))
    z[10:20, :] = np.nan  # a terrain tile missing
    found = isolines(_grid(z), 205)
    assert len(found) == 2
    assert all(not ((y > 9) & (y < 20)).any() for _, y, _ in found)


def test_a_speck_is_left_out() -> None:
    z = np.zeros((20, 20))
    z[10, 10] = 100  # a single height above the level: a line round it 1.4 mm long
    assert isolines(_grid(z, step=0.5), 50) == []
    assert len(isolines(_grid(z, step=1), 50)) == 1  # 2.8 mm: a hill


@pytest.mark.parametrize(
    ("m_per_mm", "expected"),
    [(0, STEPS[0]), (8, (10, 50)), (12, (20, 100)), (45, (50, 250)), (150, (200, 1000)), (5000, STEPS[-1])],
)
def test_the_lines_keep_their_distance_on_the_slopes(m_per_mm: float, expected: tuple[int, int]) -> None:
    z = 10 + np.tile(np.arange(40) * m_per_mm, (30, 1))
    assert interval(_grid(z)) == expected


def test_the_sea_floor_does_not_count_for_the_interval() -> None:
    z = np.tile(np.arange(40) * 300.0, (30, 1)) - 20 * 300  # a steep sea floor up to a flat coast
    z[:, 20:] = 5
    assert interval(_grid(z)) == STEPS[0]


# --- on the map


def _track(km: float) -> Track:
    """A straight road from 45° N, 5° E, north, a point every 100 m."""
    dist = np.arange(0, km * 1000 + 1, 100.0)
    lat = 45.0 + dist / M_PER_DEG
    return Track("t", lat, np.full_like(dist, 5.0), np.zeros_like(dist), dist)


def _map(track: Track, style: str = "osm") -> RouteMap:
    x, y = world(track.lat, track.lon)
    zoom, left, top, scale, height = frame(x, y, (194, 271), 19)
    return RouteMap(style, zoom, left, top, scale, 194, height, track)


def _hill(m: RouteMap, key: Any, top_m: float = 1800) -> bytes:  # noqa: ANN401  a tile's key
    """A terrain tile of a round hill in the middle of the map, `top_m` high, 3 m lower a pixel of the map out."""
    z, x, y = key
    f = 2.0 ** (z + 1 - m.zoom)
    cx, cy = (m.left + m.width / m.scale / 2) * f, (m.top + m.height / m.scale / 2) * f
    rows, cols = np.mgrid[0:SIDE, 0:SIDE] + 0.5
    d = np.hypot(x * SIDE + cols - cx, y * SIDE + rows - cy) / f
    return _tif(np.maximum(top_m - 3 * d, 0))


class FakeTerrain:
    """A hill under every map; records what was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, list[Any], float, bool]] = []
        self.map: RouteMap | None = None

    def __call__(
        self, name: str, keys: list[Any], max_age_days: float, *, offline: bool = False
    ) -> tuple[dict[Any, bytes], Report]:
        self.asked.append((name, keys, max_age_days, offline))
        assert self.map is not None
        tiles = {k: _hill(self.map, k) for k in keys}
        return tiles, Report(asked=len(keys), found=len(keys), sources=["AWS Terrain Tiles"], made_from=[MADE_FROM])


@pytest.fixture
def terrain(monkeypatch: pytest.MonkeyPatch) -> FakeTerrain:
    fake = FakeTerrain()
    monkeypatch.setattr("roadbook.contours.fetch_tiles", fake)
    return fake


def test_the_terrain_tiles_are_two_zooms_out_and_cover_the_map() -> None:
    m = _map(_track(100))
    found = keys(m)
    assert {z for z, _, _ in found} == {m.zoom - 2}
    assert len(found) == len(set(found)) <= len(m.cells())
    # each terrain tile spans 512 of its own heights, one zoom in from its own: 1024 of the map's pixels
    cols, rows = [x for _, x, _ in found], [y for _, _, y in found]
    assert min(cols) * 1024 <= m.left < (max(cols) + 1) * 1024
    assert min(rows) * 1024 <= m.top
    assert (max(rows) + 1) * 1024 >= m.top + m.height / m.scale


def test_the_heights_land_where_they_are_on_the_map() -> None:
    m = _map(_track(100))
    m.terrain = {k: _hill(m, k) for k in keys(m)}
    g = grid(m)
    assert g.step == pytest.approx(2 * m.scale)  # a height every two of the map's pixels
    assert g.x0 <= 0
    assert g.y0 <= 0
    assert g.x0 + (g.z.shape[1] - 1) * g.step >= m.width
    assert g.y0 + (g.z.shape[0] - 1) * g.step >= m.height
    top = np.unravel_index(np.nanargmax(g.z), g.z.shape)
    assert g.x0 + top[1] * g.step == pytest.approx(m.width / 2, abs=g.step)
    assert g.y0 + top[0] * g.step == pytest.approx(m.height / 2, abs=g.step)


def test_a_terrain_tile_that_cannot_be_read_leaves_its_place_blank(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="roadbook")
    m = _map(_track(100))
    m.terrain = dict.fromkeys(keys(m), b"<html>")
    assert np.isnan(grid(m).z).all()
    assert lines(m) is None
    assert "left out: not a TIFF" in caplog.text


def test_the_index_lines_bear_their_heights_clear_of_the_route_and_labels(terrain: FakeTerrain) -> None:
    track = _track(100)
    m = _map(track)
    terrain.map = m
    report = add(m, load_config()["map"])
    assert report is not None
    assert report.made_from == [MADE_FROM]
    ((name, asked, max_age, offline),) = terrain.asked
    assert (name, asked, max_age, offline) == (TERRAIN, keys(m), 30, False)
    book = Roadbook("t", track.length_km, 0, 0, [], [], [], 0, 0, terrain=report)
    out = svg(m, book, {})
    every = int(re.findall(r'class="map-contours" data-every="(\d+)"', out)[0])
    assert every in {i for i, _ in STEPS}
    labels = [int(t) for t in re.findall(r'paint-order="stroke">(\d+)</text>', out)]
    assert labels
    index = next(x for i, x in STEPS if i == every)
    assert all(h % index == 0 for h in labels)
    # each label a little apart along its line, not on the route: x = 0 mm of the map's middle, which it goes up
    places = [(float(x), float(y)) for x, y in re.findall(r'<text x="([\d.]+)" y="([\d.]+)" transform="rotate', out)]
    assert all(abs(x - m.width / 2) > 1 for x, _ in places)
    for h in set(labels):
        same = [p for p, label in zip(places, labels, strict=True) if label == h]
        assert all(math.dist(a, b) > LABEL_GAP / 4 for i, a in enumerate(same) for b in same[i + 1 :])
    assert "Contours: Mapzen Terrain Tiles · SRTM, GMTED2010 courtesy of the U.S. Geological Survey" in out


def test_no_contour_lines_unless_asked_nor_on_the_topo_style(terrain: FakeTerrain) -> None:
    cfg = load_config()["map"]
    m = _map(_track(100))
    terrain.map = m
    assert add(m, cfg | {"contours": False}) is None
    assert add(_map(_track(100), style="topo"), cfg) is None  # it has its own
    assert terrain.asked == []
    book = Roadbook("t", 100, 0, 0, [], [], [], 0, 0)
    out = svg(m, book, {})
    assert "map-contours" not in out
    assert "Contours:" not in out


def test_the_credit_names_each_survey_the_terrain_was_made_from() -> None:
    made_from = [MADE_FROM, "eudem/eudem_dem_5deg_n45e000.tif", "srtm/N46E005.tif, ned13/x.tif", "newsurvey/a.tif"]
    assert credit(made_from) == (
        "Contours: Mapzen Terrain Tiles · SRTM, GMTED2010, 3DEP courtesy of the U.S. Geological Survey"
        " · EU-DEM produced using Copernicus data and information funded by the European Union"
        " · other surveys: github.com/tilezen/joerd/blob/master/docs/attribution.md"
    )
    assert credit(["kartverket/a.tif"]) == "Contours: Mapzen Terrain Tiles · © Kartverket"
    assert credit([]) == "Contours: Mapzen Terrain Tiles"


# --- in the road book


def test_the_contours_setting_needs_a_map_without_its_own() -> None:
    cfg = load_config()
    assert cfg["map"]["contours"] is True
    assert st.MAP_CONTOURS.when is not None
    assert st.MAP_CONTOURS.when(cfg)
    assert not st.MAP_CONTOURS.when(cfg | {"map": cfg["map"] | {"style": "topo"}})
    assert not st.MAP_CONTOURS.when(cfg | {"map": cfg["map"] | {"enabled": False}})


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_draws_the_contours_and_sums_up_their_terrain(
    terrain: FakeTerrain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def make(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401  the map, kept for the terrain to fit it
        made = real(*args, **kwargs)
        terrain.map = made[0]
        return made

    real = __import__("roadbook.build", fromlist=["make_map"]).make_map
    monkeypatch.setattr("roadbook.build.make_map", make)
    out = tmp_path / "rb.html"
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(out)])
    assert result.exit_code == 0, result.output
    n = len(terrain.asked[0][1])
    assert f"Contours: {n} of {n} terrain tiles (AWS Terrain Tiles)" in result.output
    assert 'class="map-contours"' in out.read_text(encoding="utf-8")
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(out), "--no-map-contours"])
    assert result.exit_code == 0, result.output
    assert len(terrain.asked) == 1
    assert "Contours:" not in result.output
    assert 'class="map-contours"' not in out.read_text(encoding="utf-8")
