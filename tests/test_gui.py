import json
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from rainshine.models import Cancelled, DailyRecord, DayReport, HourlyRecord, Observation, Reading, WeatherError
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
        self.app.year.set("2032")
        self.app.month.set("February")
        self.app.day.set("31")
        self.app._date_changed()
        self.assertEqual(self.app.day.get(), "29")
        self.app.year.set("1989")
        self.app._date_changed()
        self.assertEqual(self.app.day.get(), "28")

        self.app.report = DayReport(
            self.app.service.catalog.location("Dayton, OH, USA"),
            date(1989, 2, 1),
            "unavailable",
        )
        self.app.day.set("1")
        self.app._date_changed()
        self.assertIsNone(self.app.report)
        self.assertEqual(self.app.heading.cget("text"), "Selection changed")
        self.app._selection_changed()

    def test_search_finds_a_venue_and_keeps_every_site_available(self):
        original = self.app.site_box.cget("values")
        self.app._filter_sites(SimpleNamespace(keysym="Up"))
        self.assertEqual(self.app.site_box.cget("values"), original)
        self.app.site.set("Nutter")
        self.app._filter_sites(SimpleNamespace(keysym="r"))
        values = self.app.site_box.cget("values")
        self.assertEqual(len(values), 1)
        self.assertIn("Nutter Center", values[0])
        self.app._site_selected()
        self.assertEqual(len(self.app.site_box.cget("values")), len(self.app.service.catalog.names))

    def test_overview_scroll_accepts_wheel_events_only_over_that_tab(self):
        with patch.object(self.app.overview_view, "yview_scroll") as scroll:
            self.app._scroll_overview(SimpleNamespace(widget=self.app.root, delta=120))
            scroll.assert_not_called()

            inside = self.app.chart
            self.app._scroll_overview(SimpleNamespace(widget=inside, num=4))
            self.app._scroll_overview(SimpleNamespace(widget=inside, num=5))
            self.app._scroll_overview(SimpleNamespace(widget=inside, delta=120))
            self.app._scroll_overview(SimpleNamespace(widget=inside, delta=-120))
            self.assertEqual(
                scroll.call_args_list,
                [call(-1, "units"), call(1, "units"), call(-1, "units"), call(1, "units")],
            )

    def test_lookup_completes_one_matching_site_and_rejects_unknown_sites(self):
        self.app.site.set("Nutter")
        with patch("rainshine.gui.threading.Thread") as thread:
            self.app.lookup()
        self.assertIn("Nutter Center", self.app.site.get())
        thread.return_value.start.assert_called_once_with()
        self.assertTrue(self.app.lookup_button.instate(["disabled"]))

        request = self.app._request
        self.app.cancel()
        self.assertTrue(self.app._cancel.is_set())
        self.assertEqual(self.app._request, request + 1)
        self.assertEqual(self.app.status.get(), "Lookup cancelled.")

        self.app.site.set("No such archive site")
        with (
            patch("rainshine.gui.threading.Thread") as thread,
            patch("rainshine.gui.messagebox.showinfo") as showinfo,
        ):
            self.app.lookup()
        thread.assert_not_called()
        showinfo.assert_called_once()
        self.assertEqual(showinfo.call_args.args[0], "Choose a date and site")

    def test_fetch_worker_queues_progress_results_cancellation_and_errors(self):
        location = self.app.service.catalog.location("Dayton, OH, USA")
        report = DayReport(location, date(2010, 2, 6), "unavailable", notes=["No observations."])
        cancel = self.app._cancel

        def fetch(site, day, *, cancel, progress):
            self.assertEqual(site, location.name)
            self.assertEqual(day, report.day)
            self.assertIs(cancel, self.app._cancel)
            progress("Reading NOAA records…")
            return report

        with patch.object(self.app.service, "fetch", side_effect=fetch):
            self.app._fetch_weather(41, location.name, report.day, cancel)
        self.assertEqual(self.app._queue.get_nowait(), (41, "progress", "Reading NOAA records…"))
        self.assertEqual(self.app._queue.get_nowait(), (41, "result", report))

        with patch.object(self.app.service, "fetch", side_effect=Cancelled("Stopped")):
            self.app._fetch_weather(42, location.name, report.day, cancel)
        self.assertEqual(self.app._queue.get_nowait(), (42, "cancelled", None))

        for request, error, expected in (
            (43, WeatherError("NOAA is unavailable."), "NOAA is unavailable."),
            (44, RuntimeError("internal detail"), "The archive could not be read. Please try another date or site."),
        ):
            with self.subTest(error=error):
                with (
                    patch.object(self.app.service, "fetch", side_effect=error),
                    patch("rainshine.gui.logging.getLogger") as get_logger,
                ):
                    self.app._fetch_weather(request, location.name, report.day, cancel)
                get_logger.return_value.exception.assert_called_once_with("Weather lookup failed")
                self.assertEqual(self.app._queue.get_nowait(), (request, "error", expected))

    def test_poll_handles_every_message_and_stops_after_close(self):
        location = self.app.service.catalog.location("Dayton, OH, USA")
        report = DayReport(location, date(2010, 2, 6), "unavailable", notes=["No observations."])
        request = self.app._request
        self.app._queue.put((request + 1, "progress", "Stale progress"))
        self.app._queue.put((request, "progress", "Current progress"))
        self.app._queue.put((request, "result", report))
        self.app._queue.put((request, "error", "Readable error"))
        self.app._queue.put((request, "unknown", None))
        with patch.object(self.root, "after") as after:
            self.app._poll()
        after.assert_called_once_with(80, self.app._poll)
        self.assertEqual(self.app.status.get(), "Readable error")
        self.assertEqual(self.app.heading.cget("text"), "The archive couldn't be reached")

        request = self.app._request
        self.app._queue.put((request, "cancelled", None))
        with patch.object(self.root, "after"):
            self.app._poll()
        self.assertEqual(self.app.status.get(), "Lookup cancelled.")

        self.app._closing = True
        with patch.object(self.root, "after") as after:
            self.app._poll()
        after.assert_not_called()
        self.app._closing = False

    def test_future_lookup_completes_in_background_without_fake_weather(self):
        self.app.year.set("2032")
        self.app.month.set("December")
        self.app.day.set("31")
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
                Observation(datetime(2010, 2, 6, 18, tzinfo=timezone.utc), {"temperature": Reading(3)}),
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
        self.assertEqual(len([item for item in chart.find_all() if chart.type(item) == "line"]), 4)
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
                "SNWD": Reading(25), "WT01": Reading(0), "WT18": Reading(1),
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

        report.daily.stale = True
        report.hourly.stale = False
        self.app._render(report)
        self.assertIn("could not be refreshed", self.app.status.get())

        missing = DayReport(location, day, "unavailable", notes=["No observations."])
        self.app.report = missing
        self.app._render(missing)
        self.assertEqual(self.app.metrics["TMAX"].cget("text"), "—")
        self.assertEqual(self.app.extra.cget("text"), "")
        self.assertEqual(self.app.chart_note.cget("text"), "")
        self.assertEqual(self.app.table.get_children(), ())
        self.assertEqual(self.app.status.get(), "No observations.")

    def test_chart_handles_missing_temperature_and_large_series(self):
        location = self.app.service.catalog.location("Dayton, OH, USA")
        station = location.hourly[0]
        day = date(2010, 2, 6)
        no_temperature = DayReport(
            location,
            day,
            "partial",
            hourly=HourlyRecord(station, [
                Observation(datetime(2010, 2, 6, 12, tzinfo=timezone.utc), {"wind_speed": Reading(3)}),
            ]),
        )
        self.app.report = no_temperature
        self.app._draw_chart()
        text = [self.app.chart.itemcget(item, "text") for item in self.app.chart.find_all()]
        self.assertIn("No usable temperature values were recorded.", text)

        start = datetime(2010, 2, 6, 5, tzinfo=timezone.utc)
        observations = [
            Observation(start + index * timedelta(minutes=5), {
                "temperature": Reading(index / 10),
            })
            for index in range(150)
        ]
        large = DayReport(location, day, "partial", hourly=HourlyRecord(station, observations))
        self.app.report = large
        self.app._draw_chart()
        self.assertFalse(any(self.app.chart.type(item) == "oval" for item in self.app.chart.find_all()))

        heading = self.app.heading.cget("text")
        self.app._render(None)
        self.assertEqual(self.app.heading.cget("text"), heading)
        self.app._render_empty("Empty", "")
        self.assertEqual(self.app.subtitle.cget("text"), "")

    def test_copy_and_json_export_handle_empty_cancel_success_and_failure(self):
        with (
            patch.object(self.root, "clipboard_clear") as clear,
            patch.object(self.root, "clipboard_append") as append,
        ):
            self.app.report = None
            self.app.copy_summary()
            clear.assert_not_called()
            append.assert_not_called()

            location = self.app.service.catalog.location("Dayton, OH, USA")
            report = DayReport(location, date(2010, 2, 6), "unavailable", notes=["No observations."])
            self.app.report = report
            self.app.copy_summary()
            clear.assert_called_once_with()
            self.assertIn("Dayton, OH, USA", append.call_args.args[0])
        self.assertEqual(self.app.status.get(), "Weather summary copied.")

        self.app.report = None
        with patch("rainshine.gui.filedialog.asksaveasfilename") as save_dialog:
            self.app.save_json()
        save_dialog.assert_not_called()

        self.app.report = report
        with patch("rainshine.gui.filedialog.asksaveasfilename", return_value=""):
            self.app.save_json()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "weather.json"
            with patch("rainshine.gui.filedialog.asksaveasfilename", return_value=str(target)):
                self.app.save_json()
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["location"], "Dayton, OH, USA")
            self.assertEqual(self.app.status.get(), "Recorded weather and source details saved.")

        with (
            patch("rainshine.gui.filedialog.asksaveasfilename", return_value="weather.json"),
            patch("builtins.open", side_effect=OSError("Disk full")),
            patch("rainshine.gui.messagebox.showerror") as showerror,
        ):
            self.app.save_json()
        showerror.assert_called_once_with("Could not save", "Disk full", parent=self.root)

    def test_launch_creates_the_application_and_enters_the_event_loop(self):
        from rainshine import gui

        root = Mock()
        with patch.object(gui.tk, "Tk", return_value=root), patch.object(gui, "RainshineApp") as app_class:
            gui.launch()
        app_class.assert_called_once_with(root)
        root.mainloop.assert_called_once_with()
