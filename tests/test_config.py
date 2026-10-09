import tomllib

import pytest

from roadbook.config import check, differences, dumps, load_config


def test_only_the_differences_are_written_and_read_back_as_they_were() -> None:
    base = load_config()
    cfg = load_config()
    cfg["ride"]["date"] = "2026-10-17"
    cfg["ride"]["breaks"] = [[95.0, 45.0]]
    cfg["render"]["details"] = False
    cfg["hours"]["kinds"]["bakery"] = ["shop=bakery"]
    cfg["categories"]["water"]["emoji"] = "💧"
    same = differences(cfg, base)
    assert same == {
        "hours": {"kinds": {"bakery": ["shop=bakery"]}},
        "ride": {"date": "2026-10-17", "breaks": [[95.0, 45.0]]},
        "render": {"details": False},
        "categories": {"water": {"emoji": "💧"}},
    }
    text = dumps(same)
    assert "[hours]" not in text  # no empty table for one that only holds others
    assert "[hours.kinds]" in text
    assert tomllib.loads(text) == same
    assert differences(base, base) == {}


def test_any_text_and_key_survive_the_round_trip() -> None:
    odd = {"a key": {'quote " and \\ back': 'tab\tnew\nline \x7f "x" 🚴'}, "plain": {"n": 3, "x": 0.5, "on": True}}
    assert tomllib.loads(dumps(odd)) == odd
    with pytest.raises(TypeError):
        dumps({"when": {"at": object()}})


def test_an_outdated_setting_is_named() -> None:
    base = load_config()
    check({"ride": {"speed_kmh": 25, "date": "2026-10-17"}}, base)  # an int where the default is a float is fine
    with pytest.raises(ValueError, match=r"no setting ride\.pace"):
        check({"ride": {"pace": 25}}, base)
    with pytest.raises(ValueError, match=r"render\.layout should be a text"):
        check({"render": {"layout": 3}}, base)
    with pytest.raises(ValueError, match=r"render should be a table"):
        check({"render": "strip"}, base)
