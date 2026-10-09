"""The map page: the whole route on one printed map, with its stops, towns and climbs, tied to the strips by km.

A picture made once, with the road book: the tiles (tiles.py) in Web Mercator at the zoom that fits the route to the
page, and over them, drawn as SVG so they print crisp, the route, each POI at its place as its emoji, the towns named
for the busy stops, the climbs' names at their summits, start, finish, and a km marker every few km. The tiles go in
as data URIs: the HTML stays one file, which prints offline.

The zoom is the one that draws the tiles nearest their size on a screen (96 dpi), so their own names print about as
legible as they read there: a route fits an A4 page in a dozen to forty tiles, on a page turned whichever way shows it
larger. Labels go on one by one, each where it overlaps none before it, nor the map's edge, and hides as little of the
route as it can: start, finish and the km markers on their places; the names beside theirs, leaving the POIs' spots
clear; then each POI as near its place as there is room, a line back to it when moved.
"""

from __future__ import annotations

import base64
import html
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from .tiles import OSM
from .tiles import fetch as fetch_tiles
from .tiles import style as tile_style

if TYPE_CHECKING:
    from .model import Roadbook, Stop, Track
    from .osm import Report
    from .tiles import Key

log = logging.getLogger(__name__)

TILE = 256  # px, a tile's side
SCREEN = 25.4 / 96  # mm a pixel takes on a screen
PAD = 12.0  # mm kept between the route and the map's edges, for its labels
TITLE_H = 10.0  # mm the page's title takes above the map, with some to spare: a map a hair too tall prints two pages
MIN_ZOOM = 3
TURN = 1.15  # the map's page turns from the others' way when that shows the route this many times larger
MAX_LAT = 85.0511  # where Web Mercator's square world ends
EARTH_M = 40_075_016.686  # the equator's length

# what goes on the map, mm
ROUTE, CASING = "#d6006f", "#fff"
ROUTE_W, CASING_W = 0.75, 1.6
STEP = 0.4  # the route is drawn through points this far apart on paper
EMOJI = 3.0  # an emoji's font size; its box is a little larger
BADGE = 3.6  # the white disc under it
END = 4.0  # start and finish, a little larger
NAME = 2.7  # a town's or climb's name
KM_FONT = 2.0  # a km marker's
CHAR_W = 0.6  # a character's width, in font sizes, bold
SPACE = 0.5  # kept clear around each label
HALO = 'stroke="#fff" stroke-width="0.7" stroke-linejoin="round" paint-order="stroke"'
CLIMB = "#92400e"  # the climbs' brown, as on the strips
CHECKPOINT = "#1d4ed8"
MERGE = 4.0  # POIs of a kind closer than this on paper show as one emoji, with their count
KM_GAP = 25.0  # km markers at least this far apart along the route, when picked to suit the scale
KM_STEPS = (1, 2, 5, 10, 20, 25, 50, 100, 200, 500)
SCALE_BAR = 35.0  # the scale bar's longest length
SAME_NAME = 40.0  # a name shown again only this far from where it already is
MOVED = 0.5  # a mark further than this from its place gets a line back to it
MARK_RINGS, NAME_RINGS = 4, 8  # how far out a mark, a name, may go for room: in rings of its size, of its gap

# the cost of a label's place: overlapping another label or the map's edge, by mm²; hiding the route, by point
# under it; and how far it is from its own place, by mm. Overlaps are worth avoiding at any price, the route
# mostly, the distance only between places otherwise as good.
OVERLAP_COST, ROUTE_COST, DISTANCE_COST = 100.0, 0.5, 2.0
SPOT_COST = 10.0  # a POI's own spot under a name: its emoji would have to move


