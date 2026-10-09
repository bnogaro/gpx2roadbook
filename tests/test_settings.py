import pytest
import typer.main
from typer.core import TyperOption

from roadbook import settings as st
from roadbook.cli import app
from roadbook.config import load_config


def test_every_setting_has_a_flag_of_its_own_and_its_key_in_the_defaults() -> None:
    cfg = load_config()
    assert len({s.flag for s in st.SETTINGS}) == len(st.SETTINGS)
    for s in st.SETTINGS:
        assert s.flag.startswith("--"), s
        assert s.help, s
        assert s.text(cfg), s
        if s.group == st.RUN:
            assert not s.section, s  # a run setting is not in the config
        else:
            assert s.key in cfg[s.section], s
            if s.kind in {float, int}:
                assert isinstance(s.get(cfg), int | float), s
            elif s.kind is not str:
                assert isinstance(s.get(cfg), s.kind), s


def test_the_cli_offers_every_setting_with_its_help() -> None:
    params = [p for p in typer.main.get_command(app).params if isinstance(p, TyperOption)]
    options = {o: p for p in params for o in [*p.opts, *p.secondary_opts]}
    for s in st.SETTINGS:
        assert options[s.flag].help == s.help, s.flag
    assert "--no-towns" in options  # a bool gets its --no- form


@pytest.mark.parametrize(
    ("check", "answer", "verdict"),
    [
        (st.is_date, "", True),
        (st.is_date, "2026-10-17", True),
        (st.is_date, "17/10", "a date like 2026-10-12, or empty"),
        (st.is_time, "07:00", True),
        (st.is_speed, "0", "a speed above 0, for the arrival times"),
        (st.are_breaks, "95:45 180:30", True),
        (st.are_breaks, "lunch", "KM:MINUTES, e.g. 95:45, separated by spaces"),
        (st.is_number(int), "-1", "must be 0 or more"),
    ],
)
def test_the_checks_say_why_an_answer_is_refused(check: object, answer: str, verdict: object) -> None:
    assert callable(check)
    assert check(answer) == verdict


def test_a_label_may_depend_on_the_answers_so_far() -> None:
    cfg = load_config()
    assert "for arrival times" in st.DATE.text(cfg)
    cfg["hours"]["enabled"] = True
    assert "the whole week's hours" in st.DATE.text(cfg)
    assert st.SPEED.when is not None
    assert not st.SPEED.when(cfg)  # no date, no start: no speed to ask for
    cfg["ride"].update(date="2026-10-17", start="07:00")
    assert st.SPEED.when(cfg)
