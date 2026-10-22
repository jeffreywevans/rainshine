import os
import time
import unittest
from datetime import date, datetime, timezone

from rainshine.catalog import Catalog
from rainshine.models import DailyRecord, DayReport, HourlyRecord, Observation, Reading
from rainshine.service import WeatherService


@unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "GUI tests need a display or Xvfb")
class GuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from rainshine.gui import RainshineApp
        self.root = tk.Tk()
        self.app = RainshineApp(self.root, WeatherService(clock=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc)))
        self.root.update()

    def tearDown(self):
        self.app.close()

    def test_date_dropdowns_cover_exact_endpoints_and_clamp_february(self):
        self.assertEqual(self.app.year_box.cget("values")[0], "1989")
        self.assertEqual(self.app.year_box.cget("values")[-1], "2032")
        self.app.year.set("2032"); self.app.month.set("February"); self.app.day.set("31")
        self.app._date_changed()
        self.assertEqual(self.app.day.get(), "29")
        self.app.year.set("1989"); self.app._date_changed()
        self.assertEqual(self.app.day.get(), "28")

    def test_search_finds_a_venue_and_keeps_every_site_available(self):
        self.app.site.set("Nutter")
        self.app._filter_sites()
        values = self.app.site_box.cget("values")
        self.assertEqual(len(values), 1)
        self.assertIn("Nutter Center", values[0])
        self.app._site_selected()
        self.assertEqual(len(self.app.site_box.cget("values")), 238)

    def test_future_lookup_completes_in_background_without_fake_weather(self):
        self.app.year.set("2032"); self.app.month.set("December"); self.app.day.set("31")
        self.app.lookup()
        deadline = time.monotonic() + 3
        while self.app.report is None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.02)
        self.assertIsNotNone(self.app.report)
        self.assertEqual(self.app.report.status, "future")
        self.assertEqual(self.app.heading.cget("text"), "No recorded weather yet")
        self.assertTrue(all(x.cget("text") == "—" for x in self.app.metrics.values()))

    def test_small_window_keeps_temperature_plot_and_time_labels_inside_canvas(self):
        location = self.app.service.catalog.location("Dayton, OH, USA")
        report = DayReport(location, date(2010, 2, 6), "partial", hourly=HourlyRecord(
            location.hourly[0], [
                Observation(datetime(2010, 2, 6, 12, tzinfo=timezone.utc), {"temperature": Reading(-2)}),
                Observation(datetime(2010, 2, 6, 13, tzinfo=timezone.utc), {"temperature": Reading(1)}),
            ]))
        self.root.geometry("920x740")
        self.app.report = report
        self.app._render(report)
        self.root.update()
        chart = self.app.chart
        labels = [item for item in chart.find_all() if chart.type(item) == "text"
                  and chart.itemcget(item, "text") in {"00:00", "06:00", "12:00", "18:00"}]
        self.assertEqual(len(labels), 4)
        self.assertTrue(all(0 <= chart.bbox(item)[1] < chart.bbox(item)[3] <= chart.winfo_height()
                            for item in labels))
        self.app.overview_view.yview_moveto(1)
        self.root.update()
        self.assertEqual(self.app.overview_view.yview()[1], 1.0)

    def test_render_preserves_metrics_extras_and_cache_status(self):
        location = self.app.service.catalog.location("Dayton, OH, USA")
        day = date(2010, 2, 6)
        report = DayReport(
            location, day, "available",
            daily=DailyRecord(location.daily[0], day, {
                "TMAX": Reading(10), "TMIN": Reading(0), "PRCP": Reading(0),
                "SNWD": Reading(25), "WT18": Reading(1),
            }, from_cache=True),
            hourly=HourlyRecord(location.hourly[0], [
                Observation(datetime(2010, 2, 6, 12, tzinfo=timezone.utc), {
                    "temperature": Reading(5), "wind_gust": Reading(10),
                }),
            ], from_cache=True),
        )
        self.app.report = report
        self.app._render(report)
        self.assertEqual(self.app.metrics["TMAX"].cget("text"), "50.0°F")
        self.assertEqual(self.app.metrics["TMIN"].cget("text"), "32.0°F")
        self.assertIn("Largest recorded gust:", self.app.extra.cget("text"))
        self.assertIn("Recorded snow depth:", self.app.extra.cget("text"))
        self.assertIn("Station reported: Snow", self.app.extra.cget("text"))
        self.assertEqual(self.app.status.get(), "Showing saved NOAA records.")
        self.assertEqual(len(self.app.table.get_children()), 1)

        self.app.units.set("°C · mm")
        report.hourly.stale = True
        self.app._render(report)
        self.assertEqual(self.app.metrics["TMAX"].cget("text"), "10.0°C")
        self.assertIn("could not be refreshed", self.app.status.get())

        missing = DayReport(location, day, "unavailable", notes=["No observations."])
        self.app.report = missing
        self.app._render(missing)
        self.assertEqual(self.app.metrics["TMAX"].cget("text"), "—")
        self.assertEqual(self.app.extra.cget("text"), "")
        self.assertEqual(self.app.chart_note.cget("text"), "")
        self.assertEqual(self.app.table.get_children(), ())
        self.assertEqual(self.app.status.get(), "No observations.")
