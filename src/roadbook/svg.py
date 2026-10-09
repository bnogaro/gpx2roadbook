from __future__ import annotations

import html
import itertools
import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from .model import Climb
    from .profile import Profile

type Scale = Sequence[Sequence[float | str]]  # [[from this grade up, %, colour], ...], by rising grade

GROUND = "#efefef"  # lighter than the gentlest climb, printed in grey too
CLIMB = "#f59e0b"  # a climb's colour without a grade scale
PIECE_KM = 1.0  # a climb is coloured a kilometre at a time, by each one's average grade, as race books do


def grade_colour(grade: float, scale: Scale) -> str:
    """The colour `scale` gives a grade, in %; below its first step, the first colour all the same."""
    colour = str(scale[0][1])
    for start, c in scale:
        if grade >= float(start):
            colour = str(c)
    return colour


def gutter_svg(  # noqa: PLR0913  the grade colours come on top of the geometry
    profile: Profile,
    climbs: list[Climb],
    anchors: list[tuple[float, float]],
    width_mm: float,
    height_mm: float,
    ele_range: tuple[float, float],
    *,
    grades: Scale = (),
) -> str:
    """A vertical profile running down a strip, aligned to its rows rather than to a km scale.

    `anchors` are (km, y_mm) pairs, one per row: the stretch of road between two anchors is drawn across
    exactly the vertical space between those rows, so each row's dot sits on the curve where the rider is.
    Elevation grows to the right, mapped from `ele_range` (lowest, highest) onto the gutter's width. Each stretch of
    a climb, about a kilometre, takes the colour `grades` gives its average grade: the steep ones show where they are,
    and a grade wavering around a step doesn't streak the climb.
    """
    lo, hi = ele_range
    span = max(hi - lo, 1.0)
    step_km = profile.step / 1000

    kms: list[float] = []
    ys: list[float] = []
    for (k0, y0), (k1, y1) in itertools.pairwise(anchors):
        seg = np.append(np.arange(k0, k1, step_km), k1) if k1 > k0 else np.array([k0, k1])
        kms += seg.tolist()
        ys += (y0 + (seg - k0) / max(k1 - k0, 1e-9) * (y1 - y0)).tolist()
    km = np.array(kms)
    y = np.array(ys)
    x = (np.interp(km * 1000, profile.x, profile.e) - lo) / span * width_mm

    def area(points: np.ndarray, cls: str, fill: str) -> str:
        xs, yy = x[points], y[points]
        pts = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(xs, yy, strict=True))
        return f'<polygon class="{cls}" points="0,{yy[0]:.2f} {pts} 0,{yy[-1]:.2f}" fill="{fill}"/>'

    shapes = [area(np.arange(len(km)), "ground", GROUND)]
    for c in climbs:
        points = np.flatnonzero((km >= c.start_km) & (km <= c.end_km))
        if not points.size:
            continue
        edges = np.linspace(c.start_km, c.end_km, max(1, round(c.length_km / PIECE_KM)) + 1)
        ele = np.interp(edges * 1000, profile.x, profile.e)
        piece_grades = np.diff(ele) / np.maximum(np.diff(edges) * 1000, 1e-9) * 100
        piece = np.clip(np.searchsorted(edges, km[points], side="right") - 1, 0, len(piece_grades) - 1)
        fills = [grade_colour(g, grades) if grades else CLIMB for g in piece_grades[piece]]
        start = 0
        for end in [*(i + 1 for i in range(len(fills) - 1) if fills[i + 1] != fills[i]), len(fills)]:
            # one point into the next stretch, so two colours meet without a gap between them
            shapes.append(area(points[start : end + 1], "climb", fills[start]))
            start = end
    line = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(x, y, strict=True))
    dots = "".join(
        f'<circle cx="{(np.interp(k * 1000, profile.x, profile.e) - lo) / span * width_mm:.2f}" '
        f'cy="{yy:.2f}" r="0.45" fill="#111"/>'
        for k, yy in anchors
        if yy < height_mm  # a trailing anchor on the bottom edge only continues the line
    )
    return (
        f'<svg class="gutter" viewBox="0 0 {width_mm} {height_mm}" width="{width_mm}mm" height="{height_mm}mm">'
        f"{''.join(shapes)}"
        f'<polyline points="{line}" fill="none" stroke="#444" stroke-width="0.25"/>'
        f"{dots}"
        f"</svg>"
    )


