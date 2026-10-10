"""Contour lines on the map: the lie of the land around the route, from Mapzen's Terrain Tiles.

The terrain tiles (tiles.TERRAIN) are GeoTIFFs of 512 x 512 heights in whole metres, in Web Mercator as the map's
tiles are, so one ZOOM_OUT zooms out from the map's spans 4 x 4 of them with a height every two of their pixels:
about 0.6 mm on paper, a few tiles for a whole map. Smoothed a little, that draws lines which read smooth.

The lines are found by marching squares, at the interval that keeps them MIN_GAP apart on all but the steepest of
the map's slopes; every fifth (or fourth) is an index line, bolder. They go over the tiles and under all else, thin
and brown as on a walker's map: there to show the lie of the land, not to hide the roads.
"""

from __future__ import annotations

import logging
import math
import struct
import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from .tiles import SOURCES, TERRAIN
from .tiles import fetch as fetch_tiles

if TYPE_CHECKING:
    from .osm import Report
    from .routemap import Placer, RouteMap
    from .tiles import Key

log = logging.getLogger(__name__)

ZOOM_OUT = 2  # the terrain tiles' zoom, from the map's: a height every two of the map's pixels
SIDE = 512  # a terrain tile's heights across
SMOOTH = (1, 4, 6, 4, 1)  # the heights are averaged with their neighbours', in these shares, across and down

# (interval, index interval), m: the closest the lines may be, keeping MIN_GAP mm between them on the map's slopes
# but its steepest STEEP %
STEPS = ((10, 50), (20, 100), (25, 100), (50, 250), (100, 500), (200, 1000), (250, 1000), (500, 2500))
MIN_GAP = 1.0
STEEP = 10

# what goes on the map, mm
COLOUR = {"satellite": "#fff"}  # by style; brown on the others
BROWN = "#a0642d"
HALO = {"#fff": "#333"}  # by colour; white under the others
LINE_W, INDEX_W = 0.1, 0.22
OPACITY = 0.6
LABEL = 1.7  # an index line's height: its font size
LABEL_GAP = 80.0  # along an index line between two of its heights, at least
LABEL_STEP = 2.0  # the places tried along it for one, this far apart
STRAIGHT = 0.95  # a height goes where the line runs this straight under it: as far from end to end as it is long
STEP = 0.5  # a line is drawn through points this far apart on paper: about its heights' own
SPECK = 2.0  # a line shorter than this is left out: a bump the terrain model may not even have

# for each way the four corners of a cell may lie above the level (top left 8, top right 4, bottom right 2, bottom
# left 1), the sides a line crosses it by, in pairs: top 0, right 1, bottom 2, left 3. A saddle's lines (5, 10) part
# the corners on the other side of the level from its middle: (middle above, middle below).
SIDES = {
    1: ((2, 3),),
    2: ((1, 2),),
    3: ((1, 3),),
    4: ((0, 1),),
    6: ((0, 2),),
    7: ((0, 3),),
    8: ((0, 3),),
    9: ((0, 2),),
    11: ((0, 1),),
    12: ((1, 3),),
    13: ((1, 2),),
    14: ((2, 3),),
}
SADDLES = {5: (((0, 3), (1, 2)), ((0, 1), (2, 3))), 10: (((0, 1), (2, 3)), ((0, 3), (1, 2)))}


def _pairs() -> np.ndarray:
    """SIDES and SADDLES as one table, by whether the middle is above the level and by case: the sides of the line
    crossing the cell, then of a saddle's second one; -1 for none."""
    table = np.full((2, 16, 2, 2), -1)
    for case, ((s, e),) in SIDES.items():
        table[:, case, 0] = s, e
    for case, (above, below) in SADDLES.items():
        table[1, case], table[0, case] = above, below
    return table


PAIRS = _pairs()

