import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Event
from unittest.mock import patch

from helpers import DAILY, DAY, HOURLY, daily_line, hourly_text, write
from rainshine.cache import CachedFile
from rainshine.catalog import Catalog
from rainshine.models import Cancelled, Location, NotFound, Observation, Reading, WeatherError
from rainshine.noaa import station_url
from rainshine.service import WeatherService


class FakeCatalog:
    snapshot_year = 2026
    def __init__(self, location):
        self.site = location
        self.names = (location.name,)
    def location(self, name):
        return self.site
    def candidates(self, stations, day):
        return list(stations)


class FakeCache:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, []
    def fetch(self, url, **kwargs):
        self.calls.append(url)
        if url not in self.mapping:
            raise NotFound("No record")
        return CachedFile(self.mapping[url], url, "2026-10-21T00:00:00+00:00", True)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.site = Location("Example", 39.76, -84.19, "America/New_York", (HOURLY,), (DAILY,))
        self.clock = lambda: datetime(2026, 10, 21, 12, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def test_future_date_returns_without_any_network_request(self):
        cache = FakeCache({})
        report = WeatherService(FakeCatalog(self.site), cache, self.clock).fetch("Example", date(2032, 12, 31))
        self.assertEqual(report.status, "future")
        self.assertEqual(cache.calls, [])

    def test_future_test_uses_site_timezone_not_computer_date(self):
        site = replace(self.site, timezone="Australia/Sydney")
        clock = lambda: datetime(2026, 10, 20, 23, tzinfo=timezone.utc)
        report = WeatherService(FakeCatalog(site), FakeCache({}), clock).fetch("Example", date(2026, 10, 21))
        self.assertNotEqual(report.status, "future")

    def test_missing_record_is_not_zero_weather(self):
        report = WeatherService(FakeCatalog(self.site), FakeCache({}), self.clock).fetch("Example", DAY)
        self.assertEqual(report.status, "unavailable")
        self.assertIsNone(report.daily)
        self.assertIsNone(report.hourly)

    def test_daily_and_subdaily_keep_separate_station_provenance(self):
        daily = write(self.path / "daily", daily_line("TMAX", 20) + daily_line("TMIN", -30) + daily_line("PRCP", 12))
        hourly = write(self.path / "hourly", hourly_text([{"temperature": "1.4", "temperature_Source_Code": "223", "temperature_Quality_Code": "1"}]))
        cache = FakeCache({station_url(DAILY): daily, station_url(HOURLY, 2010): hourly})
        report = WeatherService(FakeCatalog(self.site), cache, self.clock).fetch("Example", DAY)
        self.assertEqual(report.status, "available")
        self.assertEqual(report.daily.station.id, DAILY.id)
        self.assertEqual(report.hourly.station.id, HOURLY.id)
        self.assertEqual(report.hourly.observations[0].time.hour, 12)
        self.assertTrue(any("1 of 24" in note for note in report.notes))

    def test_fallback_preserves_actual_station_and_distance(self):
        farther = replace(DAILY, id="USC00000002", name="Farther", distance_km=8)
        site = replace(self.site, daily=(DAILY, farther))
        daily = write(self.path / "daily", daily_line("TMAX", 20, station=farther) + daily_line("TMIN", -30, station=farther) + daily_line("PRCP", 0, station=farther))
        report = WeatherService(FakeCatalog(site), FakeCache({station_url(farther): daily}), self.clock).fetch("Example", DAY)
        self.assertEqual(report.daily.station.id, farther.id)
        self.assertEqual(report.daily.station.distance_km, 8)
        self.assertEqual(report.status, "partial")

    def test_complete_daily_record_is_preferred_over_rain_only_gauge(self):
        farther = replace(DAILY, id="USC00000002", distance_km=8)
        site = replace(self.site, daily=(DAILY, farther))
        near = write(self.path / "near", daily_line("PRCP", 13))
        far = write(self.path / "far", daily_line("TMAX", 20, station=farther) + daily_line("TMIN", -30, station=farther) + daily_line("PRCP", 0, station=farther))
        cache = FakeCache({station_url(DAILY): near, station_url(farther): far})
        report = WeatherService(FakeCatalog(site), cache, self.clock).fetch("Example", DAY)
        self.assertEqual(report.daily.station.id, farther.id)
        self.assertEqual(set(report.daily.readings), {"TMAX", "TMIN", "PRCP"})

    def test_hourly_missing_year_boundary_can_still_return_partial_observations(self):
        site = replace(self.site, timezone="Australia/Sydney")
        before = "1988-12-31T14:00:00"
        hourly = write(self.path / "hourly", hourly_text([{"DATE": before, "temperature": "21.1"}]))
        report = WeatherService(FakeCatalog(site), FakeCache({station_url(HOURLY, 1988): hourly}), self.clock).fetch("Example", date(1989, 1, 1))
        self.assertEqual(len(report.hourly.observations), 1)
        self.assertEqual(report.status, "partial")

    def test_cancellation_during_either_archive_lookup_propagates(self):
        for dataset in ("ghcnd", "ghcnh"):
            with self.subTest(dataset=dataset):
                cache = FakeCache({})

                def fetch(url, **kwargs):
                    if ("/daily/" in url) == (dataset == "ghcnd"):
                        raise Cancelled("Lookup cancelled.")
                    raise NotFound("No record")

                cache.fetch = fetch
                service = WeatherService(FakeCatalog(self.site), cache, self.clock)
                cancel = Event()
                with self.assertRaises(Cancelled):
                    service.fetch("Example", DAY, cancel=cancel)

    def test_archive_failure_preserves_available_record_and_warning(self):
        daily = write(self.path / "daily", daily_line("TMAX", 20))
        cache = FakeCache({station_url(DAILY): daily})
        original_fetch = cache.fetch

        def fetch(url, **kwargs):
            if url == station_url(DAILY):
                return original_fetch(url, **kwargs)
            raise WeatherError("Archive unavailable")

        cache.fetch = fetch
        report = WeatherService(FakeCatalog(self.site), cache, self.clock).fetch("Example", DAY)
        self.assertEqual(report.status, "partial")
        self.assertIsNotNone(report.daily)
        self.assertIsNone(report.hourly)
        self.assertTrue(any("Some archive requests failed" in note for note in report.notes))

    def test_duplicate_observations_merge_fields_and_preserve_first_reading(self):
        instant = datetime(2010, 2, 6, 12, tzinfo=timezone.utc)
        observations = [
            Observation(instant, {"temperature": Reading(1)}),
            Observation(instant, {"temperature": Reading(2), "wind_speed": Reading(3)}, "OVC"),
        ]
        cache = FakeCache({station_url(HOURLY, 2010): self.path / "hourly"})
        with patch("rainshine.service.read_hourly", return_value=(observations, 2, 2)):
            report = WeatherService(FakeCatalog(self.site), cache, self.clock).fetch("Example", DAY)
        self.assertEqual(len(report.hourly.observations), 1)
        merged = report.hourly.observations[0]
        self.assertEqual(merged.readings, {"temperature": Reading(1), "wind_speed": Reading(3)})
        self.assertEqual(merged.sky, "OVC")
        self.assertEqual(report.hourly.rejected_values, 2)
        self.assertEqual(report.hourly.raw_report_count, 2)
        self.assertTrue(any("2 suspect" in note for note in report.notes))


class PackagedCatalogTests(unittest.TestCase):
    def test_all_documented_settings_load_with_ordered_station_candidates(self):
        catalog = Catalog()
        self.assertEqual(len(catalog.names), 238)
        for name in catalog.names:
            site = catalog.location(name)
            self.assertTrue(site.hourly)
            self.assertTrue(site.daily)
            for stations in (site.hourly, site.daily):
                distances = [s.distance_km for s in stations]
                self.assertEqual(distances, sorted(distances))

    def test_historical_day_does_not_select_a_station_that_opened_later(self):
        catalog = Catalog()
        site = catalog.location("Kent, OH, USA")
        candidates = catalog.candidates(site.hourly, date(1989, 12, 4))
        self.assertTrue(candidates)
        self.assertTrue(all(s.first_year <= 1989 for s in candidates))

    def test_unknown_site_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Choose a location"):
            Catalog().location("not a site")