# A climb page's card (mm): rotated labels above, the profile, the km scale below
CARD_LABELS_H, CARD_PLOT_H, CARD_AXIS_H = 22.0, 38.0, 5.0  # the most each may take
CARD_BARE_H = 3.0  # the labels' band with no label in it
CARD_GROUND_H = 3.5  # under the lowest slab's edge, at the least: room for its grade
LABEL_MM_PER_CHAR, LABEL_MM_PER_EMOJI = 1.25, 2.4  # a rotated label's length, per character, at its font size
EXAGGERATION = 2.0  # heights drawn this many times steeper than the km: the same on every card, so grades compare
CARD_LEFT, CARD_RIGHT = 2.0, 10.0  # the summit's altitude takes the right margin
MARK_GAP = 2.8  # marks closer than this share one label
ALT_GAP = 7.0  # an altitude label needs this much room from the last one
KM_GAP = 5.0  # and a km label on the scale from its neighbours
KM_EVERY = (0.2, 0.5, 1, 2, 5, 10)  # when the steps crowd the scale, km labels only at the multiples of one of these
GRADE_MM, GRADE_MIN_MM = 2.6, 1.6  # a slab's grade: this large, or smaller as the slab narrows, down to this
GRADE_EM_PER_CHAR = 0.6  # the grade's width, per character at its font size, with a margin
LABEL_CHARS = 26  # a longer label is cut, rather than run past the card's top


def _ink(colour: str) -> str:
    """Black or white, whichever reads better on `colour` ("#rrggbb")."""
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return "#111" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "#fff"  # noqa: PLR2004  half way, by eye


def climb_svg(
    sections: Sequence[tuple[float, float, float, float]],
    marks: Sequence[tuple[float, str]],
    width_mm: float,
    *,
    grades: Scale = (),
) -> str:
    """A climb's profile, a section at a time: (start km, end km, elevation at each), foot first, as race books draw
    it. Each section is a slab coloured by its grade, the grade written in it if it fits; the altitude at the slabs'
    edges, the km from the foot below. `marks`, (km, text), hang above on a line to the road: the places and stops on
    the way.
    """
    foot, top = sections[0][0], sections[-1][1]
    e_lo, e_hi = sections[0][2], max(max(s[2], s[3]) for s in sections)
    lowest = min(min(s[2], s[3]) for s in sections)
    run = max(top - foot, 1e-6) * 1000  # metres
    plot_w = width_mm - CARD_LEFT - CARD_RIGHT
    # mm a metre up: EXAGGERATION times a metre along, or less on a card that would be too tall
    up = min(plot_w * EXAGGERATION / run, (CARD_PLOT_H - CARD_GROUND_H) / max(e_hi - lowest, 1.0))
    floor = math.floor((lowest - CARD_GROUND_H / up) / 50) * 50  # some ground under the foot
    rise = max(e_hi - floor, 1.0)
    # across the page, unless that makes it too tall: then narrower, the slope as steep as on every other card
    plot_h = plot_w / run * EXAGGERATION * rise
    if plot_h > CARD_PLOT_H:
        plot_w, plot_h = plot_w * CARD_PLOT_H / plot_h, CARD_PLOT_H
    merged = _merge_marks(marks, lambda km: (km - foot) / (run / 1000) * plot_w)
    labels = [m[1] if len(m[1]) <= LABEL_CHARS else m[1][: LABEL_CHARS - 1] + "…" for m in merged]
    labels_h = min(CARD_LABELS_H, 2 + max(map(_label_mm, labels))) if labels else CARD_BARE_H
    base = labels_h + plot_h

    def x(km: float) -> float:
        return CARD_LEFT + (km - foot) / max(top - foot, 1e-9) * plot_w

    def y(ele: float) -> float:
        return base - (ele - floor) / rise * plot_h

    def ele_at(km: float) -> float:
        for k0, k1, e0, e1 in sections:
            if km <= k1:
                return e0 + (e1 - e0) * (max(km, k0) - k0) / max(k1 - k0, 1e-9)
        return sections[-1][3]

    out: list[str] = []
    last_alt = x(foot) + 0.6 + _label_mm(f"{e_lo:.0f} m") - ALT_GAP / 2  # clear of the foot's altitude, too
    for n, (k0, k1, e0, e1) in enumerate(sections):
        x0, x1 = x(k0), x(k1)
        grade = (e1 - e0) / max((k1 - k0) * 1000, 1e-9) * 100
        fill = grade_colour(grade, grades) if grades else CLIMB
        out.append(
            f'<polygon points="{x0:.2f},{base} {x0:.2f},{y(e0):.2f} {x1:.2f},{y(e1):.2f} {x1:.2f},{base}" '
            f'fill="{fill}" stroke="#fff" stroke-width="0.25"/>'
        )
        out.append(_grade_label(grade, x0, x1, base - 1.2, fill))
        if x0 - last_alt >= ALT_GAP and n:  # the foot's altitude is written apart, larger
            out.append(
                f'<text x="{x0:.2f}" y="{y(e0) - 0.8:.2f}" font-size="1.8" fill="#555" text-anchor="middle">'
                f"{e0:.0f}</text>"
            )
            last_alt = x0
    out += _km_scale(sections, x, base + 3.4)
    out.append(
        f'<text x="{x(foot) + 0.6:.2f}" y="{y(e_lo) - 1:.2f}" font-size="2.2" font-weight="700">{e_lo:.0f} m</text>'
    )
    e_top = sections[-1][3]
    out.append(
        f'<text x="{x(top) + 0.8:.2f}" y="{y(e_top) + 0.8:.2f}" font-size="2.4" font-weight="700">{e_top:.0f} m</text>'
    )
    out.append(f'<line x1="{CARD_LEFT}" y1="{base}" x2="{x(top):.2f}" y2="{base}" stroke="#444" stroke-width="0.25"/>')
    for (km, _), label in zip(merged, labels, strict=True):
        xm, ym = x(km), y(ele_at(km))
        out.append(
            f'<line x1="{xm:.2f}" y1="{ym - 0.4:.2f}" x2="{xm:.2f}" y2="{labels_h - 0.6:.2f}" stroke="#555" '
            'stroke-width="0.15" stroke-dasharray="0.6 0.5"/>'
        )
        out.append(f'<circle cx="{xm:.2f}" cy="{ym:.2f}" r="0.5" fill="#111"/>')
        out.append(
            f'<text transform="translate({xm + 0.7:.2f} {labels_h - 1:.2f}) rotate(-90)" font-size="2.2">'
            f"{html.escape(label)}</text>"
        )
    height, width_mm = base + CARD_AXIS_H, CARD_LEFT + plot_w + CARD_RIGHT
    return (
        f'<svg class="climb-profile" viewBox="0 0 {width_mm:g} {height:g}" '
        f'width="{width_mm:g}mm" height="{height:g}mm">{"".join(out)}</svg>'
    )


