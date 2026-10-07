import logging
from pathlib import Path
from typing import Any, Self

import pytest

from roadbook.osm import NOMINATIM, NOMINATIM_EVERY_S, NOMINATIM_GIVE_UP, JsonCache, http, nominatim, one_by_one


def test_the_cache_file_is_only_written_with_new_answers(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "c.json"
    JsonCache(path, 1, 30).save()  # everything came from the cache, or nothing answered
    assert not path.exists()
    cache = JsonCache(path, 1, 30)
    cache.put("k", None)  # "OSM has nothing" is an answer too
    cache.save()
    assert JsonCache(path, 1, 30).get("k") == (True, None)


def test_one_by_one_gives_up_after_failures_in_a_row() -> None:
    answered: list[int] = []

    def ask(n: int) -> int:
        if n in {1, 3, 4, 5}:  # 1 fails alone; 3, 4, 5 in a row
            raise OSError(n)
        return n * 10

    failed = one_by_one(list(range(8)), ask, lambda _n, r: answered.append(r))
    assert failed
    assert answered == [0, 20]  # a success resets the count; 6 and 7 are not even asked
    assert NOMINATIM_GIVE_UP == 3


def test_one_by_one_reports_no_failure_when_all_answer() -> None:
    assert not one_by_one([1, 2], lambda n: n, lambda _n, _r: None)


def test_nominatim_waits_before_every_request() -> None:
    sleeps: list[float] = []
    urls: list[str] = []

    def http(url: str, _data: dict[str, str] | None) -> Any:  # noqa: ANN401
        urls.append(url)
        return {"address": {"town": "Sault"}}

    for _ in range(2):
        town = nominatim("reverse", {"lat": "44.1", "lon": "5.4"}, http, sleeps.append, lambda r: r["address"]["town"])
        assert town == "Sault"
    assert sleeps == [NOMINATIM_EVERY_S] * 2  # the first too: another lookup may just have asked
    assert urls == [f"{NOMINATIM}/reverse?lat=44.1&lon=5.4"] * 2


@pytest.mark.parametrize("answer", [["a list"], {"error": "Unable to geocode"}, "<html>"])
def test_an_answer_that_cannot_be_read_counts_as_no_answer(answer: object) -> None:
    with pytest.raises(OSError, match=r"."):
        nominatim("reverse", {}, lambda _u, _d: answer, lambda _s: None, lambda r: r["address"]["town"])


class _Answer:
    """What urlopen() gives back, for the request log."""

    status = 200

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self) -> bytes:
        return b'{"elements": []}'


def test_vvv_logs_each_request_and_its_answer(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Answer())
    caplog.set_level(logging.DEBUG, logger="roadbook.http")
    assert http("https://overpass.example/api", {"data": "[out:json];"}) == {"elements": []}
    assert [r.name for r in caplog.records] == ["roadbook.http"] * 2
    assert caplog.messages[0] == "POST https://overpass.example/api, 24 bytes"
    assert caplog.messages[1].startswith("200, 0.0 kB in ")


def test_a_request_that_fails_is_logged_too(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    def down(*_args: object, **_kwargs: object) -> None:
        msg = "timed out"
        raise OSError(msg)

    monkeypatch.setattr("urllib.request.urlopen", down)
    caplog.set_level(logging.DEBUG, logger="roadbook.http")
    with pytest.raises(OSError, match="timed out"):
        http("https://nominatim.example/reverse?lat=1", None)
    assert caplog.messages[0] == "GET https://nominatim.example/reverse?lat=1"
    assert "failed after" in caplog.messages[1]
