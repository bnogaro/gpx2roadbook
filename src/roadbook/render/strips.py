"""The strips: each item a row, its emojis fitted to the room left, the rows poured into strips with a profile down
their edge; or with the line layout, a ribbon of tokens."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from roadbook.kinds import KINDS, climb_of
from roadbook.model import Glyph
from roadbook.svg import grade_colour, gutter_svg

if TYPE_CHECKING:
    from collections.abc import Callable

    from roadbook.model import Item, Roadbook, Stop

# Row geometry (mm). Must match the CSS variables in the template.
MAIN_H, SUB_H, HDR_H, PAD, EMO_H = 4.8, 3.2, 4.4, 1.0, 4.4
KM_COL, EMOJI_W, COUNT_W, MORE_W = 9.5, 4.9, 1.3, 2.0
LAYOUT_WIDTH = {"strip": 35.0, "line": 16.0}
WHOLE_KM = 10.0  # a climb this long shows its length in whole km ("21km"): the decimal is noise
DIST_W = 6.0  # an inline "↓10.6" distance to the next row, at the end of the main line
LABEL_W = 8.0  # room kept for a short label ("Cat HC", "1240 m") next to a climb's own emoji
GUTTER_MIN_SPAN_M = 300.0  # a strip's profile spans at least this much elevation, so rolling ground stays flat
HEAD_H = 3.2  # a heading line above a row: a busy stop's town, a climb's name
FRAME_PAD = 0.8  # under a town's row, so its frame's bottom line clears the emojis
OVERRUN = 6.0  # mm the route's last row (the finish) may run a strip past its length, rather than start one alone


def _wrap(emojis: list[Glyph], widths: list[float], max_emojis: int) -> tuple[list[list[Glyph]], bool, float]:
    """Pour emojis (in priority order) into lines of the given widths, mm. Returns (lines, truncated, first width).

    Only the last line keeps room for a "+"; the ones before it overflow onto the next instead. A line may stay
    empty when not even one emoji fits there, which lets a crowded climb row send its stop down a line.
    """
    lines: list[list[Glyph]] = []
    first_w = 0.0
    i = 0
    for n, avail in enumerate(widths):
        last = n == len(widths) - 1
        line: list[Glyph] = []
        used = 0.0
        while i < len(emojis) and i < max_emojis:
            # one COUNT_W per superscript, whatever its length: charging per character would widen 10+ POI tokens
            w = EMOJI_W + (COUNT_W if emojis[i].sup else 0)
            more_w = MORE_W if last and i + 1 < len(emojis) else 0
            if used + w + more_w > avail:
                break
            line.append(emojis[i])
            used += w
            i += 1
        lines.append(line)
        if n == 0:
            first_w = used
        if i >= min(len(emojis), max_emojis):
            break
    while len(lines) > 1 and not lines[-1]:
        lines.pop()
    truncated = i < len(emojis)
    return lines, truncated, first_w + (MORE_W if truncated and len(lines) == 1 else 0)


def _first_only(emojis: list[Glyph], avail: float) -> tuple[list[Glyph], bool, float]:
    """For when _wrap keeps nothing: the first emoji alone, shedding its count, then the "+", until it fits.

    The count goes first: that more kinds are there matters more than how many of the first. If even the bare
    emoji does not fit, it is shown anyway, a hair over its room rather than gone.
    """
    bare = Glyph(emojis[0].emoji, dim=emojis[0].dim)
    if len(emojis) > 1 and avail >= EMOJI_W + MORE_W:
        return [bare], True, EMOJI_W + MORE_W
    return [bare], False, EMOJI_W


@dataclass(frozen=True)
class RowLayout:
    """What every row of one render shares, worked out once by render_html() from the [render] settings."""

    avail: float  # mm left for emojis on a row's main line
    max_emojis: int
    sep: str  # between the figures of a climb's stats line
    range_m: float  # a stop stretching at least this far shows where it ends
    emoji_lines: int = 1  # lines a row's emojis may fill; more than 1 wraps a crowded stop below its main line
    wrap_avail: float = 0.0  # mm for emojis on those extra lines, which carry no ↓ distance
    grades: tuple[tuple[float, str], ...] = ()  # a climb's colour by its average grade: see svg.grade_colour()


@dataclass(frozen=True)
class Head:
    """A heading line above a row: a busy stop's town ("town"), or a climb's name ("col"). See _add_heads()."""

    cls: str  # its CSS class: "town" | "col"
    text: str


@dataclass(frozen=True)
class Row:
    """An item as a strip shows it in a row, or a ribbon in a token: its texts, emojis and size."""

    kind: str
    km: str
    label: str
    emojis: list[Glyph]  # on the main line: the row's own emoji first, if it has one, then its stop's
    emoji_lines: list[list[Glyph]]  # a crowded stop's emojis carried on below the main line
    more: bool  # some emojis are still left out: a "+" after the last
    w: float  # mm along a ribbon
    sub: str = ""  # a climb's length and grade, or a checkpoint's km to go, under the main line
    sub_short: str = ""  # a ribbon's: a climb's length and grade, or where a long stop ends
    km_end: str = ""  # a long stop's last km, at the foot of the km column
    gain: str = ""  # a climb's ↗ metres, at the end of its sub line
    dist: str = ""  # "↓4.3" to the next row, at the end of the main line
    leg_short: str = ""  # the same at the foot of a ribbon's token
    grade: str = ""  # a climb or summit row's colour, by the climb's average grade
    heads: tuple[Head, ...] = ()  # lines above the row: see _add_heads()
    town: bool = False  # framed with its town's name

    @property
    def h(self) -> float:
        """Its height down a strip, mm: its lines, and the room under a town's frame."""
        # the last km takes the km column of the row's last emoji line; with none, a short line of its own
        end_h = SUB_H if self.km_end and not self.emoji_lines else 0
        h = MAIN_H + EMO_H * len(self.emoji_lines) + (SUB_H if self.sub else 0) + end_h
        return h + (FRAME_PAD if self.town else 0) + HEAD_H * len(self.heads)


