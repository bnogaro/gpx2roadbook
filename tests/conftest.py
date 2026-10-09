import logging
from collections.abc import Iterator

import pytest

from roadbook.osm import Report


@pytest.fixture(autouse=True)
def _no_name_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Town and climb names are on by default, and looked up online, as are the places on climbs with climb pages:
    tests stay offline unless they put in a fake network."""
    monkeypatch.setattr("roadbook.build.name_towns", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("roadbook.build.name_climbs", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("roadbook.build.find_places", lambda *_args, **_kwargs: None)


@pytest.fixture(autouse=True)
def _no_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The map is on by default, its tiles fetched online and kept in the user's cache: tests get none, unless they
    put in a fake network and a cache of their own."""
    monkeypatch.setattr("roadbook.routemap.fetch_tiles", lambda *_args, **_kwargs: ({}, Report()))


@pytest.fixture(autouse=True)
def _fresh_log() -> Iterator[None]:
    """A CLI run sets the log up for its stderr, which is gone once the test ends: take it down after each test."""
    yield
    for name in ("roadbook", "roadbook.http"):
        logger = logging.getLogger(name)
        for h in logger.handlers[:]:
            logger.removeHandler(h)
        logger.setLevel(logging.NOTSET)
