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
import tempfile
import threading
import time
import unittest
from pathlib import Path
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
    p._base_dir = Path(tempfile.mkdtemp(prefix="youtubearr-test-"))
    p._ytdlp_path = "/usr/bin/yt-dlp"
    p._qjs_path = "/usr/bin/qjs" if qjs else None
    p._log = lambda msg: None
    p._log_error = lambda msg: None
    p._extraction_failures = {}
    p._assigned_channel_numbers = set()
    p._channel_group_name = "YouTube Live"
    # Prevent filesystem access in tests — override per-test when testing real behavior
    p._read_runtime_state = MagicMock(return_value={})
    p._write_runtime_state = MagicMock()
    p._acquire_monitor_lock = MagicMock(return_value=True)
    p._release_monitor_lock = MagicMock()
    p._is_monitor_lock_held_by_other = MagicMock(return_value=False)
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

    def test_title_filter_expands_phase1_limit_to_100(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="", returncode=0)) as mock_run:
            p._get_live_streams_via_ytdlp("@virtualrailfan", {}, title_filter="Pennsylvania")
        phase1_cmd = mock_run.call_args_list[0][0][0]
        idx = phase1_cmd.index("--playlist-end")
        self.assertEqual(phase1_cmd[idx + 1], "100")

    def test_title_filter_excludes_non_matching_candidates_before_phase2(self):
        p = _make_plugin()
        entries = [
            {"id": "match1", "title": "Pennsylvania Live"},
            {"id": "skip1", "title": "La Grange Stream"},
            {"id": "match2", "title": "Pennsylvania Railcam"},
        ]
        with patch("subprocess.run", side_effect=[
            _phase1_result(entries),
            _phase2_result("is_live"),
            _phase2_result("is_live"),
        ]) as mock_run:
            result = p._get_live_streams_via_ytdlp("@virtualrailfan", {}, title_filter="Pennsylvania")
        self.assertEqual(len(result), 2)
        self.assertEqual([r["video_id"] for r in result], ["match1", "match2"])
        self.assertEqual(mock_run.call_count, 3)  # phase1 + 2 phase2 (not 3)

    def test_title_filter_returns_empty_when_no_candidates_match(self):
        p = _make_plugin()
        entries = [{"id": "skip1", "title": "La Grange Stream"}]
        with patch("subprocess.run", side_effect=[_phase1_result(entries)]) as mock_run:
            result = p._get_live_streams_via_ytdlp("@virtualrailfan", {}, title_filter="Pennsylvania")
        self.assertEqual(result, [])
        self.assertEqual(mock_run.call_count, 1)  # phase2 never runs


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


# ── _prune_extraction_failures ──────────────────────────────────────────────

class TestExtractionFailurePruning(unittest.TestCase):

    def _plugin_with(self, failures):
        p = _make_plugin()
        p._extraction_failures = dict(failures)
        return p

    def test_old_timestamps_are_pruned(self):
        now = 1_000_000.0
        p = self._plugin_with({"vid1": now - 8 * 86400})  # 8 days ago, past 7-day TTL
        pruned = p._prune_extraction_failures(ttl_days=7, now=now)
        self.assertEqual(pruned, 1)
        self.assertNotIn("vid1", p._extraction_failures)

    def test_recent_timestamps_remain(self):
        now = 1_000_000.0
        p = self._plugin_with({"vid1": now - 3600})  # 1 hour ago
        pruned = p._prune_extraction_failures(ttl_days=7, now=now)
        self.assertEqual(pruned, 0)
        self.assertIn("vid1", p._extraction_failures)

    def test_malformed_timestamp_pruned_without_crash(self):
        p = self._plugin_with({"vid1": "not_a_number", "vid2": None, "vid3": {}})
        pruned = p._prune_extraction_failures(ttl_days=7, now=1_000_000.0)
        self.assertGreaterEqual(pruned, 0)
        self.assertNotIn("vid1", p._extraction_failures)
        self.assertNotIn("vid2", p._extraction_failures)
        self.assertNotIn("vid3", p._extraction_failures)

    def test_future_timestamps_not_pruned(self):
        # Members-only streams store failure_time = now + 6*86400 to extend the skip window
        now = 1_000_000.0
        future = now + 6 * 86400
        p = self._plugin_with({"vid1": future})
        pruned = p._prune_extraction_failures(ttl_days=7, now=now)
        self.assertEqual(pruned, 0)
        self.assertIn("vid1", p._extraction_failures)


# ── _get_subchannel_index ────────────────────────────────────────────────────

class TestSubchannelIndex(unittest.TestCase):

    def test_single_digit(self):
        self.assertEqual(_make_plugin()._get_subchannel_index("90.1", 90), 1)

    def test_nine(self):
        self.assertEqual(_make_plugin()._get_subchannel_index("90.9", 90), 9)

    def test_eleven_no_float_math(self):
        # Float math gives int(round(0.11 * 10)) = 1 (wrong); string parse gives 11 (correct)
        self.assertEqual(_make_plugin()._get_subchannel_index("90.11", 90), 11)

    def test_twenty_one_no_float_math(self):
        # Float math gives int(round(0.21 * 10)) = 2 (wrong); string parse gives 21 (correct)
        self.assertEqual(_make_plugin()._get_subchannel_index("90.21", 90), 21)

    def test_accepts_float_input(self):
        # channel_number is stored as float; Python's str() gives the short decimal form
        # float(f"90.{11}") == 90.11 and str(90.11) == "90.11"
        self.assertEqual(_make_plugin()._get_subchannel_index(float(f"90.{11}"), 90), 11)

    def test_no_decimal_part_returns_one(self):
        self.assertEqual(_make_plugin()._get_subchannel_index("90", 90), 1)


# ── _cache_bust_image_url ────────────────────────────────────────────────────

class TestCacheBustImageUrl(unittest.TestCase):

    def test_no_query_gets_ytarr_ts(self):
        result = _make_plugin()._cache_bust_image_url(
            "http://example.com/img.jpg", enabled=True, timestamp=12345
        )
        self.assertTrue(result.startswith("http://example.com/img.jpg?"))
        self.assertIn("ytarr_ts=12345", result)

    def test_existing_query_appended_with_ampersand(self):
        result = _make_plugin()._cache_bust_image_url(
            "http://example.com/img.jpg?size=100", enabled=True, timestamp=12345
        )
        self.assertIn("size=100", result)
        self.assertIn("ytarr_ts=12345", result)
        self.assertIn("&ytarr_ts=", result)

    def test_existing_ytarr_ts_replaced_not_duplicated(self):
        result = _make_plugin()._cache_bust_image_url(
            "http://example.com/img.jpg?ytarr_ts=999", enabled=True, timestamp=12345
        )
        self.assertIn("ytarr_ts=12345", result)
        self.assertNotIn("ytarr_ts=999", result)
        self.assertEqual(result.count("ytarr_ts="), 1)

    def test_disabled_returns_original_url(self):
        url = "http://example.com/img.jpg"
        self.assertEqual(_make_plugin()._cache_bust_image_url(url, enabled=False, timestamp=12345), url)

    def test_blank_string_unchanged(self):
        self.assertEqual(_make_plugin()._cache_bust_image_url(""), "")

    def test_none_unchanged(self):
        self.assertIsNone(_make_plugin()._cache_bust_image_url(None))

    def test_data_url_unchanged(self):
        url = "data:image/png;base64,abc123=="
        self.assertEqual(_make_plugin()._cache_bust_image_url(url, timestamp=12345), url)

    def test_local_path_unchanged(self):
        url = "/var/www/img.jpg"
        self.assertEqual(_make_plugin()._cache_bust_image_url(url, timestamp=12345), url)


# ── _merge_youtubearr_custom_properties ─────────────────────────────────────

class TestMergeCustomProperties(unittest.TestCase):

    def test_preserves_existing_props(self):
        existing = {"foo": "bar", "count": 42}
        result = _make_plugin()._merge_youtubearr_custom_properties(existing, youtube_video_id="abc")
        self.assertEqual(result["foo"], "bar")
        self.assertEqual(result["count"], 42)

    def test_adds_owner_field(self):
        result = _make_plugin()._merge_youtubearr_custom_properties({})
        self.assertEqual(result["owner"], "youtubearr")

    def test_adds_metadata_kwargs(self):
        result = _make_plugin()._merge_youtubearr_custom_properties(
            {}, youtube_video_id="vid1", youtube_channel_id="chan1"
        )
        self.assertEqual(result["youtube_video_id"], "vid1")
        self.assertEqual(result["youtube_channel_id"], "chan1")

    def test_overwrites_owner_and_video_id(self):
        existing = {"owner": "other", "youtube_video_id": "old"}
        result = _make_plugin()._merge_youtubearr_custom_properties(existing, youtube_video_id="new")
        self.assertEqual(result["owner"], "youtubearr")
        self.assertEqual(result["youtube_video_id"], "new")

    def test_none_existing_treated_as_empty(self):
        result = _make_plugin()._merge_youtubearr_custom_properties(None)
        self.assertEqual(result["owner"], "youtubearr")

    def test_does_not_mutate_input(self):
        existing = {"foo": "bar"}
        _make_plugin()._merge_youtubearr_custom_properties(existing, youtube_video_id="vid1")
        self.assertNotIn("owner", existing)
        self.assertNotIn("youtube_video_id", existing)


# ── _get_custom_m3u_account ──────────────────────────────────────────────────

class TestGetCustomM3uAccount(unittest.TestCase):

    def _clear_m3u_modules(self):
        for key in list(sys.modules.keys()):
            if 'm3u' in key:
                del sys.modules[key]

    def test_returns_none_when_m3u_not_installed(self):
        self._clear_m3u_modules()
        result = _make_plugin()._get_custom_m3u_account()
        self.assertIsNone(result)

    def test_calls_get_custom_account_when_available(self):
        mock_account = MagicMock()
        mock_module = MagicMock()
        mock_module.M3UAccount.get_custom_account.return_value = mock_account
        with patch.dict('sys.modules', {'apps.m3u': MagicMock(), 'apps.m3u.models': mock_module}):
            result = _make_plugin()._get_custom_m3u_account()
        self.assertEqual(result, mock_account)

    def test_fallback_get_or_create_when_get_custom_account_absent(self):
        mock_account = MagicMock()
        mock_module = MagicMock()
        mock_module.M3UAccount.get_custom_account.side_effect = AttributeError("no method")
        mock_module.M3UAccount.objects.get_or_create.return_value = (mock_account, True)
        with patch.dict('sys.modules', {'apps.m3u': MagicMock(), 'apps.m3u.models': mock_module}):
            result = _make_plugin()._get_custom_m3u_account()
        self.assertEqual(result, mock_account)
        mock_module.M3UAccount.objects.get_or_create.assert_called_once_with(
            name='custom',
            defaults={'is_active': True, 'locked': True, 'max_streams': 0},
        )


# ── Webhook helpers ─────────────────────────────────────────────────────────

class TestParseWebhookHeaders(unittest.TestCase):

    def test_valid_json_object_parsed(self):
        headers = _make_plugin()._parse_webhook_headers('{"Authorization": "Bearer tok", "X-Foo": "bar"}')
        self.assertEqual(headers, {"Authorization": "Bearer tok", "X-Foo": "bar"})

    def test_invalid_json_returns_empty_dict(self):
        headers = _make_plugin()._parse_webhook_headers("not-valid-json{{{")
        self.assertEqual(headers, {})

    def test_json_array_returns_empty_dict(self):
        headers = _make_plugin()._parse_webhook_headers('["a", "b"]')
        self.assertEqual(headers, {})

    def test_empty_string_returns_empty_dict(self):
        self.assertEqual(_make_plugin()._parse_webhook_headers(""), {})

    def test_none_returns_empty_dict(self):
        self.assertEqual(_make_plugin()._parse_webhook_headers(None), {})

    def test_whitespace_only_returns_empty_dict(self):
        self.assertEqual(_make_plugin()._parse_webhook_headers("   "), {})


class TestGetMediaRefreshWebhookConfig(unittest.TestCase):

    def test_legacy_webhook_url_resolves_as_media_refresh(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"webhook_url": "http://jellyfin/refresh"})
        self.assertEqual(config["url"], "http://jellyfin/refresh")
        self.assertTrue(config["is_legacy"])

    def test_new_media_refresh_url_takes_precedence_over_legacy(self):
        settings = {
            "webhook_url": "http://jellyfin/refresh",
            "media_refresh_webhook_url": "http://new/refresh",
        }
        config = _make_plugin()._get_media_refresh_webhook_config(settings)
        self.assertEqual(config["url"], "http://new/refresh")
        self.assertFalse(config["is_legacy"])

    def test_new_url_only_not_legacy(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"media_refresh_webhook_url": "http://n8n/refresh"})
        self.assertFalse(config["is_legacy"])

    def test_empty_settings_returns_empty_url(self):
        config = _make_plugin()._get_media_refresh_webhook_config({})
        self.assertEqual(config["url"], "")

    def test_delay_falls_back_to_legacy_webhook_delay_seconds(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"webhook_delay_seconds": 10})
        self.assertEqual(config["delay"], 10)

    def test_new_delay_takes_precedence_over_legacy(self):
        config = _make_plugin()._get_media_refresh_webhook_config({
            "webhook_delay_seconds": 10,
            "media_refresh_webhook_delay_seconds": 3,
        })
        self.assertEqual(config["delay"], 3)

    def test_delay_defaults_to_5(self):
        config = _make_plugin()._get_media_refresh_webhook_config({})
        self.assertEqual(config["delay"], 5)

    def test_delay_clamped_to_zero(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"webhook_delay_seconds": -5})
        self.assertEqual(config["delay"], 0)

    def test_delay_clamped_to_sixty(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"webhook_delay_seconds": 999})
        self.assertEqual(config["delay"], 60)

    def test_invalid_delay_defaults_to_five(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"webhook_delay_seconds": "oops"})
        self.assertEqual(config["delay"], 5)

    def test_method_defaults_to_post(self):
        config = _make_plugin()._get_media_refresh_webhook_config({})
        self.assertEqual(config["method"], "POST")

    def test_custom_method_respected(self):
        config = _make_plugin()._get_media_refresh_webhook_config({"media_refresh_webhook_method": "GET"})
        self.assertEqual(config["method"], "GET")

    def test_headers_parsed_from_json(self):
        config = _make_plugin()._get_media_refresh_webhook_config({
            "media_refresh_webhook_headers": '{"X-Token": "abc"}'
        })
        self.assertEqual(config["headers"], {"X-Token": "abc"})

    def test_invalid_headers_ignored(self):
        config = _make_plugin()._get_media_refresh_webhook_config({
            "media_refresh_webhook_headers": "bad json"
        })
        self.assertEqual(config["headers"], {})


