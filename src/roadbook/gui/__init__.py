"""roadbook-gui: a window to pick a GPX, set the options and make the road book.

Built with NiceGUI, an optional extra: `uv tool install "gpx2roadbook[gui]"`. Its code follows the llms.md shipped in
the installed nicegui package: pages/ builds the page, components/ its parts, models/ the per-window state.
"""

from __future__ import annotations

import argparse
import sys

INSTALL = 'The GUI needs the "gui" extra: uv tool install "gpx2roadbook[gui]" (or: pipx install "gpx2roadbook[gui]").'


def main() -> None:
    parser = argparse.ArgumentParser(prog="roadbook-gui", description=__doc__.splitlines()[0])
    parser.add_argument("--browser", action="store_true", help="open in a browser tab, not in a window of its own")
    parser.add_argument(
        "--lan",
        action="store_true",
        help="in a browser tab that phones and other devices on your network can open too, at this computer's address"
        " (port 8642); they upload the GPX and download the road book",
    )
    args = parser.parse_args()
    try:
        from .app import start  # noqa: PLC0415  the extra may be missing: say so, rather than fail on import
    except ImportError:  # as llms.md says for optional dependencies, not ModuleNotFoundError
        _say(INSTALL)
        sys.exit(1)
    start(native=not (args.browser or args.lan), lan=args.lan)


def _say(message: str) -> None:
    """Tell the user, on the console if there is one; roadbook-gui is a GUI script, with no console on Windows."""
    if sys.stderr is not None:
        sys.stderr.write(message + "\n")
        return
    import tkinter.messagebox  # noqa: PLC0415  only for the rare case of no console and no NiceGUI

    tkinter.messagebox.showerror("gpx2roadbook", message)
