"""Read the documented location/station crosswalk and rank actual collection sites."""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from datetime import date
from importlib.resources import files
from typing import Iterable

from .models import Location, Station

STATION_ID = re.compile(r"[A-Za-z0-9-]{11}\Z")


def distance_km(lat: float, lon: float, other_lat: float, other_lon: float) -> float:
    a, b = math.radians(lat), math.radians(other_lat)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(other_lon - lon) / 2) ** 2
    return 12742.0176 * math.asin(min(1.0, math.sqrt(h)))


class Catalog:
    def __init__(self, data: dict | None = None):
        if data is None:
            data = json.loads(files("rainshine").joinpath("data/catalog.json").read_text(encoding="utf-8"))
        self.snapshot_year = int(data["snapshot_year"])
        self._stations: dict[str, dict[str, Station]] = {}
        for dataset in ("ghcnh", "ghcnd"):
            self._stations[dataset] = {}
            for sid, raw in data[dataset].items():
                if not STATION_ID.fullmatch(sid):
                    raise ValueError(f"Invalid archive station identifier: {sid!r}")
                self._stations[dataset][sid] = Station(id=sid, dataset=dataset, **raw)
        self._locations = {}
        for name, raw in data["locations"].items():
            def refs(dataset: str, key: str) -> tuple[Station, ...]:
                result = []
                for sid in raw[key]:
                    s = self._stations[dataset][sid]
                    result.append(replace(s, distance_km=distance_km(raw["latitude"], raw["longitude"], s.latitude, s.longitude)))
                return tuple(sorted(result, key=lambda s: (s.distance_km, s.id)))

            self._locations[name] = Location(
                name=name, latitude=raw["latitude"], longitude=raw["longitude"], timezone=raw["timezone"],
                hourly=refs("ghcnh", "hourly"), daily=refs("ghcnd", "daily"),
            )

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._locations, key=str.casefold))

    def location(self, name: str) -> Location:
        try:
            return self._locations[name]
        except KeyError as exc:
            raise ValueError("Choose a location from the site list.") from exc

    def candidates(self, stations: Iterable[Station], day: date) -> list[Station]:
        """Distance first; metadata rules out known gaps, not future archive additions."""
        def may_have_data(s: Station) -> bool:
            if s.dataset == "ghcnd":
                intervals = [v for k, v in s.elements.items() if k in {"TMAX", "TMIN", "PRCP"}]
                if day.year > self.snapshot_year:
                    return any(v["last_year"] >= self.snapshot_year - 1 for v in intervals)
                return any(v["first_year"] <= day.year <= v["last_year"] for v in intervals)
            counts = s.months.get(str(day.year))
            if counts:
                return counts[day.month - 1] > 0 or (day.year == self.snapshot_year and s.last_year >= self.snapshot_year)
            if day.year > self.snapshot_year:
                return s.last_year >= self.snapshot_year - 1
            return s.first_year <= day.year <= s.last_year

        return sorted((s for s in stations if may_have_data(s)), key=lambda s: (s.distance_km, s.id))
