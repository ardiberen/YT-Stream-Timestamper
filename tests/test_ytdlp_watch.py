"""Tests for .github/scripts/ytdlp_watch.py (the daily 'is there a newer yt-dlp?' check). No network."""
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ytdlp_watch", Path(__file__).resolve().parents[1] / ".github" / "scripts" / "ytdlp_watch.py")
w = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w)

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


class YtdlpWatchTests(unittest.TestCase):
    def test_reads_versions(self):
        self.assertEqual(w.released_ytdlp("certifi==1\nyt-dlp==2026.10.26\nyt-dlp-ejs==0.8.0\n"), "2026.10.26")
        self.assertEqual(w.app_version('x\nAPP_VERSION = "0.9.2"\n'), "0.9.2")
        self.assertEqual(w.bump_patch("0.9.9"), "0.9.10")
        self.assertGreater(w.vtuple("2026.11.23"), w.vtuple("2026.10.26"))
        self.assertGreater(w.vtuple("2026.11.23.1"), w.vtuple("2026.11.23"))

    def test_decisions(self):
        old = NOW - timedelta(days=2)
        # same version: nothing to do
        self.assertEqual(w.decide("2026.10.26", old, "2026.10.26", "0.9.1", {"v0.9.1"}, NOW)["need"], "false")
        # newer but fresh: wait a day
        d = w.decide("2026.11.23", NOW - timedelta(hours=3), "2026.10.26", "0.9.1", {"v0.9.1"}, NOW)
        self.assertEqual(d["need"], "false")
        # newer, settled, current version already released: raise the version
        d = w.decide("2026.11.23", old, "2026.10.26", "0.9.1", {"v0.9.0", "v0.9.1"}, NOW)
        self.assertEqual((d["need"], d["version"], d["bump"], d["ytdlp"]), ("true", "0.9.2", "true", "2026.11.23"))
        # newer, settled, main already has an unreleased version: release that one as it is
        d = w.decide("2026.11.23", old, "2026.10.26", "0.9.2", {"v0.9.1"}, NOW)
        self.assertEqual((d["need"], d["version"], d["bump"]), ("true", "0.9.2", "false"))
        # nothing to compare against: don't guess
        self.assertEqual(w.decide("2026.11.23", old, "", "0.9.1", set(), NOW)["need"], "false")


if __name__ == "__main__":
    unittest.main()