def world(lat: Any, lon: Any) -> tuple[np.ndarray, np.ndarray]:  # noqa: ANN401  a float or an array of them
    """Web Mercator: where points lie on the world's map at zoom 0, one tile of 256 px, from its top left corner."""
    phi = np.radians(np.clip(np.asarray(lat, dtype=float), -MAX_LAT, MAX_LAT))
    return (np.asarray(lon, dtype=float) + 180) / 360 * TILE, (1 - np.arcsinh(np.tan(phi)) / np.pi) / 2 * TILE


@dataclass
class RouteMap:
    """The map's frame on the world at its zoom, its tiles, and the route it shows."""

    style: str
    zoom: int
    left: float  # px at `zoom` from the world's left edge to the map's
    top: float
    scale: float  # mm a tile's pixel takes on paper
    width: float  # mm
    height: float
    track: Track
    landscape: bool = False  # the page it is printed on
    tiles: dict[Key, bytes] = field(default_factory=dict)  # a tile missing is left blank

    def mm(self, lat: Any, lon: Any) -> tuple[np.ndarray, np.ndarray]:  # noqa: ANN401  a float or an array
        """Where points lie on the map, mm from its top left corner."""
        x, y = world(lat, lon)
        k = 2**self.zoom
        return (x * k - self.left) * self.scale, (y * k - self.top) * self.scale

    def cells(self) -> list[tuple[int, int]]:
        """The tiles under the map, by column and row at its zoom; a column may run past the world's edge."""
        span = range(math.floor(self.left / TILE), math.floor((self.left + self.width / self.scale) / TILE) + 1)
        bottom = math.floor((self.top + self.height / self.scale) / TILE) + 1
        return [(c, r) for r in range(max(0, math.floor(self.top / TILE)), min(2**self.zoom, bottom)) for c in span]

    def key(self, col: int, row: int) -> Key:
        return self.zoom, col % 2**self.zoom, row

    @property
    def m_per_mm(self) -> float:
        """Metres on the ground for a mm on paper, at the map's middle."""
        lat = np.degrees(
            np.arctan(np.sinh(np.pi * (1 - 2 * (self.top + self.height / 2 / self.scale) / TILE / 2**self.zoom)))
        )
        return EARTH_M * math.cos(math.radians(float(lat))) / (TILE * 2**self.zoom) / self.scale


def _fit(x: np.ndarray, y: np.ndarray, area: tuple[float, float]) -> float:
    """The mm a pixel at zoom 0 may take for the points `x`, `y` (at zoom 0) to fit `area` (width, height, mm), within
    PAD of its edges."""
    width, height = area
    return min((width - 2 * PAD) / max(np.ptp(x), 1e-9), (height - 2 * PAD) / max(np.ptp(y), 1e-9))


def frame(
    x: np.ndarray, y: np.ndarray, area: tuple[float, float], max_zoom: int
) -> tuple[int, float, float, float, float]:
    """(zoom, left, top, scale, height) of a map in `area` (width, height, mm) showing the points `x`, `y` (at zoom 0).

    The zoom draws its tiles nearest their size on a screen, the points fit within PAD of the edges, and the map is as
    tall as they need. A short route is shown with more around it, rather than its tiles blown up.
    """
    width, height = area
    fit = _fit(x, y, area)
    zoom = min(max(round(math.log2(fit / SCREEN)), MIN_ZOOM), max_zoom)
    scale = min(fit / 2**zoom, SCREEN * math.sqrt(2))
    height = min(height, float(np.ptp(y)) * 2**zoom * scale + 2 * PAD)
    left = (float(x.min()) + float(x.max())) / 2 * 2**zoom - width / 2 / scale
    top = (float(y.min()) + float(y.max())) / 2 * 2**zoom - height / 2 / scale
    return zoom, left, top, scale, height


