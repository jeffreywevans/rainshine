import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from threading import Event

from rainshine.cache import ArchiveCache
from rainshine.models import Cancelled, NotFound, WeatherError

URL = "https://www.ncei.noaa.gov/example.psv"


class Response(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_download_is_cached_without_another_network_request(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            return Response(b"STATION|DATE\nUSW00000001|2010-01-01\n")
        cache = ArchiveCache(self.path, opener=opener)
        first = cache.fetch(URL, signature=b"STATION|")
        second = cache.fetch(URL, signature=b"STATION|")
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(calls), 1)

    def test_expired_saved_observations_are_labelled_during_outage(self):
        cache = ArchiveCache(self.path, opener=lambda request, timeout: Response(b"STATION|DATE\n"))
        saved = cache.fetch(URL, signature=b"STATION|")
        cache.opener = lambda request, timeout: (_ for _ in ()).throw(urllib.error.URLError("offline"))
        stale = cache.fetch(URL, signature=b"STATION|", max_age=-1)
        self.assertTrue(stale.stale)
        self.assertTrue(stale.from_cache)
        self.assertEqual(stale.path.read_bytes(), saved.path.read_bytes())

    def test_html_error_is_not_saved_as_weather(self):
        cache = ArchiveCache(self.path, opener=lambda request, timeout: Response(b"<!doctype html>error"))
        with self.assertRaises(WeatherError):
            cache.fetch(URL, signature=b"STATION|")
        self.assertEqual(list(self.path.iterdir()), [])

    def test_oversized_response_is_rejected(self):
        cache = ArchiveCache(self.path, opener=lambda request, timeout: Response(b"STATION|too big"), max_bytes=4)
        with self.assertRaises(WeatherError):
            cache.fetch(URL, signature=b"STATION|")

    def test_http_404_is_missing_data(self):
        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Missing", {}, None)
        with self.assertRaises(NotFound):
            ArchiveCache(self.path, opener=opener).fetch(URL, signature=b"STATION|")

    def test_untrusted_download_urls_are_rejected(self):
        for url in ["http://www.ncei.noaa.gov/x", "https://example.com/weather", "file:///etc/passwd"]:
            with self.assertRaises(WeatherError):
                ArchiveCache(self.path).fetch(url, signature=b"STATION|")

    def test_cancellation_prevents_network_access(self):
        cancel = Event(); cancel.set()
        cache = ArchiveCache(self.path, opener=lambda *args, **kwargs: self.fail("Network must not be called"))
        with self.assertRaises(Cancelled):
            cache.fetch(URL, signature=b"STATION|", cancel=cancel)

    def test_corrupt_cached_file_is_downloaded_again(self):
        cache = ArchiveCache(self.path, opener=lambda request, timeout: Response(b"STATION|DATE\n"))
        result = cache.fetch(URL, signature=b"STATION|")
        result.path.write_bytes(b"wrong file")
        fixed = cache.fetch(URL, signature=b"STATION|")
        self.assertFalse(fixed.from_cache)
        self.assertTrue(fixed.path.read_bytes().startswith(b"STATION|"))