# each survey the terrain tiles are made from, by its folder in their note, and the credit it asks for
USGS = "courtesy of the U.S. Geological Survey"
SURVEYS = {
    "srtm": ("SRTM", USGS),
    "gmted": ("GMTED2010", USGS),
    "ned": ("3DEP", USGS),
    "ned13": ("3DEP", USGS),
    "ned_topobathy": ("3DEP", USGS),
    "etopo1": ("ETOPO1", "by NOAA"),
    "greatlakes": ("Great Lakes bathymetry", "by NOAA"),
    "eudem": ("EU-DEM", "produced using Copernicus data and information funded by the European Union"),
    "kartverket": ("", "© Kartverket"),
    "uk_lidar": ("", "© Environment Agency copyright and/or database right 2015"),
    "austria": ("", "© offene Daten Österreichs - Digitales Geländemodell (DGM) Österreich"),
    "nrcan_cdem": ("", "contains information licensed under the Open Government Licence - Canada"),
    "nzlinz": ("", "Crown copyright © 2011 Land Information New Zealand and the New Zealand Government"),
    "mx_lidar": ("", "source: INEGI, Continental relief, 2016"),
    "pgdc_5m": (
        "ArcticDEM",
        "created from DigitalGlobe, Inc., imagery, funded under NSF awards 1043681, 1559691 and 1542736",
    ),
}
OTHERS = "other surveys: github.com/tilezen/joerd/blob/master/docs/attribution.md"


@dataclass
class Grid:
    """Heights, m, at regular places on the map: z[row, col] at (x0 + col * step, y0 + row * step) mm; NaN where the
    terrain tiles have none."""

    z: np.ndarray
    x0: float
    y0: float
    step: float


def add(m: RouteMap, cfg: dict[str, Any], *, offline: bool = False) -> Report | None:
    """Fetch the terrain under `m` for its contour lines, if `cfg` (the map's settings) asks for them: not on the topo
    style, which has its own. The report of its tiles; None without contour lines."""
    if not cfg["contours"] or m.style == "topo":
        return None
    m.terrain, report = fetch_tiles(TERRAIN, keys(m), cfg["max_age_days"], offline=offline)
    return report


def _cells(m: RouteMap) -> tuple[int, float, range, range]:
    """The terrain tiles' zoom, their heights a map's pixel, and the columns and rows of those under the map."""
    zoom = max(0, min(m.zoom - ZOOM_OUT, SOURCES[TERRAIN].max_zoom))
    f = 2.0 ** (zoom + 1 - m.zoom)  # a terrain tile's heights are one zoom in from its own: 512 for 256 pixels
    x0, x1 = m.left * f, (m.left + m.width / m.scale) * f
    y0, y1 = m.top * f, (m.top + m.height / m.scale) * f
    cols = range(math.floor(x0 / SIDE), math.floor(x1 / SIDE) + 1)
    rows = range(max(0, math.floor(y0 / SIDE)), min(2**zoom, math.floor(y1 / SIDE) + 1))
    return zoom, f, cols, rows


def keys(m: RouteMap) -> list[Key]:
    """The terrain tiles under the map, once each, should it wrap round the world."""
    zoom, _, cols, rows = _cells(m)
    return list(dict.fromkeys((zoom, c % 2**zoom, r) for r in rows for c in cols))


def grid(m: RouteMap) -> Grid:
    """The heights over the map from its terrain tiles, a height beyond each edge; NaN for a tile it hasn't."""
    zoom, f, cols, rows = _cells(m)
    z = np.full((len(rows) * SIDE, len(cols) * SIDE), np.nan)
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            tif = m.terrain.get((zoom, c % 2**zoom, r))
            if tif is None:
                continue
            try:
                z[i * SIDE : (i + 1) * SIDE, j * SIDE : (j + 1) * SIDE] = heights(tif)
            except ValueError as exc:
                log.info("Contours: terrain tile %d/%d/%d left out: %s", zoom, c % 2**zoom, r, exc)
    step = m.scale / f
    x0 = ((cols[0] * SIDE + 0.5) / f - m.left) * m.scale
    y0 = ((rows[0] * SIDE + 0.5) / f - m.top) * m.scale
    c0, r0 = max(0, math.floor(-x0 / step) - 1), max(0, math.floor(-y0 / step) - 1)
    c1, r1 = math.ceil((m.width - x0) / step) + 2, math.ceil((m.height - y0) / step) + 2
    return Grid(_smooth(z[r0:r1, c0:c1]), x0 + c0 * step, y0 + r0 * step, step)


