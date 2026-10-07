from pathlib import Path
from typing import Any

import pytest

from roadbook.config import load_config
from roadbook.hours import NOMINATIM, OVERPASS, Place, lookup, match
from roadbook.model import Poi
from roadbook.render import pretty_hours

LAT, LON = 42.912725, 0.6478569  # Intermarché Super, km 0.2 of entrainement_ubf.gpx
CFG = load_config()["hours"]

OVERPASS_ANSWER = {
    "elements": [
        {
            "type": "node",
            "id": 3125884927,
            "lat": LAT,
            "lon": LON,
            "tags": {"name": "Intermarché Super", "shop": "supermarket", "opening_hours": "Mo-Sa 09:00-19:30"},
        },
        {  # a neighbour with hours, but not the shop we look for
            "type": "node",
            "id": 11860771992,
            "lat": LAT + 0.0002,
            "lon": LON,
            "tags": {"name": "Optique Cierp-Gaud", "shop": "optician", "opening_hours": "Tu-Fr 09:30-18:30"},
        },
    ]
}
NOMINATIM_ANSWER = [
    {
        "osm_type": "node",
        "osm_id": 3125884927,
        "lat": str(LAT),
        "lon": str(LON),
        "name": "Intermarché Super",
        "extratags": {"opening_hours": "Mo-Sa 09:00-19:30"},
    }
]


def _poi(name: str = "Intermarché", category: str = "grocery", lat: float = LAT, lon: float = LON) -> Poi:
    return Poi(name=name, type="Supermarché", lat=lat, lon=lon, category=category)


class FakeHttp:
    """Answers like the services would, or fails for the hosts in `down`; records the URLs asked."""

    def __init__(self, *, down: tuple[str, ...] = ()) -> None:
        self.down = down
        self.urls: list[str] = []

    def __call__(self, url: str, data: dict[str, str] | None) -> Any:  # noqa: ANN401
        self.urls.append(url)
        if any(url.startswith(d) for d in self.down):
            msg = "504 Gateway Timeout"
            raise OSError(msg)
        if url.startswith(NOMINATIM):
            return NOMINATIM_ANSWER
        assert data is not None
        assert '["opening_hours"]' in data["data"]
        return OVERPASS_ANSWER


def _lookup(pois: list[Poi], tmp_path: Path, http: FakeHttp, **cfg: Any) -> Any:  # noqa: ANN401
    return lookup(pois, {**CFG, **cfg}, cache_path=tmp_path / "cache.json", http=http, sleep=lambda _: None)


def test_match_picks_the_closest_name_within_reach() -> None:
    places = [
        Place("node/1", "Optique Cierp-Gaud", LAT, LON, "Tu-Fr 09:30-18:30"),
        Place("node/2", "Intermarché Super", LAT + 0.0003, LON, "Mo-Sa 09:00-19:30"),  # ~33 m north
    ]
    assert match(_poi(), places, 80) == places[1]  # "Intermarché" is part of its name
    assert match(_poi(), places, 20) is None  # too far; the optician nearby is not it
    assert match(_poi("Boulangerie Dupont"), places, 80) is None


def test_hours_come_from_overpass_for_shop_categories_only(tmp_path: Path) -> None:
    shop, fountain = _poi(), _poi("Fontaine", category="water")
    http = FakeHttp()
    report = _lookup([shop, fountain], tmp_path, http)
    assert shop.opening_hours == "Mo-Sa 09:00-19:30"
    assert shop.osm_id == "node/3125884927"
    assert fountain.opening_hours is None
    assert (report.asked, report.found, report.sources, report.failed) == (1, 1, ["Overpass"], False)
    assert http.urls == [OVERPASS[0]]


def test_a_second_run_uses_the_cache_without_network(tmp_path: Path) -> None:
    _lookup([_poi()], tmp_path, FakeHttp())
    again = _poi()
    offline = FakeHttp(down=("https://",))
    report = _lookup([again], tmp_path, offline)
    assert again.opening_hours == "Mo-Sa 09:00-19:30"
    assert (report.cached, report.failed, offline.urls) == (1, False, [])


def test_a_shop_osm_has_no_hours_for_is_cached_as_such(tmp_path: Path) -> None:
    _lookup([_poi("Boulangerie Dupont", "bakery")], tmp_path, FakeHttp())
    again = _poi("Boulangerie Dupont", "bakery")
    report = _lookup([again], tmp_path, FakeHttp(down=("https://",)))
    assert again.opening_hours is None
    assert (report.cached, report.found, report.failed) == (1, 0, False)


def test_a_stale_cache_is_looked_up_again(tmp_path: Path) -> None:
    _lookup([_poi()], tmp_path, FakeHttp())
    http = FakeHttp()
    report = _lookup([_poi()], tmp_path, http, max_age_days=0)
    assert report.cached == 0
    assert http.urls == [OVERPASS[0]]


def test_overpass_falls_back_to_its_mirror(tmp_path: Path) -> None:
    http = FakeHttp(down=(OVERPASS[0],))
    shop = _poi()
    _lookup([shop], tmp_path, http)
    assert shop.opening_hours == "Mo-Sa 09:00-19:30"
    assert http.urls == list(OVERPASS)


def test_overpass_down_falls_back_to_nominatim(tmp_path: Path) -> None:
    http = FakeHttp(down=tuple(OVERPASS))
    shops = [_poi(), _poi(lat=LAT + 0.00001)]
    report = _lookup(shops, tmp_path, http)
    assert [s.opening_hours for s in shops] == ["Mo-Sa 09:00-19:30"] * 2
    assert (report.sources, report.failed) == (["Nominatim"], False)
    assert sum(u.startswith(NOMINATIM) for u in http.urls) == 2


def test_everything_down_gives_up_quickly_and_caches_nothing(tmp_path: Path) -> None:
    http = FakeHttp(down=("https://",))
    shops = [_poi(lat=LAT + i * 0.01) for i in range(10)]
    report = _lookup(shops, tmp_path, http)
    assert all(s.opening_hours is None for s in shops)
    assert (report.found, report.failed) == (0, True)
    assert sum(u.startswith(NOMINATIM) for u in http.urls) == 3  # NOMINATIM_GIVE_UP failures in a row
    # nothing was learnt: a later run asks again
    later = FakeHttp()
    _lookup(shops[:1], tmp_path, later)
    assert later.urls == [OVERPASS[0]]


EN = "\u2013"  # the en dash pretty_hours() puts between times


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("Mo-Sa 09:00-19:30", f"Mo-Sa 09:00{EN}19:30"),
        (
            "Mo-Fr 07:00-12:30,15:30-19:00; Su 07:00-12:00; PH off",
            f"Mo-Fr 07:00{EN}12:30, 15:30{EN}19:00 · Su 07:00{EN}12:00 · PH off",
        ),
        ("24/7", "24/7"),
    ],
)
def test_pretty_hours(raw: str, shown: str) -> None:
    assert pretty_hours(raw) == shown
