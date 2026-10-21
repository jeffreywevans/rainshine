"""Unit conversion and text output, independent of Tk and network access."""

from __future__ import annotations

import calendar
from zoneinfo import ZoneInfo

from .models import DayReport, Reading


def temperature(value: float | None, imperial: bool = True) -> str:
    if value is None:
        return "—"
    v = value * 9 / 5 + 32 if imperial else value
    return f"{v:.1f}°{'F' if imperial else 'C'}"


def amount(reading: Reading | None, imperial: bool = True, *, precipitation: bool = False) -> str:
    if reading is None:
        return "—"
    if precipitation and reading.measurement_flag == "H":
        return "Reported"
    if reading.trace:
        return "Trace"
    v = reading.value / 25.4 if imperial else reading.value
    return f"{v:.2f} in" if imperial else f"{v:.1f} mm"


def speed(value: float | None, imperial: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value * 2.236936:.1f} mph" if imperial else f"{value * 3.6:.1f} km/h"


def day_title(report: DayReport) -> str:
    d = report.day
    return f"{calendar.day_name[d.weekday()]}, {calendar.month_name[d.month]} {d.day}, {d.year}"


def summary_text(report: DayReport, imperial: bool = True) -> str:
    lines = [report.location.name, day_title(report), "Recorded weather · NOAA archives"]
    if report.daily:
        d = report.daily
        lines += [f"Daily station: {d.station.display_name}, {d.station.distance_km:.1f} km away"]
        for name, label in [("TMAX", "High"), ("TMIN", "Low")]:
            r = d.readings.get(name)
            lines.append(f"{label}: {temperature(r.value if r else None, imperial)}")
        for name, label in [("PRCP", "Reported precipitation"), ("SNOW", "Snowfall"), ("SNWD", "Snow depth")]:
            lines.append(f"{label}: {amount(d.readings.get(name), imperial, precipitation=name == 'PRCP')}")
    if report.hourly:
        h = report.hourly
        lines += [f"Subdaily station: {h.station.display_name}, {h.station.distance_km:.1f} km away",
                  f"{len(h.observations)} recorded timestamps in {report.location.timezone}."]
        gusts = [o.readings["wind_gust"].value for o in h.observations if "wind_gust" in o.readings]
        if gusts:
            lines.append(f"Largest recorded gust: {speed(max(gusts), imperial)}")
    if not report.daily and not report.hourly:
        lines.append("No recorded weather is available for this selection.")
    lines.extend(report.notes)
    return "\n".join(lines)


def observation_rows(report: DayReport, imperial: bool = True) -> list[tuple[str, ...]]:
    if not report.hourly:
        return []
    tz = ZoneInfo(report.location.timezone)
    result = []
    for o in report.hourly.observations:
        r = o.readings
        def val(key):
            return r[key].value if key in r else None
        result.append((
            o.time.astimezone(tz).strftime("%H:%M %Z"), temperature(val("temperature"), imperial), o.sky or "—",
            speed(val("wind_speed"), imperial), speed(val("wind_gust"), imperial),
            f"{val('relative_humidity'):.0f}%" if val("relative_humidity") is not None else "—",
            f"{val('sea_level_pressure'):.1f} hPa" if val("sea_level_pressure") is not None else "—",
        ))
    return result
