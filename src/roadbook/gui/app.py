"""Start the window: a native one through pywebview, or a browser tab."""

from __future__ import annotations

from nicegui import ui

from .pages.main import page


def start(*, native: bool) -> None:
    """Serve the page, in a window of its own (native) or a browser tab; no reloading, as this is no dev server."""
    if native:
        ui.run(page, title="gpx2roadbook", favicon="🚴", reload=False, native=True, window_size=(1280, 900))
    else:
        ui.run(page, title="gpx2roadbook", favicon="🚴", reload=False, port=8642)
