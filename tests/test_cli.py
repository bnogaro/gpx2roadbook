import subprocess
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from roadbook import cli
from roadbook.cli import _echo_lookup, app
from roadbook.osm import Report

FLAT = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"


def test_version_prints_the_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"gpx2roadbook {version('gpx2roadbook')}"


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


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
@pytest.mark.parametrize(("flags", "ages"), [([], [30, 365, 365]), (["--refresh"], [0, 0, 0])])
def test_refresh_looks_every_cached_answer_up_again(
    flags: list[str], ages: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    used: dict[str, Any] = {}

    def build(_gpx: Path, cfg: dict[str, Any]) -> None:
        used.update(cfg)
        raise typer.Exit  # the settings are all this test needs

    monkeypatch.setattr(cli, "build", build)
    assert CliRunner().invoke(app, [str(FLAT), *flags]).exit_code == 0
    assert [used[s]["max_age_days"] for s in ("hours", "towns", "climb_names")] == ages


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
def test_without_v_only_warnings_join_the_summary(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [str(FLAT), "-o", str(tmp_path / "rb.html")])
    assert "Read " not in result.output
    assert "Done in" not in result.output


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
def test_v_says_each_step_and_vv_each_detail(tmp_path: Path) -> None:
    out = str(tmp_path / "rb.html")
    steps = CliRunner().invoke(app, [str(FLAT), "-v", "-o", out]).output
    for line in ("Read paris_le_mans.gpx: ", "POIs left out: ", "Stops: 70 from 652 POIs", "Layout: ", "Done in "):
        assert f"\n{line}" in f"\n{steps}"
    assert "\n  km " not in steps  # no details
    details = CliRunner().invoke(app, [str(FLAT), "-vv", "-o", out]).output
    assert (
        "\nStops: 70 from 652 POIs, each within 500 m of the next, spanning 1500 m at most\n"
        "  km 30.8-31.2: water x2, fastfood\n" in details
    )
    assert details.count("Done in ") == 1  # the second run replaced the first one's log, rather than add to it


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
def test_a_start_time_without_a_date_says_why_there_are_no_arrival_times(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [str(FLAT), "--start", "07:00", "--speed", "28", "-o", str(tmp_path / "rb.html")])
    assert result.exit_code == 0
    assert "No arrival times: they need a ride date" in result.output


def _track(tmp_path: Path, *ele: str) -> Path:
    """A GPX of a few points 1 km apart, with these <ele> values ("" for none)."""
    points = "".join(
        f'<trkpt lat="{45 + i / 100:.2f}" lon="5.0">{f"<ele>{e}</ele>" if e else ""}</trkpt>' for i, e in enumerate(ele)
    )
    path = tmp_path / "t.gpx"
    gpx = f'<?xml version="1.0"?><gpx version="1.1" creator="t"><trk><trkseg>{points}</trkseg></trk></gpx>'
    path.write_text(gpx, encoding="utf-8")
    return path


def test_a_route_without_elevation_says_so(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, [str(_track(tmp_path, "", "", "")), "-o", str(tmp_path / "rb.html")])
    assert result.exit_code == 0, result.output
    assert "t.gpx has no elevation: no climbs, and a flat profile." in result.output
    some = _track(tmp_path, "100", "", "120")
    result = CliRunner().invoke(app, [str(some), "-v", "-o", str(tmp_path / "rb.html")])
    assert "t.gpx: 1 of 3 points have no elevation, filled in from their neighbours" in result.output