def make(
    track: Track, cfg: dict[str, Any], area: tuple[float, float], *, landscape: bool = False, offline: bool = False
) -> tuple[RouteMap, Report]:
    """The map of `track` on a page leaving `area` (width, height, upright) mm inside its margins, with its tiles:
    those not cached are fetched, unless `offline`.

    The page is turned as the road book's other pages are (`landscape`), unless turned the other way it shows the
    route TURN times larger: a route running east to west fills a landscape page better.
    """
    name, s = tile_style(str(cfg["style"]))
    x, y = world(track.lat, track.lon)
    upright, turned = (area[0], area[1] - TITLE_H), (area[1], area[0] - TITLE_H)
    own, other = (turned, upright) if landscape else (upright, turned)
    if _fit(x, y, other) > TURN * _fit(x, y, own):
        own, landscape = other, not landscape
    zoom, left, top, scale, height = frame(x, y, own, s.max_zoom)
    m = RouteMap(name, zoom, left, top, scale, own[0], height, track, landscape)
    keys = list(dict.fromkeys(m.key(*c) for c in m.cells()))  # once each, should the map wrap round the world
    log.info(
        "Map: %s, %s, zoom %d, 1:%s, %d tiles",
        name,
        "landscape" if landscape else "portrait",
        zoom,
        f"{m.m_per_mm * 1000:,.0f}",
        len(keys),
    )
    m.tiles, report = fetch_tiles(name, keys, cfg["max_age_days"], offline=offline)
    return m, report


# ---------------------------------------------------------------- placing the labels


