import pytest


@pytest.fixture(autouse=True)
def _no_name_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Town and climb names are on by default, and looked up online: tests stay offline unless they put in a fake
    network."""
    monkeypatch.setattr("roadbook.build.name_towns", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("roadbook.build.name_climbs", lambda *_args, **_kwargs: None)
