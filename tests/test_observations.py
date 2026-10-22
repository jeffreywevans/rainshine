import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Event

from helpers import DAILY, DAY, HOURLY, daily_line, hourly_text, write
from rainshine.models import Cancelled, DayReport, Location, Reading, MAX_DATE, MIN_DATE, days_in_month, utc_window, validate_date
from rainshine.noaa import accepted_hourly_quality, read_daily, read_hourly, reported_sky
from rainshine.presentation import amount, temperature


class DateTests(unittest.TestCase):
    def test_exact_supported_endpoints(self):
        validate_date(date(1989, 1, 1))
        validate_date(date(2032, 12, 31))
        for day in [date(1988, 12, 31), date(2033, 1, 1)]:
            with self.assertRaises(ValueError):
                validate_date(day)

    def test_leap_year_days(self):
        self.assertEqual(days_in_month(2000, 2)[-1], 29)
        self.assertEqual(days_in_month(1989, 2)[-1], 28)
        self.assertEqual(days_in_month(2032, 2)[-1], 29)

    def test_dst_days_are_23_and_25_hours(self):
        for day, hours in [(date(2010, 3, 14), 23), (date(2010, 11, 7), 25)]:
            start, end = utc_window(day, "America/New_York")
            self.assertEqual(end - start, timedelta(hours=hours))

    def test_historical_indiana_timezone_rules(self):
        start, end = utc_window(date(1999, 7, 1), "America/Indiana/Indianapolis")
        self.assertEqual(start.hour, 5)
        start, end = utc_window(date(2010, 7, 1), "America/Indiana/Indianapolis")
        self.assertEqual(start.hour, 4)

    def test_australian_local_day_crosses_utc_year(self):
        start, end = utc_window(MIN_DATE, "Australia/Sydney")
        self.assertEqual(start.year, 1988)
        self.assertEqual(end.year, 1989)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "weather.txt"

    def tearDown(self):
        self.temp.cleanup()

    def test_daily_scaling_and_zero_vs_trace(self):
        write(self.path, daily_line("TMAX", -15) + daily_line("TMIN", -73) + daily_line("PRCP", 0, measurement="T") + daily_line("SNOW", 250))
        record = read_daily(self.path, DAILY, DAY)
        self.assertAlmostEqual(record.readings["TMAX"].value, -1.5)
        self.assertAlmostEqual(record.readings["TMIN"].value, -7.3)
        self.assertEqual(record.readings["SNOW"].value, 250)
        self.assertEqual(amount(record.readings["PRCP"]), "Trace")
        self.assertEqual(amount(Reading(0)), "0.00 in")

    def test_missing_and_failed_quality_values_are_not_weather(self):
        write(self.path, daily_line("TMAX", -9999) + daily_line("TMIN", 150, quality="X") + daily_line("PRCP", 0, measurement="P"))
        self.assertIsNone(read_daily(self.path, DAILY, DAY))

    def test_no_precipitation_report_is_not_a_recorded_dry_day(self):
        write(self.path, daily_line("PRCP", 0, measurement="I"))
        self.assertIsNone(read_daily(self.path, DAILY, DAY))

    def test_daily_reporting_flags_are_preserved(self):
        write(self.path, daily_line("PRCP", 0, measurement="H"))
        record = read_daily(self.path, DAILY, DAY)
        self.assertEqual(amount(record.readings["PRCP"], precipitation=True), "Reported")
        self.assertEqual(record.readings["PRCP"].source_flag, "7")

    def test_daily_precip_flag_e_is_not_an_estimation_code(self):
        write(self.path, daily_line("PRCP", 124, measurement="E"))
        self.assertAlmostEqual(read_daily(self.path, DAILY, DAY).readings["PRCP"].value, 12.4)

    def test_quality_five_depends_on_original_source(self):
        self.assertTrue(accepted_hourly_quality("5", "313"))
        self.assertFalse(accepted_hourly_quality("5", "223"))
        self.assertFalse(accepted_hourly_quality("s", "343"))
        self.assertFalse(accepted_hourly_quality("2", "223"))

    def test_hourly_interval_has_inclusive_start_exclusive_end(self):
        start, end = utc_window(DAY, "America/New_York")
        rows = [{"DATE": stamp.isoformat(), "temperature": "2.3", "temperature_Source_Code": "223", "temperature_Quality_Code": "1"}
                for stamp in [start - timedelta(minutes=1), start, end - timedelta(minutes=1), end]]
        write(self.path, hourly_text(rows))
        observations, bad, count = read_hourly(self.path, HOURLY, start, end)
        self.assertEqual([o.time for o in observations], [start, end - timedelta(minutes=1)])
        self.assertEqual(count, 2)

    def test_estimated_and_suspect_hourly_values_are_excluded(self):
        rows = [
            {"temperature": "99", "temperature_Quality_Code": "2", "temperature_Source_Code": "223"},
            {"DATE": "2010-02-06T13:00:00", "temperature": "12", "temperature_Measurement_Code": "E"},
            {"DATE": "2010-02-06T14:00:00", "temperature": "NaN"},
            {"DATE": "2010-02-06T15:00:00", "temperature": "1.7", "temperature_Quality_Code": "1", "temperature_Source_Code": "223"},
        ]
        write(self.path, hourly_text(rows))
        start, end = utc_window(DAY, "America/New_York")
        observations, bad, count = read_hourly(self.path, HOURLY, start, end)
        self.assertEqual(len(observations), 1)
        self.assertAlmostEqual(observations[0].readings["temperature"].value, 1.7)
        self.assertEqual(bad, 2)

    def test_explicit_cloud_label_only(self):
        self.assertEqual(reported_sky({"sky_cover_layer_1": "OVC:08"}), "Overcast")
        self.assertIsNone(reported_sky({"sky_condition": "08;;"}))
        self.assertIsNone(reported_sky({"sky_cover_layer_1": "OVC:08", "sky_cover_layer_1_Quality_Code": "2", "sky_cover_layer_1_Source_Code": "223"}))

    def test_hourly_ranges_preserve_boundaries_and_rejection_counts(self):
        cases = [
            ("relative_humidity", "0", True, 0),
            ("relative_humidity", "100", True, 0),
            ("relative_humidity", "101", False, 1),
            ("wind_direction", "360", True, 0),
            ("wind_direction", "361", False, 1),
            ("wind_speed", "0", True, 0),
            ("wind_speed", "-1", False, 1),
            ("wind_gust", "-1", False, 1),
            ("visibility", "-1", False, 1),
            ("temperature", "-20", True, 0),
            ("temperature", "invalid", False, 1),
            ("temperature", "-9999", False, 0),
            ("temperature", "inf", False, 0),
            ("temperature", "", False, 0),
        ]
        start, end = utc_window(DAY, "America/New_York")
        for name, value, accepted, rejected in cases:
            with self.subTest(name=name, value=value):
                write(self.path, hourly_text([{name: value}]))
                observations, bad, count = read_hourly(self.path, HOURLY, start, end)
                self.assertEqual(bool(observations), accepted)
                self.assertEqual(bad, rejected)
                self.assertEqual(count, 1)
                if accepted:
                    self.assertEqual(observations[0].readings[name].value, float(value))

    def test_hourly_timestamps_normalize_to_utc(self):
        rows = [{"DATE": stamp, "temperature": "1"} for stamp in (
            "2010-02-06T12:00:00", "2010-02-06T12:00:00Z", "2010-02-06T07:00:00-05:00",
        )]
        write(self.path, hourly_text(rows))
        start, end = utc_window(DAY, "America/New_York")
        observations, bad, count = read_hourly(self.path, HOURLY, start, end)
        self.assertEqual([o.time for o in observations], [datetime(2010, 2, 6, 12, tzinfo=timezone.utc)] * 3)
        self.assertEqual((bad, count), (0, 3))

    def test_parser_cancellation(self):
        write(self.path, daily_line("TMAX", 100))
        event = Event(); event.set()
        with self.assertRaises(Cancelled):
            read_daily(self.path, DAILY, DAY, event)

    def test_zero_celsius_conversion(self):
        self.assertEqual(temperature(0), "32.0°F")
        self.assertEqual(temperature(None), "—")

    def test_export_does_not_invent_observations(self):
        location = Location("Example", 0, 0, "UTC", (), ())
        report = DayReport(location, date(2032, 12, 31), "future")
        output = json.loads(json.dumps(report.to_dict(), allow_nan=False))
        self.assertIsNone(output["daily"])
        self.assertIsNone(output["hourly"])
        self.assertEqual(output["status"], "future")


if __name__ == "__main__":
    unittest.main()
