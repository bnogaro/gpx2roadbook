"""The settings as the GUI shows them in French: locales/fr/settings.toml words each one, and nothing else.

test_i18n can't tell whether that catalog is whole: its messages reach translate() from settings.py's data, not as
literals. This test reads that data.
"""

import tomllib
from pathlib import Path

from roadbook import i18n
from roadbook import settings as st
from roadbook.config import load_config

SETTINGS_TOML = Path(i18n.__file__).parent / "locales" / "fr" / "settings.toml"
NAMES = {"A3", "A4", "A5", "A6", "Letter", "Legal", "en", "fr", "HC", "1", "2", "3", "4"}  # choices kept as they are
NAMES |= {"osm", "cyclosm", "topo", "satellite"}  # the map styles


def _shown() -> set[str]:
    """Every English word of the settings the GUI shows: labels in every state, help, choices, POI categories."""
    shown: set[str] = set()
    for hours in (True, False):  # the date's label says what the date is for, with opening hours or without
        cfg = load_config()
        cfg["hours"]["enabled"] = hours
        shown |= {s.text(cfg) for s in st.SETTINGS}
    shown |= {s.help for s in st.SETTINGS}
    shown |= {choice for s in st.SETTINGS for choice in s.choices} - NAMES
    shown |= set(load_config()["categories"])
    shown.add(str(st.SPEED.check is not None and st.SPEED.check("0")))  # what the speed field says of a 0
    return shown


def test_every_setting_the_gui_shows_has_its_french_and_nothing_else_is_there() -> None:
    french = tomllib.loads(SETTINGS_TOML.read_text(encoding="utf-8"))
    assert not _shown() - french.keys(), "no French in settings.toml"
    assert not french.keys() - _shown(), "settings.toml translates what no setting says"


def test_a_label_that_follows_the_answers_reads_in_french_in_each_state() -> None:
    cfg = load_config()
    labels = set()
    for hours in (True, False):
        cfg["hours"]["enabled"] = hours
        labels.add(i18n.translate(st.DATE.text(cfg), "fr"))
    assert len(labels) == 2
    assert all(label.startswith("Date de la sortie") for label in labels)
