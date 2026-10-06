from pathlib import Path

import pytest
from typer.testing import CliRunner

from roadbook.cli import app

HILLY = Path(__file__).parent.parent / "samples" / "entrainement_ubf.gpx"
pytestmark = pytest.mark.skipif(not HILLY.exists(), reason="sample GPX not present")


def _run(args: list[str], *answers: str) -> str:
    result = CliRunner().invoke(app, args, input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    return result.output


def test_pressing_enter_throughout_keeps_the_defaults(tmp_path: Path) -> None:
    out = tmp_path / "rb.html"
    # gpx given; layout, page, checkpoints, categories, pdf: Enter; output; no advanced option
    output = _run(["-i", str(HILLY)], "", "", "", "", "", str(out), "")
    assert f"Same as: roadbook {HILLY} -o {out}" in output.replace('"', "").replace("'", "")
    assert out.stat().st_size > 0


def test_bad_answers_are_asked_again_and_choices_reach_the_command(tmp_path: Path) -> None:
    out = tmp_path / "rb.html"
    output = _run(
        ["-i", "--gap", "800"],
        "nope.gpx",  # no such file: asked again
        str(HILLY),
        "ribbon",  # no such layout: asked again
        "line",
        "A5",
        "50",
        "water,toilet",  # no such category: asked again
        "water,toilets",
        "n",
        str(out),
        "6, 8, 42",  # 42 is not in the list: asked again
        "6,8",
        "50",
        "y",
    )
    assert "no file at nope.gpx" in output
    assert "choose one of: strip, line" in output
    assert "unknown: toilet" in output
    assert "not in the list: 42" in output
    same = output.split("Same as: ", 1)[1].splitlines()[0]
    for part in ("--layout line", "--page A5", "--checkpoint-every 50", "--gap 800", "--min-climb 50"):
        assert part in same
    assert "--leg-elevation" in same
    assert "--categories water,toilets" in same
    assert "--pdf" not in same


def test_an_automatic_value_accepted_as_offered_stays_automatic(tmp_path: Path) -> None:
    # line layout: width 0 stands for 16 mm, max span 0 for 3 x gap; both are offered as such and Enter keeps them 0
    output = _run(["-i", str(HILLY)], "line", "", "", "", "", str(tmp_path / "rb.html"), "1,4", "", "")
    assert "Strip width, mm [16]" in output
    assert "Longest stop, m [1500]" in output
    same = output.split("Same as: ", 1)[1].splitlines()[0]
    assert "--width" not in same
    assert "--max-span" not in same


def test_without_a_gpx_or_i_it_says_what_is_missing() -> None:
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 2
    assert "use -i" in result.output