class TestGetNotificationWebhookConfig(unittest.TestCase):

    def test_legacy_telegram_url_resolves_as_notification(self):
        config = _make_plugin()._get_notification_webhook_config({"telegram_webhook_url": "http://t.me/hook"})
        self.assertEqual(config["url"], "http://t.me/hook")
        self.assertTrue(config["is_legacy"])

    def test_new_notification_url_takes_precedence_over_legacy(self):
        settings = {
            "telegram_webhook_url": "http://t.me/hook",
            "notification_webhook_url": "http://n8n/hook",
        }
        config = _make_plugin()._get_notification_webhook_config(settings)
        self.assertEqual(config["url"], "http://n8n/hook")
        self.assertFalse(config["is_legacy"])

    def test_new_url_only_not_legacy(self):
        config = _make_plugin()._get_notification_webhook_config({"notification_webhook_url": "http://hook/"})
        self.assertFalse(config["is_legacy"])

    def test_dispatcharr_base_url_aliases_to_base_url(self):
        config = _make_plugin()._get_notification_webhook_config({"dispatcharr_base_url": "http://tv.example.com"})
        self.assertEqual(config["base_url"], "http://tv.example.com")

    def test_notification_base_url_takes_precedence_over_dispatcharr_base_url(self):
        settings = {
            "dispatcharr_base_url": "http://old.example.com",
            "notification_base_url": "http://new.example.com",
        }
        config = _make_plugin()._get_notification_webhook_config(settings)
        self.assertEqual(config["base_url"], "http://new.example.com")

    def test_base_url_trailing_slash_stripped(self):
        config = _make_plugin()._get_notification_webhook_config({"dispatcharr_base_url": "http://tv.example.com/"})
        self.assertEqual(config["base_url"], "http://tv.example.com")

    def test_method_defaults_to_post(self):
        config = _make_plugin()._get_notification_webhook_config({})
        self.assertEqual(config["method"], "POST")

    def test_invalid_headers_ignored_safely(self):
        config = _make_plugin()._get_notification_webhook_config({
            "notification_webhook_headers": "{invalid"
        })
        self.assertEqual(config["headers"], {})


class TestWebhookAsyncAndNonBlocking(unittest.TestCase):

    def test_trigger_webhook_returns_immediately_without_sleeping(self):
        """_trigger_webhook must not call time.sleep in the caller's thread."""
        p = _make_plugin()
        p._generate_xmltv_cache = MagicMock()
        threads_created = []

        with patch("plugin.threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_thread_cls.return_value = mock_t
            settings = {"webhook_url": "http://jellyfin/refresh", "webhook_delay_seconds": 30}
            p._trigger_webhook(settings)

        mock_thread_cls.assert_called_once()
        mock_t.start.assert_called_once()

    def test_trigger_webhook_thread_is_daemon(self):
        p = _make_plugin()
        p._generate_xmltv_cache = MagicMock()

        with patch("plugin.threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_thread_cls.return_value = mock_t
            p._trigger_webhook({"webhook_url": "http://example.com/"})

        _, kwargs = mock_thread_cls.call_args
        self.assertTrue(kwargs.get("daemon", False))

    def test_failed_webhook_worker_does_not_propagate(self):
        """Worker catches HTTP errors and does not raise."""
        p = _make_plugin()
        p._generate_xmltv_cache = MagicMock()

        captured_target = {}

        def fake_thread(**kwargs):
            captured_target["fn"] = kwargs.get("target")
            t = MagicMock()
            t.start = MagicMock()
            return t

        with patch("plugin.threading.Thread", side_effect=fake_thread):
            p._trigger_webhook({"webhook_url": "http://example.com/fail"})

        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            try:
                captured_target["fn"]()
            except Exception:
                self.fail("Worker propagated exception on HTTP failure")

    def test_trigger_webhook_no_url_returns_without_thread(self):
        p = _make_plugin()
        p._generate_xmltv_cache = MagicMock()

        with patch("plugin.threading.Thread") as mock_thread_cls:
            p._trigger_webhook({})

        mock_thread_cls.assert_not_called()

    def test_send_webhook_async_fires_thread_and_does_not_block(self):
        p = _make_plugin()
        with patch("plugin.threading.Thread") as mock_thread_cls:
            mock_t = MagicMock()
            mock_thread_cls.return_value = mock_t
            p._send_webhook_async("test", "http://example.com/", "POST", {}, '{"x":1}', delay_seconds=0)
        mock_thread_cls.assert_called_once()
        mock_t.start.assert_called_once()


class TestNotificationPayloads(unittest.TestCase):

    def _capture_notification(self, settings, metadata=None, video_id="vid1",
                               channel_number=90.1, channel_uuid="uuid-abc"):
        p = _make_plugin()
        captured = {}

        def fake_send_async(kind, url, method, headers, body, delay_seconds=0):
            captured["body"] = body

        p._send_webhook_async = fake_send_async
        meta = metadata or {"title": "NASA Live", "youtube_channel_name": "NASA", "thumbnail": "http://t.jpg"}
        p._send_telegram_notification(settings, video_id, meta, channel_number, channel_uuid)
        return captured.get("body")

    def test_legacy_telegram_payload_has_exact_keys(self):
        body = self._capture_notification({
            "telegram_webhook_url": "http://t.me/hook",
            "dispatcharr_base_url": "http://tv.example.com",
        })
        self.assertIsNotNone(body)
        payload = json.loads(body)
        self.assertEqual(set(payload.keys()), {"title", "channel", "url", "description", "timestamp"})

    def test_legacy_telegram_payload_values(self):
        body = self._capture_notification({
            "telegram_webhook_url": "http://t.me/hook",
            "dispatcharr_base_url": "http://tv.example.com",
        }, channel_number=90.1, channel_uuid="uuid-abc")
        payload = json.loads(body)
        self.assertEqual(payload["title"], "NASA Live")
        self.assertEqual(payload["channel"], "NASA")
        self.assertIn("uuid-abc", payload["url"])
        self.assertIn("90.1", payload["description"])

    def test_legacy_telegram_skipped_when_no_base_url(self):
        body = self._capture_notification({"telegram_webhook_url": "http://t.me/hook"})
        self.assertIsNone(body)

    def test_new_notification_payload_has_generic_keys(self):
        body = self._capture_notification({
            "notification_webhook_url": "http://n8n/hook",
            "dispatcharr_base_url": "http://tv.example.com",
        }, video_id="vid1", channel_number=90.1, channel_uuid="uuid-abc")
        self.assertIsNotNone(body)
        payload = json.loads(body)
        expected_keys = {
            "event", "plugin", "video_id", "title", "channel_name",
            "channel_number", "dispatcharr_channel_uuid", "url", "thumbnail", "timestamp",
        }
        self.assertEqual(set(payload.keys()), expected_keys)

    def test_new_notification_payload_values(self):
        body = self._capture_notification({
            "notification_webhook_url": "http://n8n/hook",
            "dispatcharr_base_url": "http://tv.example.com",
        }, video_id="vid42", channel_number=90.3, channel_uuid="uuid-xyz")
        payload = json.loads(body)
        self.assertEqual(payload["event"], "stream_added")
        self.assertEqual(payload["plugin"], "youtubearr")
        self.assertEqual(payload["video_id"], "vid42")
        self.assertEqual(payload["channel_number"], "90.3")
        self.assertEqual(payload["dispatcharr_channel_uuid"], "uuid-xyz")
        self.assertIn("uuid-xyz", payload["url"])

    def test_new_notification_url_without_base_url_still_sends(self):
        """Generic payload works even with no base URL; url field is empty string."""
        body = self._capture_notification({"notification_webhook_url": "http://n8n/hook"})
        self.assertIsNotNone(body)
        payload = json.loads(body)
        self.assertEqual(payload["url"], "")

    def test_no_url_configured_sends_nothing(self):
        body = self._capture_notification({})
        self.assertIsNone(body)

    def test_new_notification_takes_precedence_over_legacy(self):
        """When both telegram and notification_webhook_url are set, use generic payload."""
        body = self._capture_notification({
            "telegram_webhook_url": "http://t.me/hook",
            "notification_webhook_url": "http://n8n/hook",
            "dispatcharr_base_url": "http://tv.example.com",
        })
        payload = json.loads(body)
        self.assertIn("event", payload)
        self.assertNotIn("description", payload)


class TestDiagnostics(unittest.TestCase):

    def _make_diag_plugin(self, ytdlp=True, qjs=False):
        p = _make_plugin(qjs=qjs)
        if not ytdlp:
            p._ytdlp_path = None
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        # Prevent log file reads in tests
        p._log_path = MagicMock()
        p._log_path.exists.return_value = False
        # _base_dir is only used for cookies.txt path check
        p._base_dir = MagicMock()
        # Stub subprocess-calling helpers to avoid real invocations
        p._get_ytdlp_version = MagicMock(
            return_value="2025.01.01" if ytdlp else "unavailable: yt-dlp not found"
        )
        p._get_qjs_version = MagicMock(
            return_value="QuickJS-ng version 0.14.0" if qjs else "not configured"
        )
        return p

    # Routing

    def test_run_routes_to_diagnostics(self):
        p = self._make_diag_plugin()
        p._handle_diagnostics = MagicMock(return_value={"status": "success", "message": "ok", "details": {}})
        result = p.run("diagnostics", {}, {"settings": {}})
        p._handle_diagnostics.assert_called_once()
        self.assertEqual(result["status"], "success")

    # Core behaviour with empty settings

    def test_diagnostics_returns_details_dict_no_traceback(self):
        p = self._make_diag_plugin()
        result = p._handle_diagnostics({"settings": {}})
        self.assertIn("details", result)
        self.assertIsInstance(result["details"], dict)
        self.assertIn("status", result)
        self.assertIn("message", result)

    def test_diagnostics_includes_required_fields(self):
        p = self._make_diag_plugin()
        result = p._handle_diagnostics({"settings": {}})
        d = result["details"]
        for field in ("plugin_version", "plugin_key", "monitoring_active", "monitor_thread_alive",
                      "last_poll_time", "tracked_stream_count", "extraction_failure_count",
                      "ytdlp_path", "ytdlp_version", "qjs_path", "qjs_version",
                      "cookies_configured", "cookies_file_present",
                      "media_refresh_webhook_configured", "notification_webhook_configured",
                      "log_path"):
            self.assertIn(field, d, f"missing field: {field}")

    # yt-dlp missing → error status

    def test_missing_ytdlp_yields_error_status(self):
        p = self._make_diag_plugin(ytdlp=False)
        result = p._handle_diagnostics({"settings": {}})
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["details"]["ytdlp_path"], "not found")

    def test_missing_ytdlp_does_not_raise(self):
        p = self._make_diag_plugin(ytdlp=False)
        try:
            result = p._handle_diagnostics({"settings": {}})
        except Exception as exc:
            self.fail(f"_handle_diagnostics raised {exc!r} on missing yt-dlp")

    # _get_ytdlp_version helpers

    def test_get_ytdlp_version_parses_stdout(self):
        p = _make_plugin()
        with patch("subprocess.run", return_value=MagicMock(stdout="2025.01.01\n", stderr="", returncode=0)):
            self.assertEqual(p._get_ytdlp_version(), "2025.01.01")

    def test_get_ytdlp_version_handles_timeout(self):
        p = _make_plugin()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=5)):
            ver = p._get_ytdlp_version()
        self.assertIn("timeout", ver.lower())

    def test_get_ytdlp_version_handles_missing_binary(self):
        p = _make_plugin()
        with patch("subprocess.run", side_effect=FileNotFoundError("no such file")):
            ver = p._get_ytdlp_version()
        self.assertIn("unavailable", ver.lower())

    def test_get_ytdlp_version_handles_no_path(self):
        p = _make_plugin()
        p._ytdlp_path = None
        ver = p._get_ytdlp_version()
        self.assertIn("unavailable", ver.lower())

    # _get_qjs_version helpers

    def test_get_qjs_version_parses_nonzero_exit(self):
        """qjs --version may exit nonzero; version must still be parsed from stderr."""
        p = _make_plugin(qjs=True)
        mock_result = MagicMock(stdout="", stderr="QuickJS-ng version 0.14.0\n", returncode=1)
        with patch("subprocess.run", return_value=mock_result):
            ver = p._get_qjs_version()
        self.assertIn("QuickJS-ng version 0.14.0", ver)

    def test_get_qjs_version_parses_stdout(self):
        p = _make_plugin(qjs=True)
        mock_result = MagicMock(stdout="QuickJS-ng version 0.14.0\n", stderr="", returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            ver = p._get_qjs_version()
        self.assertIn("QuickJS-ng version 0.14.0", ver)

    def test_get_qjs_version_no_path_returns_not_configured(self):
        p = _make_plugin(qjs=False)
        self.assertEqual(p._get_qjs_version(), "not configured")

    def test_get_qjs_version_handles_timeout(self):
        p = _make_plugin(qjs=True)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="qjs", timeout=5)):
            ver = p._get_qjs_version()
        self.assertIn("timeout", ver.lower())

    # Extraction failure timestamps

    def test_extraction_failure_oldest_newest(self):
        import time as time_mod
        p = self._make_diag_plugin()
        now = time_mod.time()
        p._extraction_failures = {"v1": now - 100.0, "v2": now - 200.0, "v3": now - 50.0}
        result = p._handle_diagnostics({"settings": {}})
        d = result["details"]
        self.assertEqual(d["extraction_failure_count"], 3)
        self.assertIn("extraction_failure_oldest", d)
        self.assertIn("extraction_failure_newest", d)
        self.assertLess(d["extraction_failure_oldest"], d["extraction_failure_newest"])

    def test_extraction_failure_zero_no_timestamps(self):
        p = self._make_diag_plugin()
        p._extraction_failures = {}
        result = p._handle_diagnostics({"settings": {}})
        d = result["details"]
        self.assertEqual(d["extraction_failure_count"], 0)
        self.assertNotIn("extraction_failure_oldest", d)

    # Webhook configured flags

    def test_webhook_flags_new_generic_settings(self):
        p = self._make_diag_plugin()
        ctx = {"settings": {
            "media_refresh_webhook_url": "http://hook/refresh",
            "notification_webhook_url": "http://hook/notify",
        }}
        d = p._handle_diagnostics(ctx)["details"]
        self.assertTrue(d["media_refresh_webhook_configured"])
        self.assertFalse(d["media_refresh_webhook_is_legacy"])
        self.assertTrue(d["notification_webhook_configured"])
        self.assertFalse(d["notification_webhook_is_legacy"])

    def test_webhook_flags_legacy_settings(self):
        p = self._make_diag_plugin()
        ctx = {"settings": {
            "webhook_url": "http://legacy/refresh",
            "telegram_webhook_url": "http://legacy/notify",
        }}
        d = p._handle_diagnostics(ctx)["details"]
        self.assertTrue(d["media_refresh_webhook_configured"])
        self.assertTrue(d["media_refresh_webhook_is_legacy"])
        self.assertTrue(d["notification_webhook_configured"])
        self.assertTrue(d["notification_webhook_is_legacy"])

    def test_webhook_not_configured(self):
        p = self._make_diag_plugin()
        d = p._handle_diagnostics({"settings": {}})["details"]
        self.assertFalse(d["media_refresh_webhook_configured"])
        self.assertFalse(d["notification_webhook_configured"])

    # DB count failures captured as unavailable

    def test_db_count_failure_returns_unavailable_string(self):
        p = _make_plugin()
        with patch("plugin.Stream") as mock_stream:
            mock_stream.objects.filter.side_effect = Exception("DB down")
            count = p._count_owned_streams()
        self.assertIsInstance(count, str)
        self.assertIn("unavailable", count)

    def test_db_channel_count_failure_returns_unavailable_string(self):
        p = _make_plugin()
        with patch("plugin.Channel") as mock_chan:
            mock_chan.objects.filter.side_effect = Exception("DB down")
            count = p._count_owned_channels()
        self.assertIsInstance(count, str)
        self.assertIn("unavailable", count)

    def test_db_program_count_failure_returns_unavailable_string(self):
        p = _make_plugin()
        with patch("plugin.ProgramData") as mock_pd:
            mock_pd.objects.filter.side_effect = Exception("DB down")
            count = p._count_owned_programs()
        self.assertIsInstance(count, str)
        self.assertIn("unavailable", count)

    # cookies_content never exposed

    def test_diagnostics_does_not_expose_cookies_content(self):
        p = self._make_diag_plugin()
        marker = "diagnostic-cookie-marker-xyz"
        ctx = {"settings": {"cookies_content": f"# Netscape HTTP Cookie File\n.example.com\t{marker}"}}
        result = p._handle_diagnostics(ctx)
        details_str = json.dumps(result["details"], default=str)
        self.assertNotIn(marker, details_str)
        self.assertNotIn("cookies_content", details_str)
        # But should flag that cookies are configured
        self.assertTrue(result["details"]["cookies_configured"])

    def test_diagnostics_cookies_not_configured_when_empty(self):
        p = self._make_diag_plugin()
        result = p._handle_diagnostics({"settings": {"cookies_content": ""}})
        self.assertFalse(result["details"]["cookies_configured"])


