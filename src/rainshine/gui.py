"""A small Tk desktop interface; all network work stays off the Tk thread."""

from __future__ import annotations

import calendar
import json
import logging
import queue
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk
from zoneinfo import ZoneInfo

from . import __version__
from .models import Cancelled, DayReport, MAX_DATE, MIN_DATE, WeatherError, days_in_month, utc_window
from .noaa import DAILY_WEATHER
from .presentation import amount, day_title, observation_rows, speed, summary_text, temperature
from .service import WeatherService

BG = "#f2f5f7"
PAPER = "#ffffff"
INK = "#163047"
MUTED = "#53697a"
TEAL = "#087f83"
BORDER = "#dce5eb"
BLUE = "#397db8"


class RainshineApp:
    def __init__(self, root: tk.Tk, service: WeatherService | None = None):
        self.root, self.service = root, service or WeatherService()
        self.report: DayReport | None = None
        self._queue: queue.Queue = queue.Queue()
        self._cancel = threading.Event()
        self._request = 0
        self._closing = False
        self.root.title(f"Rainshine {__version__} · Recorded weather")
        screen_height = root.winfo_screenheight()
        self.root.geometry(f"1100x{max(740, min(920, screen_height - 80))}")
        self.root.minsize(920, 740)
        self.root.configure(background=BG)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Paper.TFrame", background=PAPER)
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Paper.TLabel", background=PAPER)
        style.configure("Heading.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=(15, 9))
        style.configure("Accent.TButton", background=TEAL, foreground="white", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#06676b"), ("disabled", "#bbced0")])
        style.configure("TCombobox", padding=7, font=("Segoe UI", 11), fieldbackground=PAPER)
        style.configure("Treeview", rowheight=28, font=("Segoe UI", 10), background=PAPER, fieldbackground=PAPER, foreground=INK)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"), background="#e5eef3", padding=(8, 7))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=(17, 8))
        style.configure("Horizontal.TProgressbar", troughcolor=BG, background=TEAL, borderwidth=0)
        root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 11))
        root.option_add("*TCombobox*Listbox.selectBackground", TEAL)
        self.month = tk.StringVar(value="January")
        self.day = tk.StringVar(value="1")
        self.year = tk.StringVar(value="1989")
        self.site = tk.StringVar(value="Dayton, OH, USA" if "Dayton, OH, USA" in self.service.catalog.names else self.service.catalog.names[0])
        self.units = tk.StringVar(value="°F · inches")
        self.status = tk.StringVar(value="Ready. Choose a date and a site, then show the weather.")
        self._build()
        self._set_busy(False)
        root.bind("<Configure>", self._resize)
        root.bind("<MouseWheel>", self._scroll_overview, add="+")
        root.bind("<Button-4>", self._scroll_overview, add="+")
        root.bind("<Button-5>", self._scroll_overview, add="+")
        self._render_empty("A day in the weather archive", "Choose any date from January 1, 1989 through December 31, 2032.")
        root.after(80, self._poll)

    @property
    def imperial(self) -> bool:
        return self.units.get().startswith("°F")

    def _build(self):
        root = self.root
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)
        header = tk.Frame(root, background=INK, padx=28, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        logo = tk.Canvas(header, width=53, height=45, bg=INK, highlightthickness=0)
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 14))
        logo.create_oval(7, 1, 34, 28, fill="#f5c45e", outline="")
        logo.create_oval(17, 16, 42, 39, fill="#e2eff5", outline="")
        logo.create_oval(31, 24, 51, 40, fill="#e2eff5", outline="")
        logo.create_rectangle(22, 30, 44, 40, fill="#e2eff5", outline="")
        tk.Label(header, text="rainshine", background=INK, foreground=PAPER, font=("Segoe UI", 26, "bold")).grid(row=0, column=1, sticky="w")
        tk.Label(header, text="A date. A place. The weather as recorded.", background=INK, foreground="#c7dae6", font=("Segoe UI", 11)).grid(row=1, column=1, sticky="w")
        unit = ttk.Combobox(header, textvariable=self.units, state="readonly", values=("°F · inches", "°C · millimeters"), width=18)
        unit.grid(row=0, column=2, rowspan=2, sticky="e")
        unit.bind("<<ComboboxSelected>>", lambda event: self._render(self.report) if self.report else None)

        controls = ttk.Frame(root, padding=(28, 12, 28, 10))
        controls.grid(row=1, column=0, sticky="ew")
        controls.grid_columnconfigure(5, weight=1)
        self.month_box = ttk.Combobox(controls, textvariable=self.month, values=tuple(calendar.month_name)[1:], state="readonly", width=12)
        self.day_box = ttk.Combobox(controls, textvariable=self.day, values=days_in_month(1989, 1), state="readonly", width=4)
        self.year_box = ttk.Combobox(controls, textvariable=self.year, values=tuple(range(MIN_DATE.year, MAX_DATE.year + 1)), state="readonly", width=6)
        for col, widget in enumerate((self.month_box, self.day_box, self.year_box)):
            widget.grid(row=1, column=col, sticky="w", padx=(0, 9))
            widget.bind("<<ComboboxSelected>>", self._date_changed)
        self.lookup_button = ttk.Button(controls, text="Show weather", style="Accent.TButton", command=self.lookup)
        self.lookup_button.grid(row=1, column=6, sticky="e", padx=(16, 0))
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=1, column=7, padx=(8, 0))
        ttk.Label(controls, text=f"SITE  ·  {len(self.service.catalog.names)} locations", style="Muted.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", pady=(10, 7))
        self.site_box = ttk.Combobox(controls, textvariable=self.site, values=self.service.catalog.names, height=16)
        self.site_box.grid(row=3, column=0, columnspan=8, sticky="ew")
        self.site_box.bind("<KeyRelease>", self._filter_sites)
        self.site_box.bind("<<ComboboxSelected>>", self._site_selected)
        self.site_box.bind("<Return>", lambda event: self.lookup())
        ttk.Label(controls, text="Type a city or venue to narrow the list. The actual observing station is shown with the results.", style="Muted.TLabel").grid(row=4, column=0, columnspan=8, sticky="w", pady=(5, 0))
        self.progress = ttk.Progressbar(root, mode="indeterminate", maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 12))

        body = ttk.Frame(root, padding=(28, 0, 28, 0))
        body.grid(row=3, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)
        self.result_heading = ttk.Frame(body)
        self.result_heading.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.result_heading.grid_columnconfigure(1, weight=1)
        self.heading = ttk.Label(self.result_heading, text="", style="Heading.TLabel")
        self.heading.grid(row=0, column=0, sticky="w")
        self.subtitle = ttk.Label(self.result_heading, text="", style="Muted.TLabel", wraplength=550)
        self.subtitle.grid(row=0, column=1, sticky="w", padx=(12, 0))
        cards = ttk.Frame(body)
        cards.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.metrics = {}
        for column, (key, title) in enumerate((("TMAX", "Recorded high"), ("TMIN", "Recorded low"), ("PRCP", "Reported precipitation"), ("SNOW", "Recorded snowfall"))):
            cards.grid_columnconfigure(column, weight=1, uniform="metric")
            card = tk.Frame(cards, background=PAPER, highlightbackground=BORDER, highlightthickness=1, padx=17, pady=10)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            tk.Label(card, text=title, background=PAPER, foreground=MUTED, font=("Segoe UI", 10)).pack(anchor="w")
            label = tk.Label(card, text="—", background=PAPER, foreground=TEAL if key in {"TMAX", "TMIN"} else INK, font=("Segoe UI", 25, "bold"))
            label.pack(anchor="w", pady=(4, 0))
            self.metrics[key] = label
        self.daily_source = ttk.Label(body, text="", style="Muted.TLabel", wraplength=950)
        self.daily_source.grid(row=2, column=0, sticky="w", pady=(0, 12))
        self.tabs = ttk.Notebook(body)
        self.tabs.grid(row=3, column=0, sticky="nsew")
        overview, readings, sources = (ttk.Frame(self.tabs, padding=12) for _ in range(3))
        self.tabs.add(overview, text="Through the day")
        self.tabs.add(readings, text="Recorded readings")
        self.tabs.add(sources, text="Sources & notes")
        self.overview_tab = overview
        overview.grid_rowconfigure(0, weight=1)
        overview.grid_columnconfigure(0, weight=1)
        self.overview_view = tk.Canvas(overview, background=BG, highlightthickness=0, height=280)
        self.overview_view.grid(row=0, column=0, sticky="nsew")
        overview_scroll = ttk.Scrollbar(overview, command=self.overview_view.yview)
        overview_scroll.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.overview_view.configure(yscrollcommand=overview_scroll.set)
        overview = ttk.Frame(self.overview_view)
        self.overview_content = overview
        self.overview_window = self.overview_view.create_window(0, 0, window=overview, anchor="nw")
        self.overview_view.bind("<Configure>", self._resize_overview)
        overview.bind("<Configure>", self._resize_overview)
        overview.grid_columnconfigure(0, weight=1)
        overview.grid_rowconfigure(1, weight=1)
        self.hourly_source = ttk.Label(overview, text="", style="Muted.TLabel", wraplength=920)
        self.hourly_source.grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.chart = tk.Canvas(overview, background=PAPER, height=190, highlightbackground=BORDER, highlightthickness=1)
        self.chart.grid(row=1, column=0, sticky="nsew")
        self.chart.bind("<Configure>", lambda event: self._draw_chart())
        self.chart_note = ttk.Label(overview, text="", style="Muted.TLabel", wraplength=920)
        self.chart_note.grid(row=2, column=0, sticky="w", pady=(9, 0))
        self.extra = ttk.Label(overview, text="", wraplength=920)
        self.extra.grid(row=3, column=0, sticky="w", pady=(9, 0))

        readings.grid_rowconfigure(0, weight=1)
        readings.grid_columnconfigure(0, weight=1)
        columns = ("Time", "Temperature", "Sky report", "Wind", "Gust", "Humidity", "Sea pressure")
        self.table = ttk.Treeview(readings, columns=columns, show="headings", height=8)
        self.table.grid(row=0, column=0, sticky="nsew")
        for col in columns:
            self.table.heading(col, text=col)
            self.table.column(col, anchor="w", width=120 if col == "Sky report" else 105, minwidth=75)
        scroll = ttk.Scrollbar(readings, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        ttk.Label(readings, text="Actual report times in the selected site's timezone. — means not recorded or excluded by quality checks.", style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        sources.grid_rowconfigure(0, weight=1)
        sources.grid_columnconfigure(0, weight=1)
        self.source_text = tk.Text(sources, wrap="word", background=PAPER, foreground=INK, font=("Segoe UI", 10), padx=14, pady=12, borderwidth=0, height=9)
        self.source_text.grid(row=0, column=0, sticky="nsew")
        source_scroll = ttk.Scrollbar(sources, command=self.source_text.yview)
        source_scroll.grid(row=0, column=1, sticky="ns")
        self.source_text.configure(yscrollcommand=source_scroll.set, state="disabled")

        footer = ttk.Frame(root, padding=(28, 8, 28, 12))
        footer.grid(row=4, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        self.footer_label = ttk.Label(footer, textvariable=self.status, style="Muted.TLabel", wraplength=680)
        self.footer_label.grid(row=0, column=0, sticky="w", padx=(0, 15))
        self.copy_button = ttk.Button(footer, text="Copy summary", command=self.copy_summary, state="disabled")
        self.copy_button.grid(row=0, column=1, padx=(0, 8))
        self.save_button = ttk.Button(footer, text="Save JSON", command=self.save_json, state="disabled")
        self.save_button.grid(row=0, column=2)

    def _date_changed(self, event=None):
        month = list(calendar.month_name).index(self.month.get())
        days = days_in_month(int(self.year.get()), month)
        self.day_box.configure(values=days)
        if int(self.day.get()) > days[-1]:
            self.day.set(str(days[-1]))
        self._selection_changed()

    def _site_selected(self, event=None):
        self.site_box.configure(values=self.service.catalog.names)
        self._selection_changed()

    def _resize(self, event):
        if event.widget is self.root:
            width = event.width
            self.footer_label.configure(wraplength=max(300, width - 390))
            self.daily_source.configure(wraplength=max(300, width - 65))
            self._fit_result_heading(width)
            for label in (self.hourly_source, self.chart_note, self.extra):
                label.configure(wraplength=max(300, width - 100))
            self.root.after_idle(self._resize_overview)

    def _fit_result_heading(self, width=None):
        width = width if width is not None else max(self.root.winfo_width(), 920)
        available = max(180, width - 56 - self.heading.winfo_reqwidth() - 12)
        self.subtitle.configure(wraplength=available)

    def _resize_overview(self, event=None):
        view, content = self.overview_view, self.overview_content
        view.itemconfigure(self.overview_window, width=max(1, view.winfo_width()),
                           height=max(content.winfo_reqheight(), view.winfo_height()))
        view.configure(scrollregion=view.bbox("all"))

    def _scroll_overview(self, event):
        if not str(event.widget).startswith(str(self.overview_tab)):
            return
        if getattr(event, "num", None) in (4, 5):
            steps = -1 if event.num == 4 else 1
        else:
            steps = -1 if event.delta > 0 else 1
        self.overview_view.yview_scroll(steps, "units")

    def _selection_changed(self, event=None):
        if self.report:
            self.report = None
            self._render_empty("Selection changed", "Show the weather to load this date and location.")
            self.status.set("Ready for the new selection.")

    def _filter_sites(self, event=None):
        if event and event.keysym in {"Return", "Up", "Down", "Left", "Right", "Tab", "Escape"}:
            return
        query = self.site.get().casefold().strip()
        self.site_box.configure(values=tuple(name for name in self.service.catalog.names if query in name.casefold()))
        self._selection_changed()

    def lookup(self):
        try:
            day = date(int(self.year.get()), list(calendar.month_name).index(self.month.get()), int(self.day.get()))
            site = self.site.get().strip()
            matches = [name for name in self.service.catalog.names if site.casefold() in name.casefold()]
            if site not in self.service.catalog.names and len(matches) == 1:
                site = matches[0]
                self.site.set(site)
            self.service.catalog.location(site)
        except (ValueError, TypeError) as exc:
            messagebox.showinfo("Choose a date and site", str(exc), parent=self.root)
            return
        self._request += 1
        request = self._request
        self._cancel = threading.Event()
        self.report = None
        self._render_empty("Looking through the archive…", f"{site} · {day.isoformat()}")
        self._set_busy(True)
        self.status.set("Contacting NOAA… The first lookup downloads station records; later lookups reuse them.")
        cancel = self._cancel

        def worker():
            try:
                report = self.service.fetch(site, day, cancel=cancel,
                                            progress=lambda message: self._queue.put((request, "progress", message)))
                self._queue.put((request, "result", report))
            except Cancelled:
                self._queue.put((request, "cancelled", None))
            except Exception as exc:
                logging.getLogger(__name__).exception("Weather lookup failed")
                message = str(exc) if isinstance(exc, WeatherError) else "The archive could not be read. Please try another date or site."
                self._queue.put((request, "error", message))
        threading.Thread(target=worker, daemon=True, name="rainshine-weather").start()

    def _set_busy(self, busy: bool):
        for widget in (self.month_box, self.day_box, self.year_box):
            widget.configure(state="disabled" if busy else "readonly")
        self.site_box.configure(state="disabled" if busy else "normal")
        self.lookup_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.grid()
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)
            self.progress.grid_remove()

    def cancel(self):
        self._cancel.set()
        self._request += 1
        self._set_busy(False)
        self._render_empty("Lookup cancelled", "Choose another date or site whenever you're ready.")
        self.status.set("Lookup cancelled.")

    def _poll(self):
        if self._closing:
            return
        try:
            while True:
                request, kind, value = self._queue.get_nowait()
                if request != self._request:
                    continue
                if kind == "progress":
                    self.status.set(value)
                elif kind == "result":
                    self._set_busy(False)
                    self.report = value
                    self._render(value)
                elif kind == "error":
                    self._set_busy(False)
                    self._render_empty("The archive couldn't be reached", value)
                    self.status.set(value)
                elif kind == "cancelled":
                    self.cancel()
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _set_sources(self, text: str):
        self.source_text.configure(state="normal")
        self.source_text.delete("1.0", "end")
        self.source_text.insert("1.0", text)
        self.source_text.configure(state="disabled")

    def _render_empty(self, title: str, subtitle: str):
        self.heading.configure(text=title)
        self.subtitle.configure(text=f"| {subtitle}" if subtitle else "")
        self._fit_result_heading()
        for label in self.metrics.values():
            label.configure(text="—")
        self.daily_source.configure(text="Daily station records will appear here. — means not recorded.")
        self.hourly_source.configure(text="Recorded observations through the selected local day")
        self.chart_note.configure(text="")
        self.extra.configure(text="")
        self.table.delete(*self.table.get_children())
        self._set_sources("Choose a date and a location to view station identities, sources, and record notes.")
        self.copy_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self._draw_chart()

    def _render(self, report: DayReport):
        if report is None:
            return
        self.heading.configure(text=day_title(report))
        location_name = report.location.name.replace(" – ", ", ")
        self.subtitle.configure(text=f"| {location_name}")
        d = report.daily
        values = d.readings if d else {}
        for key in ("TMAX", "TMIN"):
            r = values.get(key)
            self.metrics[key].configure(text=temperature(r.value if r else None, self.imperial))
        self.metrics["PRCP"].configure(text=amount(values.get("PRCP"), self.imperial, precipitation=True))
        self.metrics["SNOW"].configure(text=amount(values.get("SNOW"), self.imperial))
        if d:
            self.daily_source.configure(text=f"Daily record: {d.station.display_name} · {d.station.distance_km:.1f} km from the site · station reporting day")
        else:
            self.daily_source.configure(text="No usable daily station record was found. — means not recorded.")
        h = report.hourly
        if h:
            self.hourly_source.configure(text=f"{h.station.display_name} · {h.station.distance_km:.1f} km away · {len(h.observations)} observation times")
            self.chart_note.configure(text=f"{report.location.timezone} · Recorded temperature points; gaps longer than 3 hours remain empty.")
            gusts = [o.readings["wind_gust"].value for o in h.observations if "wind_gust" in o.readings]
            extras = [f"Largest recorded gust: {speed(max(gusts), self.imperial)}"] if gusts else []
        else:
            self.hourly_source.configure(text="No subdaily observations were found for this selection.")
            self.chart_note.configure(text="")
            extras = []
        if "SNWD" in values:
            extras.append("Recorded snow depth: " + amount(values["SNWD"], self.imperial))
        weather = [label for key, label in DAILY_WEATHER.items() if key in values and values[key].value > 0]
        if weather:
            extras.append("Station reported: " + ", ".join(weather))
        self.extra.configure(text="  ·  ".join(extras))
        self.table.delete(*self.table.get_children())
        for row in observation_rows(report, self.imperial):
            self.table.insert("", "end", values=row)
        self._set_sources(summary_text(report, self.imperial) + "\n\nRecorded values and provenance\n\n" + json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        self.copy_button.configure(state="normal")
        self.save_button.configure(state="normal")
        if report.status == "future":
            self.heading.configure(text="No recorded weather yet")
            self.subtitle.configure(text=f"| {day_title(report)} · {location_name}")
            self.status.set(report.notes[0])
        elif report.status in {"unavailable", "error"}:
            self.status.set(report.notes[0])
        else:
            cache_flags = [r.from_cache for r in (d, h) if r]
            cached = "Showing saved NOAA records." if all(cache_flags) else "NOAA records loaded and saved for later lookups."
            if (d and d.stale) or (h and h.stale):
                cached = "Showing previously downloaded records; NOAA could not be refreshed."
            self.status.set(("Partial record. " if report.status == "partial" else "") + cached)
        self._fit_result_heading()
        self._draw_chart()

    def _draw_chart(self):
        canvas = self.chart
        canvas.delete("all")
        w, h = max(canvas.winfo_width(), 300), max(canvas.winfo_height(), 90)
        report = self.report
        if not report or not report.hourly:
            msg = "No subdaily temperature observations to plot."
            if report is None:
                msg = "The day's recorded temperature will appear here."
            canvas.create_text(w / 2, h / 2, text=msg, fill=MUTED, font=("Segoe UI", 12), width=w - 100)
            return
        points = [(o.time, o.readings["temperature"].value) for o in report.hourly.observations if "temperature" in o.readings]
        if not points:
            canvas.create_text(w / 2, h / 2, text="No usable temperature values were recorded.", fill=MUTED, font=("Segoe UI", 12))
            return
        start, end = utc_window(report.day, report.location.timezone)
        duration = (end - start).total_seconds()
        values = [v * 9 / 5 + 32 if self.imperial else v for _, v in points]
        low, high = min(values), max(values)
        pad = max(2.0, (high - low) * .15)
        low, high = low - pad, high + pad
        left, right, top, bottom = 61, w - 24, 21, h - 38
        def xy(stamp, value):
            return left + (right - left) * (stamp - start).total_seconds() / duration, bottom - (bottom - top) * (value - low) / (high - low)
        for fraction in (0, .5, 1):
            y = bottom - (bottom - top) * fraction
            canvas.create_line(left, y, right, y, fill="#e8eef2")
            canvas.create_text(left - 9, y, text=f"{low + (high - low) * fraction:.0f}°", anchor="e", fill=MUTED, font=("Segoe UI", 9))
        tz = ZoneInfo(report.location.timezone)
        for hour in (0, 6, 12, 18):
            tick = datetime.combine(report.day, datetime.min.time(), tz) + timedelta(hours=hour)
            x, _ = xy(tick.astimezone(start.tzinfo), low)
            canvas.create_text(x, bottom + 18, text=f"{hour:02d}:00", fill=MUTED, font=("Segoe UI", 9))
        previous = None
        for (stamp, _), value in zip(points, values):
            x, y = xy(stamp, value)
            if previous and (stamp - previous[0]).total_seconds() <= 3 * 3600:
                canvas.create_line(previous[1], previous[2], x, y, fill=TEAL, width=2)
            if len(points) < 150:
                canvas.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=TEAL, outline=PAPER)
            previous = stamp, x, y

    def copy_summary(self):
        if self.report:
            self.root.clipboard_clear()
            self.root.clipboard_append(summary_text(self.report, self.imperial))
            self.status.set("Weather summary copied.")

    def save_json(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Save recorded weather", defaultextension=".json",
                                          initialfile=f"rainshine-{self.report.day.isoformat()}.json", filetypes=[("JSON files", "*.json")])
        if path:
            try:
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump(self.report.to_dict(), stream, ensure_ascii=False, indent=2, allow_nan=False)
                    stream.write("\n")
                self.status.set("Recorded weather and source details saved.")
            except OSError as exc:
                messagebox.showerror("Could not save", str(exc), parent=self.root)

    def close(self):
        self._closing = True
        self._cancel.set()
        self.root.destroy()


def launch():
    root = tk.Tk()
    RainshineApp(root)
    root.mainloop()
