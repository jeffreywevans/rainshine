import io
import json
import runpy
import sys
import unittest
import warnings
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from rainshine import __version__
from rainshine.__main__ import main
from rainshine.models import WeatherError


def gui_module(launch):
    module = ModuleType("rainshine.gui")
    module.launch = launch
    return module


class MainTests(unittest.TestCase):
    def test_version_and_site_listing(self):
        version_output = io.StringIO()
        with redirect_stdout(version_output):
            with self.assertRaises(SystemExit) as raised:
                main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(version_output.getvalue().strip(), f"Rainshine {__version__}")

        catalog = SimpleNamespace(names=("Alpha, OH, USA", "Beta, OH, USA"))
        sites_output = io.StringIO()
        with patch("rainshine.catalog.Catalog", return_value=catalog), redirect_stdout(sites_output):
            status = main(["--list-sites"])
        self.assertEqual(status, 0)
        self.assertEqual(sites_output.getvalue(), "Alpha, OH, USA\nBeta, OH, USA\n")

    def test_date_and_site_must_be_supplied_together(self):
        cases = (
            ["--date", "2010-02-06"],
            ["--site", "Dayton, OH, USA"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                error_output = io.StringIO()
                with redirect_stderr(error_output):
                    with self.assertRaises(SystemExit) as raised:
                        main(arguments)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--date and --site must be supplied together", error_output.getvalue())

    def test_command_line_report_returns_status_and_json(self):
        requested_day = date(2010, 2, 6)
        for report_status, expected_status in (("available", 0), ("error", 1)):
            with self.subTest(report_status=report_status):
                report = Mock(status=report_status)
                report.to_dict.return_value = {"status": report_status}
                service = Mock()

                def fetch(site, day, *, progress):
                    self.assertEqual(site, "Dayton, OH, USA")
                    self.assertEqual(day, requested_day)
                    progress("Reading records…")
                    return report

                service.fetch.side_effect = fetch
                standard_output = io.StringIO()
                error_output = io.StringIO()
                with (
                    patch("rainshine.service.WeatherService", return_value=service),
                    redirect_stdout(standard_output),
                    redirect_stderr(error_output),
                ):
                    status = main(["--date", requested_day.isoformat(), "--site", "Dayton, OH, USA"])
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(standard_output.getvalue()), {"status": report_status})
                self.assertEqual(error_output.getvalue(), "Reading records…\n")

    def test_command_line_errors_are_readable(self):
        for error in (ValueError("Invalid date"), WeatherError("Archive unavailable")):
            with self.subTest(error=error):
                service = Mock()
                service.fetch.side_effect = error
                error_output = io.StringIO()
                with (
                    patch("rainshine.service.WeatherService", return_value=service),
                    redirect_stderr(error_output),
                ):
                    status = main(["--date", "2010-02-06", "--site", "Dayton, OH, USA"])
                self.assertEqual(status, 1)
                self.assertEqual(error_output.getvalue(), f"Rainshine: {error}\n")

    def test_gui_launch_success_and_readable_startup_failures(self):
        launch = Mock()
        with patch.dict(sys.modules, {"rainshine.gui": gui_module(launch)}):
            self.assertEqual(main([]), 0)
        launch.assert_called_once_with()

        error_output = io.StringIO()
        with patch.dict(sys.modules, {"rainshine.gui": None}), redirect_stderr(error_output):
            self.assertEqual(main([]), 1)
        self.assertIn("Rainshine requires Tkinter", error_output.getvalue())

        tcl_error = type("TclError", (Exception,), {})("no display")
        launch = Mock(side_effect=tcl_error)
        error_output = io.StringIO()
        with patch.dict(sys.modules, {"rainshine.gui": gui_module(launch)}), redirect_stderr(error_output):
            self.assertEqual(main([]), 1)
        self.assertEqual(
            error_output.getvalue(),
            "Rainshine could not open a desktop window: no display\n",
        )

    def test_unexpected_gui_failure_is_raised(self):
        failure = RuntimeError("Unexpected GUI failure")
        launch = Mock(side_effect=failure)
        with patch.dict(sys.modules, {"rainshine.gui": gui_module(launch)}):
            with self.assertRaises(RuntimeError) as raised:
                main([])
        self.assertIs(raised.exception, failure)

    def test_module_entry_point_exits_with_main_status(self):
        catalog = SimpleNamespace(names=("Example",))
        output = io.StringIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            with (
                patch("rainshine.catalog.Catalog", return_value=catalog),
                patch.object(sys, "argv", ["rainshine", "--list-sites"]),
                redirect_stdout(output),
            ):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_module("rainshine.__main__", run_name="__main__")
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue(), "Example\n")
