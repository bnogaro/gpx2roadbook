import logging
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from roadbook import api
from roadbook.cli import app
from roadbook.config import load_config

FLAT = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"
pytestmark = pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")


def test_a_run_writes_the_road_book_next_to_the_gpx(tmp_path: Path) -> None:
    gpx = tmp_path / "ride.gpx"
    shutil.copy(FLAT, gpx)
    result = api.run(gpx, load_config())
    assert result.html == tmp_path / "ride.roadbook.html" == api.default_out(gpx)
    assert "<html" in result.html.read_text(encoding="utf-8")
    assert result.book.length_km > 0
    assert (result.pdf, result.pdf_error, result.warnings) == (None, None, [])


def test_a_pdf_that_fails_leaves_the_html_and_says_why(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_html: Path, _pdf: Path) -> None:
        msg = "No Edge/Chrome found"
        raise RuntimeError(msg)

    monkeypatch.setattr(api, "html_to_pdf", fail)
    result = api.run(FLAT, load_config(), out=tmp_path / "rb.html", pdf=True)
    assert result.html.exists()
    assert (result.pdf, result.pdf_error) == (None, "No Edge/Chrome found")


def test_a_run_keeps_its_warnings_and_leaves_the_log_as_it_found_it(tmp_path: Path) -> None:
    gpx = tmp_path / "flat.gpx"
    points = "".join(f'<trkpt lat="{45 + i / 100:.2f}" lon="5.0"/>' for i in range(3))  # no elevation
    gpx.write_text(f'<gpx version="1.1" creator="t"><trk><trkseg>{points}</trkseg></trk></gpx>', encoding="utf-8")
    handlers = list(logging.getLogger("roadbook").handlers)
    result = api.run(gpx, load_config(), out=tmp_path / "rb.html")
    assert result.warnings == ["flat.gpx has no elevation: no climbs, and a flat profile."]
    assert logging.getLogger("roadbook").handlers == handlers


@pytest.mark.parametrize(
    ("content", "says"),
    [("not xml", "not a GPX file the road book can read"), ('<gpx version="1.1"></gpx>', "no track or route points")],
)
def test_a_file_the_road_book_cannot_be_made_from_is_a_gpx_error(tmp_path: Path, content: str, says: str) -> None:
    gpx = tmp_path / "bad.gpx"
    gpx.write_text(content, encoding="utf-8")
    with pytest.raises(api.GpxError, match=says):
        api.run(gpx, load_config(), out=tmp_path / "rb.html")
    # the CLI says so in one line, not with a traceback
    result = CliRunner().invoke(app, [str(gpx), "-o", str(tmp_path / "rb.html")])
    assert result.exit_code == 1
    assert says in result.output
    assert "Traceback" not in result.output


def test_a_pdf_asked_for_comes_next_to_the_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    printed: list[tuple[Path, Path]] = []
    monkeypatch.setattr(api, "html_to_pdf", lambda html, pdf: printed.append((html, pdf)))
    result = api.run(FLAT, load_config(), out=tmp_path / "rb.html", pdf=True)
    assert printed == [(tmp_path / "rb.html", tmp_path / "rb.pdf")]
    assert (result.pdf, result.pdf_error) == (tmp_path / "rb.pdf", None)
