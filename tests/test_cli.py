import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from roadbook import cli
from roadbook.cli import _echo_lookup, app
from roadbook.osm import Report

FLAT = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
@pytest.mark.parametrize("bad", ["abc", "lunch:87.5"])
def test_a_bad_checkpoint_is_a_usage_error_not_a_crash(bad: str) -> None:
    result = CliRunner().invoke(app, [str(FLAT), "--checkpoint", bad])
    assert result.exit_code == 2
    assert "87.5:Lunch" in result.output


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
@pytest.mark.parametrize(
    "error",
    [
        subprocess.CalledProcessError(1, ["msedge"]),  # the browser failed
        subprocess.TimeoutExpired(["msedge"], 120),  # it never finished
    ],
)
def test_a_pdf_the_browser_could_not_print_is_reported(
    error: Exception, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_html: Path, _pdf: Path) -> None:
        raise error

    monkeypatch.setattr(cli, "html_to_pdf", fail)
    result = CliRunner().invoke(app, [str(FLAT), "--pdf", "-o", str(tmp_path / "rb.html")])
    assert result.exit_code == 1
    assert "PDF not written" in result.output
    assert (tmp_path / "rb.html").exists()  # the HTML is written all the same


def test_a_lookup_says_what_it_found_and_where(capsys: pytest.CaptureFixture[str]) -> None:
    _echo_lookup("Climb names", Report(asked=10, found=3, cached=4, sources=["Overpass"]), "climbs named", "climbs")
    assert capsys.readouterr().out == "Climb names: 3 of 10 climbs named (Overpass, 4 from cache)\n"


def test_a_lookup_with_nothing_to_look_up_says_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    _echo_lookup("Climb names", Report(), "climbs named", "climbs")  # a flat route: no climbs to name
    _echo_lookup("Towns", None, "busy stops named", "stops")  # not asked for
    assert capsys.readouterr() == ("", "")
