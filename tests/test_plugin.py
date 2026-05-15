"""
YouTubearr plugin tests.

Covers:
- Two-phase live stream detection (_get_live_streams_via_ytdlp)
- Direct live check (_verify_video_is_live)
- Subchannel decimal collision fix (_get_next_subchannel_number)
- Version/changelog consistency

Run with: python -m pytest tests/ -v
      or: python -m unittest discover tests/
"""
import json
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

# ── Mock Django and app imports before loading plugin ────────────────────────
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


def _make_plugin(qjs=False):
    p = Plugin.__new__(Plugin)
    p._ytdlp_path = "/usr/bin/yt-dlp"
    p._qjs_path = "/usr/bin/qjs" if qjs else None
    p._log = lambda msg: None
    p._log_error = lambda msg: None
    p._extraction_failures = {}
    p._assigned_channel_numbers = set()
    p._channel_group_name = "YouTube Live"
    return p


def _phase1_result(entries):
    lines = "\n".join(json.dumps(e) for e in entries)
    return MagicMock(stdout=lines + "\n", returncode=0)


def _phase2_result(status):
    return MagicMock(stdout=status + "\n", returncode=0)


# ── _get_live_streams_via_ytdlp ──────────────────────────────────────────────

class TestGetLiveStreams(unittest.TestCase):

    def test_live_stream_detected(self):
        p = _make_plugin()
        entries = [{"id": "abc123", "title": "ISS Stream", "thumbnail": "http://t.jpg"}]
        with patch("subprocess.run", side_effect=[_phase1_result(entries), _phase2_result("is_live")]):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["video_id"], "abc123")
        self.assertEqual(result[0]["title"], "ISS Stream")

    def test_was_live_excluded(self):
        p = _make_plugin()
        entries = [{"id": "abc123", "title": "Old Stream"}]
        with patch("subprocess.run", side_effect=[_phase1_result(entries), _phase2_result("was_live")]):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertEqual(result, [])

    def test_null_live_status_excluded(self):
        # Regression guard: the original flat-playlist bug returned null/empty live_status.
        # An empty status must never be treated as live.
        p = _make_plugin()
        entries = [{"id": "abc123", "title": "Stream"}]
        with patch("subprocess.run", side_effect=[_phase1_result(entries), _phase2_result("")]):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertEqual(result, [])

    def test_multiple_candidates_only_live_returned(self):
        p = _make_plugin()
        entries = [
            {"id": "live1", "title": "Live Now"},
            {"id": "past1", "title": "Past Stream"},
        ]
        with patch("subprocess.run", side_effect=[
            _phase1_result(entries),
            _phase2_result("is_live"),
            _phase2_result("was_live"),
        ]):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["video_id"], "live1")

    def test_phase1_failure_returns_none(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="", returncode=1)):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertIsNone(result)

    def test_phase1_timeout_returns_none(self):
        p = _make_plugin()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("yt-dlp", 60)):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertIsNone(result)

    def test_phase2_timeout_skips_candidate(self):
        p = _make_plugin()
        entries = [{"id": "abc123", "title": "Stream"}]
        with patch("subprocess.run", side_effect=[
            _phase1_result(entries),
            subprocess.TimeoutExpired("yt-dlp", 30),
        ]):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertEqual(result, [])

    def test_empty_streams_tab_returns_empty_list(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)):
            result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertEqual(result, [])

    def test_no_ytdlp_returns_none(self):
        p = _make_plugin()
        p._ytdlp_path = None
        result = p._get_live_streams_via_ytdlp("@nasa", {})
        self.assertIsNone(result)

    def test_quickjs_included_in_phase2_when_available(self):
        p = _make_plugin(qjs=True)
        entries = [{"id": "abc123", "title": "Stream"}]
        with patch("subprocess.run", side_effect=[
            _phase1_result(entries),
            _phase2_result("is_live"),
        ]) as mock_run:
            p._get_live_streams_via_ytdlp("@nasa", {})
        phase2_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn("--js-runtimes", phase2_cmd)
        self.assertIn("quickjs:/usr/bin/qjs", phase2_cmd)

    def test_quickjs_absent_when_not_configured(self):
        p = _make_plugin(qjs=False)
        entries = [{"id": "abc123", "title": "Stream"}]
        with patch("subprocess.run", side_effect=[
            _phase1_result(entries),
            _phase2_result("is_live"),
        ]) as mock_run:
            p._get_live_streams_via_ytdlp("@nasa", {})
        phase2_cmd = mock_run.call_args_list[1][0][0]
        self.assertNotIn("--js-runtimes", phase2_cmd)

    def test_max_streams_setting_applied_to_phase1(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)) as mock_run:
            p._get_live_streams_via_ytdlp("@nasa", {"max_streams_per_channel": 30})
        phase1_cmd = mock_run.call_args_list[0][0][0]
        idx = phase1_cmd.index("--playlist-end")
        self.assertEqual(phase1_cmd[idx + 1], "30")

    def test_handle_normalized_with_at_prefix(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)) as mock_run:
            p._get_live_streams_via_ytdlp("nasa", {})  # no @ prefix
        phase1_cmd = mock_run.call_args_list[0][0][0]
        url = phase1_cmd[-1]
        self.assertIn("/@nasa/streams", url)