class Placer:
    """Puts each label where it overlaps no label placed before, nor the map's edge, and hides as little as it can of
    the route and of the spots asked to be kept clear; among places otherwise as good, the nearest its own."""

    def __init__(self, width: float, height: float, route: tuple[np.ndarray, np.ndarray]) -> None:
        self.width, self.height = width, height
        self.route = np.column_stack(route)
        self.spots = np.empty((0, 2))
        self.boxes = np.empty((0, 4))  # x0, y0, x1, y1 of each label placed

    def keep_clear(self, x: np.ndarray, y: np.ndarray) -> None:
        """Spots the labels to come had better not hide, at SPOT_COST each: those the next ones will be placed on."""
        self.spots = np.column_stack([x, y])

    def take(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Keep a box from the labels to come: a mark of fixed place, the scale, the credit."""
        self.boxes = np.vstack([self.boxes, [x0, y0, x1, y1]])

    def place(self, centres: np.ndarray, prefer: np.ndarray, w: float, h: float) -> tuple[float, float]:
        """The best of `centres` (n x 2) for the middle of a `w` x `h` label, each at a cost `prefer` of its own on top;
        its box, and SPACE around it, is then taken."""
        w, h = w + SPACE, h + SPACE
        x0, y0 = centres[:, 0] - w / 2, centres[:, 1] - h / 2
        x1, y1 = x0 + w, y0 + h
        b = self.boxes
        ox = np.clip(np.minimum(x1[:, None], b[:, 2]) - np.maximum(x0[:, None], b[:, 0]), 0, None)
        oy = np.clip(np.minimum(y1[:, None], b[:, 3]) - np.maximum(y0[:, None], b[:, 1]), 0, None)
        inside = np.clip(np.minimum(x1, self.width) - np.maximum(x0, 0), 0, None) * np.clip(
            np.minimum(y1, self.height) - np.maximum(y0, 0), 0, None
        )

        def hidden(points: np.ndarray) -> np.ndarray:
            px, py = points[:, 0], points[:, 1]
            return ((px >= x0[:, None]) & (px <= x1[:, None]) & (py >= y0[:, None]) & (py <= y1[:, None])).sum(axis=1)

        cost = ((ox * oy).sum(axis=1) + w * h - inside) * OVERLAP_COST + prefer
        cost += hidden(self.route) * ROUTE_COST + hidden(self.spots) * SPOT_COST
        i = int(np.argmin(cost))
        self.take(float(x0[i]), float(y0[i]), float(x1[i]), float(y1[i]))
        return float(centres[i, 0]), float(centres[i, 1])

    def near(self, x: float, y: float, w: float, h: float) -> tuple[float, float]:
        """A mark's place: its own, or as near as there is room, in rings around it."""
        rings = [(0.0, 0.0)]
        step = max(w, h) + SPACE
        for k in range(1, MARK_RINGS + 1):
            rings += [
                (math.cos(a) * k * step, math.sin(a) * k * step)
                for a in np.linspace(0, 2 * math.pi, 8 * k, endpoint=False)
            ]
        offsets = np.array(rings)
        return self.place(offsets + np.array([x, y]), np.hypot(offsets[:, 0], offsets[:, 1]) * DISTANCE_COST, w, h)

    def beside(self, x: float, y: float, w: float, h: float, gap: float) -> tuple[float, float, int]:
        """A name's place, next to (x, y), `gap` from it: right, left, above, below, the corners, then further out.
        Also how far out: 1 is right next to it."""
        places, prefer, ring = [], [], []
        for k in range(1, NAME_RINGS + 1):
            g, d = gap * k, gap * k * 0.7
            for n, (dx, dy) in enumerate(
                [
                    (g + w / 2, 0),
                    (-g - w / 2, 0),
                    (0, -g - h / 2),
                    (0, g + h / 2),
                    (d + w / 2, -d - h / 2),
                    (-d - w / 2, -d - h / 2),
                    (d + w / 2, d + h / 2),
                    (-d - w / 2, d + h / 2),
                ]
            ):
                places.append((x + dx, y + dy))
                prefer.append((k - 1) * 4 * gap * DISTANCE_COST + n * 0.1)
                ring.append(k)
        centres = np.array(places)
        cx, cy = self.place(centres, np.array(prefer), w, h)
        i = int(np.argmin(np.hypot(centres[:, 0] - cx, centres[:, 1] - cy)))
        return cx, cy, ring[i]


# ---------------------------------------------------------------- drawing


def _text_w(text: str, size: float) -> float:
    return len(text) * size * CHAR_W + 0.6


def _route(m: RouteMap) -> tuple[np.ndarray, np.ndarray]:
    """The route on paper through points STEP apart: enough for a smooth line, a light file and a quick placing."""
    x, y = m.mm(m.track.lat, m.track.lon)
    along = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    keep = np.flatnonzero(np.diff(np.floor(along / STEP), prepend=-1) > 0)
    keep = np.append(keep, len(x) - 1) if keep[-1] != len(x) - 1 else keep
    return x[keep], y[keep]


def _at(m: RouteMap, km: float) -> tuple[float, float]:
    lat, lon = m.track.at(km)
    x, y = m.mm(lat, lon)
    return float(x), float(y)


def km_every(route: tuple[np.ndarray, np.ndarray], length_km: float) -> float:
    """The km between two markers that keeps them about KM_GAP apart along the route on paper."""
    on_paper = float(np.hypot(np.diff(route[0]), np.diff(route[1])).sum())
    mm_per_km = on_paper / max(length_km, 1e-9)
    return next((k for k in KM_STEPS if k * mm_per_km >= KM_GAP), KM_STEPS[-1])


def _emoji(x: float, y: float, emoji: str, *, size: float = EMOJI, count: int = 1, cls: str = "map-poi") -> str:
    """An emoji on a white disc, with its count when it stands for several POIs."""
    sup = (
        f'<text x="{x + size * 0.5:.2f}" y="{y - size * 0.25:.2f}" font-size="{size * 0.5:.2f}" font-weight="700" '
        f"{HALO}>{count}</text>"
        if count > 1
        else ""
    )
    return (
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{size * BADGE / EMOJI / 2:.2f}" fill="#fff" fill-opacity="0.85" '
        f'stroke="#999" stroke-width="0.12"/>'
        f'<text class="{cls}" x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" text-anchor="middle" '
        f'dominant-baseline="central">{html.escape(emoji)}</text>{sup}'
    )


def _pill(x: float, y: float, text: str, colour: str) -> str:
    w, h = _text_w(text, KM_FONT) + 0.6, KM_FONT * 1.4
    return (
        f'<rect x="{x - w / 2:.2f}" y="{y - h / 2:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{h / 2:.2f}" '
        f'fill="#fff" stroke="{colour}" stroke-width="0.3"/>'
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{KM_FONT}" font-weight="700" fill="{colour}" text-anchor="middle" '
        f'dominant-baseline="central">{html.escape(text)}</text>'
    )


def _leader(x0: float, y0: float, x1: float, y1: float, colour: str = "#333") -> str:
    """A line from a mark's own place to where its label went, and a dot on the place."""
    return (
        f'<line x1="{x0:.2f}" y1="{y0:.2f}" x2="{x1:.2f}" y2="{y1:.2f}" stroke="{colour}" stroke-width="0.18"/>'
        f'<circle cx="{x0:.2f}" cy="{y0:.2f}" r="0.4" fill="{colour}" stroke="#fff" stroke-width="0.1"/>'
    )


@dataclass
class _Layers:
    """The SVG drawn over the tiles, from the bottom up."""

    leaders: list[str] = field(default_factory=list)
    emojis: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)  # km markers, checkpoints, summits: over an emoji short of room
    names: list[str] = field(default_factory=list)

    def all(self) -> str:
        return "".join(self.leaders + self.emojis + self.marks + self.names)


