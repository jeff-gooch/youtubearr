"""
Integration tests — run yt-dlp against real YouTube data.

These are slow (30–90s), require internet and the bundled yt-dlp binary.
Run manually before deploying:

    python -m unittest tests/test_integration.py -v

Or via deploy script:

    ./deploy.sh test --integration
"""
import os
import socket
import sys
import unittest
from unittest.mock import MagicMock

# ── Mock Django imports (same as unit tests) ─────────────────────────────────
for _mod in [
    "django", "django.db", "django.db.models", "django.db.transaction",
    "django.utils", "django.utils.timezone",
    "apps", "apps.channels", "apps.channels.models",
    "apps.plugins", "apps.plugins.models",
    "apps.epg", "apps.epg.models",
    "core", "core.models", "core.scheduling",
]:
    sys.modules.setdefault(_mod, MagicMock())

sys.path.insert(0, ".")
from plugin import Plugin  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YTDLP_PATH = os.path.join(_ROOT, "yt-dlp")
QJS_PATH = os.path.join(_ROOT, "qjs")

# ── Skip conditions ───────────────────────────────────────────────────────────
def _has_internet():
    try:
        socket.setdefaulttimeout(5)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False

_SKIP = None
if not (os.path.exists(YTDLP_PATH) and os.access(YTDLP_PATH, os.X_OK)):
    _SKIP = f"yt-dlp binary not found at {YTDLP_PATH}"
elif not _has_internet():
    _SKIP = "No internet connection"


def _make_plugin():
    p = Plugin.__new__(Plugin)
    p._ytdlp_path = YTDLP_PATH
    p._qjs_path = QJS_PATH if os.path.exists(QJS_PATH) else None
    p._log = print
    p._log_error = print
    p._extraction_failures = {}
    p._assigned_channel_numbers = set()
    p._channel_group_name = "YouTube Live"
    return p


# ── Two-phase scan ────────────────────────────────────────────────────────────

@unittest.skipIf(_SKIP, _SKIP or "")
class TestTwoPhaseScanning(unittest.TestCase):
    """Validates the full two-phase scan against real YouTube channels."""

    @classmethod
    def setUpClass(cls):
        cls.plugin = _make_plugin()
        # Run the scan once and cache the result — all tests in this class share it.
        print("\n[integration] Scanning @nasa (this takes ~30s)...")
        cls.nasa_result = cls.plugin._get_live_streams_via_ytdlp("@nasa", {})
        print(f"[integration] Scan complete: {cls.nasa_result}")

    def test_returns_list_not_none(self):
        """A valid channel always returns a list, never None."""
        self.assertIsNotNone(
            self.nasa_result,
            "@nasa scan returned None — yt-dlp failed entirely"
        )
        self.assertIsInstance(self.nasa_result, list)

    def test_stream_entries_have_required_fields(self):
        """Every returned stream has video_id, title, and thumbnail."""
        if not self.nasa_result:
            self.skipTest("No live streams on @nasa right now")
        for stream in self.nasa_result:
            with self.subTest(video_id=stream.get("video_id")):
                self.assertIn("video_id", stream)
                self.assertIn("title", stream)
                self.assertIn("thumbnail", stream)
                self.assertTrue(stream["video_id"])

    def test_null_live_status_not_returned(self):
        """Regression: flat-playlist returns null live_status — must not be treated as live.

        If this fails it means Phase 2 is being skipped and flat-playlist results
        are being returned directly (the original v1.10.0 bug).
        """
        if self.nasa_result is None:
            self.skipTest("Scan returned None")
        # All returned entries must have actually passed Phase 2's is_live check.
        # We verify by spot-checking the first entry with _verify_video_is_live.
        if not self.nasa_result:
            self.skipTest("No live streams to verify")
        video_id = self.nasa_result[0]["video_id"]
        confirmed = self.plugin._verify_video_is_live(video_id)
        self.assertTrue(
            confirmed,
            f"{video_id} was returned as live by scan but _verify_video_is_live disagrees — "
            "Phase 2 may not be running correctly"
        )

    def test_max_streams_cap_respected(self):
        """Setting max_streams_per_channel=1 limits Phase 1 to one candidate."""
        result = self.plugin._get_live_streams_via_ytdlp("@nasa", {"max_streams_per_channel": 1})
        self.assertIsNotNone(result)
        self.assertLessEqual(len(result), 1)

    def test_invalid_channel_does_not_raise(self):
        """A non-existent channel returns None or [] and never raises."""
        try:
            result = self.plugin._get_live_streams_via_ytdlp(
                "@xyzxyz_this_channel_does_not_exist_999", {}
            )
            self.assertTrue(result is None or isinstance(result, list))
        except Exception as exc:
            self.fail(f"Scan raised unexpectedly: {exc}")


# ── Direct live check ─────────────────────────────────────────────────────────

@unittest.skipIf(_SKIP, _SKIP or "")
class TestVerifyVideoIsLiveIntegration(unittest.TestCase):
    """Validates _verify_video_is_live against real video IDs.

    Reuses the @nasa scan result from TestTwoPhaseScanning to avoid a second full scan.
    """

    @classmethod
    def setUpClass(cls):
        cls.plugin = _make_plugin()
        # TestTwoPhaseScanning.setUpClass runs first (alphabetical order),
        # so its cached result is available here.
        cls.nasa_live_streams = TestTwoPhaseScanning.nasa_result

    def test_regular_video_not_detected_as_live(self):
        """A regular (non-stream) YouTube video must return False.

        Uses a well-known stable video that will never be live.
        If this fails, the live detection logic has a false-positive bug.
        """
        result = self.plugin._verify_video_is_live("dQw4w9WgXcQ")  # Rick Astley
        self.assertFalse(result, "A regular video should never be detected as live")

    def test_known_live_stream_detected(self):
        """A confirmed-live stream returns True.

        Uses a live stream found by the two-phase scan. If no streams are live
        right now the test is skipped — that's expected, not a failure.
        """
        if not self.nasa_live_streams:
            self.skipTest("No live streams on @nasa right now — cannot test True case")
        video_id = self.nasa_live_streams[0]["video_id"]
        print(f"\n[integration] Direct live check for {video_id}...")
        self.assertTrue(
            self.plugin._verify_video_is_live(video_id),
            f"{video_id} was found live by scan but _verify_video_is_live returned False"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
