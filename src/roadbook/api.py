"""One road book from a GPX and its settings: what the CLI runs, and a GUI too.

Progress goes through the `roadbook` loggers (see `roadbook -v`): a caller attaches its own handler to show it.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from .build import build
from .parse import GpxError
from .render import html_to_pdf, render_html

if TYPE_CHECKING:
    from pathlib import Path

    from .model import Roadbook

__all__ = ["GpxError", "Result", "default_out", "run"]


@dataclass
class Result:
    """What a run made."""

    book: Roadbook  # with the lookups' reports: book.hours, book.towns, book.climb_names
    html: Path
    pdf: Path | None = None  # None when no PDF was asked for, or it could not be written
    pdf_error: str | None = None  # why the PDF asked for was not written; the HTML is, all the same
    warnings: list[str] = field(default_factory=list)  # what the run warned of: no elevation, no arrival times…


class _Collect(logging.Handler):
    """Keeps the warnings a run logs, for its Result."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def default_out(gpx: Path) -> Path:
    """Where the road book goes when no output is given: next to the GPX."""
    return gpx.with_suffix(".roadbook.html")


def run(gpx: Path, cfg: dict[str, Any], *, out: Path | None = None, pdf: bool = False) -> Result:
    """Build the road book of `gpx` with the settings `cfg`, write its HTML, and its PDF if asked for.

    Raises GpxError for a file that is no GPX the road book can use. A PDF that can't be written is not an error:
    the HTML is there all the same, and the Result says why.
    """
    collect = _Collect()
    logger = logging.getLogger("roadbook")
    logger.addHandler(collect)
    try:
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
    finally:
        logger.removeHandler(collect)
    result.warnings = collect.messages
    return result