def _smooth(z: np.ndarray) -> np.ndarray:
    """Each height averaged with its neighbours' in SMOOTH's shares: the terrain's whole metres and its specks gone."""
    k = np.array(SMOOTH, dtype=float) / sum(SMOOTH)
    r = len(k) // 2
    for axis in (0, 1):
        padded = np.pad(z, [(r, r) if a == axis else (0, 0) for a in (0, 1)], mode="edge")
        n = z.shape[axis]
        z = sum(w * padded.take(range(i, i + n), axis=axis) for i, w in enumerate(k))
    return z


# ---------------------------------------------------------------- reading a terrain tile

_TYPES = {1: "B", 2: "s", 3: "H", 4: "I", 12: "d"}  # a TIFF tag's types, as struct reads them


class Tag:
    """The TIFF tags read, by number."""

    WIDTH, HEIGHT, BITS, COMPRESSION, SAMPLES, PREDICTOR = 256, 257, 258, 259, 277, 317
    TILE_W, TILE_H, OFFSETS, SIZES, FORMAT, NODATA = 322, 323, 324, 325, 339, 42113


DEFLATE = (8, 32946)
SIGNED = 2


def heights(tif: bytes) -> np.ndarray:
    """A terrain tile's SIDE x SIDE heights, m, NaN where it has none.

    The tiles are tiled TIFFs of 16-bit signed integers, deflated, each row's differences kept (the horizontal
    predictor). Raises ValueError for one that is not: a broken download, or a change of format.
    """
    try:
        tags, order = _tags(tif)

        def tag(n: int, default: int | None = None) -> Any:  # noqa: ANN401  a tag's first value
            return tags[n][0] if n in tags else default

        compression, predictor = tag(Tag.COMPRESSION, 1), tag(Tag.PREDICTOR, 1)
        if (
            (tag(Tag.WIDTH), tag(Tag.HEIGHT), tag(Tag.BITS), tag(Tag.SAMPLES, 1), tag(Tag.FORMAT, 1))
            != (SIDE, SIDE, 16, 1, SIGNED)
            or compression not in (1, *DEFLATE)
            or predictor not in {1, 2}
        ):
            msg = "not 512 x 512 16-bit heights, deflated or not"
            raise ValueError(msg)
        tw, th = tag(Tag.TILE_W), tag(Tag.TILE_H)
        across = -(-SIDE // tw)
        out = np.empty((-(-SIDE // th) * th, across * tw), dtype=np.int16)
        for k, (offset, size) in enumerate(zip(tags[Tag.OFFSETS], tags[Tag.SIZES], strict=True)):
            raw = tif[offset : offset + size]
            raw = zlib.decompress(raw) if compression in DEFLATE else raw
            block = np.frombuffer(raw, np.dtype(order + "i2"), count=tw * th).reshape(th, tw).astype(np.int16)
            if predictor == 2:  # noqa: PLR2004  differences along each row, which wrap as 16-bit integers do
                block = np.cumsum(block, axis=1, dtype=np.int16)
            r, c = divmod(k, across)
            out[r * th : (r + 1) * th, c * tw : (c + 1) * tw] = block
        whole = out[:SIDE, :SIDE]
        z = whole.astype(float)
        if Tag.NODATA in tags:
            z[whole == int(float(tags[Tag.NODATA].rstrip(b"\0")))] = np.nan
    except (struct.error, zlib.error, KeyError, IndexError, TypeError) as exc:
        raise ValueError(str(exc) or type(exc).__name__) from exc
    return z


def _tags(tif: bytes) -> tuple[dict[int, Any], str]:
    """A TIFF's first image's tags, by number: a tuple of numbers, or bytes for text; and its byte order."""
    order = {b"II": "<", b"MM": ">"}.get(tif[:2])
    if order is None or struct.unpack_from(order + "H", tif, 2)[0] != 42:  # noqa: PLR2004  the TIFF's mark
        msg = "not a TIFF"
        raise ValueError(msg)
    (at,) = struct.unpack_from(order + "I", tif, 4)
    (n,) = struct.unpack_from(order + "H", tif, at)
    tags: dict[int, Any] = {}
    for entry in range(at + 2, at + 2 + 12 * n, 12):
        number, kind, count = struct.unpack_from(order + "HHI", tif, entry)
        if (code := _TYPES.get(kind)) is None:
            continue
        where = entry + 8
        if struct.calcsize(code) * count > 4:  # noqa: PLR2004  else the value itself is there
            (where,) = struct.unpack_from(order + "I", tif, where)
        tags[number] = (
            tif[where : where + count] if code == "s" else struct.unpack_from(f"{order}{count}{code}", tif, where)
        )
    return tags, order


# ---------------------------------------------------------------- the lines


def interval(g: Grid) -> tuple[int, int]:
    """(interval, index interval), m: the closest lines that stay MIN_GAP apart on all but the steepest STEEP % of the
    map's land."""
    gy, gx = np.gradient(g.z, g.step)  # m a mm on paper
    with np.errstate(invalid="ignore"):
        slope = np.hypot(gx, gy)[g.z > 0]  # the sea floor is none of the ride's business
    slope = slope[np.isfinite(slope)]
    steep = float(np.percentile(slope, 100 - STEEP)) if slope.size else 0.0
    return next(((i, x) for i, x in STEPS if i >= steep * MIN_GAP), STEPS[-1])


def isolines(g: Grid, level: float) -> list[tuple[np.ndarray, np.ndarray, bool]]:
    """The lines at `level` on the map, by marching squares: (x, y, closed) each, mm, through points STEP apart."""
    z = g.z
    h, w = z.shape
    if h < 2 or w < 2:  # noqa: PLR2004  no cell
        return []
    with np.errstate(invalid="ignore"):
        up = (z >= level).astype(np.int8)
    case = up[:-1, :-1] * 8 + up[:-1, 1:] * 4 + up[1:, 1:] * 2 + up[1:, :-1]
    whole = np.isfinite(z)
    whole = whole[:-1, :-1] & whole[:-1, 1:] & whole[1:, 1:] & whole[1:, :-1]
    cells = np.flatnonzero(whole & (case % 15 != 0))  # those the level crosses: not all below it, nor all above
    i, j = np.divmod(cells, w - 1)
    middle = (z[i, j] + z[i, j + 1] + z[i + 1, j + 1] + z[i + 1, j]) / 4 >= level
    pairs = PAIRS[middle.astype(int), case.ravel()[cells]]

    # each side of a cell by the number of its edge: the edges across first, row by row, then those down
    n_across = h * (w - 1)
    sides = np.column_stack([i * (w - 1) + j, n_across + i * w + j + 1, (i + 1) * (w - 1) + j, n_across + i * w + j])
    second = pairs[:, 1, 0] >= 0  # a saddle's
    segments = np.concatenate(
        [np.take_along_axis(sides, pairs[:, 0], axis=1), np.take_along_axis(sides[second], pairs[second, 1], axis=1)]
    )
    edges, chains = _chains(segments)
    rows, cols = _crossings(z, level, edges)
    x, y = g.x0 + cols * g.step, g.y0 + rows * g.step
    lines = []
    for chain, closed in chains:
        cx, cy = _thin(x[chain], y[chain])
        if float(np.hypot(np.diff(cx), np.diff(cy)).sum()) >= SPECK:
            lines.append((cx, cy, closed))
    return lines


def _chains(segments: np.ndarray) -> tuple[np.ndarray, list[tuple[list[int], bool]]]:
    """The segments, edge to edge, joined into lines at the edges they share: those edges, once each, and each line
    as the indexes of its edges in turn, with whether it comes back round to its start. An edge is the side of two
    cells at most, so of two segments."""
    edges, ends = np.unique(segments.ravel(), return_inverse=True)
    ends = ends.reshape(-1, 2)
    n = len(edges)
    at, to = np.concatenate([ends[:, 0], ends[:, 1]]), np.concatenate([ends[:, 1], ends[:, 0]])
    order = np.argsort(at, kind="stable")
    at, to = at[order], to[order]
    first, linked = np.searchsorted(at, np.arange(n)), np.bincount(at, minlength=n)
    one = to[first].tolist()  # each edge's neighbours along the line, -1 for none
    other = np.where(linked == 2, to[np.minimum(first + 1, len(to) - 1)], -1).tolist()  # noqa: PLR2004
    seen = bytearray(n)
    chains = []
    for start in [*np.flatnonzero(linked == 1).tolist(), *range(n)]:  # the open lines first, from an end
        if seen[start]:
            continue
        seen[start] = 1
        chain, before, here = [start], -1, start
        while True:
            after = other[here] if one[here] == before else one[here]
            if after < 0 or seen[after]:
                break
            seen[after] = 1
            chain.append(after)
            before, here = here, after
        closed = after == start and len(chain) > 2  # noqa: PLR2004
        chains.append(([*chain, start] if closed else chain, closed))
    return edges, chains


def _crossings(z: np.ndarray, level: float, edges: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where `level` crosses each of the `edges` between the heights `z`, numbered as isolines() has them: in rows
    and columns, between those of their two ends."""
    h, w = z.shape
    n_across = h * (w - 1)
    across = edges < n_across
    down = ~across
    r = np.where(across, edges // (w - 1), (edges - n_across) // w)
    c = np.where(across, edges % (w - 1), (edges - n_across) % w)
    a, b = z[r, c], z[r + down, c + across]  # the other end: on the right of an edge across, below one down
    t = (level - a) / (b - a)  # one end above the level, the other below: never the same height
    return r + t * down, c + t * across


def _thin(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The line through points STEP apart, its ends kept."""
    along = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
    keep = np.flatnonzero(np.diff(np.floor(along / STEP), prepend=-1) > 0)
    keep = np.append(keep, len(x) - 1) if keep[-1] != len(x) - 1 else keep
    return x[keep], y[keep]


# ---------------------------------------------------------------- drawing


@dataclass
class Contours:
    """The lines to draw, each at its height, and the heights written on the index lines."""

    every: int  # m between two lines
    index: int  # m between two index lines
    colour: str
    lines: list[tuple[int, np.ndarray, np.ndarray, bool]]  # height, x, y, closed
    labels: list[str] = field(default_factory=list)  # SVG


def lines(m: RouteMap) -> Contours | None:
    """The contour lines over the map; None without terrain."""
    if not m.terrain:
        return None
    g = grid(m)
    if not np.isfinite(g.z).any():
        return None
    every, index = interval(g)
    low, high = float(np.nanmin(g.z)), float(np.nanmax(g.z))
    levels = range(max(every, math.ceil(low / every) * every), math.floor(high) + 1, every)  # none at sea level
    found = [(level, x, y, closed) for level in levels for x, y, closed in isolines(g, level)]
    log.info("Contours: every %d m, an index line every %d m, %d lines", every, index, len(found))
    return Contours(every, index, COLOUR.get(m.style, BROWN), found)


def label(c: Contours, placer: Placer) -> None:
    """Write its height on each index line, LABEL_GAP apart along it at least, where the line runs straight and the
    height hides nothing: the route, a label placed before."""
    halo = HALO.get(c.colour, "#fff")
    for level, x, y, closed in c.lines:
        if level % c.index:
            continue
        text = str(level)
        w, h = len(text) * LABEL * 0.6 + 0.4, LABEL
        along = np.concatenate([[0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])
        after, before = 0.0, along[-1] - w  # the next label no nearer the last, nor round a closed line to the first
        for s in np.arange(w, along[-1] - w, LABEL_STEP):
            if not after <= s <= before:
                continue
            (x0, x1, cx), (y0, y1, cy) = (np.interp([s - w / 2, s + w / 2, s], along, v) for v in (x, y))
            if math.hypot(x1 - x0, y1 - y0) < STRAIGHT * w:
                continue
            a = math.atan2(y1 - y0, x1 - x0)
            a = a - math.pi if a > math.pi / 2 else a + math.pi if a <= -math.pi / 2 else a  # upright
            bw, bh = abs(w * math.cos(a)) + abs(h * math.sin(a)), abs(w * math.sin(a)) + abs(h * math.cos(a))
            if not placer.free(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2):
                continue
            placer.take(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
            c.labels.append(
                f'<text x="{cx:.2f}" y="{cy:.2f}" transform="rotate({math.degrees(a):.1f} {cx:.2f} {cy:.2f})" '
                f'font-size="{LABEL}" fill="{c.colour}" text-anchor="middle" dominant-baseline="central" '
                f'stroke="{halo}" stroke-width="0.5" stroke-linejoin="round" paint-order="stroke">{text}</text>'
            )
            if closed and not after:  # its first: the last no nearer to it, round the line
                before = min(before, s + along[-1] - LABEL_GAP)
            after = s + LABEL_GAP


def svg(c: Contours | None) -> str:
    """The contour lines, as an SVG group, their heights on top; "" without."""
    if c is None:
        return ""
    thin = "".join(_d(x, y, closed) for level, x, y, closed in c.lines if level % c.index)
    bold = "".join(_d(x, y, closed) for level, x, y, closed in c.lines if not level % c.index)
    return (
        f'<g class="map-contours" data-every="{c.every}">'
        f'<g fill="none" stroke="{c.colour}" stroke-opacity="{OPACITY}" stroke-linejoin="round" stroke-linecap="round">'
        f'<path stroke-width="{LINE_W}" d="{thin}"/><path class="map-index" stroke-width="{INDEX_W}" d="{bold}"/></g>'
        f'{"".join(c.labels)}</g>'
    )


def _d(x: np.ndarray, y: np.ndarray, closed: bool) -> str:  # noqa: FBT001  a line's own flag
    """A line as an SVG path: its first point, then each from the one before, to a tenth of a mm. Those steps are
    between points rounded first, so the line never drifts; their short numbers keep the file light."""
    tenths = np.round(np.column_stack([x, y]) * 10).astype(int)
    steps = np.diff(tenths, axis=0)
    steps = steps[(steps != 0).any(axis=1)]  # points a tenth apart at most may round to the same
    moves = " ".join(f"{_tenths(a)},{_tenths(b)}" for a, b in steps.tolist())
    return f"M{_tenths(tenths[0, 0])},{_tenths(tenths[0, 1])}l{moves}{'z' if closed else ''}"


def _tenths(n: int) -> str:
    """`n` tenths, as briefly as SVG reads them: 0.3 as .3, -0.3 as -.3."""
    return f"{n / 10:g}".replace("0.", ".", 1) if -10 < n < 10 else f"{n / 10:g}"  # noqa: PLR2004  a whole mm


def credit(made_from: list[str]) -> str:
    """The contour lines' credit: the terrain tiles', and each survey's they were made from (the tiles' notes)."""
    folders = {f.strip().split("/")[0] for note in made_from for f in note.split(",") if f.strip()}
    by: dict[str, list[str]] = {}
    for folder in sorted(folders & SURVEYS.keys(), key=list(SURVEYS).index):
        name, words = SURVEYS[folder]
        if name not in by.setdefault(words, []):
            by[words].append(name)
    parts = [f"{', '.join(n for n in names if n)} {words}".strip() for words, names in by.items()]
    if folders - SURVEYS.keys():
        parts.append(OTHERS)
    return " · ".join([SOURCES[TERRAIN].credit, *parts])
