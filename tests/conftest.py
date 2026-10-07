import logging
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _no_name_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Town and climb names are on by default, and looked up online: tests stay offline unless they put in a fake
    network."""
    monkeypatch.setattr("roadbook.build.name_towns", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("roadbook.build.name_climbs", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _fresh_log() -> Iterator[None]:
    """A CLI run sets the log up for its stderr, which is gone once the test ends: take it down after each test."""
    yield
    for name in ("roadbook", "roadbook.http"):
        logger = logging.getLogger(name)
        for h in logger.handlers[:]:
            logger.removeHandler(h)
        logger.setLevel(logging.NOTSET)
