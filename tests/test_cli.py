import pytest

from roadbook.cli import _echo_lookup
from roadbook.osm import Report


def test_a_lookup_says_what_it_found_and_where(capsys: pytest.CaptureFixture[str]) -> None:
    _echo_lookup("Climb names", Report(asked=10, found=3, cached=4, sources=["Overpass"]), "climbs named", "climbs")
    assert capsys.readouterr().out == "Climb names: 3 of 10 climbs named (Overpass, 4 from cache)\n"


def test_a_lookup_with_nothing_to_look_up_says_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    _echo_lookup("Climb names", Report(), "climbs named", "climbs")  # a flat route: no climbs to name
    _echo_lookup("Towns", None, "busy stops named", "stops")  # not asked for
    assert capsys.readouterr() == ("", "")
