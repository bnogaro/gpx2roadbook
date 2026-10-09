"""Start the window: a native one through pywebview, or a browser tab."""

from __future__ import annotations

from nicegui import ui

from .pages.main import page

PORT = 8642


def start(*, native: bool, lan: bool = False) -> None:
    """Serve the page, in a window of its own (native) or a browser tab; no reloading, as this is no dev server.

    A browser tab is served to this computer only, unless `lan`: then other devices on the network may open it too,
    and they get the upload and the download, never this computer's paths (pages.main).
    """
    if native:
        ui.run(page, title="gpx2roadbook", favicon="🚴", reload=False, native=True, window_size=(1280, 900))
        return

    def root() -> None:
        page(lan=lan)

    host = "0.0.0.0" if lan else "127.0.0.1"  # noqa: S104  all interfaces only when asked for, with --lan
    ui.run(root, title="gpx2roadbook", favicon="🚴", reload=False, host=host, port=PORT)