# ── _verify_video_is_live ────────────────────────────────────────────────────

class TestVerifyVideoIsLive(unittest.TestCase):

    def test_is_live_returns_true(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="is_live\n")):
            self.assertTrue(p._verify_video_is_live("abc123"))

    def test_was_live_returns_false(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="was_live\n")):
            self.assertFalse(p._verify_video_is_live("abc123"))

    def test_not_live_returns_false(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="not_live\n")):
            self.assertFalse(p._verify_video_is_live("abc123"))

    def test_timeout_returns_true(self):
        p = _make_plugin()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("yt-dlp", 30)):
            self.assertTrue(p._verify_video_is_live("abc123"))

    def test_no_ytdlp_returns_true(self):
        p = _make_plugin()
        p._ytdlp_path = None
        self.assertTrue(p._verify_video_is_live("abc123"))

    def test_quickjs_included_when_available(self):
        p = _make_plugin(qjs=True)
        with patch("subprocess.run", return_value=MagicMock(stdout="is_live\n")) as mock_run:
            p._verify_video_is_live("abc123")
        cmd = mock_run.call_args[0][0]
        self.assertIn("--js-runtimes", cmd)
        self.assertIn("quickjs:/usr/bin/qjs", cmd)


# ── _get_next_subchannel_number ──────────────────────────────────────────────

class TestSubchannelNumbering(unittest.TestCase):

    def _run(self, occupied_decimals, base=90, assigned=None):
        p = _make_plugin()
        p._assigned_channel_numbers = set(assigned or [])
        floats = [float(f"{base}.{d}") for d in occupied_decimals]
        with patch("plugin.ChannelGroup") as mock_cg, patch("plugin.Channel") as mock_ch:
            mock_cg.objects.get.return_value = MagicMock()
            mock_ch.objects.filter.return_value.values_list.return_value = floats
            return p._get_next_subchannel_number(base, {})

    def test_first_slot_when_empty(self):
        self.assertEqual(self._run([]), 90.1)

    def test_fills_gap(self):
        result = self._run([1, 3])  # 90.2 is free
        self.assertEqual(result, 90.2)

    def test_skips_10_to_avoid_float_collision(self):
        # 90.10 as a float equals 90.1 — must skip to 90.11
        result = self._run(range(1, 10))
        decimal = str(result).split(".")[1]
        self.assertNotEqual(decimal, "10", "90.10 collides with 90.1 as a float")
        self.assertEqual(decimal, "11")

    def test_skips_20(self):
        result = self._run(list(range(1, 10)) + list(range(11, 20)))
        decimal = str(result).split(".")[1]
        self.assertNotEqual(decimal, "20")
        self.assertEqual(decimal, "21")


# ── Version / changelog consistency ─────────────────────────────────────────

class TestVersionConsistency(unittest.TestCase):

    def test_plugin_py_version_matches_plugin_json(self):
        import importlib.util, json as _json, re, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        with open(os.path.join(root, "plugin.json")) as f:
            json_version = _json.load(f)["version"]

        with open(os.path.join(root, "plugin.py")) as f:
            src = f.read()
        match = re.search(r'version\s*=\s*"([^"]+)"', src)
        self.assertIsNotNone(match, "Could not find version in plugin.py")
        self.assertEqual(match.group(1), json_version,
                         f"plugin.py version {match.group(1)!r} != plugin.json {json_version!r}")

    def test_changelog_mentions_current_version(self):
        import json as _json, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        with open(os.path.join(root, "plugin.json")) as f:
            version = _json.load(f)["version"]

        changelog = os.path.join(root, "CHANGELOG.md")
        self.assertTrue(os.path.exists(changelog), "CHANGELOG.md not found")
        with open(changelog) as f:
            content = f.read()
        self.assertIn(version, content,
                      f"Version {version} not mentioned in CHANGELOG.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)