def _row(it: Item, book: Roadbook, layout: RowLayout) -> Row:
    avail, max_emojis, sep = layout.avail, layout.max_emojis, layout.sep
    kind = KINDS[it.kind]
    label = kind.label(it, book)
    if kind.emoji:
        # the row's own emoji leads; a stop snapped onto a climb's foot or summit follows in the space left.
        # The row is too narrow for both a stop and a full label, so with a stop on board the category
        # shrinks to a superscript on ⛰️, and the summit's elevation only shows if room is left.
        sup = kind.sup(it)
        room = avail - EMOJI_W - COUNT_W * len(sup)
        # at least one stop emoji, even when max_emojis is 1 or the room is too tight for its count and a "+":
        # a snapped stop must not vanish behind its climb
        stop_emojis, extra, more, stop_w = _emoji_lines(it.emojis, room, max(1, max_emojis - 1), layout)
        if it.emojis and not stop_emojis and not extra:
            stop_emojis, more, stop_w = _first_only(it.emojis, room)
        emojis = [Glyph(kind.emoji, sup), *stop_emojis]
        emoji_w = EMOJI_W + stop_w
        if kind.label_if_room and stop_emojis and stop_w + LABEL_W > room:  # alone, the label clips at worst
            label = ""
    else:
        emojis, extra, more, emoji_w = _emoji_lines(it.emojis, avail, max_emojis, layout)
    sub, sub_short, km_end, gain = kind.line(it, book), "", "", ""
    if kind.stats:
        c = climb_of(it)
        length = f"{c.length_km:.0f}" if c.length_km >= WHOLE_KM else f"{c.length_km:.1f}"
        sub = f"{length}km{sep}{c.avg_grade:.1f}%"
        # the climb's gain is what a rider plans by: never cut, while length and grade may be, should the line be short
        gain = f"↗︎{c.gain_m:.0f}"  # the arrow as text (VS15): narrower than its emoji
        sub_short = f"{length}km {c.avg_grade:.0f}%"
    elif it.stop and _spans(it.stop, layout.range_m):
        # the row sits at the stop's first POI; a long stop says where its last one is, so nothing hides past it: on a
        # strip down the km column, under the first; a ribbon's token has no column for it
        km_end = f"{it.stop.km_end:.1f}"
        sub_short = f"→{km_end}"
    # the distance to the next row: at the end of the main line on a strip, at the foot of a ribbon's token
    leg = f"{it.dist_to_next:.1f}" if it.dist_to_next is not None else ""
    return Row(
        kind=it.kind,
        km=f"{it.km:.1f}",
        label=label,
        emojis=emojis,
        emoji_lines=extra,
        more=more,
        w=max(11.0, emoji_w + 2 * PAD + 0.6, 15.0 if sub_short else 0.0),
        sub=sub,
        sub_short=sub_short,
        km_end=km_end,
        gain=gain,
        dist=f"↓{leg}" if leg else "",
        leg_short=leg,
        grade=grade_colour(it.climb.avg_grade, layout.grades) if it.climb and layout.grades else "",
    )


