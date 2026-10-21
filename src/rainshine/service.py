"""Date-aware station selection and the application's recorded-weather API."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from threading import Event
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .cache import ArchiveCache
from .catalog import Catalog
from .models import Cancelled, DayReport, HourlyRecord, NotFound, WeatherError, utc_window, validate_date
from .noaa import read_daily, read_hourly, station_url


class WeatherService:
    def __init__(self, catalog: Catalog | None = None, cache: ArchiveCache | None = None,
                 clock: Callable[[], datetime] | None = None):
        self.catalog = catalog or Catalog()
        self.cache = cache or ArchiveCache()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, site: str, day: date, *, cancel: Event | None = None,
              progress: Callable[[str], None] | None = None) -> DayReport:
        validate_date(day)
        location = self.catalog.location(site)
        try:
            local_today = self.clock().astimezone(ZoneInfo(location.timezone)).date()
        except ZoneInfoNotFoundError as exc:
            raise WeatherError("Timezone data is missing. Install Rainshine's dependencies, including tzdata on Windows.") from exc
        if day > local_today:
            return DayReport(location, day, "future", notes=[
                "This date is in the future at the selected location. Recorded weather does not exist yet."])
        if cancel and cancel.is_set():
            raise Cancelled("Lookup cancelled.")
        report = DayReport(location, day, "unavailable")
        issues = []
        progress = progress or (lambda message: None)
        daily_candidates = self.catalog.candidates(location.daily, day)
        best_daily, best_score = None, -1
        for station in daily_candidates:
            progress(f"Looking for the daily record at {station.display_name}…")
            try:
                resource = self.cache.fetch(station_url(station), signature=station.id.encode(), cancel=cancel)
                daily = read_daily(resource.path, station, day, cancel)
                if daily and any(e in daily.readings for e in {"TMAX", "TMIN", "PRCP", "SNOW"}):
                    daily.source_url, daily.retrieved_at = resource.url, resource.retrieved_at
                    daily.from_cache, daily.stale = resource.from_cache, resource.stale
                    score = sum(e in daily.readings for e in {"TMAX", "TMIN", "PRCP"})
                    if score > best_score:
                        best_daily, best_score = daily, score
                    if score == 3:
                        break
            except NotFound:
                continue
            except WeatherError as exc:
                if isinstance(exc, Cancelled):
                    raise
                issues.append(str(exc))
        report.daily = best_daily
        if best_daily:
            daily = best_daily
            if daily.rejected_values:
                report.notes.append(f"{daily.rejected_values} daily value(s) with failed quality checks or presumed precipitation were omitted.")
            p = daily.readings.get("PRCP")
            if p and p.measurement_flag == "H":
                report.notes.append("The station reported precipitation, but the recorded zero does not establish a measured total (NOAA flag H).")
            if p and p.measurement_flag in {"A", "C", "E"}:
                report.notes.append("The precipitation record covers fewer than 24 hours; see its measurement flag in Sources & notes.")
            if any(daily.readings.get(e) and daily.readings[e].measurement_flag == "H" for e in {"TMAX", "TMIN"}):
                report.notes.append("The station's reported high/low was derived from hourly temperatures (NOAA flag H).")
            if daily.station.id != location.daily[0].id:
                report.notes.append("The daily record uses a documented alternative with more available values for this date.")

        start, end = utc_window(day, location.timezone)
        years = range(start.year, (end - timedelta(microseconds=1)).year + 1)
        for station in self.catalog.candidates(location.hourly, day):
            progress(f"Reading observations from {station.display_name}…")
            observations, urls, resources = [], [], []
            rejected = raw_reports = 0
            for year in years:
                try:
                    resource = self.cache.fetch(station_url(station, year), signature=b"STATION|", cancel=cancel,
                                                max_age=86400 if year >= local_today.year - 1 else 30 * 86400)
                    values, bad, raw = read_hourly(resource.path, station, start, end, cancel)
                    observations.extend(values)
                    rejected += bad
                    raw_reports += raw
                    urls.append(resource.url)
                    resources.append(resource)
                except NotFound:
                    continue
                except WeatherError as exc:
                    if isinstance(exc, Cancelled):
                        raise
                    issues.append(str(exc))
            if observations:
                # Archives can contain two reports at one timestamp. Merge
                # clean fields, never count the same instant twice in the plot.
                by_time = {}
                for observation in observations:
                    if observation.time in by_time:
                        for key, value in observation.readings.items():
                            by_time[observation.time].readings.setdefault(key, value)
                        if not by_time[observation.time].sky:
                            by_time[observation.time].sky = observation.sky
                    else:
                        by_time[observation.time] = observation
                report.hourly = HourlyRecord(station, sorted(by_time.values(), key=lambda o: o.time), urls,
                                             min(r.retrieved_at for r in resources),
                                             all(r.from_cache for r in resources), any(r.stale for r in resources),
                                             rejected, raw_reports)
                if rejected:
                    report.notes.append(f"{rejected} suspect, estimated, or invalid subdaily value(s) were omitted.")
                if station.id != location.hourly[0].id:
                    report.notes.append("The closest subdaily candidate had no usable observations for this date; a documented alternative is shown.")
                temp_hours = {o.time.replace(minute=0, second=0, microsecond=0)
                              for o in report.hourly.observations if "temperature" in o.readings}
                expected_hours = int((end - start).total_seconds() / 3600)
                if len(temp_hours) < expected_hours:
                    report.notes.append(f"Temperature observations cover {len(temp_hours)} of {expected_hours} hours in the local day. Gaps were left empty.")
                break

        if cancel and cancel.is_set():
            raise Cancelled("Lookup cancelled.")
        if report.daily and report.hourly:
            report.status = "available"
        elif report.daily or report.hourly:
            report.status = "partial"
            report.notes.append("Only part of the requested record is available. Missing values are shown as not recorded.")
        elif issues:
            report.status = "error"
            report.notes.append(issues[0])
        else:
            report.notes.append("No usable observations were found at the documented candidate stations for this date.")
        if report.daily:
            report.notes.append("Daily values use the station's reporting day, which may end at an observation time rather than local midnight.")
        if report.hourly:
            report.notes.append(f"Subdaily observations are filtered to the local calendar day in {location.timezone}, using historical daylight-saving rules.")
        if issues and (report.daily or report.hourly):
            report.notes.append("Some archive requests failed; the available records are displayed. Try again to check for additional data.")
        if (report.daily and report.daily.stale) or (report.hourly and report.hourly.stale):
            report.notes.append("NOAA could not be refreshed. This display uses previously downloaded observations; download timestamps are in Sources & notes.")
        if day == local_today:
            report.notes.append("Today's archive is still being collected and may be incomplete.")
        return report
