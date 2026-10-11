"""Build the downloadable app: the `roadbook-gui` window, with Python and every dependency inside, in dist/app/.

    uv sync --group app --extra gui
    uv run --group app python scripts/build_app.py [--zip]

A folder rather than one file, which would unpack itself on every start: zipped (--zip) for a GitHub Release. Run it
on the system it is for: PyInstaller builds for the one it runs on. What NiceGUI's `nicegui-pack` passes PyInstaller,
and what the road book needs besides: its templates and settings, and its version, which the window and each road
book show.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import nicegui

import roadbook

ROOT = Path(__file__).resolve().parent.parent
NAME = "gpx2roadbook"
OUT = ROOT / "dist" / "app"  # dist/ and build/ are ignored by git
SYSTEMS = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}


def data(source: Path, target: str) -> list[str]:
    return ["--add-data", f"{source}{os.pathsep}{target}"]


def build() -> Path:
    """The app's folder (or, on macOS, its .app), built afresh."""
    package = Path(roadbook.__file__).parent
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        NAME,
        "--windowed",  # no console: the window is the app, and closing it quits
        "--onedir",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(OUT),
        "--workpath",
        str(ROOT / "build" / "app"),
        "--specpath",
        str(ROOT / "build" / "app"),
        *data(Path(nicegui.__file__).parent, "nicegui"),
        *data(package / "templates", "roadbook/templates"),
        *data(package / "default.toml", "roadbook"),
        "--copy-metadata",
        NAME,  # importlib.metadata.version("gpx2roadbook")
        "--collect-all",
        "opening_hours",  # the shops' hours: a compiled module, with its holidays
        "--osx-bundle-identifier",
        "io.github.bnogaro.gpx2roadbook",
        str(ROOT / "scripts" / "app.py"),
    ]
    print("$", " ".join(command))
    subprocess.run(command, check=True)  # noqa: S603  fixed arguments, this interpreter
    return OUT / (f"{NAME}.app" if platform.system() == "Darwin" else NAME)


def archive(app: Path) -> Path:
    """The app zipped, named for its version and system: gpx2roadbook-0.6.0-windows.zip."""
    stem = f"{NAME}-{version(NAME)}-{SYSTEMS.get(platform.system(), platform.system().lower())}"
    return Path(shutil.make_archive(str(OUT / stem), "zip", app.parent, app.name))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--zip", action="store_true", help="also zip it, for a release")
    args = parser.parse_args()
    app = build()
    print(f"Built {app}")
    if args.zip:
        print(f"Zipped {archive(app)}")


if __name__ == "__main__":
    main()
