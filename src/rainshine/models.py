"""Small, GUI-independent types shared by the archive and presentation layers."""

from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

MIN_DATE = date(1989, 1, 1)
MAX_DATE = date(2032, 12, 31)
UTC = timezone.utc


class WeatherError(Exception):
    """An expected, user-readable retrieval or data-format problem."""


class Cancelled(WeatherError):
    """The user cancelled a lookup."""


class NotFound(WeatherError):
    """An archive file is not available."""


def validate_date(day: date) -> None:
    if not MIN_DATE <= day <= MAX_DATE:
        raise ValueError("Choose a date from January 1, 1989 through December 31, 2032.")


def days_in_month(year: int, month: int) -> tuple[int, ...]:
    return tuple(range(1, calendar.monthrange(year, month)[1] + 1))


def utc_window(day: date, zone: str) -> tuple[datetime, datetime]:
    """The true local calendar day, including 23- and 25-hour DST days."""
    tz = ZoneInfo(zone)
    return (
        datetime.combine(day, time.min, tz).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), time.min, tz).astimezone(UTC),
    )


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    latitude: float
    longitude: float
    dataset: Literal["ghcnh", "ghcnd"]
    code: str | None = None
    distance_km: float = 0.0
    first_year: int = 0
    last_year: int = 0
    months: dict[str, list[int]] = field(default_factory=dict)
    elements: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.code or self.id})"


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    timezone: str
    hourly: tuple[Station, ...]
    daily: tuple[Station, ...]


@dataclass(frozen=True)
class Reading:
    """A NOAA value in native SI units, retaining the archive's flags."""

    value: float
    measurement_flag: str = ""
    quality_flag: str = ""
    source_flag: str = ""

    @property
    def trace(self) -> bool:
        return self.measurement_flag == "T"


@dataclass
class DailyRecord:
    station: Station
    day: date
    readings: dict[str, Reading]
    source_url: str = ""
    retrieved_at: str = ""
    from_cache: bool = False
    stale: bool = False
    rejected_values: int = 0


@dataclass
class Observation:
    time: datetime
    readings: dict[str, Reading]
    sky: str | None = None


@dataclass
class HourlyRecord:
    station: Station
    observations: list[Observation]
    source_urls: list[str] = field(default_factory=list)
    retrieved_at: str = ""
    from_cache: bool = False
    stale: bool = False
    rejected_values: int = 0
    raw_report_count: int = 0


@dataclass
class DayReport:
    location: Location
    day: date
    status: Literal["available", "partial", "unavailable", "future", "error"]
    daily: DailyRecord | None = None
    hourly: HourlyRecord | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Export exact values, units, and provenance; no model substitutes."""
        def encode(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: encode(v) for k, v in obj.items()}
            if isinstance(obj, (tuple, list)):
                return [encode(v) for v in obj]
            return obj

        return {
            "application": "Rainshine",
            "data_kind": "recorded_observations",
            "location": self.location.name,
            "location_coordinates": [self.location.latitude, self.location.longitude],
            "timezone": self.location.timezone,
            "day": self.day.isoformat(),
            "status": self.status,
            "native_units": {
                "TMAX": "degC", "TMIN": "degC", "TAVG": "degC",
                "PRCP": "mm", "SNOW": "mm", "SNWD": "mm", "AWND": "m/s", "WSFG": "m/s", "RHAV": "percent",
                "temperature": "degC", "dew_point_temperature": "degC",
                "wind_speed": "m/s", "wind_gust": "m/s", "wind_direction": "degrees",
                "relative_humidity": "percent", "sea_level_pressure": "hPa",
                "visibility": "km",
            },
            "daily": encode(asdict(self.daily)) if self.daily else None,
            "hourly": encode(asdict(self.hourly)) if self.hourly else None,
            "notes": list(self.notes),
        }
