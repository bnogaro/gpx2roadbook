"""One road book from a GPX and its settings: what the CLI runs, and a GUI too.

Progress goes through the `roadbook` loggers (see `roadbook -v`): a caller attaches its own handler to show it.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from .build import build
from .parse import GpxError
from .render import html_to_pdf, render_html

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from .model import Roadbook
    from .osm import Report

__all__ = ["GpxError", "Preview", "Result", "default_out", "lookup_line", "preview", "run"]


@dataclass
class Result:
    """What a run made."""

    book: Roadbook  # with the lookups' reports: book.hours, book.towns, book.climb_names
    html: Path
    pdf: Path | None = None  # None when no PDF was asked for, or it could not be written
    pdf_error: str | None = None  # why the PDF asked for was not written; the HTML is, all the same
    warnings: list[str] = field(default_factory=list)  # what the run warned of: no elevation, no arrival times…


@dataclass
class Preview:
    """What a preview made: the road book built offline (see `preview`), and its HTML, written nowhere."""

    book: Roadbook  # its lookups' reports count what they left unasked: report.unasked
    html: str
    warnings: list[str] = field(default_factory=list)


class _Collect(logging.Handler):
    """Keeps the warnings logged on this thread, for a Result: a preview may build alongside a run, on another."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.thread = threading.get_ident()
        self.messages: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread:
            self.messages.append(record.getMessage())


@contextmanager
def _warnings() -> Iterator[list[str]]:
    """The warnings logged within, on this thread."""
    collect = _Collect()
    logger = logging.getLogger("roadbook")
    logger.addHandler(collect)
    try:
        yield collect.messages
    finally:
        logger.removeHandler(collect)


def lookup_line(what: str, r: Report | None, found: str, items: str) -> tuple[str, str | None] | None:
    """What an OpenStreetMap lookup found, e.g. "Towns: 5 of 7 busy stops named (Nominatim)", and a warning if some
    went unanswered; None when it wasn't asked for, or had nothing to look up (a flat route has no climbs to name)."""
    if r is None or not r.asked:
        return None
    where = [*r.sources, *([f"{r.cached} from cache"] if r.cached else [])]
    line = f"{what}: {r.found} of {r.asked} {found}" + (f" ({', '.join(where)})" if where else "")
    failure = f"{what}: OpenStreetMap did not answer for some {items}; run again later." if r.failed else None
    return line, failure


def default_out(gpx: Path) -> Path:
    """Where the road book goes when no output is given: next to the GPX."""
    return gpx.with_suffix(".roadbook.html")


def run(gpx: Path, cfg: dict[str, Any], *, out: Path | None = None, pdf: bool = False) -> Result:
    """Build the road book of `gpx` with the settings `cfg`, write its HTML, and its PDF if asked for.

    Raises GpxError for a file that is no GPX the road book can use. A PDF that can't be written is not an error:
    the HTML is there all the same, and the Result says why.
    """
    with _warnings() as warnings:
        book = build(gpx, cfg)
        out = out or default_out(gpx)
        out.write_text(render_html(book, cfg), encoding="utf-8")
        result = Result(book, out)
        if pdf:
            target = out.with_suffix(".pdf")
            try:
                html_to_pdf(out, target)
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:  # the browser failed, or never finished
                result.pdf_error = str(exc)
            else:
                result.pdf = target
    result.warnings = warnings
    return result


def preview(gpx: Path, cfg: dict[str, Any]) -> Preview:
    """Build the road book of `gpx` with the settings `cfg` and render it, writing nothing: a look before the run.

    The OpenStreetMap lookups only read their cache, whatever its age, and never go online: names and hours not
    cached yet are missing, and the book's reports count them (`unasked`). Raises GpxError as `run` does.
    """
    with _warnings() as warnings:
        book = build(gpx, cfg, offline=True)
        html = render_html(book, cfg)
    return Preview(book, html, warnings)
