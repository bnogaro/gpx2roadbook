"""The GUI, through NiceGUI's User fixture: a simulated user, no browser (see nicegui/llms.md, "Testing")."""

import shutil
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

pytest.importorskip("nicegui", reason="the GUI is an optional extra: uv sync --extra gui")

from nicegui import ui
from nicegui.testing.user import User
from nicegui.testing.user_simulation import user_simulation

import roadbook.gui
import roadbook.gui.app
from roadbook.config import load_config
from roadbook.gui.models.form import Form, Row
from roadbook.gui.pages.main import page

FLAT = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"


@pytest.fixture
async def user() -> AsyncIterator[User]:
    async with user_simulation(root=page) as simulated:
        yield simulated


async def test_the_window_shows_the_common_settings_and_folds_the_others(user: User) -> None:
    await user.open("/")
    await user.should_see("gpx2roadbook")
    for marker in ("gpx", "out", "layout", "page", "towns", "hours", "date", "make"):
        await user.should_see(marker=marker)
    await user.should_see(marker="category-water")
    await user.should_see("Advanced settings")


async def test_the_start_time_and_speed_come_once_there_is_a_date(user: User) -> None:
    await user.open("/")
    await user.should_not_see(marker="start")
    _pick(user, "date", "2026-10-17")  # pickers take no typing: set them as a pick does
    await user.should_see(marker="start")
    await user.should_not_see(marker="speed")
    _pick(user, "start", "07:00")
    await user.should_see(marker="speed")
    await user.should_see(marker="break-add")  # planned breaks too, once there are arrival times
    user.find(marker="break-add").click()
    await user.should_see(marker="break-km-0")
    user.find(marker="break-remove-0").click()
    await user.should_not_see(marker="break-km-0")


def _pick(user: User, marker: str, value: str) -> None:
    for element in user.find(marker=marker).elements:
        element.value = value  # ty: ignore[unresolved-attribute]  # a date or time input


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
async def test_picking_a_gpx_and_making_the_road_book(user: User, tmp_path: Path) -> None:
    gpx = tmp_path / "ride.gpx"
    shutil.copy(FLAT, gpx)
    await user.open("/")
    user.find(marker="make").click()
    await user.should_see("Pick a GPX file first")
    user.find(marker="gpx").type(str(gpx))
    await user.should_see(str(tmp_path / "ride.roadbook.html"))  # the output, next to it
    for chip in user.find(marker="category-water").elements:  # unticked: no water on this road book
        chip.selected = False  # ty: ignore[unresolved-attribute]  # as a click does in a window; the simulation can't
    user.find(marker="make").click()
    await user.should_see(marker="wrote", retries=50)
    assert (tmp_path / "ride.roadbook.html").is_file()
    await user.should_see("690 POIs")
    await user.should_see(marker="command")
    command = next(iter(user.find(marker="command").elements)).text  # ty: ignore[unresolved-attribute]  # a label
    assert "--categories toilets,bakery,cafe" in command
    await user.should_see(kind=ui.log)


def test_the_form_hands_its_rows_and_refresh_to_the_run() -> None:
    form = Form()
    form.choose(Path("ride.gpx"))
    assert form.out == "ride.roadbook.html"
    form.breaks = [Row(180.0, 30.0), Row(95.0, 45.0)]
    form.checkpoints = [Row(50.0, "Lunch")]
    form.refresh = True
    cfg = form.settings()
    assert cfg["ride"]["breaks"] == [[95.0, 45.0], [180.0, 30.0]]  # in route order
    assert cfg["checkpoints"]["extra"] == [[50.0, "Lunch"]]
    assert cfg["towns"]["max_age_days"] == 0
    assert form.cfg["ride"]["breaks"] == load_config()["ride"]["breaks"]  # the form's own settings are left as they are
    same = form.command()
    for part in ("--break 95:45", "--break 180:30", "--checkpoint 50:Lunch", "--refresh"):
        assert part in same


def test_roadbook_gui_starts_the_window_or_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[bool] = []
    monkeypatch.setattr(roadbook.gui.app, "start", lambda *, native: started.append(native))
    for argv, native in ((["roadbook-gui"], True), (["roadbook-gui", "--browser"], False)):
        monkeypatch.setattr(sys, "argv", argv)
        roadbook.gui.main()
        assert started.pop() is native
    monkeypatch.setitem(sys.modules, "roadbook.gui.app", None)  # as if the extra were not installed
    monkeypatch.setattr(sys, "argv", ["roadbook-gui"])
    with pytest.raises(SystemExit):
        roadbook.gui.main()
    assert 'uv tool install "gpx2roadbook[gui]"' in capsys.readouterr().err
