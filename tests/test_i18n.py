import ast
import datetime as dt
import re
import threading
import tomllib
from pathlib import Path

import pytest

from roadbook import api, i18n
from roadbook.config import Settings, load_config

SRC = Path(i18n.__file__).parent
FR = SRC / "locales" / "fr"
FLAT = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"
FROM_DATA = {"settings.toml"}  # its messages reach translate() from settings.py's data, not as literals: see the GUI's
FIELD = re.compile(r"\{(\w*)[^{}]*\}")
TEMPLATE_CALL = re.compile(r"""\b_\(\s*(?:'([^']*)'|"([^"]*)")""")


def _in_python() -> set[str]:
    """The literal messages of every _("…") and translate("…") call in the package."""
    found: set[str] = set()
    for file in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"_", "translate"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                found.add(node.args[0].value)
    return found


def _in_templates() -> set[str]:
    return {
        a or b
        for file in (SRC / "templates").glob("*.j2")
        for a, b in TEMPLATE_CALL.findall(file.read_text(encoding="utf-8"))
    }


def _catalog(*, skip: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    merged: dict[str, str] = {}
    for file in sorted(FR.glob("*.toml")):
        if file.name not in skip:
            merged |= tomllib.loads(file.read_text(encoding="utf-8"))
    return merged


def test_every_message_has_its_french() -> None:
    missing = (_in_python() | _in_templates()) - _catalog().keys()
    assert not missing, f"no French in locales/fr/ for: {sorted(missing)}"


def test_every_french_message_is_still_used() -> None:
    unused = _catalog(skip=FROM_DATA).keys() - (_in_python() | _in_templates())
    assert not unused, f"locales/fr/ translates what nothing says any more: {sorted(unused)}"


def test_a_message_and_its_french_have_the_same_fields() -> None:
    for english, french in _catalog().items():
        assert set(FIELD.findall(english)) == set(FIELD.findall(french)), english


def test_two_catalog_files_translate_one_message_alike() -> None:
    # a word both the booklet and the GUI say ("water", "climbs") may be in both their files, as long as it reads the
    # same: the catalogs merge into one, where the last file would otherwise win unseen
    seen: dict[str, tuple[str, str]] = {}
    for file in sorted(FR.glob("*.toml")):
        for english, french in tomllib.loads(file.read_text(encoding="utf-8")).items():
            if english in seen:
                where, before = seen[english]
                assert french == before, f"{english!r} is {before!r} in {where}, {french!r} in {file.name}"
            seen[english] = file.name, french


def test_a_message_reads_in_english_without_a_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(i18n.catalog("fr"), "{n} to go", "encore {n}")
    assert i18n.translate("{n} to go", "fr", n=12) == "encore 12"
    assert i18n.translate("{n} to go", "en", n=12) == "12 to go"
    assert i18n.translate("nothing like it {n}", "fr", n=1) == "nothing like it 1"
    assert i18n.translate("{n} to go", "fr") == "encore {n}"  # no fields given: the text as it is, braces and all


def test_the_road_book_language_holds_within_and_on_its_own_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(i18n.catalog("fr"), "climbs", "montées")
    seen: list[str] = []
    with i18n.language("fr"):
        assert i18n._("climbs") == "montées"
        thread = threading.Thread(target=lambda: seen.append(i18n._("climbs")))
        thread.start()
        thread.join()
    assert i18n._("climbs") == "climbs"
    assert seen == ["climbs"]  # a new thread starts in English, whatever another is doing


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(ValueError, match="'de'"), i18n.language("de"):
        pass
    cfg = load_config()
    cfg["render"]["language"] = "de"
    with pytest.raises(ValueError, match=r"render\.language 'de'"):
        Settings.of(cfg)


def test_dates_are_named_in_the_language_not_the_locale() -> None:
    day = dt.date(2026, 10, 5)
    assert i18n.date_text(day) == "Mon 05 Oct 2026"
    with i18n.language("fr"):
        assert i18n.date_text(day) == "lun. 05 oct. 2026"
        assert i18n.date_text(day, year=False) == "lun. 05 oct."


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
def test_a_french_road_book_says_so() -> None:
    cfg = load_config()
    cfg["render"]["language"] = "fr"
    shot = api.preview(FLAT, cfg)
    assert '<html lang="fr">' in shot.html
    assert "— roadbook</title>" in shot.html
    assert shot.booklet is not None
    assert '<html lang="fr">' in shot.booklet
    assert '<html lang="en">' in api.preview(FLAT, load_config()).html
