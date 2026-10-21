"""Launch the desktop UI, or retrieve a report without a desktop."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rainshine", description="Recorded weather for a date and location.")
    parser.add_argument("--version", action="version", version=f"Rainshine {__version__}")
    parser.add_argument("--date", type=date.fromisoformat, help="Fetch YYYY-MM-DD without opening the GUI.")
    parser.add_argument("--site", help="An exact catalog location, e.g. 'Dayton, OH, USA'.")
    parser.add_argument("--list-sites", action="store_true", help="List the documented locations.")
    args = parser.parse_args(argv)
    if args.list_sites:
        from .catalog import Catalog
        print("\n".join(Catalog().names))
        return 0
    if args.date is not None or args.site is not None:
        if not args.date or not args.site:
            parser.error("--date and --site must be supplied together")
        from .models import WeatherError
        from .service import WeatherService
        try:
            report = WeatherService().fetch(args.site, args.date, progress=lambda s: print(s, file=sys.stderr))
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, allow_nan=False))
            return 1 if report.status == "error" else 0
        except (ValueError, WeatherError) as exc:
            print(f"Rainshine: {exc}", file=sys.stderr)
            return 1
    try:
        from .gui import launch
        launch()
    except ImportError as exc:
        print("Rainshine requires Tkinter. On Debian/Ubuntu, install python3-tk; on Windows, enable Tcl/Tk in Python's installer.", file=sys.stderr)
        return 1
    except Exception as exc:
        if exc.__class__.__name__ == "TclError":
            print(f"Rainshine could not open a desktop window: {exc}", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
