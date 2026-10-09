import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from typer.testing import CliRunner

from roadbook import settings as st
from roadbook.build import build
from roadbook.cli import app
from roadbook.config import load_config
from roadbook.model import Climb, Item, Poi, Roadbook, Stop, Track
from roadbook.osm import M_PER_DEG, Report
from roadbook.render import printable_area, render_html
from roadbook.routemap import (
    PAD,
    SCREEN,
    SPACE,
    TILE,
    TITLE_H,
    Placer,
    RouteMap,
    _Layers,
    _names,
    frame,
    km_every,
    make,
    svg,
    world,
)

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
A4 = printable_area("A4")
PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff\xe0"


def _track(km: float, *, east: bool = False) -> Track:
    """A straight road from 45° N, 5° E, north or east, a point every 100 m."""
    dist = np.arange(0, km * 1000 + 1, 100.0)
    step = dist / M_PER_DEG
    lat, lon = (
        (np.full_like(dist, 45.0), 5.0 + step / math.cos(math.radians(45))) if east else (45.0 + step, 5.0 + 0 * step)
    )
    return Track("t", lat, lon, np.zeros_like(dist), dist)


class FakeTiles:
    """Every tile there, a PNG or, for odd columns, a JPEG; records what was asked."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, list[Any], float, bool]] = []

    def __call__(
        self, name: str, keys: list[Any], max_age_days: float, *, offline: bool = False
    ) -> tuple[dict[Any, bytes], Report]:
        self.asked.append((name, keys, max_age_days, offline))
        source = {"satellite": "EOX", "topo": "OpenTopoMap"}.get(name, "OpenStreetMap France")
        tiles = {k: JPEG if k[1] % 2 else PNG for k in keys}
        return tiles, Report(asked=len(keys), found=len(keys), sources=[source], service=source)


@pytest.fixture
def tiles(monkeypatch: pytest.MonkeyPatch) -> FakeTiles:
    fake = FakeTiles()
    monkeypatch.setattr("roadbook.routemap.fetch_tiles", fake)
    return fake


# --- the frame


def test_web_mercator_puts_the_equator_and_greenwich_in_the_middle() -> None:
    assert world(0, 0) == (pytest.approx(TILE / 2), pytest.approx(TILE / 2))
    x, y = world(90, -180)  # the poles are cut at 85.05°, the square's edge
    assert (float(x), float(y)) == (pytest.approx(0), pytest.approx(0, abs=1e-3))


@pytest.mark.parametrize("km", [3, 40, 200, 1500])
def test_the_route_fits_the_page_with_its_tiles_near_their_size_on_screen(km: float) -> None:
    track = _track(km)
    x, y = world(track.lat, track.lon)
    zoom, left, top, scale, height = frame(x, y, (194, 271), 19)
    assert SCREEN / math.sqrt(2) <= scale <= SCREEN * math.sqrt(2)
    assert height <= 271
    xs, ys = (x * 2**zoom - left) * scale, (y * 2**zoom - top) * scale
    assert PAD - 0.01 <= xs.min() <= xs.max() <= 194 - PAD + 0.01
    assert PAD - 0.01 <= ys.min() <= ys.max() <= height - PAD + 0.01


def test_a_short_route_shows_more_around_it_rather_than_blown_up_tiles() -> None:
    track = _track(3)
    x, y = world(track.lat, track.lon)
    zoom, *_, scale, height = frame(x, y, (194, 271), 14)  # the satellite's closest
    assert zoom == 14
    assert scale == pytest.approx(SCREEN * math.sqrt(2))
    assert height < 271


@pytest.mark.parametrize(
    ("east", "landscape", "turned"),
    [
        (True, False, True),  # a road east on the strips' portrait pages: the map's page turns
        (False, False, False),
        (False, True, True),  # a road north on a line's landscape pages: upright
        (True, True, False),
    ],
)
def test_the_map_turns_its_page_when_that_shows_the_route_larger(
    tiles: FakeTiles, *, east: bool, landscape: bool, turned: bool
) -> None:
    m, _ = make(_track(150, east=east), load_config()["map"], A4, landscape=landscape)
    assert m.landscape == (landscape != turned)
    assert m.width == (A4[1] if m.landscape else A4[0])
    assert m.height <= (A4[0] if m.landscape else A4[1]) - TITLE_H
    assert tiles.asked


def test_a_route_a_little_wider_than_tall_keeps_the_pages_way() -> None:
    track = _track(100)  # 0.9° north, which Mercator stretches by 1 / cos(45°) on paper...
    track.lon = 5.0 + track.dist / track.dist[-1] * 1.1 * 0.9 / math.cos(math.radians(45))  # ...and 1.1 times that east
    x, y = world(track.lat, track.lon)
    assert np.ptp(x) / np.ptp(y) == pytest.approx(1.1, abs=0.02)
    m, _ = make(track, load_config()["map"], A4, landscape=False)
    assert not m.landscape  # a landscape page would show it a little larger: not enough to turn it


def test_each_tile_under_the_map_is_asked_for_once(tiles: FakeTiles) -> None:
    cfg = load_config()["map"] | {"style": "topo"}
    m, report = make(_track(80), cfg, A4, offline=True)
    ((name, keys, max_age, offline),) = tiles.asked
    assert (name, max_age, offline) == ("topo", 30, True)
    assert len(keys) == len(set(keys)) == len(m.cells())
    assert all(z == m.zoom for z, _, _ in keys)
    assert report.found == len(keys)


# --- placing the labels


def test_a_mark_takes_its_own_place_when_free_or_the_nearest_free_one() -> None:
    placer = Placer(100, 100, (np.array([]), np.array([])))
    assert placer.near(50, 50, 3, 3) == (50, 50)
    x, y = placer.near(50, 50, 3, 3)  # taken now
    assert math.hypot(x - 50, y - 50) == pytest.approx(3 + SPACE)
    assert len(placer.boxes) == 2


def test_a_mark_hides_the_route_only_if_nothing_else_is_near() -> None:
    route = (np.full(200, 50.0), np.linspace(0, 100, 200))  # a road down the middle
    placer = Placer(100, 100, route)
    assert placer.near(50, 50, 3, 3) == (50, 50)  # on its own place, the road there or not
    x, _ = placer.beside(50, 20, 10, 3, 1.0)[:2]
    assert abs(x - 50) >= 5  # a name beside the road, not across it


def test_a_name_goes_right_of_its_place_else_where_there_is_room() -> None:
    placer = Placer(100, 100, (np.array([]), np.array([])))
    assert placer.beside(50, 50, 10, 3, 1.0) == (pytest.approx(56), 50, 1)
    assert placer.beside(50, 50, 10, 3, 1.0) == (pytest.approx(44), 50, 1)  # the right is taken now
    x, _, _ = placer.beside(98, 20, 10, 3, 1.0)  # no room right, before the map's edge
    assert x < 98


def test_a_name_pushed_away_from_its_place_has_a_line_back_to_it() -> None:
    placer = Placer(100, 100, (np.array([]), np.array([])))
    placer.take(30, 40, 70, 60)  # all round its place, taken
    layers = _Layers()
    _names([("Sault", 50, 50), ("Sault", 52, 50)], "town", "#222", placer, layers, gap=2.0)
    assert len(layers.names) == 1  # the same name right by: once
    assert len(layers.leaders) == 1
    assert 'class="map-town"' in layers.names[0]


def test_km_markers_keep_their_distance_on_paper() -> None:
    hundred_mm = (np.linspace(0, 100, 101), np.zeros(101))
    assert km_every(hundred_mm, 100) == 25  # 1 mm a km
    assert km_every(hundred_mm, 10) == 5  # 10 mm a km
    assert km_every(hundred_mm, 100_000) == 500


# --- the picture


def _map(track: Track, tiles: dict[Any, bytes] | None = None, style: str = "osm") -> RouteMap:
    x, y = world(track.lat, track.lon)
    zoom, left, top, scale, height = frame(x, y, (194, 271), 19)
    m = RouteMap(style, zoom, left, top, scale, 194, height, track)
    m.tiles = tiles if tiles is not None else {m.key(*c): JPEG if c[0] % 2 else PNG for c in m.cells()}
    return m


def _poi(track: Track, km: float, category: str, east_m: float = 0.0) -> Poi:
    lat, lon = track.at(km)
    return Poi("p", category, lat, lon + east_m / M_PER_DEG / math.cos(math.radians(lat)), km=km, category=category)


def _book(track: Track, stops: list[Stop], climbs: list[Climb], items: list[Item] | None = None) -> Roadbook:
    return Roadbook("t", track.length_km, 0, 0, items or [], stops, climbs, 0, 0)


def _texts(svg_text: str, cls: str) -> list[str]:
    return re.findall(rf'class="{cls}"[^>]*>([^<]*)<', svg_text)


def test_the_map_shows_the_route_its_pois_towns_and_climbs() -> None:
    track = _track(100)
    cats = load_config()["categories"]
    stops = [
        Stop([_poi(track, 30, "water"), _poi(track, 30.2, "water"), _poi(track, 30.3, "bakery")], town="Sault"),
        Stop([_poi(track, 31, "toilets")], town="Sault"),  # the same town, close by: named once
        Stop([_poi(track, 80, "water")], town="Sault"),  # the same name far away: another visit, named again
    ]
    climbs = [Climb(40, 52, 600, 5, 8, "2", name="Col de la Croix"), Climb(60, 62, 90, 4, 5, "")]
    items = [Item("checkpoint", 50, 0, label="Lunch")]
    out = svg(_map(track), _book(track, stops, climbs, items), cats, every_km=10)
    assert out.startswith('<svg class="route-map" viewBox="0 0 194.00 ')
    assert out.count("<image ") == len(_map(track).cells())
    assert "data:image/png;base64," in out
    assert "data:image/jpeg;base64," in out
    assert out.count('class="map-route"') == 1
    assert _texts(out, "map-start") == ["🟢"]
    assert _texts(out, "map-finish") == ["🏁"]
    pills = re.findall(r'dominant-baseline="central">(\w+)</text>', out)
    assert [p for p in pills if p.isdigit()] == ["10", "20", "30", "40", "60", "70", "80", "90"]  # 50 is Lunch's
    assert "Lunch" in pills
    assert _texts(out, "map-town") == ["Sault", "Sault"]
    assert _texts(out, "map-climb") == ["Col de la Croix"]  # an unnamed climb has no label
    # the two waters at km 30 make one emoji, with their count; each stop's kinds are there
    assert sorted(_texts(out, "map-poi")) == sorted([cats[c]["emoji"] for c in ("water", "bakery", "toilets", "water")])
    assert re.search(r'font-weight="700" [^>]*>2</text>', out)
    assert "© OpenStreetMap contributors" in _texts(out, "map-credit")[0]
    assert re.fullmatch(r"\d+ km", _texts(out, "map-scale")[0])


def test_a_map_without_tiles_still_shows_the_route() -> None:
    track = _track(50)
    out = svg(_map(track, tiles={}), _book(track, [], []), load_config()["categories"])
    assert "<image " not in out
    assert 'class="map-route"' in out
    assert 'fill="#eceae4"' in out  # the blank under the missing tiles


def test_the_satellite_map_credits_the_names_to_openstreetmap() -> None:
    track = _track(50)
    book = _book(track, [], [])
    m = _map(track, style="satellite")
    assert "Names ©" not in "".join(_texts(svg(m, book, {}), "map-credit"))
    book.towns = Report(asked=1, found=1)
    credit = " ".join(_texts(svg(m, book, {}), "map-credit"))
    assert "Copernicus Sentinel data 2017" in credit
    assert "Names © OpenStreetMap contributors" in credit


# --- in the road book


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_map_page_comes_between_the_strips_and_the_reference_sheet(tiles: FakeTiles) -> None:
    cfg = load_config()
    book = build(HILLY, cfg)
    assert book.route_map is not None
    assert book.tiles is not None
    assert book.tiles.found == len(tiles.asked[0][1])
    html = render_html(book, cfg)
    assert (
        html.index('<div class="sheet">') < html.index('<div class="map-page">') < html.index('<div class="details">')
    )
    assert "@page map { size: A4 portrait; }" in html
    cfg["map"]["enabled"] = False
    book = build(HILLY, cfg)
    assert (book.route_map, book.tiles) == (None, None)
    assert '<div class="map-page">' not in render_html(book, cfg)


@pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")
def test_the_cli_takes_the_map_options_and_sums_up_its_tiles(tiles: FakeTiles, tmp_path: Path) -> None:
    out = tmp_path / "rb.html"
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(out), "--map-style", "satellite"])
    assert result.exit_code == 0, result.output
    n = len(tiles.asked[0][1])
    assert tiles.asked[0][0] == "satellite"
    assert f"Map: {n} of {n} tiles (EOX)" in result.output
    assert "Copernicus Sentinel data 2017" in out.read_text(encoding="utf-8")
    result = CliRunner().invoke(app, [str(HILLY), "-o", str(out), "--no-map"])
    assert result.exit_code == 0, result.output
    assert len(tiles.asked) == 1
    assert "Map:" not in result.output
    assert '<div class="map-page">' not in out.read_text(encoding="utf-8")


def test_refresh_fetches_the_tiles_again() -> None:
    cfg = load_config()
    st.refresh(cfg)
    assert cfg["map"]["max_age_days"] == 0
    assert st.MAP_STYLE.when is not None
    assert st.MAP_STYLE.when(cfg)
    cfg["map"]["enabled"] = False
    assert not st.MAP_STYLE.when(cfg)  # no style to pick without a map