def _pois(m: RouteMap, stops: list[Stop], categories: dict[str, Any]) -> list[tuple[str, float, float, int]]:
    """(emoji, x, y, count) for every kept POI, by category in their order: those of a kind closer than MERGE on
    paper as one, at their middle."""
    order = list(categories)
    groups: list[tuple[str, list[tuple[float, float]]]] = []
    pois = sorted(
        (p for s in stops for p in s.pois if p.category in categories),
        key=lambda p: (order.index(str(p.category)), p.km),
    )
    last: dict[str, list[tuple[float, float]]] = {}
    for p in pois:
        x, y = (float(v) for v in m.mm(p.lat, p.lon))
        same = last.get(str(p.category))
        if same and math.hypot(x - same[0][0], y - same[0][1]) < MERGE:
            same.append((x, y))
            continue
        last[str(p.category)] = [(x, y)]
        groups.append((categories[str(p.category)]["emoji"], last[str(p.category)]))
    return [(e, sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts), len(pts)) for e, pts in groups]


def svg(m: RouteMap, book: Roadbook, categories: dict[str, Any], every_km: float = 0) -> str:
    """The map, as an SVG `m.width` x `m.height` mm: its tiles, the route, and what the road book shows on it.

    A km marker every `every_km` km; 0 picks one to suit the scale (km_every).
    """
    route = _route(m)
    placer = Placer(m.width, m.height, route)
    layers = _Layers()
    foot = _scale_and_credit(m, book, placer)

    for kind, km, emoji in (("start", 0.0, "🟢"), ("finish", book.length_km, "🏁")):
        cx, cy = _near(placer, layers, *_at(m, km), END * 1.1, END * 1.1)
        layers.names.append(_emoji(cx, cy, emoji, size=END, cls=f"map-{kind}"))

    checkpoints = [it for it in book.items if it.kind == "checkpoint"]
    every = every_km or km_every(route, book.length_km)
    # none right by the finish, nor where a checkpoint already is
    kms = [every * k for k in range(1, int(book.length_km / every) + 1) if every * k < book.length_km - every / 4]
    marks = [(km, f"{km:g}", ROUTE) for km in kms if all(abs(km - c.km) > every / 4 for c in checkpoints)]
    marks += [(c.km, c.label, CHECKPOINT) for c in checkpoints]
    for km, text, colour in sorted(marks):
        cx, cy = _near(placer, layers, *_at(m, km), _text_w(text, KM_FONT) + 0.6, KM_FONT * 1.4, colour=colour)
        layers.marks.append(_pill(cx, cy, text, colour))

    summits = [(c.name, *_at(m, c.end_km)) for c in book.climbs if c.name is not None]
    for _, x, y in summits:
        placer.take(x - 1.3, y - 1.3, x + 1.3, y + 1.0)
        layers.marks.append(
            f'<path d="M{x:.2f},{y - 1.3:.2f} L{x + 1.2:.2f},{y + 0.8:.2f} L{x - 1.2:.2f},{y + 0.8:.2f} Z" '
            f'fill="{CLIMB}" stroke="#fff" stroke-width="0.3"/>'
        )

    # the names beside their places, keeping the POIs' own spots clear: then each emoji finds its spot free
    pois = _pois(m, book.stops, categories)
    placer.keep_clear(np.array([x for _, x, _, _ in pois]), np.array([y for _, _, y, _ in pois]))
    towns = []
    for s in book.stops:
        if s.town:
            x, y = m.mm(sum(p.lat for p in s.pois) / len(s.pois), sum(p.lon for p in s.pois) / len(s.pois))
            towns.append((s.town, float(x), float(y)))
    _names(towns, "town", "#222", placer, layers, gap=2.0)
    _names(summits, "climb", CLIMB, placer, layers, gap=1.6)
    placer.keep_clear(np.empty(0), np.empty(0))
    for emoji, x, y, count in pois:
        cx, cy = _near(placer, layers, x, y, EMOJI * 1.15 + (EMOJI * 0.5 if count > 1 else 0), EMOJI * 1.15)
        layers.emojis.append(_emoji(cx, cy, emoji, count=count))

    tiles = _tiles(m)
    return (
        f'<svg class="route-map" viewBox="0 0 {m.width:.2f} {m.height:.2f}" width="{m.width:.2f}mm" '
        f'height="{m.height:.2f}mm" font-size="{NAME}">'
        f'<rect width="{m.width:.2f}" height="{m.height:.2f}" fill="#eceae4"/>{tiles}'
        f'<polyline points="{_points(route)}" fill="none" stroke="{CASING}" stroke-width="{CASING_W}" '
        f'stroke-linejoin="round" stroke-linecap="round" stroke-opacity="0.9"/>'
        f'<polyline class="map-route" points="{_points(route)}" fill="none" stroke="{ROUTE}" stroke-width="{ROUTE_W}" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f"{layers.all()}{foot}"
        f'<rect width="{m.width:.2f}" height="{m.height:.2f}" fill="none" stroke="#777" stroke-width="0.3"/>'
        "</svg>"
    )


