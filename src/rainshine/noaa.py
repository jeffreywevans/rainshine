"""Decode NOAA archive formats without filling missing values or adding models."""

from __future__ import annotations

import csv
import math
from datetime import date, datetime
from pathlib import Path
from threading import Event

from .catalog import STATION_ID
from .models import Cancelled, DailyRecord, Observation, Reading, Station, UTC, WeatherError

DAILY_DIVISORS = {"TMAX": 10, "TMIN": 10, "TAVG": 10, "PRCP": 10,
                  "SNOW": 1, "SNWD": 1, "AWND": 10, "WSFG": 10, "RHAV": 1}
DAILY_WEATHER = {"WT01": "Fog", "WT02": "Heavy fog", "WT03": "Thunder", "WT04": "Ice pellets or sleet",
                 "WT05": "Hail", "WT06": "Glaze or rime", "WT08": "Smoke or haze", "WT09": "Blowing snow",
                 "WT11": "Strong wind", "WT14": "Drizzle", "WT16": "Rain", "WT17": "Freezing rain", "WT18": "Snow"}
HOURLY_PARAMETERS = ("temperature", "dew_point_temperature", "wind_speed", "wind_gust", "wind_direction",
                     "relative_humidity", "sea_level_pressure", "visibility")
LEGACY_ISD = {"313", "314", "315", "322", "335", "343", "344", "346"}
LEGACY_NATIONAL = {"220", "221", "222", "223", "347", "348"}
SKY_LABELS = {"CLR": "Clear sky", "SKC": "Clear sky", "FEW": "Few clouds", "SCT": "Scattered clouds",
              "BKN": "Broken cloud", "OVC": "Overcast", "VV": "Sky obscured", "NSC": "No significant cloud"}


def reported_sky(row: dict[str, str]) -> str | None:
    """Read explicit ICAO cloud labels; numeric legacy sky codes stay unknown."""
    labels = []
    order = {"NSC": 0, "CLR": 0, "SKC": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 5}
    for prefix in ("sky_cover_summation_", "sky_cover_layer_"):
        for n in range(1, 5):
            key = prefix + str(n)
            text = (row.get(key) or "").split(":", 1)[0].strip("; ")
            quality = (row.get(key + "_Quality_Code") or "").strip("; ")
            source = (row.get(key + "_Source_Code") or "").strip()
            if text in SKY_LABELS and accepted_hourly_quality(quality, source):
                labels.append(text)
    return SKY_LABELS[max(labels, key=lambda code: order[code])] if labels else None


def station_url(station: Station, year: int | None = None) -> str:
    if not STATION_ID.fullmatch(station.id):
        raise ValueError("Invalid NOAA station identifier.")
    if station.dataset == "ghcnd":
        return f"https://www.ncei.noaa.gov/pub/data/ghcn/daily/all/{station.id}.dly"
    if year is None or not 1800 <= year <= 2100:
        raise ValueError("A valid station-year is required.")
    return ("https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/"
            f"access/by-year/{year}/psv/GHCNh_{station.id}_{year}.psv")


def _daily_reading(cell: str, element: str) -> tuple[Reading | None, int]:
    """Decode one daily cell, keeping missing and rejected values distinct."""
    try:
        raw = int(cell[:5])
    except ValueError as exc:
        raise WeatherError("NOAA returned an invalid daily value.") from exc
    if raw == -9999:
        return None, 0
    measurement, quality, source = (c.strip() for c in cell[5:8])
    # A nonblank daily QFLAG means a failed NOAA quality check. P and I
    # precipitation flags are presumed/missing, not measured dry days.
    if quality or (element == "PRCP" and measurement in {"P", "I"}):
        return None, 1
    divisor = DAILY_DIVISORS.get(element, 1)
    value = raw / divisor if divisor != 1 else raw
    return Reading(value, measurement, quality, source), 0