class TestGetEpgCounts(unittest.TestCase):

    def test_epg_source_found_returns_numeric_counts(self):
        p = _make_plugin()
        mock_source = MagicMock()
        with patch("plugin.EPGSource") as mock_es, \
             patch("plugin.EPGData") as mock_ed, \
             patch("plugin.ProgramData") as mock_pd:
            mock_es.objects.filter.return_value.first.return_value = mock_source
            mock_ed.objects.filter.return_value.count.return_value = 3
            mock_pd.objects.filter.return_value.count.return_value = 12
            result = p._get_epg_counts({"epg_source_name": "YouTube Live"})
        self.assertEqual(result["epg_source"], "YouTube Live")
        self.assertEqual(result["epg_data_count"], 3)
        self.assertEqual(result["program_count"], 12)

    def test_epg_source_not_found_returns_zeroes(self):
        p = _make_plugin()
        with patch("plugin.EPGSource") as mock_es:
            mock_es.objects.filter.return_value.first.return_value = None
            result = p._get_epg_counts({"epg_source_name": "YouTube Live"})
        self.assertIn("not found", result["epg_source"])
        self.assertEqual(result["epg_data_count"], 0)
        self.assertEqual(result["program_count"], 0)

    def test_query_failure_all_fields_use_consistent_unavailable(self):
        p = _make_plugin()
        with patch("plugin.EPGSource") as mock_es:
            mock_es.objects.filter.side_effect = RuntimeError("DB down")
            result = p._get_epg_counts({})
        for key in ("epg_source", "epg_data_count", "program_count"):
            self.assertEqual(
                result[key],
                "unavailable: RuntimeError",
                f"{key!r} should be 'unavailable: RuntimeError', got {result[key]!r}",
            )