def _near(  # noqa: PLR0913  a place, a size and a colour
    placer: Placer, layers: _Layers, x: float, y: float, w: float, h: float, *, colour: str = "#333"
) -> tuple[float, float]:
    """Where a mark of its own place goes (Placer.near); moved from it, a line back to it."""
    cx, cy = placer.near(x, y, w, h)
    if math.hypot(cx - x, cy - y) > MOVED:
        layers.leaders.append(_leader(x, y, cx, cy, colour))
    return cx, cy


def _names(
    names: list[tuple[str, float, float]], cls: str, colour: str, placer: Placer, layers: _Layers, gap: float
) -> None:
    """Each name beside its place, once in a while: a town the route comes back to is named once per visit."""
    shown: list[tuple[str, float, float]] = []
    italic = ' font-style="italic"' if cls == "town" else ""
    for name, x, y in names:
        if any(n == name and math.hypot(x - sx, y - sy) < SAME_NAME for n, sx, sy in shown):
            continue
        shown.append((name, x, y))
        w, h = _text_w(name, NAME), NAME * 1.25
        cx, cy, ring = placer.beside(x, y, w, h, gap)
        if ring > 1:  # far from its place: a line back to it
            ex = min(max(x, cx - w / 2), cx + w / 2)
            ey = min(max(y, cy - h / 2), cy + h / 2)
            layers.leaders.append(_leader(x, y, ex, ey, colour))
        layers.names.append(
            f'<text class="map-{cls}" x="{cx:.2f}" y="{cy:.2f}" font-size="{NAME}" font-weight="700"{italic} '
            f'fill="{colour}" text-anchor="middle" dominant-baseline="central" {HALO}>{html.escape(name)}</text>'
        )