def read_daily(path: Path, station: Station, day: date, cancel: Event | None = None) -> DailyRecord | None:
    readings = {}
    rejected = 0
    expected = f"{station.id}{day.year:04d}{day.month:02d}"
    with path.open(encoding="utf-8", errors="strict") as stream:
        for line in stream:
            if cancel and cancel.is_set():
                raise Cancelled("Lookup cancelled.")
            if not line.startswith(expected):
                continue
            if len(line.rstrip("\r\n")) < 269:
                raise WeatherError("NOAA returned a truncated daily record.")
            element = line[17:21]
            if element not in DAILY_DIVISORS and element not in DAILY_WEATHER:
                continue
            offset = 21 + (day.day - 1) * 8
            cell = line[offset:offset + 8]
            reading, bad = _daily_reading(cell, element)
            rejected += bad
            if reading is not None:
                readings[element] = reading
    return DailyRecord(station, day, readings, rejected_values=rejected) if readings else None


def accepted_hourly_quality(quality: str, source: str) -> bool:
    """NOAA quality codes vary by source: code 5 is not universally 'good'."""
    if not quality:
        return True
    if source in LEGACY_ISD:
        return quality in {"0", "1", "4", "5", "9", "A", "U", "P", "I", "M", "R"}
    if source in LEGACY_NATIONAL:
        return quality in {"0", "1", "4"}
    # New GHCNh lowercase flags identify failed tests. Unrecognized source-
    # specific codes are preserved through an exclusion count, not guessed at.
    return quality in {"0", "1", "9"}


def _hourly_timestamp(text: str) -> datetime:
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise WeatherError("NOAA returned an invalid observation timestamp.") from exc


def _hourly_reading(row: dict[str, str], name: str) -> tuple[Reading | None, int]:
    """Decode one field, distinguishing missing data from rejected values."""
    text = (row.get(name) or "").strip()
    if not text:
        return None, 0
    try:
        value = float(text)
    except ValueError:
        return None, 1
    measurement = (row.get(name + "_Measurement_Code") or "").strip()
    quality = (row.get(name + "_Quality_Code") or "").strip()
    source = (row.get(name + "_Source_Code") or "").strip()
    if math.isclose(value, -9999, rel_tol=0, abs_tol=1e-9) or not math.isfinite(value):
        return None, 0
    if measurement == "E" or not accepted_hourly_quality(quality, source):
        return None, 1
    if not _valid_hourly_range(name, value):
        return None, 1
    return Reading(value, measurement, quality, source), 0


def _valid_hourly_range(name: str, value: float) -> bool:
    if name == "relative_humidity":
        return 0 <= value <= 100
    if name == "wind_direction":
        return 0 <= value <= 360
    if name in {"wind_speed", "wind_gust", "visibility"}:
        return value >= 0
    return True


def _hourly_readings(row: dict[str, str]) -> tuple[dict[str, Reading], int]:
    readings = {}
    rejected = 0
    for name in HOURLY_PARAMETERS:
        reading, bad = _hourly_reading(row, name)
        rejected += bad
        if reading is not None:
            readings[name] = reading
    return readings, rejected


def read_hourly(path: Path, station: Station, start: datetime, end: datetime,
                cancel: Event | None = None) -> tuple[list[Observation], int, int]:
    observations = []
    rejected = 0
    reports = 0
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="|")
        if not reader.fieldnames or not {"STATION", "DATE", "temperature"}.issubset(reader.fieldnames):
            raise WeatherError("NOAA's hourly archive columns were not recognized.")
        for row in reader:
            if cancel and cancel.is_set():
                raise Cancelled("Lookup cancelled.")
            if row.get("STATION") != station.id:
                raise WeatherError("The hourly file contains a different station identifier.")
            stamp = _hourly_timestamp(row["DATE"])
            if not start <= stamp < end:
                continue
            reports += 1
            readings, bad = _hourly_readings(row)
            rejected += bad
            if readings:
                observations.append(Observation(stamp, readings, reported_sky(row)))
    return observations, rejected, reports
