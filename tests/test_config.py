import datetime as dt
import tomllib

import pytest

from roadbook.config import Category, Settings, check, differences, dumps, load_config


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


def test_the_settings_of_a_run_are_typed_once() -> None:
    cfg = load_config()
    s = Settings.of(cfg)
    assert s.render.length_mm == 200  # a number stays as TOML gives it: "200" in the road book, not "200.0"
    assert isinstance(s.render.length_mm, int)
    assert s.climbs.categories[0] == ("HC", 80000.0)
    assert s.climbs.grade_colours[0] == (0.0, "#fcd34d")
    assert s.pois.enabled == tuple(cfg["pois"]["enabled"])
    assert s.hours.kinds["fuel"] == ("amenity=fuel",)
    assert list(s.categories) == list(cfg["categories"])  # in their order
    assert s.categories["water"] == Category("🚰", tuple(cfg["categories"]["water"]["match"]))
    assert (s.ride.date, s.ride.start, s.ride.speed_kmh, s.ride.breaks) == (None, None, 0.0, ())
    assert s.booklet.water_gap_h == 3.0
    assert s.booklet.max_per_kind == 5
    # what a --config file is written as reads back as the same settings
    assert Settings.of(tomllib.loads(dumps(cfg))) == s


def test_the_ride_takes_text_or_toml_dates_and_times() -> None:
    cfg = load_config()
    cfg["ride"] |= {"date": "2026-10-11", "start": "07:30", "speed_kmh": 24, "breaks": [[95, 45]]}
    ride = Settings.of(cfg).ride
    assert (ride.date, ride.start, ride.speed_kmh, ride.breaks) == (
        dt.date(2026, 10, 11),
        dt.time(7, 30),
        24.0,
        ((95.0, 45.0),),
    )
    cfg["ride"] |= tomllib.loads("[ride]\ndate = 2026-10-11\nstart = 07:30:00")["ride"]  # bare, from a file
    again = Settings.of(cfg).ride
    assert (again.date, again.start) == (ride.date, ride.start)
    cfg["checkpoints"]["extra"] = [[50, "Lunch"], [120.5, 2]]
    assert Settings.of(cfg).checkpoints.extra == ((50.0, "Lunch"), (120.5, "2"))


def test_a_setting_missing_or_of_the_wrong_kind_is_named() -> None:
    cfg = load_config()
    del cfg["map"]["style"], cfg["map"]["contours"]
    with pytest.raises(ValueError, match=r"settings: no map\.style, map\.contours"):
        Settings.of(cfg)
    cfg = load_config()
    del cfg["booklet"]
    with pytest.raises(ValueError, match=r"settings: no \[booklet\]"):
        Settings.of(cfg)
    cfg = load_config()
    cfg["render"] = "strip"
    with pytest.raises(ValueError, match=r"settings: \[render\] should be a table"):
        Settings.of(cfg)
    cfg = load_config()
    del cfg["categories"]["water"]["emoji"]
    with pytest.raises(ValueError, match=r"settings: no categories\.water\.emoji"):
        Settings.of(cfg)
    cfg = load_config()
    cfg["booklet"]["water_gap_h"] = "three"
    with pytest.raises(ValueError, match=r"settings: could not convert string to float: 'three'"):
        Settings.of(cfg)
    cfg = load_config()
    cfg["ride"]["start"] = "7h30"
    with pytest.raises(ValueError, match=r"settings: Invalid isoformat string: '7h30'"):
        Settings.of(cfg)


def test_a_setting_no_run_reads_is_left_aside_as_before() -> None:
    cfg = load_config()
    cfg["map"]["zoom"] = 12  # an outdated --config file: merged, not checked
    cfg["old"] = {"key": 1}
    assert Settings.of(cfg) == Settings.of(load_config())
