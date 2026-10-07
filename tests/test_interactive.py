from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner

from roadbook.cli import app
from roadbook.config import load_config
from roadbook.interactive import ask, command

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
pytestmark = pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")

ENTER, DOWN, SPACE, CLEAR = "\r", "\x1b[B", " ", "\x15"  # Ctrl+U clears a pre-filled answer


def _session(
    keys: str, gpx: Path | None = None, flags: dict[tuple[str, str], Any] | None = None
) -> tuple[Path, Path, bool, dict, str]:
    """Answer the prompts with `keys`, as typed in a terminal; returns the run set up and its `Same as:` command.

    `flags` stand for options given next to -i, as (section, key): value.
    """
    cfg = load_config()
    for (section, key), value in (flags or {}).items():
        cfg[section][key] = value
    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        gpx, out, pdf = ask(gpx, None, pdf=False, cfg=cfg, input=pipe, output=DummyOutput())
    return gpx, out, pdf, cfg, command(gpx, out, pdf=pdf, cfg=cfg, base=load_config(), config=None)


def test_pressing_enter_throughout_keeps_the_defaults() -> None:
    # layout, paper, checkpoints, opening hours, categories, PDF, output, advanced
    _, out, pdf, cfg, same = _session(ENTER * 4 + ENTER + "n" + ENTER + ENTER, gpx=HILLY)
    assert (out, pdf) == (HILLY.with_suffix(".roadbook.html"), False)
    assert cfg == load_config()
    assert same.replace('"', "").replace("'", "") == f"roadbook {HILLY}"


def test_a_session_picks_from_menus_and_reaches_the_command() -> None:
    keys = (
        CLEAR
        + "nope.gpx"
        + ENTER  # no such file: the prompt stays
        + CLEAR
        + str(HILLY)
        + ENTER
        + DOWN
        + ENTER  # layout: line
        + DOWN
        + ENTER  # paper: A5
        + CLEAR
        + "x"
        + ENTER  # not a number: the prompt stays
        + CLEAR
        + "50"
        + ENTER  # checkpoint every 50 km
        + "y"  # opening hours
        + "12/10"
        + ENTER  # not a date: the prompt stays
        + CLEAR
        + "2026-10-12"
        + ENTER  # ride date
        + "6h"
        + ENTER  # not a time: the prompt stays
        + CLEAR
        + "06:00"
        + ENTER  # start time
        + CLEAR
        + "22"
        + ENTER  # average speed
        + DOWN * 3
        + (SPACE + DOWN) * 7
        + ENTER  # categories: untick cafe … icecream, keep water, toilets, bakery
        + "y"  # PDF
        + ENTER  # output: as offered
        + DOWN * 5
        + SPACE
        + DOWN * 2
        + SPACE
        + ENTER  # advanced: smallest climb, leg climbing
        + CLEAR
        + "50"
        + ENTER
        + "y"
    )
    gpx, _, pdf, cfg, same = _session(keys)
    assert gpx == HILLY
    assert pdf
    assert cfg["pois"]["enabled"] == ["water", "toilets", "bakery"]
    parts = (
        "--layout line",
        "--page A5",
        "--checkpoint-every 50",
        "--hours",
        "--date 2026-10-12",
        "--start 06:00",
        "--speed 22",
        "--min-climb 50",
        "--leg-elevation",
        "--pdf",
    )
    for part in parts:
        assert part in same
    assert "--categories water,toilets,bakery" in same


def test_an_automatic_value_accepted_as_offered_stays_automatic() -> None:
    # line layout: width 0 stands for 16 mm, longest stop 0 for 3 x gap; Enter on their offered values keeps them 0
    keys = DOWN + ENTER + ENTER * 4 + "n" + ENTER + SPACE + DOWN * 3 + SPACE + ENTER + ENTER + ENTER
    _, _, _, cfg, same = _session(keys, gpx=HILLY)
    assert cfg["render"]["width_mm"] == 0
    assert cfg["stops"]["max_span_m"] == 0
    assert "--width" not in same
    assert "--max-span" not in same


def test_flags_given_with_i_are_the_defaults_and_stay_in_the_command() -> None:
    keys = ENTER * 5 + "n" + ENTER + ENTER
    _, _, _, cfg, same = _session(keys, gpx=HILLY, flags={("stops", "gap_m"): 800})
    assert cfg["stops"]["gap_m"] == 800
    assert "--gap 800" in same


def test_i_needs_a_terminal() -> None:
    result = CliRunner().invoke(app, ["-i", str(HILLY)])
    assert result.exit_code == 2
    assert "terminal" in result.output


def test_without_a_gpx_or_i_it_says_what_is_missing() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 2
    assert "use -i" in result.output