def _km_scale(
    sections: Sequence[tuple[float, float, float, float]], x: Callable[[float], float], y: float
) -> list[str]:
    """The km from the foot under the slabs' edges: at each one, or at round km, as few as keeps them apart; and the
    summit's, to the tenth, rather than a label too close to it."""
    foot, top = sections[0][0], sections[-1][1]
    step = sections[0][1] - foot  # every section a step long, but the last
    multiples = [round(k / step) for k in KM_EVERY if k > step and math.isclose(k / step, round(k / step))]
    every = next((n for n in (1, *multiples) if n * (x(foot + step) - x(foot)) >= KM_GAP), max(multiples, default=1))
    kms = [
        (k0, f"{k0 - foot:g}" if n else "0")
        for n, (k0, *_) in enumerate(sections)
        if n % every == 0 and (n == 0 or x(top) - x(k0) >= KM_GAP)
    ]
    kms.append((top, f"{top - foot:.1f}"))
    return [f'<text x="{x(km):.2f}" y="{y:.2f}" font-size="2" text-anchor="middle">{text}</text>' for km, text in kms]


def _grade_label(grade: float, x0: float, x1: float, y: float, fill: str) -> str:
    """A slab's grade, as the slab holds it: to the tenth, else to the whole percent, else not at all, its colour
    telling it already."""
    for text in (f"{grade:.1f}", f"{grade:.0f}"):
        size = min(GRADE_MM, (x1 - x0) / (len(text) * GRADE_EM_PER_CHAR))
        if size >= GRADE_MIN_MM:
            return (
                f'<text x="{(x0 + x1) / 2:.2f}" y="{y:.2f}" font-size="{size:.2f}" font-weight="700" '
                f'text-anchor="middle" fill="{_ink(fill)}">{text}</text>'
            )
    return ""


def _label_mm(text: str) -> float:
    """About how long a label runs, mm: an emoji twice a letter; joiners and variation selectors nothing."""
    unseen = {0x200D, *range(0xFE00, 0xFE10)}
    return sum(
        0 if ord(c) in unseen else LABEL_MM_PER_EMOJI if ord(c) >= 0x2190 else LABEL_MM_PER_CHAR  # noqa: PLR2004  arrows on
        for c in text
    )


def _merge_marks(marks: Sequence[tuple[float, str]], x: Callable[[float], float]) -> list[tuple[float, str]]:
    """The marks in km order, those too close to read apart merged into one label, at the first one's km."""
    merged: list[tuple[float, str]] = []
    for km, text in sorted(marks):
        if merged and x(km) - x(merged[-1][0]) < MARK_GAP:
            merged[-1] = (merged[-1][0], f"{merged[-1][1]} · {text}")
        else:
            merged.append((km, text))
    return merged