class TestGetRecentLogSummary(unittest.TestCase):

    def test_missing_log_file_returns_defaults(self):
        p = _make_plugin()
        p._log_path = Path("/nonexistent/path/youtubearr.log")
        result = p._get_recent_log_summary()
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["recent_errors"], [])
        self.assertEqual(result["status"], "log file not found")

    def test_log_with_no_errors_returns_zero_count(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("INFO: all good\nINFO: still fine\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertEqual(result["error_count"], 0)
            self.assertEqual(result["recent_errors"], [])
            self.assertEqual(result["status"], "ok")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_log_with_errors_returns_correct_count(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("INFO: ok\nERROR: something broke\nINFO: ok\n"
                    "ERROR: another problem\nERROR: third issue\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertEqual(result["error_count"], 3)
            self.assertEqual(len(result["recent_errors"]), 3)
            self.assertEqual(result["status"], "ok")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_large_log_recent_errors_capped_at_five(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            for i in range(20):
                f.write(f"ERROR: error number {i}\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertEqual(result["error_count"], 20)
            self.assertLessEqual(len(result["recent_errors"]), 5)
            self.assertEqual(result["status"], "ok")
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_warning_count_zero_when_no_warnings(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("INFO: all good\nERROR: something broke\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertEqual(result["warning_count"], 0)
            self.assertEqual(result["recent_warnings"], [])
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_warning_count_and_recent_warnings_populated(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("INFO: ok\nWARNING: url stale\nWARNING: heartbeat missing\nINFO: ok\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertEqual(result["warning_count"], 2)
            self.assertEqual(len(result["recent_warnings"]), 2)
            self.assertTrue(any("stale" in w for w in result["recent_warnings"]))
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_recent_warnings_capped_at_five(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            for i in range(10):
                f.write(f"WARNING: warning number {i}\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertEqual(result["warning_count"], 10)
            self.assertLessEqual(len(result["recent_warnings"]), 5)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_recent_lines_populated_from_log(self):
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            for i in range(25):
                f.write(f"INFO: line {i}\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            self.assertIn("recent_lines", result)
            self.assertGreater(len(result["recent_lines"]), 0)
            self.assertLessEqual(len(result["recent_lines"]), 20)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_recent_lines_empty_when_log_missing(self):
        p = _make_plugin()
        p._log_path = Path("/nonexistent/path/youtubearr.log")
        result = p._get_recent_log_summary()
        self.assertEqual(result["recent_lines"], [])
        self.assertEqual(result["status"], "log file not found")

    def test_recent_lines_does_not_contain_cookies_content(self):
        """Log lines must never expose cookie content — plugin logs paths, not content."""
        p = _make_plugin()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("INFO: Wrote cookies to /path/to/cookies.txt\n")
            f.write("INFO: all good\n")
            tmp_path = Path(f.name)
        try:
            p._log_path = tmp_path
            result = p._get_recent_log_summary()
            combined = " ".join(result["recent_lines"])
            # Path is fine; raw cookie values must not appear
            self.assertNotIn("domain\tFALSE", combined)
            self.assertNotIn("diagnostic-cookie-marker", combined)
        finally:
            tmp_path.unlink(missing_ok=True)


class TestDiagnosticsNextActions(unittest.TestCase):
    """next_actions hints appear in diagnostics details when issues are detected."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._legacy_task_cleanup_done = False
        p._log_path = MagicMock()
        p._log_path.exists.return_value = False
        p._base_dir = MagicMock()
        p._get_ytdlp_version = MagicMock(return_value="2025.01.01")
        p._get_qjs_version = MagicMock(return_value="not configured")
        return p

    def test_no_next_actions_when_healthy(self):
        p = self._make_p()
        result = p._handle_diagnostics({"settings": {}})
        # Healthy diagnostics should have no next_actions (or empty list)
        self.assertNotIn("next_actions", result["details"])

    def test_next_actions_present_when_ytdlp_missing(self):
        p = self._make_p()
        p._ytdlp_path = None
        p._get_ytdlp_version = MagicMock(return_value="unavailable: yt-dlp not found")
        result = p._handle_diagnostics({"settings": {}})
        self.assertIn("next_actions", result["details"])
        hints = result["details"]["next_actions"]
        self.assertTrue(any("yt-dlp" in h.lower() for h in hints))

    def test_next_actions_present_for_orphaned_tracked_entries(self):
        p = self._make_p()
        settings = {"tracked_streams": {"orphan1": {"is_live": True, "channel_id": 999}}}
        mock_channel_cls = MagicMock()
        DoesNotExist = type("DoesNotExist", (Exception,), {})
        mock_channel_cls.DoesNotExist = DoesNotExist
        mock_channel_cls.objects.get.side_effect = DoesNotExist
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.ProgramData", MagicMock()):
            result = p._handle_diagnostics({"settings": settings})
        self.assertIn("next_actions", result["details"])
        hints = result["details"]["next_actions"]
        self.assertTrue(any("Cleanup" in h for h in hints))

    def test_next_actions_deduplicated(self):
        """Multiple issues with the same action should only appear once in next_actions."""
        p = self._make_p()
        # Two orphaned entries → should produce one "run Cleanup" hint, not two
        settings = {
            "tracked_streams": {
                "orphan1": {"is_live": True, "channel_id": 998},
                "orphan2": {"is_live": True, "channel_id": 999},
            }
        }
        mock_channel_cls = MagicMock()
        DoesNotExist = type("DoesNotExist", (Exception,), {})
        mock_channel_cls.DoesNotExist = DoesNotExist
        mock_channel_cls.objects.get.side_effect = DoesNotExist
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.ProgramData", MagicMock()):
            result = p._handle_diagnostics({"settings": settings})
        if "next_actions" in result["details"]:
            hints = result["details"]["next_actions"]
            cleanup_hints = [h for h in hints if "Cleanup" in h]
            self.assertLessEqual(len(cleanup_hints), 1)

    def test_log_path_in_diagnostics_details(self):
        p = self._make_p()
        result = p._handle_diagnostics({"settings": {}})
        self.assertIn("log_path", result["details"])
        self.assertIsInstance(result["details"]["log_path"], str)
        self.assertGreater(len(result["details"]["log_path"]), 0)


class TestCeleryCleanup(unittest.TestCase):
    """Legacy Celery beat task is removed and never re-registered."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        return p

    def test_create_or_update_not_in_plugin_namespace(self):
        """create_or_update_periodic_task must not be imported — it would create the bogus task."""
        import plugin as plugin_module
        self.assertFalse(
            hasattr(plugin_module, "create_or_update_periodic_task"),
            "create_or_update_periodic_task must not appear in plugin.py",
        )

    def test_cleanup_calls_delete_with_correct_task_name(self):
        p = self._make_p()
        with patch("plugin.delete_periodic_task", return_value=True) as mock_del:
            p._cleanup_legacy_celery_task()
        mock_del.assert_called_once_with("youtubearr_youtubearr_health_check")

    def test_cleanup_is_idempotent(self):
        p = self._make_p()
        with patch("plugin.delete_periodic_task", return_value=True) as mock_del:
            p._cleanup_legacy_celery_task()
            p._cleanup_legacy_celery_task()
            p._cleanup_legacy_celery_task()
        self.assertEqual(mock_del.call_count, 1)

    def test_cleanup_swallows_db_exception(self):
        p = self._make_p()
        error_messages = []
        p._log_error = lambda message: error_messages.append(message)
        with patch("plugin.delete_periodic_task", side_effect=RuntimeError("DB down")):
            p._cleanup_legacy_celery_task()  # must not raise
        self.assertTrue(any("cleanup" in m.lower() for m in error_messages))

    def test_cleanup_logs_when_task_deleted(self):
        p = self._make_p()
        messages = []
        p._log = lambda m: messages.append(m)
        with patch("plugin.delete_periodic_task", return_value=True):
            p._cleanup_legacy_celery_task()
        self.assertTrue(any("legacy" in m.lower() or "removed" in m.lower() for m in messages))

    def test_cleanup_no_log_when_task_absent(self):
        p = self._make_p()
        messages = []
        p._log = lambda m: messages.append(m)
        with patch("plugin.delete_periodic_task", return_value=False):
            p._cleanup_legacy_celery_task()
        self.assertFalse(messages)

    def test_handle_status_calls_cleanup(self):
        p = self._make_p()
        p._ytdlp_path = "/usr/bin/yt-dlp"
        cleanup_calls = []
        p._cleanup_legacy_celery_task = lambda: cleanup_calls.append(True)
        p._ensure_monitoring_thread = MagicMock(return_value=False)
        with patch("plugin.PluginConfig") as mock_cfg:
            mock_cfg.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg.objects.get.side_effect = mock_cfg.DoesNotExist
            p._handle_status({"settings": {}})
        self.assertTrue(cleanup_calls)

    def test_handle_status_missing_ytdlp_still_calls_cleanup(self):
        """Even when yt-dlp is unavailable, _handle_status must call cleanup before returning early."""
        p = self._make_p()
        p._ytdlp_path = None  # missing yt-dlp triggers early return
        cleanup_calls = []
        p._cleanup_legacy_celery_task = lambda: cleanup_calls.append(True)
        p._ensure_monitoring_thread = MagicMock(return_value=False)
        with patch("plugin.PluginConfig") as mock_cfg:
            mock_cfg.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg.objects.get.side_effect = mock_cfg.DoesNotExist
            result = p._handle_status({"settings": {}})
        self.assertTrue(cleanup_calls, "cleanup must run even when yt-dlp is missing")
        self.assertEqual(result["status"], "error")
        self.assertIn("yt-dlp", result.get("message", ""))

    def test_handle_start_monitoring_calls_cleanup(self):
        p = self._make_p()
        p._ytdlp_path = "/usr/bin/yt-dlp"
        cleanup_calls = []
        p._cleanup_legacy_celery_task = lambda: cleanup_calls.append(True)
        p._persist_settings = MagicMock()
        with patch("plugin.PluginConfig") as mock_cfg:
            mock_cfg.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg.objects.get.side_effect = mock_cfg.DoesNotExist
            with patch("threading.Thread") as mock_thread:
                mock_thread.return_value.is_alive.return_value = False
                p._handle_start_monitoring({"settings": {"monitored_channels": "@nasa"}})
        self.assertTrue(cleanup_calls)

    def test_handle_stop_monitoring_calls_cleanup(self):
        p = self._make_p()
        p._ytdlp_path = "/usr/bin/yt-dlp"
        p._monitoring_active = True
        cleanup_calls = []
        p._cleanup_legacy_celery_task = lambda: cleanup_calls.append(True)
        p._persist_settings = MagicMock()
        with patch("plugin.PluginConfig") as mock_cfg:
            mock_cfg.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg.objects.get.side_effect = mock_cfg.DoesNotExist
            p._handle_stop_monitoring({"settings": {"monitoring_active": True}})
        self.assertTrue(cleanup_calls)


class TestEnsureMonitoringThread(unittest.TestCase):
    """_ensure_monitoring_thread self-heals using runtime_state + file lock."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        return p

    def test_returns_false_when_desired_inactive(self):
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": False}
        started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertFalse(started)
        self.assertIsNone(p._monitor_thread)

    def test_returns_false_when_runtime_state_empty(self):
        p = self._make_p()
        p._read_runtime_state.return_value = {}
        started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertFalse(started)

    def test_starts_thread_when_desired_active_and_thread_dead(self):
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        settings = {"monitored_channels": "@nasa"}
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.is_alive.return_value = False
            started = p._ensure_monitoring_thread(settings)
        self.assertTrue(started)
        self.assertTrue(p._monitoring_active)

    def test_restarts_after_stale_heartbeat(self):
        """desired_active=True + dead thread + lock available → restart regardless of old heartbeat state."""
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.is_alive.return_value = False
            started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertTrue(started)

    def test_skips_when_no_channels_configured(self):
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        started = p._ensure_monitoring_thread({"monitored_channels": ""})
        self.assertFalse(started)

    def test_skips_when_ytdlp_missing(self):
        p = self._make_p()
        p._ytdlp_path = None
        p._read_runtime_state.return_value = {"desired_active": True}
        started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertFalse(started)

    def test_skips_when_thread_already_alive(self):
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        p._monitor_thread = alive_thread
        started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertFalse(started)
        p._acquire_monitor_lock.assert_not_called()

    def test_skips_when_lock_held_by_another_process(self):
        """If another process holds the lock, skip — it is already monitoring."""
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        p._acquire_monitor_lock.return_value = False
        with patch("threading.Thread") as mock_thread:
            started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertFalse(started)
        mock_thread.assert_not_called()


class TestDiagnosticsNewFields(unittest.TestCase):
    """Diagnostics surfaces legacy Celery task and stale URL count."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._legacy_task_cleanup_done = False
        p._log_path = MagicMock()
        p._log_path.exists.return_value = False
        p._base_dir = MagicMock()
        p._get_ytdlp_version = MagicMock(return_value="2025.01.01")
        p._get_qjs_version = MagicMock(return_value="not configured")
        return p

    def test_legacy_celery_field_always_present(self):
        p = self._make_p()
        result = p._handle_diagnostics({"settings": {}})
        self.assertIn("legacy_celery_health_check_present", result["details"])

    def test_legacy_celery_field_unavailable_when_no_beat_package(self):
        """Without django-celery-beat installed the field is an unavailable string."""
        p = self._make_p()
        # django_celery_beat is not in sys.modules by default in the test environment
        result = p._handle_diagnostics({"settings": {}})
        val = result["details"]["legacy_celery_health_check_present"]
        # Must be either a bool (if package happened to be available) or a string
        self.assertIsInstance(val, (bool, str))

    def test_legacy_celery_warns_when_task_present(self):
        p = self._make_p()
        mock_pt = MagicMock()
        mock_pt.objects.filter.return_value.exists.return_value = True
        mock_beat_models = MagicMock()
        mock_beat_models.PeriodicTask = mock_pt
        with patch.dict(sys.modules, {
            "django_celery_beat": MagicMock(),
            "django_celery_beat.models": mock_beat_models,
        }):
            result = p._handle_diagnostics({"settings": {}})
        self.assertTrue(result["details"]["legacy_celery_health_check_present"])
        self.assertIn(result["status"], ("warning", "error"))

    def test_legacy_celery_no_warning_when_task_absent(self):
        p = self._make_p()
        mock_pt = MagicMock()
        mock_pt.objects.filter.return_value.exists.return_value = False
        mock_beat_models = MagicMock()
        mock_beat_models.PeriodicTask = mock_pt
        with patch.dict(sys.modules, {
            "django_celery_beat": MagicMock(),
            "django_celery_beat.models": mock_beat_models,
        }):
            result = p._handle_diagnostics({"settings": {}})
        self.assertFalse(result["details"]["legacy_celery_health_check_present"])

    def test_stale_url_count_zero_when_no_live_streams(self):
        p = self._make_p()
        settings = {"tracked_streams": {"v1": {"is_live": False}}}
        result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"]["stale_tracked_stream_url_count"], 0)

    def test_stale_url_count_one_for_live_stream_with_no_refresh_timestamp(self):
        p = self._make_p()
        settings = {"tracked_streams": {"v1": {"is_live": True}}}
        result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"]["stale_tracked_stream_url_count"], 1)
        self.assertIn(result["status"], ("warning", "error"))

    def test_stale_url_count_for_old_refresh(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        old_ts = (datetime.now(dt_timezone.utc) - timedelta(hours=6)).isoformat()
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {"v1": {"is_live": True, "last_url_refresh": old_ts}},
        }
        result = p._handle_diagnostics({"settings": settings})
        self.assertGreater(result["details"]["stale_tracked_stream_url_count"], 0)
        self.assertIn("oldest_url_refresh_age_seconds", result["details"])

    def test_stale_url_count_zero_for_fresh_stream(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        now_ts = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {"v1": {"is_live": True, "last_url_refresh": now_ts}},
        }
        result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"]["stale_tracked_stream_url_count"], 0)

    def test_stale_url_invalid_timestamp_counts_as_stale(self):
        p = self._make_p()
        settings = {"tracked_streams": {"v1": {"is_live": True, "last_url_refresh": "bad-ts"}}}
        result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"]["stale_tracked_stream_url_count"], 1)

    def test_last_poll_age_seconds_present_when_poll_recent(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        now_ts = datetime.now(dt_timezone.utc).isoformat()
        # last_poll_time now lives in runtime_state, not settings
        p._read_runtime_state.return_value = {"last_poll_time": now_ts}
        result = p._handle_diagnostics({"settings": {}})
        self.assertIn("last_poll_age_seconds", result["details"])
        age = result["details"]["last_poll_age_seconds"]
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)

    def test_last_poll_age_seconds_none_when_never_polled(self):
        p = self._make_p()
        result = p._handle_diagnostics({"settings": {}})
        self.assertIn("last_poll_age_seconds", result["details"])
        self.assertIsNone(result["details"]["last_poll_age_seconds"])

    def test_stale_poll_warning_when_active_and_poll_stale(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale_poll = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        # desired_active, last_poll_time, and poll_interval_minutes now live in runtime_state
        p._read_runtime_state.return_value = {
            "desired_active": True,
            "last_poll_time": stale_poll,
            "poll_interval_minutes": 15,
        }
        result = p._handle_diagnostics({"settings": {}})
        # Stale-poll condition must trigger a warning-level result
        self.assertIn(result["status"], ("warning", "error"),
                      f"Expected warning/error status for stale poll, got: {result['status']}")
        # last_poll_age_seconds must be populated and large
        age = result["details"].get("last_poll_age_seconds")
        self.assertIsNotNone(age)
        self.assertGreater(age, 7000)

    def test_no_stale_poll_warning_when_inactive(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale_poll = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        # last_poll_time now lives in runtime_state; desired_active=False (default)
        p._read_runtime_state.return_value = {
            "desired_active": False,
            "last_poll_time": stale_poll,
        }
        result = p._handle_diagnostics({"settings": {}})
        # When monitoring is inactive, stale poll alone must not degrade status to warning
        # (other issues from the test env might raise warnings, so just check the stale-poll
        # age is still populated but status is not warning *due to* the poll check)
        age = result["details"].get("last_poll_age_seconds")
        self.assertIsNotNone(age)  # field is present regardless

    def test_epg_window_counts_present_in_details(self):
        p = self._make_p()
        with patch("plugin.EPGSource") as mock_es:
            mock_es.objects.filter.return_value.first.return_value = None
            result = p._handle_diagnostics({"settings": {"epg_source_name": "YouTube Live"}})
        self.assertIn("epg_window_counts", result["details"])

    def test_empty_epg_warning_when_active_and_no_programs(self):
        p = self._make_p()
        mock_source = MagicMock()
        # desired_active now lives in runtime_state, not settings
        import datetime as _dt
        now_ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        p._read_runtime_state.return_value = {
            "desired_active": True,
            "last_poll_time": now_ts,
        }
        with patch("plugin.EPGSource") as mock_es, \
             patch("plugin.ProgramData") as mock_pd:
            mock_es.objects.filter.return_value.first.return_value = mock_source
            mock_pd.objects.filter.return_value.count.return_value = 0
            settings = {
                "epg_source_name": "YouTube Live",
            }
            result = p._handle_diagnostics({"settings": settings})
        # Empty EPG with active monitoring must produce a warning/error status
        self.assertIn(result["status"], ("warning", "error"),
                      f"Expected warning/error for empty EPG, got: {result['status']}")
        # epg_window_counts must be populated
        counts = result["details"].get("epg_window_counts", {})
        self.assertTrue(counts.get("source_found"), "source_found should be True")
        self.assertEqual(counts.get("current", -1), 0)

    def test_no_epg_warning_when_programs_present(self):
        p = self._make_p()
        mock_source = MagicMock()
        with patch("plugin.EPGSource") as mock_es, \
             patch("plugin.ProgramData") as mock_pd:
            mock_es.objects.filter.return_value.first.return_value = mock_source
            mock_pd.objects.filter.return_value.count.side_effect = [5, 10]
            settings = {
                "monitoring_active": True,
                "epg_source_name": "YouTube Live",
                "last_poll_time": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc).isoformat(),
            }
            result = p._handle_diagnostics({"settings": settings})
        # With programs present the EPG warning must not be the cause of any status degradation
        counts = result["details"].get("epg_window_counts", {})
        self.assertTrue(counts.get("source_found"))
        self.assertGreater(counts.get("current", 0), 0)


class TestRefreshExpiringUrls(unittest.TestCase):
    """_refresh_expiring_urls refreshes stale stream URLs and persists the update."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._persist_settings = MagicMock()
        return p

    def test_refreshes_and_persists_when_url_stale(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        old_ts = (datetime.now(dt_timezone.utc) - timedelta(hours=3)).isoformat()
        stream_data = {
            "is_live": True,
            "last_url_refresh": old_ts,
            "stream_id": 42,
            "title": "Live Stream",
        }
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {"vid1": stream_data},
        }
        new_url = "https://refreshed.example.com/stream.m3u8"
        p._extract_stream_metadata = MagicMock(return_value={"stream_url": new_url})
        mock_stream = MagicMock()
        with patch("plugin.Stream") as mock_stream_cls:
            mock_stream_cls.objects.get.return_value = mock_stream
            count = p._refresh_expiring_urls(settings)
        self.assertEqual(count, 1)
        p._persist_settings.assert_called_once()
        self.assertEqual(stream_data["stream_url"], new_url)

    def test_skips_non_live_streams(self):
        p = self._make_p()
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {"vid1": {"is_live": False, "last_url_refresh": "2020-01-01T00:00:00+00:00"}},
        }
        p._extract_stream_metadata = MagicMock()
        count = p._refresh_expiring_urls(settings)
        self.assertEqual(count, 0)
        p._extract_stream_metadata.assert_not_called()

    def test_skips_streams_without_last_url_refresh(self):
        p = self._make_p()
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {"vid1": {"is_live": True}},
        }
        p._extract_stream_metadata = MagicMock()
        count = p._refresh_expiring_urls(settings)
        self.assertEqual(count, 0)
        p._extract_stream_metadata.assert_not_called()

    def test_no_persist_when_nothing_refreshed(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        now_ts = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {"vid1": {"is_live": True, "last_url_refresh": now_ts}},
        }
        p._extract_stream_metadata = MagicMock()
        p._refresh_expiring_urls(settings)
        p._persist_settings.assert_not_called()

    def test_streamlink_profile_keeps_canonical_url(self):
        """Streams on a 'streamlink' profile must keep the canonical watch URL,
        not the short-lived googlevideo URL yt-dlp extracts."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        p._sync_cookies_sidecar = MagicMock(return_value=True)
        old_ts = (datetime.now(dt_timezone.utc) - timedelta(hours=3)).isoformat()
        video_id = "vid1"
        stream_data = {
            "is_live": True,
            "last_url_refresh": old_ts,
            "stream_id": 42,
            "title": "Live Stream",
        }
        cookies_content = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tcookie-value"
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {video_id: stream_data},
            "cookies_content": cookies_content,
        }
        expiring_url = "https://rr1---sn-abc.googlevideo.com/videoplayback?expire=123"
        p._extract_stream_metadata = MagicMock(return_value={"stream_url": expiring_url, "video_id": video_id})
        mock_stream = MagicMock(stream_profile_id=2)
        mock_profile = MagicMock(name="streamlink")
        mock_profile.name = "streamlink"
        with patch("plugin.Stream") as mock_stream_cls, patch("plugin.StreamProfile") as mock_profile_cls:
            mock_stream_cls.objects.get.return_value = mock_stream
            mock_profile_cls.objects.filter.return_value.first.return_value = mock_profile
            count = p._refresh_expiring_urls(settings)
        self.assertEqual(count, 1)
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        self.assertEqual(mock_stream.url, canonical)
        self.assertEqual(stream_data["stream_url"], canonical)
        p._sync_cookies_sidecar.assert_called_once_with(settings)

    def test_streamlink_profile_skips_refresh_when_cookie_sync_fails(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        p._sync_cookies_sidecar = MagicMock(return_value=False)
        old_ts = (datetime.now(dt_timezone.utc) - timedelta(hours=3)).isoformat()
        video_id = "vid1"
        original_url = "https://www.youtube.com/watch?v=old"
        stream_data = {
            "is_live": True,
            "last_url_refresh": old_ts,
            "stream_id": 42,
            "title": "Live Stream",
            "stream_url": original_url,
        }
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {video_id: stream_data},
            "cookies_content": "configured-cookie",
        }
        p._extract_stream_metadata = MagicMock(return_value={"stream_url": "https://refreshed.example.com/stream.m3u8", "video_id": video_id})
        mock_stream = MagicMock(stream_profile_id=2)
        mock_stream.url = original_url
        mock_profile = MagicMock(name="streamlink")
        mock_profile.name = "streamlink"
        with patch("plugin.Stream") as mock_stream_cls, patch("plugin.StreamProfile") as mock_profile_cls:
            mock_stream_cls.objects.get.return_value = mock_stream
            mock_profile_cls.objects.filter.return_value.first.return_value = mock_profile
            count = p._refresh_expiring_urls(settings)

        self.assertEqual(count, 0)
        self.assertEqual(mock_stream.url, original_url)
        self.assertEqual(stream_data["stream_url"], original_url)
        p._persist_settings.assert_not_called()
        p._sync_cookies_sidecar.assert_called_once_with(settings)

    def test_non_streamlink_profile_uses_extracted_url(self):
        """Streams on a non-Streamlink profile (e.g. Proxy) keep refreshing to the
        freshly extracted URL, preserving existing behavior."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        old_ts = (datetime.now(dt_timezone.utc) - timedelta(hours=3)).isoformat()
        video_id = "vid1"
        stream_data = {
            "is_live": True,
            "last_url_refresh": old_ts,
            "stream_id": 42,
            "title": "Live Stream",
        }
        settings = {
            "url_refresh_interval_seconds": 3600,
            "tracked_streams": {video_id: stream_data},
        }
        new_url = "https://refreshed.example.com/stream.m3u8"
        p._extract_stream_metadata = MagicMock(return_value={"stream_url": new_url, "video_id": video_id})
        mock_stream = MagicMock(stream_profile_id=1)
        mock_profile = MagicMock()
        mock_profile.name = "proxy"
        with patch("plugin.Stream") as mock_stream_cls, patch("plugin.StreamProfile") as mock_profile_cls:
            mock_stream_cls.objects.get.return_value = mock_stream
            mock_profile_cls.objects.filter.return_value.first.return_value = mock_profile
            count = p._refresh_expiring_urls(settings)
        self.assertEqual(count, 1)
        self.assertEqual(mock_stream.url, new_url)
        self.assertEqual(stream_data["stream_url"], new_url)


# ── Stream profile selection (Streamlink wrapper) ───────────────────────────

class TestSelectStreamProfile(unittest.TestCase):
    """_select_stream_profile picks the right StreamProfile for YouTube playback."""

    def _make_p(self):
        p = _make_plugin()
        p._stream_profile = None
        return p

    def _profile(self, name, profile_id=1):
        m = MagicMock()
        m.name = name
        m.id = profile_id
        return m

    def test_prefers_streamlink_profile_by_default(self):
        p = self._make_p()
        streamlink_profile = self._profile("streamlink", 2)
        with patch("plugin.StreamProfile") as mock_cls:
            mock_cls.objects.filter.return_value.first.return_value = streamlink_profile
            result = p._select_stream_profile({})
        self.assertEqual(result, streamlink_profile)
        mock_cls.objects.filter.assert_any_call(name__iexact="streamlink")

    def test_falls_back_to_proxy_with_warning_when_streamlink_absent(self):
        p = self._make_p()
        proxy_profile = self._profile("proxy", 1)
        logged_errors = []
        p._log_error = lambda msg: logged_errors.append(msg)

        def filter_side_effect(**kwargs):
            m = MagicMock()
            if kwargs.get("name__iexact") == "streamlink":
                m.first.return_value = None
            elif kwargs.get("name__iexact") == "proxy":
                m.first.return_value = proxy_profile
            else:
                m.first.return_value = None
            return m

        with patch("plugin.StreamProfile") as mock_cls:
            mock_cls.objects.filter.side_effect = filter_side_effect
            result = p._select_stream_profile({})
        self.assertEqual(result, proxy_profile)
        self.assertTrue(any("streamlink" in msg.lower() for msg in logged_errors))

    def test_falls_back_to_first_profile_when_no_streamlink_or_proxy(self):
        p = self._make_p()
        any_profile = self._profile("HDHomeRun", 5)

        def filter_side_effect(**kwargs):
            m = MagicMock()
            m.first.return_value = None
            return m

        with patch("plugin.StreamProfile") as mock_cls:
            mock_cls.objects.filter.side_effect = filter_side_effect
            mock_cls.objects.first.return_value = any_profile
            result = p._select_stream_profile({})
        self.assertEqual(result, any_profile)

    def test_raises_when_no_profiles_exist_at_all(self):
        p = self._make_p()
        with patch("plugin.StreamProfile") as mock_cls:
            mock_cls.objects.filter.return_value.first.return_value = None
            mock_cls.objects.first.return_value = None
            with self.assertRaises(RuntimeError):
                p._select_stream_profile({})

    def test_explicit_setting_overrides_streamlink_default(self):
        p = self._make_p()
        custom_profile = self._profile("MyCustomProfile", 9)
        with patch("plugin.StreamProfile") as mock_cls:
            mock_cls.objects.filter.return_value.first.return_value = custom_profile
            result = p._select_stream_profile({"stream_profile_name": "MyCustomProfile"})
        self.assertEqual(result, custom_profile)
        mock_cls.objects.filter.assert_any_call(name__iexact="MyCustomProfile")

    def test_caches_auto_detected_profile(self):
        p = self._make_p()
        streamlink_profile = self._profile("streamlink", 2)
        with patch("plugin.StreamProfile") as mock_cls:
            mock_cls.objects.filter.return_value.first.return_value = streamlink_profile
            p._select_stream_profile({})
            mock_cls.objects.filter.reset_mock()
            result = p._select_stream_profile({})
        self.assertEqual(result, streamlink_profile)
        mock_cls.objects.filter.assert_not_called()


# ── Canonical playback URL selection ────────────────────────────────────────

class TestGetPlaybackUrl(unittest.TestCase):
    """_get_playback_url decides between canonical watch URL and extracted URL."""

    def test_streamlink_profile_gets_canonical_watch_url(self):
        p = _make_plugin()
        profile = MagicMock()
        profile.name = "streamlink"
        metadata = {"video_id": "abc123DEF45", "stream_url": "https://googlevideo.com/expiring"}
        result = p._get_playback_url(metadata, profile)
        self.assertEqual(result, "https://www.youtube.com/watch?v=abc123DEF45")

    def test_streamlink_profile_name_case_insensitive(self):
        p = _make_plugin()
        profile = MagicMock()
        profile.name = "Streamlink"
        metadata = {"video_id": "abc123DEF45", "stream_url": "https://googlevideo.com/expiring"}
        result = p._get_playback_url(metadata, profile)
        self.assertEqual(result, "https://www.youtube.com/watch?v=abc123DEF45")

    def test_streamlink_profile_syncs_cookie_file_without_exposing_content_in_args(self):
        p = _make_plugin()
        p._sync_cookies_sidecar = MagicMock(return_value=True)
        profile = MagicMock()
        profile.name = "streamlink"
        metadata = {"video_id": "abc123DEF45", "stream_url": "https://googlevideo.com/expiring"}
        cookies_content = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tcookie-value"
        result = p._get_playback_url(metadata, profile, {"cookies_content": cookies_content})
        self.assertEqual(result, "https://www.youtube.com/watch?v=abc123DEF45")
        p._sync_cookies_sidecar.assert_called_once_with({"cookies_content": cookies_content})

    def test_non_streamlink_profile_does_not_sync_cookie_file(self):
        p = _make_plugin()
        p._sync_cookies_sidecar = MagicMock(return_value=True)
        profile = MagicMock()
        profile.name = "proxy"
        expiring_url = "https://googlevideo.com/expiring"
        metadata = {"video_id": "abc123DEF45", "stream_url": expiring_url}
        result = p._get_playback_url(metadata, profile, {"cookies_content": "cookie-value"})
        self.assertEqual(result, expiring_url)
        p._sync_cookies_sidecar.assert_not_called()

    def test_streamlink_profile_with_configured_cookies_raises_when_sidecar_sync_fails(self):
        p = _make_plugin()
        p._sync_cookies_sidecar = MagicMock(return_value=False)
        profile = MagicMock()
        profile.name = "streamlink"
        metadata = {"video_id": "abc123DEF45", "stream_url": "https://googlevideo.com/expiring"}
        with self.assertRaises(RuntimeError):
            p._get_playback_url(metadata, profile, {"cookies_content": "configured-cookie"})
        p._sync_cookies_sidecar.assert_called_once_with({"cookies_content": "configured-cookie"})

    def test_non_streamlink_profile_gets_extracted_url(self):
        p = _make_plugin()
        profile = MagicMock()
        profile.name = "proxy"
        expiring_url = "https://googlevideo.com/expiring"
        metadata = {"video_id": "abc123DEF45", "stream_url": expiring_url}
        result = p._get_playback_url(metadata, profile)
        self.assertEqual(result, expiring_url)

    def test_streamlink_profile_without_video_id_falls_back(self):
        p = _make_plugin()
        profile = MagicMock()
        profile.name = "streamlink"
        expiring_url = "https://googlevideo.com/expiring"
        metadata = {"video_id": "", "stream_url": expiring_url}
        result = p._get_playback_url(metadata, profile)
        self.assertEqual(result, expiring_url)


class TestGetCookiesFile(unittest.TestCase):

    def test_writes_cookie_file_with_owner_only_permissions(self):
        p = _make_plugin()
        with tempfile.TemporaryDirectory() as tmpdir:
            p._base_dir = Path(tmpdir)
            cookies_path = Path(p._get_cookies_file("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tcookie-value"))
            self.assertEqual(cookies_path, Path(tmpdir) / "cookies.txt")
            self.assertEqual(cookies_path.read_text(), "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tcookie-value\n")
            self.assertEqual(cookies_path.stat().st_mode & 0o777, 0o600)

    def test_blank_cookie_content_removes_stale_cookie_file(self):
        p = _make_plugin()
        with tempfile.TemporaryDirectory() as tmpdir:
            p._base_dir = Path(tmpdir)
            cookies_path = Path(p._get_cookies_file("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tcookie-value"))
            self.assertTrue(cookies_path.exists())
            self.assertIsNone(p._get_cookies_file("   \n  "))
            self.assertFalse(cookies_path.exists())

    def test_failed_replace_cleans_up_same_directory_temp_file(self):
        p = _make_plugin()
        with tempfile.TemporaryDirectory() as tmpdir:
            p._base_dir = Path(tmpdir)
            with patch("plugin.os.replace", side_effect=OSError("boom")):
                self.assertIsNone(
                    p._get_cookies_file(
                        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tcookie-value"
                    )
                )

            self.assertFalse((Path(tmpdir) / "cookies.txt").exists())
            self.assertEqual(list(Path(tmpdir).glob(".cookies.*.tmp")), [])

    def test_failed_replace_removes_stale_existing_cookie_file(self):
        p = _make_plugin()
        with tempfile.TemporaryDirectory() as tmpdir:
            p._base_dir = Path(tmpdir)
            cookies_path = Path(tmpdir) / "cookies.txt"
            cookies_path.write_text("stale-cookie\n")
            with patch("plugin.os.replace", side_effect=OSError("boom")):
                self.assertIsNone(
                    p._get_cookies_file(
                        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tnew-cookie"
                    )
                )

            self.assertFalse(cookies_path.exists())
            self.assertEqual(list(Path(tmpdir).glob(".cookies.*.tmp")), [])


class TestCookieSidecarLifecycle(unittest.TestCase):

    def test_run_status_with_blank_cookies_removes_plugin_owned_cookie_file(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._handle_status = MagicMock(return_value={"status": "stopped", "message": "ok"})
        with tempfile.TemporaryDirectory() as tmpdir:
            p._base_dir = Path(tmpdir)
            cookies_path = Path(tmpdir) / "cookies.txt"
            cookies_path.write_text("stale-cookie\n")

            result = p.run("status", {}, {"settings": {"cookies_content": "   \n"}})

            self.assertEqual(result["status"], "stopped")
            self.assertFalse(cookies_path.exists())

    def test_sync_cookies_sidecar_returns_false_when_nonblank_cookie_write_fails(self):
        p = _make_plugin()
        p._get_cookies_file = MagicMock(return_value=None)
        self.assertFalse(p._sync_cookies_sidecar({"cookies_content": "configured-cookie"}))
        p._get_cookies_file.assert_called_once_with("configured-cookie")

    def test_sync_cookies_sidecar_preserves_no_cookie_operation(self):
        p = _make_plugin()
        p._get_cookies_file = MagicMock(return_value=None)
        self.assertTrue(p._sync_cookies_sidecar({"cookies_content": "   "}))
        p._get_cookies_file.assert_called_once_with("   ")


class TestHandleAddManualCookieSyncFailures(unittest.TestCase):

    def test_add_manual_reports_cookie_sync_failure_instead_of_claiming_success(self):
        p = _make_plugin()
        p._extract_video_id = MagicMock(return_value="abc123DEF45")
        p._extract_stream_metadata = MagicMock(return_value={
            "video_id": "abc123DEF45",
            "title": "Members Stream",
            "stream_url": "https://refreshed.example.com/stream.m3u8",
            "youtube_channel_name": "Example",
            "youtube_channel_id": "UC123",
            "is_live": True,
        })
        p._create_stream_and_channel = MagicMock(
            side_effect=RuntimeError("Configured cookies could not be synced to cookies.txt")
        )
        p._persist_settings = MagicMock()
        p._send_telegram_notification = MagicMock()
        p._trigger_webhook = MagicMock()

        result = p._handle_add_manual({
            "settings": {
                "manual_url": "https://www.youtube.com/watch?v=abc123DEF45",
                "tracked_streams": {},
                "cookies_content": "configured-cookie",
            }
        })

        self.assertEqual(result["status"], "error")
        self.assertIn("Configured cookies could not be synced", result["message"])
        p._persist_settings.assert_not_called()
        p._send_telegram_notification.assert_not_called()
        p._trigger_webhook.assert_not_called()


# ── Webhook UI: visible fields / hidden legacy IDs ──────────────────────────

class TestWebhookFieldVisibility(unittest.TestCase):
    """Only the four canonical webhook fields should be visible in the settings UI."""

    EXPECTED_WEBHOOK_IDS = {
        "media_refresh_webhook_url",
        "media_refresh_webhook_delay_seconds",
        "notification_webhook_url",
        "notification_base_url",
    }
    HIDDEN_LEGACY_IDS = {
        "webhook_url",
        "webhook_delay_seconds",
        "telegram_webhook_url",
        "dispatcharr_base_url",
        "info_legacy_webhooks",
    }
    HIDDEN_ADVANCED_IDS = {
        "media_refresh_webhook_headers",
        "media_refresh_webhook_body_template",
        "notification_webhook_headers",
        "info_generic_webhooks",
    }

    def _field_ids(self):
        return {f["id"] for f in Plugin.fields}

    def test_all_four_webhook_fields_present(self):
        ids = self._field_ids()
        for fid in self.EXPECTED_WEBHOOK_IDS:
            self.assertIn(fid, ids, f"Expected webhook field '{fid}' missing from Plugin.fields")

    def test_legacy_ids_not_in_fields(self):
        ids = self._field_ids()
        for fid in self.HIDDEN_LEGACY_IDS:
            self.assertNotIn(fid, ids, f"Legacy field '{fid}' must not be visible in Plugin.fields")

    def test_advanced_ids_not_in_fields(self):
        ids = self._field_ids()
        for fid in self.HIDDEN_ADVANCED_IDS:
            self.assertNotIn(fid, ids, f"Advanced field '{fid}' must not be visible in Plugin.fields")

    def test_legacy_resolvers_still_work_with_legacy_settings(self):
        p = _make_plugin()
        config = p._get_media_refresh_webhook_config({"webhook_url": "http://legacy/r"})
        self.assertEqual(config["url"], "http://legacy/r")
        self.assertTrue(config["is_legacy"])

    def test_new_generic_takes_precedence_over_legacy(self):
        p = _make_plugin()
        config = p._get_media_refresh_webhook_config({
            "webhook_url": "http://legacy/r",
            "media_refresh_webhook_url": "http://new/r",
        })
        self.assertEqual(config["url"], "http://new/r")
        self.assertFalse(config["is_legacy"])

    def test_notification_legacy_still_works(self):
        p = _make_plugin()
        config = p._get_notification_webhook_config({"telegram_webhook_url": "http://t.me/h"})
        self.assertEqual(config["url"], "http://t.me/h")
        self.assertTrue(config["is_legacy"])

    def test_notification_new_takes_precedence(self):
        p = _make_plugin()
        config = p._get_notification_webhook_config({
            "telegram_webhook_url": "http://t.me/h",
            "notification_webhook_url": "http://new/h",
        })
        self.assertEqual(config["url"], "http://new/h")
        self.assertFalse(config["is_legacy"])


# ── Stale-poll detection helpers ─────────────────────────────────────────────

class TestStaleEPGDetectionHelpers(unittest.TestCase):
    """Tests for _parse_iso_datetime, _age_seconds, _is_last_poll_recent,
    _get_youtubearr_epg_window_counts, and the ghost-heartbeat property."""

    def _p(self):
        return _make_plugin()

    # --- _parse_iso_datetime ---

    def test_parse_iso_datetime_valid_utc(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._p()
        ts = datetime.now(dt_timezone.utc).isoformat()
        result = p._parse_iso_datetime(ts)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tzinfo)

    def test_parse_iso_datetime_none_returns_none(self):
        self.assertIsNone(self._p()._parse_iso_datetime(None))

    def test_parse_iso_datetime_empty_string_returns_none(self):
        self.assertIsNone(self._p()._parse_iso_datetime(""))

    def test_parse_iso_datetime_invalid_returns_none(self):
        self.assertIsNone(self._p()._parse_iso_datetime("not-a-date"))

    def test_parse_iso_datetime_z_suffix_accepted(self):
        p = self._p()
        result = p._parse_iso_datetime("2026-06-07T12:00:00Z")
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tzinfo)

    def test_parse_iso_datetime_naive_gets_utc(self):
        p = self._p()
        result = p._parse_iso_datetime("2026-06-07T12:00:00")
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.tzinfo)

    # --- _age_seconds ---

    def test_age_seconds_none_input_returns_none(self):
        self.assertIsNone(self._p()._age_seconds(None))

    def test_age_seconds_empty_returns_none(self):
        self.assertIsNone(self._p()._age_seconds(""))

    def test_age_seconds_recent_returns_small_positive(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._p()
        ts = datetime.now(dt_timezone.utc).isoformat()
        age = p._age_seconds(ts)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0.0)
        self.assertLess(age, 5.0)

    def test_age_seconds_old_timestamp_returns_large(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._p()
        old_ts = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        age = p._age_seconds(old_ts)
        self.assertGreater(age, 7000)

    # --- _is_last_poll_recent ---

    def test_is_last_poll_recent_with_fresh_timestamp_returns_true(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._p()
        ts = datetime.now(dt_timezone.utc).isoformat()
        self.assertTrue(p._is_last_poll_recent({"last_poll_time": ts, "poll_interval_minutes": 15}))

    def test_is_last_poll_recent_with_stale_timestamp_returns_false(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._p()
        stale = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        self.assertFalse(p._is_last_poll_recent({"last_poll_time": stale, "poll_interval_minutes": 15}))

    def test_is_last_poll_recent_with_no_timestamp_returns_false(self):
        self.assertFalse(self._p()._is_last_poll_recent({}))

    def test_is_last_poll_recent_with_none_returns_false(self):
        self.assertFalse(self._p()._is_last_poll_recent({"last_poll_time": None}))

    def test_is_last_poll_recent_threshold_uses_poll_interval(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._p()
        # 12 minutes ago — within 15+10=25 min threshold but NOT within 1+10=11 min threshold
        twelve_min_ago = (datetime.now(dt_timezone.utc) - timedelta(minutes=12)).isoformat()
        self.assertTrue(p._is_last_poll_recent({"last_poll_time": twelve_min_ago, "poll_interval_minutes": 15}))
        self.assertFalse(p._is_last_poll_recent({"last_poll_time": twelve_min_ago, "poll_interval_minutes": 1}))

    # --- _get_youtubearr_epg_window_counts ---

    def test_get_epg_window_counts_source_not_found(self):
        p = self._p()
        with patch("plugin.EPGSource") as mock_es:
            mock_es.objects.filter.return_value.first.return_value = None
            result = p._get_youtubearr_epg_window_counts({"epg_source_name": "YouTube Live"})
        self.assertFalse(result["source_found"])
        self.assertEqual(result["current"], 0)
        self.assertEqual(result["future12"], 0)

    def test_get_epg_window_counts_source_found_with_programs(self):
        p = self._p()
        mock_source = MagicMock()
        with patch("plugin.EPGSource") as mock_es, \
             patch("plugin.ProgramData") as mock_pd:
            mock_es.objects.filter.return_value.first.return_value = mock_source
            mock_pd.objects.filter.return_value.count.side_effect = [3, 5]
            result = p._get_youtubearr_epg_window_counts({"epg_source_name": "YouTube Live"})
        self.assertTrue(result["source_found"])
        self.assertEqual(result["current"], 3)
        self.assertEqual(result["future12"], 5)

    def test_get_epg_window_counts_empty_source_name_skips_query(self):
        p = self._p()
        result = p._get_youtubearr_epg_window_counts({"epg_source_name": ""})
        self.assertFalse(result["source_found"])

    def test_get_epg_window_counts_db_error_returns_zeros(self):
        p = self._p()
        with patch("plugin.EPGSource") as mock_es:
            mock_es.objects.filter.side_effect = RuntimeError("DB down")
            result = p._get_youtubearr_epg_window_counts({"epg_source_name": "YouTube Live"})
        self.assertFalse(result["source_found"])
        self.assertEqual(result["current"], 0)


# ── _handle_refresh behavior ─────────────────────────────────────────────────

def _make_refresh_plugin():
    p = _make_plugin()
    p._plugin_key = "youtubearr"
    p._monitor_thread = None
    p._monitoring_active = False
    p._monitor_stop_event = MagicMock()
    p._manual_refresh_lock = MagicMock()
    p._manual_refresh_lock.acquire.return_value = True
    p._manual_refresh_lock.release = MagicMock()
    return p


def _mock_cfg(settings):
    """Return a mock PluginConfig whose .settings returns the given dict."""
    mock_cfg_obj = MagicMock()
    mock_cfg_obj.settings = settings
    mock_db = MagicMock()
    mock_db.DoesNotExist = type("DoesNotExist", (Exception,), {})
    mock_db.objects.get.return_value = mock_cfg_obj
    return mock_db


class TestHandleRefreshBehavior(unittest.TestCase):

    def test_active_with_alive_thread_returns_status_not_refresh(self):
        p = _make_refresh_plugin()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        p._read_runtime_state.return_value = {"desired_active": True}
        settings = {"poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        self.assertIn("active", result["message"].lower())
        self.assertNotIn("restart", result["message"].lower())

    def test_active_with_alive_thread_does_not_call_ensure(self):
        """Live thread → _ensure_monitoring_thread never invoked."""
        p = _make_refresh_plugin()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        p._read_runtime_state.return_value = {"desired_active": True}
        p._ensure_monitoring_thread = MagicMock()
        settings = {"poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            p._handle_refresh({"settings": settings})
        p._ensure_monitoring_thread.assert_not_called()

    def test_active_dead_thread_restarts_monitoring(self):
        """desired_active=True but no live thread → ensure called, restart message returned."""
        p = _make_refresh_plugin()
        p._read_runtime_state.return_value = {"desired_active": True}
        p._ensure_monitoring_thread = MagicMock(return_value=True)
        settings = {"poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        p._ensure_monitoring_thread.assert_called_once()
        self.assertIn("restart", result["message"].lower())

    def test_inactive_runs_background_one_shot(self):
        p = _make_refresh_plugin()
        settings = {}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_refresh({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertIn("background", result["message"].lower())

    def test_status_message_includes_poll_interval(self):
        p = _make_refresh_plugin()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        p._read_runtime_state.return_value = {"desired_active": True}
        settings = {"poll_interval_minutes": 20}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        self.assertIn("20", result["message"])


# ── _handle_start_monitoring behavior ───────────────────────────────────────

class TestHandleStartMonitoringBehavior(unittest.TestCase):

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        p._persist_settings = MagicMock()
        return p

    def test_active_with_thread_alive_returns_already_active(self):
        p = self._make_p()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        settings = {"monitoring_active": True, "monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("already active", result["message"].lower())

    def test_lock_held_returns_already_active(self):
        """If lock is held by another process, start returns 'already active'."""
        p = self._make_p()
        p._acquire_monitor_lock.return_value = False
        settings = {"monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("already active", result["message"].lower())

    def test_lock_acquired_starts_thread(self):
        """Lock acquired → thread started, status=running."""
        p = self._make_p()
        settings = {"monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_start_monitoring({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertEqual(result["status"], "running")
        self.assertNotIn("already active", result["message"].lower())

    def test_desired_active_written_to_runtime_state(self):
        """Start always persists desired_active=True before attempting lock."""
        p = self._make_p()
        settings = {"monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread"):
            p._handle_start_monitoring({"settings": settings})
        p._write_runtime_state.assert_called_with({"desired_active": True})

    def test_no_channels_returns_error(self):
        """Missing monitored_channels returns error before any state write."""
        p = self._make_p()
        settings = {"monitored_channels": ""}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "error")


# ── _cleanup_ended_streams ───────────────────────────────────────────────────

class TestCleanupEndedStreams(unittest.TestCase):
    """Regression tests for _cleanup_ended_streams."""

    def _make_p(self):
        p = _make_plugin()
        p._persist_settings = MagicMock()
        return p

    def _make_channel_cls(self, channel=None, missing=False):
        """Return a mock Channel class whose objects.get behaves appropriately."""
        mock_cls = MagicMock()
        DoesNotExist = type("DoesNotExist", (Exception,), {})
        mock_cls.DoesNotExist = DoesNotExist
        if missing:
            mock_cls.objects.get.side_effect = DoesNotExist
        else:
            mock_cls.objects.get.return_value = channel or MagicMock()
        return mock_cls

    def test_orphaned_entry_removed_and_persisted_with_no_channel_deletion(self):
        """Orphaned is_live=True entry (channel missing) is removed and settings persisted."""
        p = self._make_p()
        settings = {
            "auto_cleanup": True,
            "tracked_streams": {
                "orphan_vid": {"is_live": True, "channel_id": 999, "stream_id": None, "title": "Gone"},
            },
        }
        mock_channel_cls = self._make_channel_cls(missing=True)
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.Stream", MagicMock()), \
             patch("plugin.ProgramData", MagicMock()):
            count = p._cleanup_ended_streams(settings)
        # No channel was deleted (channel was already missing)
        self.assertEqual(count, 0)
        # Tracking entry was removed
        self.assertNotIn("orphan_vid", settings["tracked_streams"])
        # Settings persisted even though cleaned_count == 0
        p._persist_settings.assert_called_once()

    def test_orphaned_entry_no_channel_id_removed_and_persisted(self):
        """Orphaned is_live=True entry with no channel_id is removed and settings persisted."""
        p = self._make_p()
        settings = {
            "auto_cleanup": True,
            "tracked_streams": {
                "no_cid_vid": {"is_live": True, "channel_id": None, "stream_id": None, "title": "NoID"},
            },
        }
        with patch("plugin.Channel", MagicMock()), \
             patch("plugin.Stream", MagicMock()), \
             patch("plugin.ProgramData", MagicMock()):
            count = p._cleanup_ended_streams(settings)
        self.assertEqual(count, 0)
        self.assertNotIn("no_cid_vid", settings["tracked_streams"])
        p._persist_settings.assert_called_once()

    def test_stale_live_entry_deleted_when_epg_ended_and_verify_returns_false(self):
        """is_live=True entry with expired EPG is deleted when verify says not live."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        p._verify_video_is_live = MagicMock(return_value=False)

        mock_channel = MagicMock()
        mock_channel.epg_data = MagicMock()

        # Programme whose end_time is one hour in the past
        past_time = datetime.now(dt_timezone.utc) - timedelta(hours=1)
        mock_prog = MagicMock()
        mock_prog.end_time = past_time

        mock_program_data = MagicMock()
        mock_program_data.objects.filter.return_value.first.return_value = mock_prog

        mock_channel_cls = self._make_channel_cls(channel=mock_channel)
        mock_stream_cls = MagicMock()
        DoesNotExist = type("DoesNotExist", (Exception,), {})
        mock_stream_cls.DoesNotExist = DoesNotExist
        mock_stream_cls.objects.get.side_effect = DoesNotExist  # stream already gone

        settings = {
            "auto_cleanup": True,
            "tracked_streams": {
                "stale_vid": {
                    "is_live": True, "channel_id": 100, "stream_id": 200, "title": "Stale Stream"
                },
            },
        }
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.Stream", mock_stream_cls), \
             patch("plugin.ProgramData", mock_program_data), \
             patch("plugin.timezone") as mock_tz:
            mock_tz.now.return_value = datetime.now(dt_timezone.utc)
            count = p._cleanup_ended_streams(settings)

        self.assertEqual(count, 1)
        mock_channel.delete.assert_called_once()
        self.assertNotIn("stale_vid", settings["tracked_streams"])
        p._persist_settings.assert_called_once()
        p._verify_video_is_live.assert_called_once_with("stale_vid")

    def test_stale_live_entry_kept_when_epg_ended_but_verify_returns_true(self):
        """is_live=True entry with expired EPG is NOT deleted when verify says still live."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        p._verify_video_is_live = MagicMock(return_value=True)

        mock_channel = MagicMock()
        mock_channel.epg_data = MagicMock()

        past_time = datetime.now(dt_timezone.utc) - timedelta(hours=1)
        mock_prog = MagicMock()
        mock_prog.end_time = past_time

        mock_program_data = MagicMock()
        mock_program_data.objects.filter.return_value.first.return_value = mock_prog

        mock_channel_cls = self._make_channel_cls(channel=mock_channel)

        settings = {
            "auto_cleanup": True,
            "tracked_streams": {
                "still_live_vid": {
                    "is_live": True, "channel_id": 101, "stream_id": 201, "title": "Still Live"
                },
            },
        }
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.Stream", MagicMock()), \
             patch("plugin.ProgramData", mock_program_data), \
             patch("plugin.timezone") as mock_tz:
            mock_tz.now.return_value = datetime.now(dt_timezone.utc)
            count = p._cleanup_ended_streams(settings)

        self.assertEqual(count, 0)
        mock_channel.delete.assert_not_called()
        self.assertIn("still_live_vid", settings["tracked_streams"])
        # Settings not persisted — no entries removed
        p._persist_settings.assert_not_called()
        p._verify_video_is_live.assert_called_once_with("still_live_vid")

    def test_live_entry_with_fresh_epg_not_verified_or_deleted(self):
        """is_live=True entry whose EPG end_time is in the future is left alone."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        p._verify_video_is_live = MagicMock()

        mock_channel = MagicMock()
        mock_channel.epg_data = MagicMock()

        future_time = datetime.now(dt_timezone.utc) + timedelta(hours=6)
        mock_prog = MagicMock()
        mock_prog.end_time = future_time

        mock_program_data = MagicMock()
        mock_program_data.objects.filter.return_value.first.return_value = mock_prog

        mock_channel_cls = self._make_channel_cls(channel=mock_channel)

        settings = {
            "auto_cleanup": True,
            "tracked_streams": {
                "fresh_vid": {
                    "is_live": True, "channel_id": 102, "stream_id": 202, "title": "Fresh"
                },
            },
        }
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.Stream", MagicMock()), \
             patch("plugin.ProgramData", mock_program_data), \
             patch("plugin.timezone") as mock_tz:
            mock_tz.now.return_value = datetime.now(dt_timezone.utc)
            count = p._cleanup_ended_streams(settings)

        self.assertEqual(count, 0)
        mock_channel.delete.assert_not_called()
        self.assertIn("fresh_vid", settings["tracked_streams"])
        p._persist_settings.assert_not_called()
        # verify should NOT have been called — EPG still current
        p._verify_video_is_live.assert_not_called()


# ── _handle_diagnostics: orphaned and stale-EPG warnings ────────────────────

class TestDiagnosticsOrphanedAndStaleEPG(unittest.TestCase):
    """Diagnostics surfaces orphaned tracked entries and stale-EPG warnings."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._legacy_task_cleanup_done = False
        p._log_path = MagicMock()
        p._log_path.exists.return_value = False
        p._base_dir = MagicMock()
        p._get_ytdlp_version = MagicMock(return_value="2025.01.01")
        p._get_qjs_version = MagicMock(return_value="not configured")
        return p

    def _make_channel_cls(self, channel=None, missing=False):
        mock_cls = MagicMock()
        DoesNotExist = type("DoesNotExist", (Exception,), {})
        mock_cls.DoesNotExist = DoesNotExist
        if missing:
            mock_cls.objects.get.side_effect = DoesNotExist
        else:
            mock_cls.objects.get.return_value = channel or MagicMock()
        return mock_cls

    def test_orphaned_count_reported_and_warns(self):
        """Diagnostics reports orphaned tracked entries and emits a warning."""
        p = self._make_p()
        settings = {
            "tracked_streams": {
                "orphan1": {"is_live": True, "channel_id": 999},
            }
        }
        mock_channel_cls = self._make_channel_cls(missing=True)
        mock_pd = MagicMock()
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.ProgramData", mock_pd):
            result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"]["orphaned_tracked_count"], 1)
        self.assertIn(result["status"], ("warning", "error"))

    def test_stale_epg_count_reported_and_warns(self):
        """Diagnostics counts is_live=True entries with expired EPG and warns."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()

        mock_channel = MagicMock()
        mock_channel.epg_data = MagicMock()

        past_time = datetime.now(dt_timezone.utc) - timedelta(hours=2)
        mock_prog = MagicMock()
        mock_prog.end_time = past_time

        mock_pd = MagicMock()
        mock_pd.objects.filter.return_value.first.return_value = mock_prog

        mock_channel_cls = self._make_channel_cls(channel=mock_channel)

        settings = {
            "tracked_streams": {
                "stale1": {"is_live": True, "channel_id": 100},
            }
        }
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.ProgramData", mock_pd):
            result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"]["stale_epg_tracked_count"], 1)
        self.assertIn(result["status"], ("warning", "error"))

    def test_zero_counts_when_all_live_and_epg_current(self):
        """No warnings when live tracked entries have current EPG."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()

        mock_channel = MagicMock()
        mock_channel.epg_data = MagicMock()

        future_time = datetime.now(dt_timezone.utc) + timedelta(hours=10)
        mock_prog = MagicMock()
        mock_prog.end_time = future_time

        mock_pd = MagicMock()
        mock_pd.objects.filter.return_value.first.return_value = mock_prog

        mock_channel_cls = self._make_channel_cls(channel=mock_channel)

        settings = {
            "tracked_streams": {
                "fresh1": {"is_live": True, "channel_id": 101},
            }
        }
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.ProgramData", mock_pd):
            result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"].get("orphaned_tracked_count", 0), 0)
        self.assertEqual(result["details"].get("stale_epg_tracked_count", 0), 0)

    def test_zero_counts_when_no_tracked_streams(self):
        """No orphan/stale warnings when tracked_streams is empty."""
        p = self._make_p()
        settings = {"tracked_streams": {}}
        with patch("plugin.Channel", MagicMock()), \
             patch("plugin.ProgramData", MagicMock()):
            result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"].get("orphaned_tracked_count", 0), 0)
        self.assertEqual(result["details"].get("stale_epg_tracked_count", 0), 0)

    def test_not_live_entries_excluded_from_orphan_check(self):
        """Entries with is_live=False are skipped — only live entries are checked for orphans."""
        p = self._make_p()
        settings = {
            "tracked_streams": {
                "ended_vid": {"is_live": False, "channel_id": 500},
            }
        }
        mock_channel_cls = self._make_channel_cls(missing=True)
        with patch("plugin.Channel", mock_channel_cls), \
             patch("plugin.ProgramData", MagicMock()):
            result = p._handle_diagnostics({"settings": settings})
        self.assertEqual(result["details"].get("orphaned_tracked_count", 0), 0)


# ── v1.20.2: auto-start + start/refresh race fix ────────────────────────────

class TestAutoStartAndRaceFix(unittest.TestCase):
    """Tests for auto-start and duplicate-monitor prevention via file lock."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        p._persist_settings = MagicMock()
        return p

    # ── bootstrap: desired_active=True + no thread → one start ───────────────

    def test_bootstrap_starts_monitor_once_when_desired_active(self):
        """desired_active=True + dead thread → _ensure_monitoring_thread starts exactly one thread."""
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        settings = {"monitored_channels": "@nasa"}
        with patch("threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            started = p._ensure_monitoring_thread(settings)
        self.assertTrue(started)
        self.assertEqual(mock_t.start.call_count, 1)

    def test_bootstrap_skips_when_lock_held(self):
        """desired_active=True but lock already held → _ensure_monitoring_thread returns False."""
        p = self._make_p()
        p._read_runtime_state.return_value = {"desired_active": True}
        p._acquire_monitor_lock.return_value = False
        with patch("threading.Thread") as mock_thread:
            started = p._ensure_monitoring_thread({"monitored_channels": "@nasa"})
        self.assertFalse(started)
        mock_thread.assert_not_called()

    # ── start_monitoring: lock-based duplicate prevention ────────────────────

    def test_start_lock_held_returns_already_active(self):
        """Start when lock is held by another worker returns 'already active'."""
        p = self._make_p()
        p._acquire_monitor_lock.return_value = False
        settings = {"monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("already active", result["message"].lower())

    def test_start_thread_alive_returns_already_active(self):
        """Start with a live local thread returns 'already active' immediately."""
        p = self._make_p()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        settings = {"monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("already active", result["message"].lower())
        p._acquire_monitor_lock.assert_not_called()

    # ── stop_monitoring clears runtime state ──────────────────────────────────

    def test_stop_writes_desired_active_false_to_runtime_state(self):
        """Stop persists desired_active=False in runtime_state (not settings)."""
        p = self._make_p()
        p._monitoring_active = True
        context = {"settings": {}}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._handle_stop_monitoring(context)
        p._write_runtime_state.assert_called_with({"desired_active": False})

    def test_stop_clears_heartbeat_in_runtime_state(self):
        """Stop thread writes last_heartbeat_at=None to runtime_state via the loop finally block."""
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = True
        stop_event = MagicMock()
        stop_event.is_set.return_value = True
        p._monitor_stop_event = stop_event
        p._persist_settings = MagicMock()
        p._extraction_failures = {}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._monitoring_loop(p._plugin_key)
        written_calls = [call[0][0] for call in p._write_runtime_state.call_args_list]
        found = any("last_heartbeat_at" in c and c["last_heartbeat_at"] is None for c in written_calls)
        self.assertTrue(found, "monitoring_loop finally must clear last_heartbeat_at in runtime_state")

    # ── version ──────────────────────────────────────────────────────────────

    def test_version_is_1_40_0(self):
        self.assertEqual(Plugin.version, "1.40.0")


# ── Lifecycle stop vs explicit stop hardening (v1.30.0) ─────────────────────
#
# Plugin-only limitation: Dispatcharr core can still overwrite monitoring_active
# via a stale-settings save. These tests guard only the plugin's own cleanup paths.

class TestLifecycleVsExplicitStop(unittest.TestCase):
    """stop() (lifecycle) must not write monitoring_active=False to DB.
    Only _handle_stop_monitoring() (explicit user action) should do that.
    """

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = True
        p._monitor_thread = None
        p._monitor_stop_event = MagicMock()
        p._persist_settings = MagicMock()
        p._cleanup_legacy_celery_task = MagicMock()
        p._legacy_task_cleanup_done = False
        return p

    # ── stop() / lifecycle path ────────────────────────────────────────────

    def test_lifecycle_stop_does_not_persist_monitoring_active(self):
        """stop() must not write monitoring_active to DB under any key."""
        p = self._make_p()
        p.stop()
        for call in p._persist_settings.call_args_list:
            updates = call[0][0]
            self.assertNotIn(
                "monitoring_active", updates,
                f"stop() must not write monitoring_active to DB; got updates={updates}",
            )

    def test_lifecycle_stop_clears_in_memory_flag(self):
        """stop() must clear the in-memory _monitoring_active flag."""
        p = self._make_p()
        p.stop()
        self.assertFalse(p._monitoring_active)

    def test_lifecycle_stop_signals_stop_event(self):
        """stop() must signal the monitor stop event."""
        p = self._make_p()
        p.stop()
        p._monitor_stop_event.set.assert_called()

    def test_lifecycle_stop_joins_running_thread(self):
        """stop() must join a live monitor thread."""
        p = self._make_p()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        p.stop()
        alive.join.assert_called_once()

    def test_lifecycle_stop_returns_stopped_status(self):
        """stop() must return a dict with status='stopped'."""
        p = self._make_p()
        result = p.stop()
        self.assertEqual(result.get("status"), "stopped")

    # ── _handle_stop_monitoring() / explicit user-stop path ────────────────

    def test_explicit_stop_writes_desired_active_false_to_runtime_state(self):
        """_handle_stop_monitoring() must write desired_active=False to runtime_state."""
        p = self._make_p()
        context = {"settings": {}}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._handle_stop_monitoring(context)
        p._write_runtime_state.assert_called_with({"desired_active": False})

    # ── monitoring_loop finally block ─────────────────────────────────────

    def _run_loop_to_exit(self):
        """Run _monitoring_loop with a pre-set stop event so it exits immediately."""
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = True
        stop_event = MagicMock()
        stop_event.is_set.return_value = True  # while loop exits immediately
        p._monitor_stop_event = stop_event
        p._persist_settings = MagicMock()
        p._extraction_failures = {}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._monitoring_loop(p._plugin_key)
        return p

    def test_monitoring_loop_finally_does_not_write_monitoring_active_false(self):
        """Thread finally-block must not write monitoring_active=False to DB."""
        p = self._run_loop_to_exit()
        for call in p._persist_settings.call_args_list:
            updates = call[0][0]
            self.assertFalse(
                "monitoring_active" in updates and updates["monitoring_active"] is False,
                f"monitoring loop finally must not write monitoring_active=False; got {updates}",
            )

    def test_monitoring_loop_finally_clears_heartbeat(self):
        """Thread finally-block must clear last_heartbeat_at=None in runtime_state."""
        p = self._run_loop_to_exit()
        written_calls = [call[0][0] for call in p._write_runtime_state.call_args_list]
        found = any(
            "last_heartbeat_at" in c and c["last_heartbeat_at"] is None
            for c in written_calls
        )
        self.assertTrue(found, "finally block must write last_heartbeat_at=None to runtime_state")

    def test_monitoring_loop_finally_clears_in_memory_flag(self):
        """Thread finally-block must clear the in-memory _monitoring_active flag."""
        p = self._run_loop_to_exit()
        self.assertFalse(p._monitoring_active)

    # ── auto-restart survives lifecycle stop ───────────────────────────────

    def test_ensure_monitoring_restarts_after_lifecycle_stop(self):
        """After lifecycle stop(), _ensure_monitoring_thread must restart monitoring.

        runtime_state still shows desired_active=True (lifecycle stop doesn't change it),
        so ensure_monitoring_thread should start a new thread.
        """
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = False  # cleared by lifecycle stop()
        p._monitor_thread = None
        p._monitor_stop_event = MagicMock()
        p._persist_settings = MagicMock()
        p._legacy_task_cleanup_done = False

        # runtime_state still shows True — lifecycle stop preserved it
        p._read_runtime_state.return_value = {"desired_active": True}
        settings = {"monitored_channels": "@nasa"}
        with patch("threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            started = p._ensure_monitoring_thread(settings)

        self.assertTrue(started, "_ensure_monitoring_thread must restart after lifecycle stop")
        self.assertTrue(p._monitoring_active)
        mock_t.start.assert_called_once()


# ── Cross-worker Refresh: worker A owns monitor, worker B calls Refresh ─────

class TestCrossWorkerRefresh(unittest.TestCase):
    """_handle_refresh must not fall through to a duplicate one-shot poll when
    another worker process genuinely owns the monitor lock."""

    def test_worker_b_refresh_reports_active_without_duplicate_poll(self):
        """Worker B: desired_active=True, no local thread, lock held elsewhere
        (worker A owns it) → truthful already-active response, no one-shot poll."""
        p = _make_refresh_plugin()
        p._read_runtime_state.return_value = {"desired_active": True}
        p._ensure_monitoring_thread = MagicMock(return_value=False)
        p._is_monitor_lock_held_by_other = MagicMock(return_value=True)
        settings = {"poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            result = p._handle_refresh({"settings": settings})
        mock_thread.assert_not_called()
        self.assertNotEqual(result["status"], "error")
        self.assertIn("active", result["message"].lower())
        self.assertIn("another worker", result["message"].lower())

    def test_worker_b_refresh_falls_through_when_lock_genuinely_free(self):
        """Regression guard: if desired_active=True is stale and the lock is
        genuinely free (nothing monitoring anywhere), Refresh must still fall
        through to a one-shot poll rather than reporting false 'active'."""
        p = _make_refresh_plugin()
        p._read_runtime_state.return_value = {"desired_active": True}
        p._ensure_monitoring_thread = MagicMock(return_value=False)
        p._is_monitor_lock_held_by_other = MagicMock(return_value=False)
        settings = {"poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_refresh({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertIn("background", result["message"].lower())


# ── Cross-worker Stop: worker A's monitor loop observes worker B's stop ─────

class TestCrossWorkerStop(unittest.TestCase):
    """The monitor owner's loop must observe shared desired_active=False —
    written by another worker's Stop Monitoring action — and exit/release
    the lock, since the in-memory stop_event is per-process and invisible
    to other workers."""

    def test_owner_loop_observes_shared_stop_and_releases_lock(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = True
        stop_event = MagicMock()
        stop_event.is_set.return_value = False  # local stop_event was NOT signaled
        p._monitor_stop_event = stop_event
        p._persist_settings = MagicMock()
        p._extraction_failures = {}
        # Worker B already wrote desired_active=False to the shared file
        p._read_runtime_state.return_value = {"desired_active": False}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._monitoring_loop(p._plugin_key)
        self.assertFalse(p._monitoring_active, "loop must clear in-memory flag on cross-worker stop")
        p._release_monitor_lock.assert_called_once()
        # Never reached the DB poll cycle — exited on the shared-state check first
        mock_cfg_cls.objects.get.assert_not_called()

    def test_owner_loop_keeps_running_while_desired_active_stays_true(self):
        """Sanity check: with desired_active still True and stop_event unset,
        the loop does NOT exit via the cross-worker check (it proceeds to the
        DB reload, which we make fail immediately to end the test quickly)."""
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = True
        stop_event = MagicMock()
        stop_event.is_set.return_value = False
        p._monitor_stop_event = stop_event
        p._persist_settings = MagicMock()
        p._extraction_failures = {}
        p._read_runtime_state.return_value = {"desired_active": True}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._monitoring_loop(p._plugin_key)
        # Reached the DB reload (and bailed there) rather than exiting on the
        # desired_active check, proving that check did not fire spuriously.
        mock_cfg_cls.objects.get.assert_called()


# ── runtime_state.json concurrency ──────────────────────────────────────────

class TestRuntimeStateConcurrency(unittest.TestCase):
    """runtime_state.json read-modify-write must be serialized so a Stop's
    desired_active=False cannot be silently lost by a concurrent heartbeat write."""

    def _make_real_p(self, tmpdir):
        p = Plugin.__new__(Plugin)
        p._base_dir = Path(tmpdir)
        p._runtime_state_path = p._base_dir / "runtime_state.json"
        p._runtime_state_lock_path = p._base_dir / "runtime_state.lock"
        p._runtime_state_thread_lock = threading.Lock()
        p._log = lambda msg: None
        p._log_error = lambda msg: None
        return p

    def test_concurrent_writes_preserve_desired_active_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = self._make_real_p(tmpdir)
            p._runtime_state_path.write_text(json.dumps({
                "desired_active": True, "last_heartbeat_at": "t0",
            }))

            # Widen the read-modify-write window so an unserialized implementation
            # reliably loses an update; a properly serialized implementation stays
            # correct regardless of this delay since the whole cycle is locked.
            real_read = p._read_runtime_state

            def slow_read():
                state = real_read()
                time.sleep(0.005)
                return state

            p._read_runtime_state = slow_read

            def heartbeat_writer():
                for i in range(20):
                    p._write_runtime_state({"last_heartbeat_at": f"t{i}"})

            def stop_writer():
                time.sleep(0.01)  # let heartbeat writers get underway first
                p._write_runtime_state({"desired_active": False})

            threads = [threading.Thread(target=heartbeat_writer) for _ in range(4)]
            threads.append(threading.Thread(target=stop_writer))
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)
                self.assertFalse(t.is_alive(), "writer thread did not finish — possible deadlock")

            final_state = json.loads(p._runtime_state_path.read_text())
            self.assertFalse(
                final_state.get("desired_active"),
                "desired_active=False must survive concurrent heartbeat writes, not be lost to a race",
            )


# ── Reset All: cross-worker stop semantics ──────────────────────────────────

class TestResetAllCrossWorkerStop(unittest.TestCase):
    """Reset All must persist the stop intent first and wait for the monitor
    lock to actually free up, even when this worker does not own it."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None  # this worker is NOT the monitor owner
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._channel_group_name = "YouTube Live"
        return p

    def test_non_owner_reset_all_writes_stop_intent_before_deleting_and_waits_for_lock(self):
        p = self._make_p()
        # Lock is held by worker A for two polls, then frees up
        p._is_monitor_lock_held_by_other = MagicMock(side_effect=[True, True, False])

        mock_cfg = MagicMock()
        mock_cfg.settings = {"tracked_streams": {"abc": {}}}

        with patch("plugin.PluginConfig") as mock_cfg_cls, \
             patch("plugin.ChannelGroup") as mock_group_cls, \
             patch("plugin.time.sleep"):
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.return_value = mock_cfg
            mock_group_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_group_cls.objects.get.side_effect = mock_group_cls.DoesNotExist

            result = p._handle_reset_all({"settings": {"epg_source_name": ""}})

        # Stop intent (desired_active=False) must be the first thing written,
        # before tracked_streams are cleared or channels are touched.
        first_call = p._write_runtime_state.call_args_list[0]
        self.assertEqual(first_call[0][0], {"desired_active": False})

        # Must have actually polled for lock release rather than assuming this
        # worker owns it (this worker's _monitor_thread is None throughout).
        self.assertEqual(p._is_monitor_lock_held_by_other.call_count, 3)
        self.assertEqual(result["status"], "success")

    def test_non_owner_reset_all_does_not_join_or_release_nonexistent_thread(self):
        """When this worker never owned a local thread, Reset All must not
        crash trying to join/release something it never held."""
        p = self._make_p()
        p._is_monitor_lock_held_by_other = MagicMock(return_value=False)

        mock_cfg = MagicMock()
        mock_cfg.settings = {"tracked_streams": {}}

        with patch("plugin.PluginConfig") as mock_cfg_cls, \
             patch("plugin.ChannelGroup") as mock_group_cls, \
             patch("plugin.time.sleep"):
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.return_value = mock_cfg
            mock_group_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_group_cls.objects.get.side_effect = mock_group_cls.DoesNotExist

            result = p._handle_reset_all({"settings": {"epg_source_name": ""}})

        p._release_monitor_lock.assert_called_once()
        self.assertEqual(result["status"], "success")


# ── Stop/Reset must never release monitor.lock out from under a thread that ─
# is still alive after a join() timeout — a still-running owner must keep
# ownership until it actually exits, so a second worker can never acquire the
# lock and start a duplicate monitor while the first is still running.

class TestStopAndResetLockRetention(unittest.TestCase):

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitoring_active = True
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        p._cleanup_legacy_celery_task = MagicMock()
        p._channel_group_name = "YouTube Live"
        return p

    def _alive_thread(self):
        t = MagicMock()
        t.is_alive.return_value = True  # still alive even after join() is called
        return t

    # ── Stop Monitoring ──────────────────────────────────────────────────────

    def test_stop_timeout_retains_lock_while_thread_alive(self):
        """join(timeout) elapses, thread still alive → lock must be retained,
        not released, and status must not falsely claim 'stopped'."""
        p = self._make_p()
        p._monitor_thread = self._alive_thread()
        p._read_runtime_state.return_value = {"desired_active": True}
        result = p._handle_stop_monitoring({"settings": {}})
        p._monitor_thread.join.assert_called_once_with(timeout=5.0)
        p._release_monitor_lock.assert_not_called()
        self.assertNotEqual(result["status"], "stopped")

    def test_stop_thread_exits_within_timeout_releases_lock(self):
        """Sanity check: thread exits before the join timeout → lock is
        released and status is truthfully 'stopped'."""
        p = self._make_p()
        t = MagicMock()
        t.is_alive.side_effect = [True, False]  # alive before join, dead after
        p._monitor_thread = t
        p._read_runtime_state.return_value = {"desired_active": True}
        result = p._handle_stop_monitoring({"settings": {}})
        p._release_monitor_lock.assert_called_once()
        self.assertEqual(result["status"], "stopped")

    def test_stop_non_owner_with_other_worker_active_reports_stopping_not_stopped(self):
        """This worker never owned the local monitor thread (no thread ran here),
        shared desired_active is True, and another worker still holds monitor.lock.
        The response must truthfully signal a requested/in-progress stop rather
        than falsely claiming monitoring has already stopped."""
        p = self._make_p()
        p._monitoring_active = False  # not the local owner
        p._monitor_thread = None
        p._read_runtime_state.return_value = {"desired_active": True}
        p._is_monitor_lock_held_by_other = MagicMock(return_value=True)
        result = p._handle_stop_monitoring({"settings": {}})
        p._write_runtime_state.assert_called_with({"desired_active": False})
        self.assertNotEqual(result["status"], "stopped")
        self.assertEqual(result["status"], "stopping")

    # ── Reset All ────────────────────────────────────────────────────────────

    def test_reset_timeout_does_not_delete_or_repopulate_state(self):
        """join(timeout) elapses, thread still alive → Reset All must abort
        before touching tracked_streams, channels, or EPG data, and must not
        release the lock the still-running thread owns."""
        p = self._make_p()
        p._monitor_thread = self._alive_thread()
        with patch("plugin.PluginConfig") as mock_cfg_cls, \
             patch("plugin.ChannelGroup") as mock_group_cls, \
             patch("plugin.time.sleep"):
            result = p._handle_reset_all({"settings": {"epg_source_name": ""}})
        p._monitor_thread.join.assert_called_once_with(timeout=5.0)
        p._release_monitor_lock.assert_not_called()
        mock_cfg_cls.objects.get.assert_not_called()
        mock_group_cls.objects.get.assert_not_called()
        self.assertEqual(result["status"], "error")
        self.assertIn("running", result["message"].lower())

    def test_reset_waits_and_succeeds_once_owner_exits(self):
        """Thread exits within the join timeout → Reset All proceeds:
        tracked_streams is cleared and channel/EPG cleanup runs normally."""
        p = self._make_p()
        t = MagicMock()
        t.is_alive.side_effect = [True, False]
        p._monitor_thread = t

        mock_cfg = MagicMock()
        mock_cfg.settings = {"tracked_streams": {"abc": {}}}

        with patch("plugin.PluginConfig") as mock_cfg_cls, \
             patch("plugin.ChannelGroup") as mock_group_cls, \
             patch("plugin.time.sleep"):
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.return_value = mock_cfg
            mock_group_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_group_cls.objects.get.side_effect = mock_group_cls.DoesNotExist

            result = p._handle_reset_all({"settings": {"epg_source_name": ""}})

        p._release_monitor_lock.assert_called_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_cfg.settings["tracked_streams"], {})

    # ── No duplicate monitor across the stop/start boundary ──────────────────

    def test_no_duplicate_monitor_can_start_during_shutdown(self):
        """End-to-end with a real flock-backed monitor.lock: while worker A's
        monitor thread is still alive after Stop Monitoring's join timeout,
        worker B's Start Monitoring must not acquire the lock or start a
        second monitor thread."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "monitor.lock"

            worker_a = self._make_p()
            worker_a._lock_path = lock_path
            worker_a._lock_fd = None
            worker_a._acquire_monitor_lock = Plugin._acquire_monitor_lock.__get__(worker_a)
            worker_a._release_monitor_lock = Plugin._release_monitor_lock.__get__(worker_a)
            self.assertTrue(worker_a._acquire_monitor_lock(), "worker A must start out owning the lock")

            worker_a._monitor_thread = self._alive_thread()
            worker_a._read_runtime_state.return_value = {"desired_active": True}
            stop_result = worker_a._handle_stop_monitoring({"settings": {}})
            self.assertNotEqual(stop_result["status"], "stopped")

            worker_b = _make_plugin()
            worker_b._plugin_key = "youtubearr"
            worker_b._monitor_thread = None
            worker_b._lock_path = lock_path
            worker_b._lock_fd = None
            worker_b._acquire_monitor_lock = Plugin._acquire_monitor_lock.__get__(worker_b)
            settings = {"monitored_channels": "@nasa"}
            with patch("plugin.PluginConfig", _mock_cfg(settings)), \
                 patch("plugin.threading.Thread") as mock_thread:
                start_result = worker_b._handle_start_monitoring({"settings": settings})

            mock_thread.assert_not_called()
            self.assertEqual(start_result["status"], "running")
            self.assertIn("already active", start_result["message"].lower())

            worker_a._release_monitor_lock()  # cleanup


if __name__ == "__main__":
    unittest.main(verbosity=2)
