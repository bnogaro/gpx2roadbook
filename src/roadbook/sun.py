"""When the sun rises and sets, for the booklet to mark the night: the sunrise equation, to about a minute.

The ride's times are local times without a zone (opening.py), so the sun's are given in the computer's own time
zone: right for a ride in the country the road book is made in, an hour or so off for one abroad.
"""

from __future__ import annotations

import datetime as dt
import math

J2000 = 2451545.0  # the Julian day of 2000-01-01 12:00 UTC
EPOCH = dt.datetime(2000, 1, 1, 12, tzinfo=dt.UTC)
TILT = 23.4397  # the Earth's axial tilt, degrees
HORIZON = -0.833  # the sun's centre this far below the horizon at sunrise and sunset: refraction, and its radius


def sun_times(lat: float, lon: float, day: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
    """(sunrise, sunset) on `day` at (`lat`, `lon`), in UTC; None when the sun doesn't rise or doesn't set."""
    n = (day - EPOCH.date()).days + 0.0008 - lon / 360  # days from J2000 to the sun's transit here, near enough
    m = (357.5291 + 0.98560028 * n) % 360  # the sun's mean anomaly
    mr = math.radians(m)
    centre = 1.9148 * math.sin(mr) + 0.02 * math.sin(2 * mr) + 0.0003 * math.sin(3 * mr)
    ecliptic = math.radians((m + centre + 180 + 102.9372) % 360)
    transit = J2000 + n + 0.0053 * math.sin(mr) - 0.0069 * math.sin(2 * ecliptic)
    declination = math.asin(math.sin(ecliptic) * math.sin(math.radians(TILT)))
    phi = math.radians(lat)
    cos_hour = (math.sin(math.radians(HORIZON)) - math.sin(phi) * math.sin(declination)) / (
        math.cos(phi) * math.cos(declination)
    )
    if not -1 <= cos_hour <= 1:  # polar day or night
        return None
    half = math.degrees(math.acos(cos_hour)) / 360  # half the day's length, in days

    def utc(julian: float) -> dt.datetime:
        return EPOCH + dt.timedelta(days=julian - J2000)

    return utc(transit - half), utc(transit + half)


def local(t: dt.datetime) -> dt.datetime:
    """`t` in the computer's time zone, without its zone, as the ride's times are."""
    return t.astimezone().replace(tzinfo=None)
