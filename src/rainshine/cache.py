"""Bounded HTTPS downloads and an atomic, reusable disk cache."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from urllib.parse import urlsplit

from . import __version__
from .models import Cancelled, NotFound, WeatherError

MAX_DOWNLOAD_BYTES = 96 * 1024 * 1024


def user_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Rainshine"
    elif __import__("sys").platform == "darwin":
        base = Path.home() / "Library/Caches/Rainshine"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rainshine"
    return base


@dataclass(frozen=True)
class CachedFile:
    path: Path
    url: str
    retrieved_at: str
    from_cache: bool
    stale: bool = False


class ArchiveCache:
    def __init__(self, directory: Path | None = None, *, opener=urllib.request.urlopen, max_bytes=MAX_DOWNLOAD_BYTES):
        self.directory = Path(directory) if directory else user_cache_dir() / "archives"
        self.opener = opener
        self.max_bytes = max_bytes

    def fetch(self, url: str, *, signature: bytes, cancel: Event | None = None, max_age: int = 86400) -> CachedFile:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "www.ncei.noaa.gov" or parsed.username or parsed.password:
            raise WeatherError("Archive requests must use NOAA's HTTPS data service.")
        if cancel and cancel.is_set():
            raise Cancelled("Lookup cancelled.")
        self.directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode()).hexdigest()
        target, metadata = self.directory / f"{key}.txt", self.directory / f"{key}.json"
        old = None
        try:
            info = json.loads(metadata.read_text(encoding="utf-8"))
            valid = info.get("url") == url and target.is_file() and target.stat().st_size == info.get("size")
            if valid:
                with target.open("rb") as stream:
                    valid = stream.read(len(signature)).startswith(signature)
            if valid:
                old = CachedFile(target, url, str(info["retrieved_at"]), True)
                if time.time() - float(info["timestamp"]) < max_age:
                    return old
        except (OSError, ValueError, KeyError, TypeError):
            pass

        temporary = None
        try:
            request = urllib.request.Request(url, headers={"User-Agent": f"Rainshine/{__version__} (historical weather desktop app)", "Accept": "text/plain"})
            with self.opener(request, timeout=20) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > self.max_bytes:
                    raise WeatherError("This archive file exceeds Rainshine's download size limit.")
                total = 0
                started = time.monotonic()
                with tempfile.NamedTemporaryFile(dir=self.directory, prefix="download-", delete=False) as stream:
                    temporary = Path(stream.name)
                    while True:
                        if cancel and cancel.is_set():
                            raise Cancelled("Lookup cancelled.")
                        chunk = response.read(128 * 1024)
                        if not chunk:
                            break
                        if total == 0 and not chunk.startswith(signature):
                            raise WeatherError("NOAA returned an unexpected file format; no observations were accepted.")
                        total += len(chunk)
                        if total > self.max_bytes or time.monotonic() - started > 120:
                            raise WeatherError("The archive download exceeded its size or time limit.")
                        stream.write(chunk)
                if not total:
                    raise WeatherError("NOAA returned an empty archive file.")
                if length and total != int(length):
                    raise WeatherError("The archive download was incomplete. Please try again.")
            if cancel and cancel.is_set():
                raise Cancelled("Lookup cancelled.")
            stamp = datetime.now(timezone.utc).isoformat()
            info = {"url": url, "retrieved_at": stamp, "timestamp": time.time(), "size": total}
            os.replace(temporary, target)
            temporary = None
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, prefix="metadata-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(info, stream)
            os.replace(temporary, metadata)
            temporary = None
            return CachedFile(target, url, stamp, False)
        except Cancelled:
            raise
        except urllib.error.HTTPError as exc:
            if old:
                return CachedFile(old.path, old.url, old.retrieved_at, True, True)
            if exc.code == 404:
                raise NotFound("No archive file is available for that station and year.") from exc
            raise WeatherError(f"NOAA returned HTTP {exc.code}. Please try again later.") from exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            if old:
                return CachedFile(old.path, old.url, old.retrieved_at, True, True)
            raise WeatherError("Could not reach NOAA. Check the internet connection and try again.") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
