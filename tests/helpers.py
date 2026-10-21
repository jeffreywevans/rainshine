"""Synthetic NOAA-format fixtures for isolated, offline tests."""

import csv
import io
from datetime import date
from pathlib import Path

from rainshine.models import Station

DAY = date(2010, 2, 6)
DAILY = Station("USC00000001", "Near daily station", 39.76, -84.19, "ghcnd", distance_km=1)
HOURLY = Station("USW00000001", "Near hourly station", 39.77, -84.20, "ghcnh", code="KAAA", distance_km=2)


def daily_line(element, value, *, station=DAILY, day=DAY, measurement="", quality="", source="7"):
    cells = ["-9999   "] * 31
    cells[day.day - 1] = f"{value:5d}{measurement or ' '}{quality or ' '}{source or ' '}"
    return f"{station.id}{day.year:04d}{day.month:02d}{element}" + "".join(cells) + "\n"


def hourly_text(rows):
    fields = ["STATION", "DATE", "temperature", "temperature_Measurement_Code", "temperature_Quality_Code", "temperature_Source_Code"]
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields, delimiter="|")
    writer.writeheader()
    for row in rows:
        writer.writerow({"STATION": HOURLY.id, "DATE": "2010-02-06T12:00:00", **row})
    return stream.getvalue()


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
