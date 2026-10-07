import pytest


@pytest.fixture(autouse=True)
def _no_climb_name_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Climb names are on by default, and looked up online: tests stay offline unless they put in a fake network."""
    monkeypatch.setattr("roadbook.build.name_climbs", lambda *_args, **_kwargs: None)
