"""The downloadable app's entry point, which build_app.py freezes: the window `roadbook-gui` opens.

Native mode runs the window in a process of its own. Frozen, `freeze_support()` must come first, or each new process
would start the app again (NiceGUI's "Packaging with Native Mode"). Frozen without a console, the app has nowhere to
print: its output goes to `app.log` in the user's cache folder instead, where it can be found when something fails.

`--check GPX` makes the road book of GPX and its booklet, offline and writing nothing, then quits: 0 if they were made,
1 if not. The release workflow runs it on the frozen app: what the app needs, in the bundle, makes a road book.
"""

import multiprocessing
import sys
from pathlib import Path


def check(gpx: Path) -> int:
    """Make the road book of `gpx` and its booklet as the window's preview does, and say how it went."""
    import traceback  # noqa: PLC0415  only for this check

    from roadbook.api import preview  # noqa: PLC0415
    from roadbook.config import load_config  # noqa: PLC0415

    try:
        shot = preview(gpx, load_config())
    except Exception:  # noqa: BLE001  any failure is the check's answer
        traceback.print_exc()
        return 1
    made = "<html" in shot.html and shot.booklet is not None and 'class="booklet"' in shot.booklet
    print(f"{'OK' if made else 'FAILED'}: {gpx.name}, {shot.book.length_km:.1f} km, {len(shot.book.stops)} stops")
    return 0 if made else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()  # first: a window's process stops here
    if sys.stdout is None or sys.stderr is None:
        from roadbook.osm import cache_path

        log = cache_path("app.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = log.open("w", encoding="utf-8", buffering=1)

    if sys.argv[1:2] == ["--check"]:
        sys.exit(check(Path(sys.argv[2])))

    from roadbook.gui import main

    main()