def _points(route: tuple[np.ndarray, np.ndarray]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(*route, strict=True))


def _tiles(m: RouteMap) -> str:
    """The tiles as images, each a hair larger than its place, so no seam shows between two."""
    size = TILE * m.scale
    out = []
    for col, row in m.cells():
        image = m.tiles.get(m.key(col, row))
        if image is None:
            continue
        kind = "jpeg" if image[:3] == b"\xff\xd8\xff" else "png"
        x, y = (col * TILE - m.left) * m.scale, (row * TILE - m.top) * m.scale
        out.append(
            f'<image x="{x:.3f}" y="{y:.3f}" width="{size + 0.05:.3f}" height="{size + 0.05:.3f}" '
            f'preserveAspectRatio="none" href="data:image/{kind};base64,{base64.b64encode(image).decode()}"/>'
        )
    return "".join(out)


def _scale_and_credit(m: RouteMap, book: Roadbook, placer: Placer) -> str:
    """A scale bar at the bottom left, and the tiles' credit at the bottom right, on white; their room is taken."""
    _, s = tile_style(m.style)
    credit = s.credit
    named = any(r is not None and r.found for r in (book.towns, book.climb_names))
    if named and "OpenStreetMap contributors" not in credit:  # the names come from OpenStreetMap
        credit += f" · Names {OSM}"
    size, line = 1.7, 1.7 * 1.3
    room = m.width - 2 - SCALE_BAR - 8
    lines = [credit]
    if _text_w(credit, size) * 0.9 > room:  # two lines, cut at the middle-most dot
        parts = credit.split(" · ")
        cut = min(range(1, len(parts)), key=lambda i: abs(len(" · ".join(parts[:i])) - len(credit) / 2), default=1)
        lines = [" · ".join(parts[:cut]), " · ".join(parts[cut:])]
    w = max(_text_w(t, size) * 0.9 for t in lines) + 1.2
    h = line * len(lines) + 0.8
    x0, y0 = m.width - w, m.height - h
    placer.take(x0, y0, m.width, m.height)
    out = [f'<rect x="{x0:.2f}" y="{y0:.2f}" width="{w:.2f}" height="{h:.2f}" fill="#fff" fill-opacity="0.85"/>']
    out += [
        f'<text class="map-credit" x="{m.width - 0.6:.2f}" y="{y0 + 0.4 + line * (i + 0.75):.2f}" font-size="{size}" '
        f'fill="#333" text-anchor="end">{html.escape(t)}</text>'
        for i, t in enumerate(lines)
    ]

    mm_per_km = 1000 / m.m_per_mm
    km = max((k for k in (0.5, *KM_STEPS) if k * mm_per_km <= SCALE_BAR), default=0.5)
    bar = km * mm_per_km
    bx, by = 2.0, m.height - 2.5
    placer.take(0, by - 4, bx + bar + 10, m.height)
    out.append(
        f'<rect x="0" y="{by - 4:.2f}" width="{bx + bar + 10:.2f}" height="{m.height - by + 4:.2f}" fill="#fff" '
        'fill-opacity="0.85"/>'
        f'<rect x="{bx:.2f}" y="{by - 0.6:.2f}" width="{bar / 2:.2f}" height="1" fill="#222"/>'
        f'<rect x="{bx + bar / 2:.2f}" y="{by - 0.6:.2f}" width="{bar / 2:.2f}" height="1" fill="#fff" stroke="#222" '
        'stroke-width="0.15"/>'
        f'<text x="{bx:.2f}" y="{by - 1.3:.2f}" font-size="1.8">0</text>'
        f'<text class="map-scale" x="{bx + bar:.2f}" y="{by - 1.3:.2f}" font-size="1.8" text-anchor="middle">'
        f"{km:g} km</text>"
    )
    return "".join(out)
