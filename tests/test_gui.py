"""The GUI, through NiceGUI's User fixture: a simulated user, no browser (see nicegui/llms.md, "Testing")."""

import asyncio
import shutil
import sys
import tomllib
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("nicegui", reason="the GUI is an optional extra: uv sync --extra gui")

from nicegui import app, ui
from nicegui.testing.user import User
from nicegui.testing.user_simulation import user_simulation

import roadbook.gui
import roadbook.gui.app
from roadbook.config import load_config
from roadbook.gui.models import memory
from roadbook.gui.models.form import Form, Row
from roadbook.gui.pages.main import page
from roadbook.osm import Report

FLAT = Path(__file__).parent.parent / "samples" / "paris_le_mans.gpx"


@pytest.fixture(autouse=True)
def remembered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The last settings go to a file of the test's own, never the user's config folder."""
    file = tmp_path / "config" / "gui.toml"
    monkeypatch.setattr(memory, "path", lambda: file)
    return file


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


@pytest.mark.skipif(not FLAT.exists(), reason="sample GPX not present")
async def test_the_preview_follows_the_settings_from_the_cache_only(
    user: User, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    offline_calls: list[bool] = []

    def towns(_stops: object, _cfg: object, *, offline: bool) -> Report:
        offline_calls.append(offline)
        return Report(asked=3, cached=1, unasked=2)

    monkeypatch.setattr("roadbook.build.name_towns", towns)
    gpx = tmp_path / "ride.gpx"
    shutil.copy(FLAT, gpx)
    await user.open("/")
    await user.should_see(marker="preview-empty")
    user.find(marker="gpx").type(str(gpx))
    await user.should_see(marker="preview-frame", retries=50)
    assert set(offline_calls) == {True}  # never online
    await user.should_see("Towns: 2 stops not looked up yet")
    assert not (tmp_path / "ride.roadbook.html").is_file()  # a preview writes nothing
    assert ".details { display: none; }" in _srcdoc(user)
    user.find(marker="zoom-in").click()
    await user.should_see("125%")
    assert "zoom: 1.25" in _srcdoc(user)
    for tabs in user.find(kind=ui.tabs).elements:
        tabs.value = "sheet"  # as a click does in a window
    assert ".sheet { display: none; }" in _srcdoc(user)
    _pick(user, "layout", "line")
    for _ in range(50):  # built again, a moment after the change
        await asyncio.sleep(0.1)
        if 'class="ribbon"' in _srcdoc(user):
            break
    assert 'class="ribbon"' in _srcdoc(user)


def _srcdoc(user: User) -> str:
    return next(iter(user.find(marker="preview-frame").elements)).props["srcdoc"]


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


async def test_the_last_settings_come_back_and_are_saved_as_they_change(user: User, remembered: Path) -> None:
    _write(remembered, '[ride]\ndate = "2026-10-17"\nstart = "07:00"\nbreaks = [[95, 45]]\n')
    await user.open("/")
    await user.should_see(marker="break-km-0")  # the rows too, which no binding reaches
    await user.should_see("2026-10-17")
    _pick(user, "start", "08:30")
    saved = await _saved(remembered, lambda cfg: cfg["ride"]["start"] == "08:30")
    assert saved == {"ride": {"date": "2026-10-17", "start": "08:30", "breaks": [[95.0, 45.0]]}}
    assert load_config(remembered)["ride"]["start"] == "08:30"  # a file the CLI takes with --config
    for chip in user.find(marker="category-water").elements:
        chip.selected = False  # ty: ignore[unresolved-attribute]  # as a click does
    await _saved(remembered, lambda cfg: "water" not in cfg.get("pois", {}).get("enabled", ["water"]))
    user.find(marker="settings-menu").click()
    user.find(marker="reset").click()
    await user.should_see("Settings back to the defaults")
    await user.should_not_see(marker="break-km-0")
    await user.should_not_see(marker="start")  # no date any more
    assert all(chip.selected for chip in user.find(marker="category-water").elements)  # ty: ignore[unresolved-attribute]
    assert await _saved(remembered, lambda cfg: not cfg) == {}


async def test_a_broken_or_outdated_file_is_left_out_with_a_notice(user: User, remembered: Path) -> None:
    _write(remembered, "[render]\nlayout = 3\n")
    await user.open("/")
    await user.should_see("Your last settings are left out")
    await user.should_see("strip")  # the default layout
    _write(remembered, "[ride\n")
    await user.open("/")
    await user.should_see("Your last settings are left out")


async def test_save_as_downloads_the_settings_in_a_browser_tab(user: User) -> None:
    await user.open("/")
    _pick(user, "date", "2026-10-17")
    await user.should_see(marker="start")
    user.find(marker="settings-menu").click()
    user.find(marker="save-as").click()
    response = await user.download.next()
    assert tomllib.loads(response.text) == {"ride": {"date": "2026-10-17"}}


async def test_save_as_writes_where_the_dialog_says_in_a_window(
    user: User, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shared = tmp_path / "event.toml"

    class Window:
        async def create_file_dialog(self, *_args: object, **_kwargs: object) -> str:
            return str(shared)  # Windows hands the path back as it is; others as a tuple of one

    await user.open("/")
    with monkeypatch.context() as window:  # a window only for the dialog: nicegui would shut it down after the test
        window.setattr(app.native, "main_window", Window())
        user.find(marker="settings-menu").click()
        user.find(marker="save-as").click()
        await user.should_see("Saved")
    assert tomllib.loads(shared.read_text(encoding="utf-8")) == {}  # the defaults: nothing to say


async def _saved(file: Path, done: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    """The remembered settings, once they say what the test waits for: they are saved within a second."""
    for _ in range(40):
        if (cfg := _read(file)) is not None and done(cfg):
            return cfg
        await asyncio.sleep(0.1)
    raise AssertionError(_read(file))


def _write(file: Path, text: str) -> None:
    file.parent.mkdir(exist_ok=True)
    file.write_text(text, encoding="utf-8")


def _read(file: Path) -> dict[str, Any] | None:
    return tomllib.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