def _emoji_lines(
    emojis: list[Glyph], avail: float, max_emojis: int, layout: RowLayout
) -> tuple[list[Glyph], list[list[Glyph]], bool, float]:
    """A row's emojis: those on its main line, the lines wrapped below it, whether some are still left out, and the
    main line's width."""
    widths = [avail] + [layout.wrap_avail] * (layout.emoji_lines - 1)
    lines, truncated, first_w = _wrap(emojis, widths, max_emojis)
    return lines[0], lines[1:], truncated, first_w


def _spans(stop: Stop, range_m: float) -> bool:
    return (stop.km_end - stop.km) * 1000 >= range_m


def _add_heads(rows: list[Row], book: Roadbook) -> list[Row]:
    """The rows with their heading lines, each HEAD_H mm tall: a named stop's town, then a named climb's name.

    A town is not named again on the next row in the same town: its stops make one (towns.group), but two too far
    apart to join may still follow each other, and naming it once is enough on a narrow strip. A climb's name goes
    above its foot's row, the one that always shows (its summit only gets a row when a stop is there), so it
    announces the climb before it starts and sits right above the length and grade it goes with. A climb row whose
    foot is in a named town gets both lines: where you are, then what comes.
    """
    out: list[Row] = []
    last = None  # the last town shown
    for it, row in zip(book.items, rows, strict=True):
        heads: list[Head] = []
        town = it.stop.town if it.stop else None
        if town and town != last:
            heads.append(Head("town", town))
        framed = bool(heads)  # a named town gets a frame round its name and row
        last = town or last
        if it.kind == "climb" and it.climb and it.climb.name:
            heads.append(Head("col", it.climb.name))
        out.append(replace(row, heads=tuple(heads), town=framed))
    return out


def _paginate[T](rows: list[T], capacity: float, size: Callable[[T], float]) -> list[list[T]]:
    """Fill strips with rows up to `capacity` mm; only the very last row may overrun a strip, by up to OVERRUN."""
    pages: list[list[T]] = [[]]
    used = 0.0
    for i, r in enumerate(rows):
        room = capacity + (OVERRUN if i == len(rows) - 1 else 0)
        if pages[-1] and used + size(r) > room:
            pages.append([])
            used = 0.0
        pages[-1].append(r)
        used += size(r)
    return pages


@dataclass(frozen=True)
class Strip:
    """A strip of rows, or a ribbon of tokens, as printed."""

    rows: list[Row]
    first: str  # its first row's km
    last: str
    over: float  # how far the last strip runs past its length to keep the finish, 0 for the others
    gutter: str = ""  # the profile down its right edge, an SVG: see _gutters()


@dataclass(frozen=True)
class Geometry:
    """The row geometry the template's CSS variables take, mm."""

    gutter: float  # the profile's width, 0 without one
    main_h: float = MAIN_H
    head_h: float = HEAD_H
    frame_pad: float = FRAME_PAD
    sub_h: float = SUB_H
    hdr_h: float = HDR_H
    km_col: float = KM_COL
    pad: float = PAD
    emo_h: float = EMO_H


def _gutters(
    strips: list[Strip], book: Roadbook, width: float, height: float, grades: tuple[tuple[float, str], ...]
) -> list[str]:
    """Each strip's profile, whose km scale follows its rows: one anchor at each row's main line."""
    p = book.profile
    if p is None:  # render_html() only asks for gutters when there is a profile
        return [""] * len(strips)
    out: list[str] = []
    first = 0  # index in book.items of the strip's first row
    for s in strips:
        items = book.items[first : first + len(s.rows)]
        anchors, y = [], 0.0
        for it, row in zip(items, s.rows, strict=True):
            anchors.append((it.km, y + HEAD_H * len(row.heads) + MAIN_H / 2))  # under its heading lines
            y += row.h
        first += len(items)
        if first < len(book.items):  # carry the line to the bottom edge, towards the next strip's first row
            anchors.append((book.items[first].km, height + s.over))
        # each strip gets its own scale: one shared with a 1500 m pass would flatten every other strip
        lo, hi = p.ele_range(anchors[0][0], anchors[-1][0])
        span = (lo, max(hi, lo + GUTTER_MIN_SPAN_M))
        out.append(gutter_svg(p, book.climbs, anchors, width, height + s.over, span, grades=grades))
    return out
