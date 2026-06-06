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
                      "media_refresh_webhook_configured", "notification_webhook_configured"):
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
        secret = "secret-cookie-value-xyz-unique"
        ctx = {"settings": {"cookies_content": f"# Netscape HTTP Cookie File\n.example.com\t{secret}"}}
        result = p._handle_diagnostics(ctx)
        details_str = json.dumps(result["details"], default=str)
        self.assertNotIn(secret, details_str)
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
    """_ensure_monitoring_thread self-heals without Celery."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        return p

    def test_returns_false_when_monitoring_inactive(self):
        p = self._make_p()
        started = p._ensure_monitoring_thread({"monitoring_active": False})
        self.assertFalse(started)
        self.assertIsNone(p._monitor_thread)

    def test_starts_thread_when_db_active_and_thread_dead(self):
        p = self._make_p()
        settings = {"monitoring_active": True, "monitored_channels": "@nasa"}
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.is_alive.return_value = False
            started = p._ensure_monitoring_thread(settings)
        self.assertTrue(started)
        self.assertTrue(p._monitoring_active)

    def test_skips_when_heartbeat_is_recent(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        recent_hb = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_heartbeat": recent_hb,
            "poll_interval_minutes": 15,
        }
        started = p._ensure_monitoring_thread(settings)
        self.assertFalse(started)
        self.assertIsNone(p._monitor_thread)

    def test_restarts_when_heartbeat_is_stale(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale_hb = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_heartbeat": stale_hb,
            "poll_interval_minutes": 15,
        }
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value.is_alive.return_value = False
            started = p._ensure_monitoring_thread(settings)
        self.assertTrue(started)

    def test_skips_when_no_channels_configured(self):
        p = self._make_p()
        settings = {"monitoring_active": True, "monitored_channels": ""}
        started = p._ensure_monitoring_thread(settings)
        self.assertFalse(started)

    def test_skips_when_ytdlp_missing(self):
        p = self._make_p()
        p._ytdlp_path = None
        settings = {"monitoring_active": True, "monitored_channels": "@nasa"}
        started = p._ensure_monitoring_thread(settings)
        self.assertFalse(started)

    def test_skips_when_thread_already_alive(self):
        p = self._make_p()
        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True
        p._monitor_thread = alive_thread
        settings = {"monitoring_active": True, "monitored_channels": "@nasa"}
        started = p._ensure_monitoring_thread(settings)
        self.assertFalse(started)


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


# ── _is_heartbeat_recent ────────────────────────────────────────────────────

class TestIsHeartbeatRecent(unittest.TestCase):
    from datetime import datetime, timezone as _tz, timedelta as _td

    def _p(self):
        return _make_plugin()

    def test_recent_heartbeat_returns_true(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._p()
        hb = datetime.now(dt_timezone.utc).isoformat()
        self.assertTrue(p._is_heartbeat_recent({"monitoring_heartbeat": hb, "poll_interval_minutes": 15}))

    def test_stale_heartbeat_returns_false(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._p()
        stale = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        self.assertFalse(p._is_heartbeat_recent({"monitoring_heartbeat": stale, "poll_interval_minutes": 15}))

    def test_no_heartbeat_returns_false(self):
        self.assertFalse(self._p()._is_heartbeat_recent({}))

    def test_none_heartbeat_returns_false(self):
        self.assertFalse(self._p()._is_heartbeat_recent({"monitoring_heartbeat": None}))

    def test_malformed_heartbeat_returns_false(self):
        self.assertFalse(self._p()._is_heartbeat_recent({"monitoring_heartbeat": "not-a-date"}))

    def test_threshold_uses_poll_interval(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._p()
        # 9 minutes ago — within the default 15+10=25 min threshold
        recent_ish = (datetime.now(dt_timezone.utc) - timedelta(minutes=9)).isoformat()
        self.assertTrue(p._is_heartbeat_recent({"monitoring_heartbeat": recent_ish, "poll_interval_minutes": 5}))


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
        from datetime import datetime, timezone as dt_timezone
        p = _make_refresh_plugin()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        settings = {"monitoring_active": True, "poll_interval_minutes": 15,
                    "last_poll_time": datetime.now(dt_timezone.utc).isoformat()}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        self.assertIn("active", result["message"].lower())
        self.assertNotIn("restart", result["message"].lower())

    def test_active_with_recent_heartbeat_returns_status_not_refresh(self):
        from datetime import datetime, timezone as dt_timezone
        p = _make_refresh_plugin()
        recent_hb = datetime.now(dt_timezone.utc).isoformat()
        settings = {"monitoring_active": True, "poll_interval_minutes": 15,
                    "monitoring_heartbeat": recent_hb,
                    "last_poll_time": recent_hb}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        self.assertIn("active", result["message"].lower())

    def test_active_with_recent_heartbeat_does_not_start_thread(self):
        from datetime import datetime, timezone as dt_timezone
        p = _make_refresh_plugin()
        p._ensure_monitoring_thread = MagicMock(return_value=False)
        recent_hb = datetime.now(dt_timezone.utc).isoformat()
        settings = {"monitoring_active": True, "poll_interval_minutes": 15,
                    "monitoring_heartbeat": recent_hb}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            p._handle_refresh({"settings": settings})
        p._ensure_monitoring_thread.assert_not_called()

    def test_active_stale_heartbeat_dead_thread_restarts_monitoring(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = _make_refresh_plugin()
        p._ensure_monitoring_thread = MagicMock(return_value=True)
        stale = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        settings = {"monitoring_active": True, "poll_interval_minutes": 15,
                    "monitoring_heartbeat": stale}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        p._ensure_monitoring_thread.assert_called_once()
        self.assertIn("restart", result["message"].lower())

    def test_active_no_heartbeat_dead_thread_restarts_monitoring(self):
        p = _make_refresh_plugin()
        p._ensure_monitoring_thread = MagicMock(return_value=True)
        settings = {"monitoring_active": True, "poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_refresh({"settings": settings})
        p._ensure_monitoring_thread.assert_called_once()
        self.assertIn("restart", result["message"].lower())

    def test_inactive_runs_background_one_shot(self):
        p = _make_refresh_plugin()
        settings = {"monitoring_active": False}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_refresh({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertIn("background", result["message"].lower())

    def test_status_message_includes_poll_interval(self):
        from datetime import datetime, timezone as dt_timezone
        p = _make_refresh_plugin()
        alive = MagicMock()
        alive.is_alive.return_value = True
        p._monitor_thread = alive
        settings = {"monitoring_active": True, "poll_interval_minutes": 20}
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

    def test_active_with_recent_heartbeat_returns_already_active(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        recent_hb = datetime.now(dt_timezone.utc).isoformat()
        settings = {"monitoring_active": True, "monitored_channels": "@nasa",
                    "monitoring_heartbeat": recent_hb, "poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("already active", result["message"].lower())

    def test_active_stale_heartbeat_dead_thread_starts_monitoring(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        settings = {"monitoring_active": True, "monitored_channels": "@nasa",
                    "monitoring_heartbeat": stale, "poll_interval_minutes": 15}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_start_monitoring({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertEqual(result["status"], "running")
        self.assertNotIn("already active", result["message"].lower())

    def test_active_no_heartbeat_dead_thread_starts_monitoring(self):
        p = self._make_p()
        settings = {"monitoring_active": True, "monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_start_monitoring({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertNotIn("already active", result["message"].lower())

    def test_inactive_with_channels_starts_monitoring(self):
        p = self._make_p()
        settings = {"monitoring_active": False, "monitored_channels": "@nasa"}
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            result = p._handle_start_monitoring({"settings": settings})
        mock_t.start.assert_called_once()
        self.assertEqual(result["status"], "running")


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
    """Tests for v1.20.2 auto-start and duplicate-monitor prevention."""

    def _make_p(self):
        p = _make_plugin()
        p._plugin_key = "youtubearr"
        p._monitor_thread = None
        p._monitoring_active = False
        p._monitor_stop_event = MagicMock()
        p._legacy_task_cleanup_done = False
        p._persist_settings = MagicMock()
        return p

    # ── _is_starting_recent ──────────────────────────────────────────────────

    def test_is_starting_recent_with_fresh_timestamp(self):
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        fresh = datetime.now(dt_timezone.utc).isoformat()
        self.assertTrue(p._is_starting_recent({"monitoring_starting_at": fresh}))

    def test_is_starting_recent_with_stale_timestamp(self):
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale = (datetime.now(dt_timezone.utc) - timedelta(seconds=120)).isoformat()
        self.assertFalse(p._is_starting_recent({"monitoring_starting_at": stale}))

    def test_is_starting_recent_with_no_field(self):
        self.assertFalse(self._make_p()._is_starting_recent({}))

    def test_is_starting_recent_with_none_field(self):
        self.assertFalse(self._make_p()._is_starting_recent({"monitoring_starting_at": None}))

    def test_is_starting_recent_with_malformed_timestamp(self):
        self.assertFalse(self._make_p()._is_starting_recent({"monitoring_starting_at": "not-a-date"}))

    # ── bootstrap: monitoring_active=True + stale hb + no thread → one start ─

    def test_bootstrap_starts_monitor_once_when_active_and_thread_dead(self):
        """monitoring_active=True + dead thread + stale hb → ensure_monitoring_thread starts exactly one thread."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale_hb = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_heartbeat": stale_hb,
            "poll_interval_minutes": 15,
        }
        with patch("threading.Thread") as mock_thread:
            mock_t = MagicMock()
            mock_thread.return_value = mock_t
            started = p._ensure_monitoring_thread(settings)
        self.assertTrue(started)
        self.assertEqual(mock_t.start.call_count, 1)

    def test_bootstrap_does_not_start_when_starting_at_fresh(self):
        """monitoring_active=True + fresh monitoring_starting_at → ensure_monitoring_thread skips."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = self._make_p()
        stale_hb = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        fresh_start = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_heartbeat": stale_hb,
            "monitoring_starting_at": fresh_start,
            "poll_interval_minutes": 15,
        }
        with patch("threading.Thread") as mock_thread:
            started = p._ensure_monitoring_thread(settings)
        self.assertFalse(started)
        mock_thread.assert_not_called()

    # ── handle_start_monitoring: fresh starting_at → already starting ─────────

    def test_start_monitoring_returns_already_starting_when_starting_at_fresh(self):
        """Start returns 'already starting' when another worker holds the lease."""
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        fresh_start = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_starting_at": fresh_start,
            "poll_interval_minutes": 15,
        }
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("starting", result["message"].lower())

    def test_start_after_refresh_returns_already_active_when_heartbeat_fresh(self):
        """Start after refresh/bootstrap sees fresh heartbeat and returns already active."""
        from datetime import datetime, timezone as dt_timezone
        p = self._make_p()
        fresh_hb = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_heartbeat": fresh_hb,
            "poll_interval_minutes": 15,
        }
        with patch("plugin.PluginConfig", _mock_cfg(settings)):
            result = p._handle_start_monitoring({"settings": settings})
        self.assertEqual(result["status"], "running")
        self.assertIn("already active", result["message"].lower())

    # ── handle_refresh: fresh starting_at → returns starting not manual poll ──

    def test_refresh_returns_starting_message_when_monitor_just_claimed(self):
        """Refresh with stale hb but fresh monitoring_starting_at returns 'starting' not one-shot poll."""
        from datetime import datetime, timezone as dt_timezone, timedelta
        p = _make_refresh_plugin()
        stale_hb = (datetime.now(dt_timezone.utc) - timedelta(hours=2)).isoformat()
        fresh_start = datetime.now(dt_timezone.utc).isoformat()
        settings = {
            "monitoring_active": True,
            "monitored_channels": "@nasa",
            "monitoring_heartbeat": stale_hb,
            "monitoring_starting_at": fresh_start,
            "poll_interval_minutes": 15,
        }
        with patch("plugin.PluginConfig", _mock_cfg(settings)), \
             patch("plugin.threading.Thread") as mock_thread:
            result = p._handle_refresh({"settings": settings})
        # Should NOT start a manual one-shot thread
        mock_thread.assert_not_called()
        self.assertIn("starting", result["message"].lower())

    # ── handle_stop_monitoring clears starting_at ─────────────────────────────

    def test_stop_clears_monitoring_starting_at(self):
        """Stop persists monitoring_starting_at=None so Start can work cleanly after."""
        p = self._make_p()
        p._monitoring_active = True
        p._persist_settings = MagicMock()
        context = {"settings": {"monitoring_active": True, "monitoring_starting_at": "2026-06-06T12:00:00+00:00"}}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._handle_stop_monitoring(context)
        persisted = p._persist_settings.call_args[0][0]
        self.assertIn("monitoring_starting_at", persisted)
        self.assertIsNone(persisted["monitoring_starting_at"])

    def test_stop_clears_heartbeat(self):
        """Stop also persists monitoring_heartbeat=None."""
        p = self._make_p()
        p._monitoring_active = True
        p._persist_settings = MagicMock()
        context = {"settings": {"monitoring_active": True}}
        with patch("plugin.PluginConfig") as mock_cfg_cls:
            mock_cfg_cls.DoesNotExist = type("DoesNotExist", (Exception,), {})
            mock_cfg_cls.objects.get.side_effect = mock_cfg_cls.DoesNotExist
            p._handle_stop_monitoring(context)
        persisted = p._persist_settings.call_args[0][0]
        self.assertIn("monitoring_heartbeat", persisted)
        self.assertIsNone(persisted["monitoring_heartbeat"])

    # ── version ──────────────────────────────────────────────────────────────

    def test_version_is_1_20_2(self):
        self.assertEqual(Plugin.version, "1.20.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
