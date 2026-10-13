"""The strips, the reference sheet and the climb pages in French: the words of locales/fr/output.toml."""

import datetime as dt
import html
import re
import tomllib
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from roadbook import api, i18n
from roadbook.config import load_config
from roadbook.opening import Window
from roadbook.render import eta_text, grade_key

OUTPUT = Path(i18n.__file__).parent / "locales" / "fr" / "output.toml"
PROVENCE = Path(__file__).parent.parent / "samples" / "ubf_300_poi.gpx"  # the UBF 300: climbs, checkpoints
FIELD = re.compile(r"\{\w*\}")
NB = chr(0xA0)  # the no-break space French puts before a colon and between a number and its unit
EN = chr(0x2013)  # the en dash between times and grades

needs_sample = pytest.mark.skipif(not PROVENCE.exists(), reason="sample GPX not present")


def _settings(language: str) -> dict[str, Any]:
    cfg = load_config()
    cfg["booklet"]["format"] = "off"  # the reference sheet and the climb pages in the strips' file
    cfg["climb_pages"]["enabled"] = True
    cfg["checkpoints"]["every_km"] = 50
    cfg["ride"].update(date="2026-10-11", start="07:30", speed_kmh=24, breaks=[[95, 45]])
    cfg["render"]["language"] = language
    return cfg


@cache
def _render(language: str) -> str:
    return api.preview(PROVENCE, _settings(language)).html


def _visible(page: str) -> str:
    """The text a reader sees, an element's text a line: no styles, no tags."""
    page = re.sub(r"(?s)<style.*?</style>", "", page)
    return html.unescape(re.sub(r"<[^>]+>", "\n", page))


def _at(day: int, hhmm: str) -> dt.datetime:
    return dt.datetime.combine(dt.date(2026, 10, day), dt.time.fromisoformat(hhmm))


@needs_sample
def test_a_french_road_book_holds_the_french_words() -> None:
    text = _visible(_render("fr"))
    for words in (
        "Départ",
        "Arrivée",
        "CP1 · 256 restants",
        f"départ dim. 11 oct. 2026 07:30 à 24{NB}km/h",
        f"pauses{NB}: km 95 (45{NB}min)",
        "— montées",  # the climb pages' title
        f"{NB}km à ",  # a climb's length and grade
        f"Montées par pente{NB}:",
        "Fait avec gpx2roadbook",
    ):
        assert words in text, words


@needs_sample
def test_a_french_road_book_has_no_english_left() -> None:
    """No English message of output.toml shows in French, its fields filled in by anything on the same line."""
    text = _visible(_render("fr"))
    for english, french in tomllib.loads(OUTPUT.read_text(encoding="utf-8")).items():
        words = re.findall(r"[A-Za-z]+", FIELD.sub(" ", english))
        if len(words) < 2 or english == french:  # a lone word ("map") could be anything, a name for one
            continue
        pattern = r"[^\n]*?".join(re.escape(part) for part in FIELD.split(english))
        assert not re.search(pattern, text), english


@needs_sample
def test_the_french_strips_are_the_english_ones_in_size() -> None:
    """The strips are measured in mm: a French word must not move a row onto another strip."""
    en, fr = _render("en"), _render("fr")
    for counted in ('class="strip"', '<div class="row ', 'class="climb-card"'):
        assert fr.count(counted) == en.count(counted), counted


def test_the_climb_key_and_a_late_arrival_in_french() -> None:
    grades = ((0.0, "#a"), (3.0, "#b"), (6.0, "#c"))
    overnight = Window(_at(11, "22:18"), _at(12, "03:17"))
    with i18n.language("fr"):
        labels = [label for label, _colour in grade_key(grades)]
        late = eta_text(overnight, dt.date(2026, 10, 11))
    assert labels == [f"moins de 3{NB}%", f"3{EN}6{NB}%", f"6{NB}% et plus"]
    assert late == f"~22:18{EN}lun. 03:17"
    assert eta_text(overnight, dt.date(2026, 10, 11)) == f"~22:18{EN}Mon 03:17"
