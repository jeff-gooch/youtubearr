import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, Optional, List

from django.db import transaction
from django.utils import timezone

from apps.plugins.models import PluginConfig
from apps.channels.models import Channel, ChannelGroup, ChannelStream, Stream, Logo
from apps.epg.models import EPGData
from core.models import StreamProfile


class Plugin:
    name = "YouTubearr"
    version = "1.12.1"
    description = "Ingest YouTube livestreams into Dispatcharr channels with automatic monitoring"
    author = "Dispatcharr Community"
    help_url = "https://github.com/Dispatcharr/Dispatcharr"

    fields = [
        {
            "id": "info_manual",
            "label": "Manual Stream Addition",
            "type": "info",
            "description": "Add one or more YouTube livestreams by pasting URLs below (newline or comma-separated).",
        },
        {
            "id": "manual_url",
            "label": "Manual YouTube URLs",
            "type": "text",
            "default": "",
            "help_text": "Paste YouTube livestream URLs here (one per line or comma-separated) and click 'Add Streams'. Multiple URLs will be added at once.",
        },
        {
            "id": "info_monitoring",
            "label": "Automatic Monitoring",
            "type": "info",
            "description": "Automatically detect and add livestreams from YouTube channels. Uses yt-dlp (zero API quota).",
        },
        {
            "id": "monitored_channels",
            "label": "Monitored YouTube Channels",
            "type": "text",
            "default": "",
            "help_text": "One channel per line. Format: @channel or @channel=BaseNumber or @channel=BaseNumber:TitleFilter\n\nExamples:\n@NASA=92\n@RyanHallYall=90\n@OfficialYallBot=90\n@VirtualRailfan=91:Horseshoe Curve|La Grange\n\nChannels without =Number get auto-assigned. Multiple channels can share a base number. Title filter uses regex (case-insensitive).",
        },
        {
            "id": "poll_interval_minutes",
            "label": "Poll Interval (minutes)",
            "type": "number",
            "default": 15,
            "min": 5,
            "max": 60,
            "help_text": "How often to check for new/ended livestreams (5-60 minutes).",
        },
        {
            "id": "info_settings",
            "label": "General Settings",
            "type": "info",
            "description": "Configure stream quality and channel management.",
        },
        {
            "id": "stream_quality",
            "label": "Stream Quality",
            "type": "select",
            "default": "best",
            "options": [
                {"value": "best", "label": "Best Available"},
                {"value": "1080p", "label": "1080p"},
                {"value": "720p", "label": "720p"},
                {"value": "480p", "label": "480p"},
            ],
            "help_text": "Preferred quality for ingested streams",
        },
        {
            "id": "auto_cleanup",
            "label": "Auto-cleanup Ended Streams",
            "type": "boolean",
            "default": True,
            "help_text": "Automatically remove Dispatcharr channels when YouTube livestreams end",
        },
        {
            "id": "url_refresh_interval_seconds",
            "label": "URL Refresh Interval (seconds)",
            "type": "number",
            "default": 3600,
            "min": 300,
            "max": 21600,
            "help_text": "How often to refresh stream URLs to prevent expiration (default: 3600 = 1 hour). YouTube URLs expire after ~6 hours.",
        },
        {
            "id": "channel_group_name",
            "label": "Channel Group",
            "type": "string",
            "default": "YouTube Live",
            "help_text": "Group name for created channels",
        },
        {
            "id": "starting_channel_number",
            "label": "Starting Channel Number",
            "type": "number",
            "default": 2000,
            "min": 1,
            "max": 99999,
            "help_text": "First channel number to assign (default: 2000). Each new stream increments from here.",
        },
        {
            "id": "channel_number_increment",
            "label": "Channel Number Increment",
            "type": "number",
            "default": 1,
            "min": 1,
            "max": 100,
            "help_text": "How much to increment channel numbers for each new stream (default: 1)",
        },
        {
            "id": "info_webhook",
            "label": "Webhook Integration",
            "type": "info",
            "description": "Trigger external services (like Jellyfin LiveTV refresh) when channels are added or removed.",
        },
        {
            "id": "webhook_url",
            "label": "Webhook URL (Jellyfin)",
            "type": "string",
            "default": "",
            "help_text": "URL to POST when channels change (e.g., Jellyfin refresh). Leave empty to disable.",
        },
        {
            "id": "webhook_delay_seconds",
            "label": "Webhook Delay (seconds)",
            "type": "number",
            "default": 5,
            "min": 0,
            "max": 60,
            "help_text": "Delay before triggering webhook to allow Dispatcharr to finish processing (default: 5 seconds).",
        },
        {
            "id": "telegram_webhook_url",
            "label": "Telegram Notification URL",
            "type": "string",
            "default": "",
            "help_text": "URL to POST for Telegram notifications when new channels are added (e.g., https://example.com/webhook/notify). Leave empty to disable.",
        },
        {
            "id": "dispatcharr_base_url",
            "label": "Dispatcharr Base URL",
            "type": "string",
            "default": "",
            "help_text": "Base URL for Dispatcharr stream links in notifications (e.g., https://tv.example.com). Used to build stream URLs like {base_url}/proxy/ts/stream/{uuid}.",
        },
        {
            "id": "info_epg",
            "label": "EPG Settings",
            "type": "info",
            "description": "Automatically assign a Dummy EPG source to created channels.",
        },
        {
            "id": "epg_source_name",
            "label": "EPG Source Name",
            "type": "string",
            "default": "",
            "help_text": "Name of the Dummy EPG source to assign to new channels (e.g., 'YouTube Live'). Must match exactly. Leave empty to skip EPG assignment.",
        },
    ]

    actions = [
        {
            "id": "add_manual",
            "label": "Add Streams",
            "description": "Add YouTube livestream(s) using the Manual URLs field (supports multiple URLs)",
            "button_label": "Add Streams",
            "button_color": "blue",
        },
        {
            "id": "start_monitoring",
            "label": "Start Monitoring",
            "description": "Start automatic monitoring of configured YouTube channels",
            "button_label": "Start Monitoring",
            "button_color": "green",
        },
        {
            "id": "stop_monitoring",
            "label": "Stop Monitoring",
            "description": "Stop automatic channel monitoring",
            "confirm": {
                "required": True,
                "title": "Stop Monitoring?",
                "message": "This will stop checking for new livestreams.",
            },
            "button_label": "Stop",
            "button_color": "yellow",
        },
        {
            "id": "refresh",
            "label": "Refresh Now",
            "description": "Immediately check for new/ended livestreams",
            "button_label": "Refresh",
            "button_color": "blue",
        },
        {
            "id": "cleanup",
            "label": "Cleanup Ended Streams",
            "description": "Remove channels for ended streams and clean up orphaned tracked_streams entries",
            "confirm": {
                "required": True,
                "title": "Cleanup Ended Streams?",
                "message": "This will remove channels for ended YouTube streams (live streams will NOT be affected).",
            },
            "button_label": "Cleanup",
            "button_color": "red",
        },
    ]

    def __init__(self) -> None:
        self._base_dir = Path(__file__).resolve().parent
        self._plugin_key = self._base_dir.name.replace(" ", "_").lower()
        self._log_path = self._base_dir / "youtubearr.log"
        self._log_max_bytes = 5 * 1024 * 1024

        self._channel_group_name = "YouTube Live"
        self._starting_channel_number = 2000

        # Monitoring thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop_event = threading.Event()
        self._monitoring_active = False  # In-memory flag to prevent race with Dispatcharr form saves

        # Stream profile cache
        self._stream_profile_id: Optional[int] = None

        # Field defaults
        self._field_defaults = {field["id"]: field.get("default") for field in self.fields}

        # Check for yt-dlp binary
        self._ytdlp_path = self._find_ytdlp_binary()

    def run(self, action: str, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Main entry point for all plugin actions"""
        action = (action or "").lower()

        # Merge params into context settings
        settings = dict(context.get("settings") or {})
        if params:
            settings.update(params)
        context["settings"] = settings

        if action in {"", "status"}:
            response = self._handle_status(context)
        elif action == "add_manual":
            response = self._handle_add_manual(context)
        elif action == "start_monitoring":
            response = self._handle_start_monitoring(context)
        elif action == "stop_monitoring":
            response = self._handle_stop_monitoring(context)
        elif action == "refresh":
            response = self._handle_refresh(context)
        elif action == "cleanup":
            response = self._handle_cleanup(context)
        else:
            response = {"status": "error", "message": f"Unknown action '{action}'"}

        return response

    def stop(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Called when plugin is disabled/reloaded"""
        if not context or "settings" not in context:
            try:
                cfg = PluginConfig.objects.get(key=self._plugin_key)
                settings = dict(cfg.settings or {})
            except PluginConfig.DoesNotExist:
                settings = {}
            context = {"settings": settings}

        return self._handle_stop_monitoring(context)

    # --- Action Handlers ---

    def _handle_status(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Return current status"""
        settings = context.get("settings", {})
        tracked_streams = settings.get("tracked_streams", {})
        monitoring_active = settings.get("monitoring_active", False)

        # Check yt-dlp availability
        if not self._ytdlp_path:
            return {
                "status": "error",
                "message": "yt-dlp not found (bundled version may not be working). Check logs.",
            }

        # Auto-restart monitoring if DB says active but thread isn't running
        # This handles container/service restarts
        if monitoring_active and not self._monitoring_active:
            channels = settings.get("monitored_channels", "").strip()
            if channels and self._ytdlp_path:
                self._log("Auto-restarting monitoring after service restart")
                self._monitoring_active = True
                self._monitor_stop_event.clear()
                self._monitor_thread = threading.Thread(
                    target=self._monitoring_loop,
                    args=(self._plugin_key,),
                    daemon=True,
                    name="YouTubearr-Monitor"
                )
                self._monitor_thread.start()

        message_parts = []
        if monitoring_active:
            message_parts.append(f"Monitoring active ({len(tracked_streams)} streams tracked)")
        else:
            message_parts.append(f"Monitoring inactive ({len(tracked_streams)} streams tracked)")

        api_calls = settings.get("api_calls_today", 0)
        if api_calls > 0:
            message_parts.append(f"API quota used today: {api_calls}/10000 units")

        return {
            "status": "running" if monitoring_active else "stopped",
            "message": " | ".join(message_parts) if message_parts else "Ready",
        }

    def _handle_add_manual(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Add YouTube livestream(s) manually - supports multiple URLs"""
        # Check yt-dlp availability
        if not self._ytdlp_path:
            return {
                "status": "error",
                "message": "yt-dlp not found (bundled version may not be working). Check logs.",
            }

        settings = context.get("settings", {})
        urls_raw = settings.get("manual_url", "").strip()

        if not urls_raw:
            return {"status": "error", "message": "No URL provided. Please enter one or more YouTube URLs."}

        # Parse multiple URLs (newline or comma separated)
        urls = re.split(r'[,\n]+', urls_raw)
        urls = [u.strip() for u in urls if u.strip()]

        if not urls:
            return {"status": "error", "message": "No valid URLs found"}

        added_count = 0
        skipped_count = 0
        error_count = 0
        errors = []

        tracked_streams = settings.get("tracked_streams", {})
        quality = settings.get("stream_quality", "best")

        for url in urls:
            try:
                # Extract video ID
                video_id = self._extract_video_id(url)
                if not video_id:
                    errors.append(f"Could not extract video ID from: {url[:50]}")
                    error_count += 1
                    continue

                # Check if already tracked
                is_tracked = video_id in tracked_streams

                # If tracked, verify the Dispatcharr channel still exists
                if is_tracked:
                    channel_id_to_check = tracked_streams[video_id].get("channel_id")
                    try:
                        Channel.objects.get(id=channel_id_to_check)
                        self._log(f"Stream {video_id} already tracked (Channel #{channel_id_to_check}), skipping")
                        skipped_count += 1
                        continue  # Channel exists, skip re-adding
                    except Channel.DoesNotExist:
                        self._log(f"Stream {video_id} tracked but channel #{channel_id_to_check} was deleted, will re-add")
                        # Remove from tracked_streams so it can be re-added
                        del tracked_streams[video_id]
                        is_tracked = False

                # Extract stream metadata
                metadata = self._extract_stream_metadata(video_id, quality)

                if not metadata:
                    errors.append(f"Failed to extract info for video {video_id}")
                    error_count += 1
                    continue

                if not metadata.get("is_live"):
                    errors.append(f"Stream {video_id} is not currently live")
                    error_count += 1
                    continue

                # Create Dispatcharr Stream and Channel
                stream, channel = self._create_stream_and_channel(metadata, settings)

                # Track the stream
                tracked_streams[video_id] = {
                    "video_id": video_id,
                    "channel_id": channel.id,
                    "stream_id": stream.id,
                    "youtube_channel_id": metadata.get("youtube_channel_id", ""),
                    "youtube_channel_name": metadata.get("youtube_channel_name", ""),
                    "title": metadata.get("title", ""),
                    "added_at": timezone.now().isoformat(),
                    "last_url_refresh": timezone.now().isoformat(),
                    "stream_url": metadata.get("stream_url", ""),
                    "is_live": True,
                    "channel_number": channel.channel_number,
                }

                # Persist immediately to prevent duplicate channel numbers
                self._persist_settings({"tracked_streams": tracked_streams})

                self._log(f"Added stream: {metadata.get('title')} (Channel #{channel.channel_number})")
                added_count += 1

                # Send Telegram notification (use channel.uuid for Dispatcharr URL)
                self._send_telegram_notification(settings, video_id, metadata, channel.channel_number, str(channel.uuid))

            except Exception as exc:
                errors.append(f"Error processing {url[:50]}: {str(exc)[:100]}")
                error_count += 1

        # Trigger webhook if streams were added
        if added_count > 0:
            self._trigger_webhook(settings)

        # Build response message
        message_parts = []
        if added_count > 0:
            message_parts.append(f"{added_count} stream(s) added")
        if skipped_count > 0:
            message_parts.append(f"{skipped_count} already tracked")
        if error_count > 0:
            message_parts.append(f"{error_count} failed")

        message = ", ".join(message_parts) if message_parts else "No streams processed"

        if errors and len(errors) <= 3:
            message += f". Errors: {'; '.join(errors)}"

        return {
            "status": "success" if added_count > 0 else ("warning" if skipped_count > 0 else "error"),
            "message": message,
        }

    def _handle_start_monitoring(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Start background monitoring thread"""
        # Check dependencies
        if not self._ytdlp_path:
            return {
                "status": "error",
                "message": "yt-dlp not found (bundled version may not be working). Check logs.",
            }

        settings = context.get("settings", {})

        if settings.get("monitoring_active"):
            return {"status": "running", "message": "Monitoring already active"}

        monitored = settings.get("monitored_channels", "").strip()
        if not monitored:
            return {"status": "error", "message": "No channels to monitor. Add channel IDs/URLs in settings."}

        # Set in-memory flag BEFORE starting thread (prevents race with Dispatcharr form saves)
        self._monitoring_active = True
        self._monitor_stop_event.clear()

        # Update settings in DB
        updates = {
            "monitoring_active": True,
            "last_poll_time": timezone.now().isoformat(),
        }
        self._persist_settings(updates)

        # Start monitoring thread AFTER persisting settings
        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(self._plugin_key,),
            daemon=True,
            name="YouTubearr-Monitor"
        )
        self._monitor_thread.start()

        self._log("Monitoring started")

        return {
            "status": "running",
            "message": "Monitoring started",
        }

    def _handle_stop_monitoring(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Stop background monitoring thread"""
        # Check in-memory flag first, then DB
        if not self._monitoring_active:
            settings = context.get("settings", {})
            if not settings.get("monitoring_active"):
                return {"status": "stopped", "message": "Monitoring not active"}

        # Set in-memory flag to stop
        self._monitoring_active = False

        # Signal thread to stop
        self._monitor_stop_event.set()

        # Wait for thread to finish (with timeout)
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)

        # Update settings in DB
        updates = {
            "monitoring_active": False,
        }
        self._persist_settings(updates)

        self._log("Monitoring stopped")

        return {
            "status": "stopped",
            "message": "Monitoring stopped",
        }

    def _handle_refresh(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Manually trigger a refresh cycle"""
        self._log(f"!!! REFRESH ACTION TRIGGERED - Plugin version {self.version} !!!")

        # Get settings from database to preserve monitoring_active flag
        try:
            cfg = PluginConfig.objects.get(key=self._plugin_key)
            settings = dict(cfg.settings or {})
        except PluginConfig.DoesNotExist:
            settings = context.get("settings", {})

        try:
            # Run one poll cycle
            added, ended = self._poll_monitored_channels(settings)

            # Refresh URLs
            refreshed = self._refresh_expiring_urls(settings)

            # Cleanup if enabled
            cleaned = 0
            if settings.get("auto_cleanup", True):
                cleaned = self._cleanup_ended_streams(settings)

            message_parts = []
            if added > 0:
                message_parts.append(f"{added} stream(s) added")
            if ended > 0:
                message_parts.append(f"{ended} stream(s) ended")
            if refreshed > 0:
                message_parts.append(f"{refreshed} URL(s) refreshed")
            if cleaned > 0:
                message_parts.append(f"{cleaned} channel(s) cleaned up")

            # Trigger webhook if channels changed
            if added > 0 or cleaned > 0:
                self._trigger_webhook(settings)

            message = ", ".join(message_parts) if message_parts else "No changes detected"

            return {
                "status": "success",
                "message": f"Refresh complete: {message}",
            }

        except Exception as exc:
            self._log_error(f"Refresh failed: {exc}")
            return {"status": "error", "message": f"Refresh failed: {str(exc)}"}

    def _handle_cleanup(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Manually cleanup ended streams and orphaned tracked_streams entries"""
        # Get settings from database to preserve monitoring_active flag
        try:
            cfg = PluginConfig.objects.get(key=self._plugin_key)
            settings = dict(cfg.settings or {})
        except PluginConfig.DoesNotExist:
            settings = context.get("settings", {})

        try:
            # Clean up ended streams (not live streams)
            cleaned = self._cleanup_ended_streams(settings, force=False)

            # Also clean up orphaned entries in tracked_streams where channel was manually deleted
            tracked_streams = settings.get("tracked_streams", {})
            orphaned = []

            for video_id, stream_data in list(tracked_streams.items()):
                channel_id = stream_data.get("channel_id")
                if channel_id:
                    try:
                        Channel.objects.get(id=channel_id)
                    except Channel.DoesNotExist:
                        # Channel was deleted but still in tracked_streams
                        orphaned.append(video_id)

            # Remove orphaned entries
            for video_id in orphaned:
                del tracked_streams[video_id]

            if orphaned:
                self._persist_settings({"tracked_streams": tracked_streams})
                self._log(f"Removed {len(orphaned)} orphaned tracked_streams entries")

            total_cleaned = cleaned + len(orphaned)
            return {
                "status": "success",
                "message": f"Cleaned up {cleaned} ended stream(s), removed {len(orphaned)} orphaned entry(ies)",
            }

        except Exception as exc:
            self._log_error(f"Cleanup failed: {exc}")
            return {"status": "error", "message": f"Cleanup failed: {str(exc)}"}

    # --- YouTube URL Parsing ---

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from various YouTube URL formats"""
        patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
            r'(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})',
            r'(?:https?://)?youtu\.be/([a-zA-Z0-9_-]{11})',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        # If no pattern matched, try using yt-dlp subprocess to extract
        if self._ytdlp_path:
            try:
                result = subprocess.run(
                    [str(self._ytdlp_path), "--print", "id", "--no-download", url],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    video_id = result.stdout.strip()
                    if len(video_id) == 11:  # Valid YouTube video ID length
                        return video_id
            except Exception:
                pass

        return None

    def _extract_stream_metadata(self, video_id: str, quality_preference: str = "best") -> Optional[Dict[str, Any]]:
        """Extract stream metadata and URL using yt-dlp command-line tool"""
        if not self._ytdlp_path:
            self._log_error("yt-dlp binary not found. Install with: pip install yt-dlp")
            return None

        try:
            url = f"https://www.youtube.com/watch?v={video_id}"
            format_str = self._get_format_string(quality_preference)

            # Build yt-dlp command
            cmd = [
                self._ytdlp_path,
                "--dump-json",
                "--no-warnings",
                "--format", format_str,
                url
            ]

            # Run yt-dlp
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                self._log_error(f"yt-dlp failed for {video_id} (returncode={result.returncode})")
                self._log_error(f"yt-dlp stderr: {result.stderr[:500]}")  # First 500 chars
                return None

            # Parse JSON output
            self._log(f"yt-dlp succeeded for {video_id}, parsing JSON output...")
            info = json.loads(result.stdout)

            if not info:
                self._log_error(f"yt-dlp returned empty info for {video_id}")
                return None

            # Check live status
            is_live_field = info.get("is_live", False)
            live_status_field = info.get("live_status", "unknown")
            is_live = is_live_field or live_status_field == "is_live"

            self._log(f"yt-dlp live status for {video_id}: is_live={is_live_field}, live_status={live_status_field}, computed_is_live={is_live}")

            # Extract channel name from multiple possible fields
            channel_name = (
                info.get("channel") or
                info.get("uploader") or
                info.get("channel_name") or
                "YouTube"
            )

            # Try to get channel avatar from channel page (yt-dlp doesn't provide it)
            channel_avatar = ""
            channel_url = info.get("channel_url") or info.get("uploader_url", "")
            if channel_url:
                channel_avatar = self._fetch_channel_avatar(channel_url)

            metadata = {
                "video_id": video_id,
                "title": info.get("title", "Unknown"),
                "is_live": is_live,
                "stream_url": info.get("url", ""),
                "thumbnail": info.get("thumbnail", ""),
                "channel_thumbnail": channel_avatar,
                "youtube_channel_id": info.get("channel_id", ""),
                "youtube_channel_name": channel_name,
            }

            self._log(f"Metadata: title='{metadata['title'][:60]}...', channel='{channel_name}'")
            self._log(f"DEBUG: channel_thumbnail='{metadata.get('channel_thumbnail', '')[:80]}', thumbnail='{metadata.get('thumbnail', '')[:80]}'")
            return metadata

        except subprocess.TimeoutExpired:
            self._log_error(f"yt-dlp timed out for {video_id}")
            return None
        except json.JSONDecodeError as exc:
            self._log_error(f"Failed to parse yt-dlp output for {video_id}: {exc}")
            return None
        except Exception as exc:
            self._log_error(f"Failed to extract metadata for {video_id}: {exc}")
            return None

    def _fetch_channel_avatar(self, channel_url: str) -> str:
        """Fetch channel avatar URL by scraping the channel page"""
        try:
            req = urllib.request.Request(
                channel_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')

            # Look for channel avatar in various patterns
            # Pattern 1: "avatar":{"thumbnails":[{"url":"https://yt3.ggpht.com/...
            import re
            patterns = [
                r'"avatar"\s*:\s*\{\s*"thumbnails"\s*:\s*\[\s*\{\s*"url"\s*:\s*"([^"]+)"',
                r'"thumbnails"\s*:\s*\[\s*\{\s*"url"\s*:\s*"(https://yt3\.ggpht\.com/[^"]+)"',
                r'(https://yt3\.ggpht\.com/ytc/[^"\'\\]+)',
            ]

            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    avatar_url = match.group(1)
                    # Clean up the URL (unescape)
                    avatar_url = avatar_url.replace("\\u0026", "&")
                    self._log(f"Found channel avatar: {avatar_url[:80]}...")
                    return avatar_url

            self._log(f"Could not find channel avatar in page HTML")
            return ""

        except Exception as exc:
            self._log(f"Failed to fetch channel avatar: {exc}")
            return ""

    def _get_format_string(self, preference: str) -> str:
        """Get yt-dlp format string for quality preference"""
        formats = {
            "best": "best",
            "1080p": "bestvideo[height<=1080]+bestaudio/best",
            "720p": "bestvideo[height<=720]+bestaudio/best",
            "480p": "bestvideo[height<=480]+bestaudio/best",
        }
        return formats.get(preference, "best")

    # --- Dispatcharr Integration ---

    @transaction.atomic
    def _create_stream_and_channel(
        self,
        metadata: Dict[str, Any],
        settings: Dict[str, Any],
        monitored_channel_id: str = ""
    ) -> tuple[Stream, Channel]:
        """Create Dispatcharr Stream and Channel objects.

        Args:
            metadata: Stream metadata from yt-dlp
            settings: Plugin settings
            monitored_channel_id: The YouTube channel ID being monitored (may differ from
                                  stream's actual channel for aggregated/sub-channels)
        """
        # Lock plugin config to prevent race conditions
        cfg = PluginConfig.objects.select_for_update().get(key=self._plugin_key)

        video_title = metadata.get("title", "YouTube Live")
        stream_url = metadata.get("stream_url", "")
        thumbnail = metadata.get("thumbnail", "")
        channel_thumbnail = metadata.get("channel_thumbnail", "")
        youtube_channel_name = metadata.get("youtube_channel_name", "YouTube")
        youtube_channel_id = metadata.get("youtube_channel_id", "")

        # Create Stream (use video thumbnail for stream logo)
        stream = Stream.objects.create(
            name=video_title,
            url=stream_url,
            logo_url=thumbnail if thumbnail else None,
            tvg_id=None,
            stream_profile_id=self._get_stream_profile_id(),
        )

        # Get or create channel group
        group_name = settings.get("channel_group_name", self._channel_group_name)
        group, _ = ChannelGroup.objects.get_or_create(name=group_name)

        # Get channel number using sub-channel mapping (e.g., 90.1, 90.2)
        # Pass monitored_channel_id for mapping (handles sub-channels/aggregated streams)
        # Falls back to stream's youtube_channel_id if not from monitoring
        lookup_channel_id = monitored_channel_id if monitored_channel_id else youtube_channel_id
        channel_number = self._get_channel_number_for_stream(youtube_channel_name, cfg.settings or {}, lookup_channel_id)

        # Format channel name as: {youtube_channel_name} #{stream_number}
        # Extract stream number from sub-channel (e.g., 93.2 → #2)
        stream_number = int(round((channel_number % 1) * 10))
        channel_name = f"{youtube_channel_name} #{stream_number}"

        # Create or get Logo from channel thumbnail URL
        logo = None
        logo_url = channel_thumbnail if channel_thumbnail else thumbnail
        if logo_url:
            try:
                # Try to find existing logo with same URL or create new one
                logo, created = Logo.objects.get_or_create(
                    url=logo_url,
                    defaults={"name": youtube_channel_name}
                )
                if created:
                    self._log(f"Created logo for {youtube_channel_name}: {logo_url[:60]}...")
                else:
                    self._log(f"Reusing existing logo for {youtube_channel_name}")
            except Exception as logo_exc:
                self._log(f"Could not create logo: {logo_exc}")

        # Create Channel with formatted name and logo
        channel = Channel.objects.create(
            name=channel_name,
            channel_number=channel_number,
            channel_group=group,
            logo=logo,
            stream_profile_id=self._get_stream_profile_id(),
        )

        # Try to assign EPG source if configured
        epg_source_name = settings.get("epg_source_name", "").strip()
        if epg_source_name:
            try:
                epg_source = EPGData.objects.get(name=epg_source_name)
                channel.epg_data = epg_source
                channel.save(update_fields=['epg_data'])
                self._log(f"Assigned EPG '{epg_source_name}' to channel")
            except EPGData.DoesNotExist:
                self._log(f"EPG source '{epg_source_name}' not found")
            except Exception as epg_exc:
                self._log(f"Could not assign EPG: {epg_exc}")

        # Link Channel to Stream
        ChannelStream.objects.get_or_create(
            channel=channel,
            stream=stream,
            defaults={"order": 0}
        )

        return stream, channel

    def _parse_channel_number_mapping(self, settings: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Parse channel number mapping from monitored_channels setting.

        Combined format: @Handle or @Handle=BaseNumber or @Handle=BaseNumber:TitleFilter

        Examples:
            @NASA=92
            @RyanHallYall=90
            @VirtualRailfan=91:Horseshoe Curve|La Grange|Glendale

        Channels without =Number are monitored but get auto-assigned numbers.

        Returns dict mapping (channel_id or lowercase name) to:
            {"base": int, "filter": str or None}
        """
        # Read from monitored_channels (combined format)
        mapping_raw = settings.get("monitored_channels", "")
        mapping = {}

        for line in mapping_raw.split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue

            try:
                channel_part, rest = line.split("=", 1)
                channel_part = channel_part.strip()
                rest = rest.strip()

                # Check for title filter after ":"
                if ":" in rest:
                    number_part, filter_part = rest.split(":", 1)
                    base_number = int(number_part.strip())
                    title_filter = filter_part.strip() if filter_part.strip() else None
                else:
                    base_number = int(rest)
                    title_filter = None

                mapping_entry = {"base": base_number, "filter": title_filter}

                if channel_part.startswith("@"):
                    # Resolve @handle to channel_id for reliable matching
                    username = channel_part[1:]
                    channel_id = self._resolve_username_to_channel_id(username)
                    if channel_id:
                        mapping[channel_id] = mapping_entry
                        filter_info = f", filter='{title_filter}'" if title_filter else ""
                        self._log(f"Mapping: @{username} ({channel_id}) → base {base_number}{filter_info}")
                    else:
                        # Fallback to lowercase handle name
                        mapping[username.lower()] = mapping_entry
                        filter_info = f", filter='{title_filter}'" if title_filter else ""
                        self._log(f"Mapping: @{username} (unresolved) → base {base_number}{filter_info}")
                else:
                    # Plain channel name - store lowercase for matching
                    mapping[channel_part.lower()] = mapping_entry

            except (ValueError, AttributeError):
                continue

        return mapping

    def _check_title_filter(self, title: str, channel_id: str, settings: Dict[str, Any]) -> bool:
        """Check if a stream title passes the filter for a channel.

        Returns True if:
            - No filter is configured for this channel
            - Title matches the filter pattern (case-insensitive)

        Returns False if filter exists and title doesn't match.
        """
        mapping = self._parse_channel_number_mapping(settings)

        # Find mapping entry for this channel
        entry = mapping.get(channel_id)
        if not entry:
            return True  # No mapping = no filter = allow all

        title_filter = entry.get("filter")
        if not title_filter:
            return True  # No filter = allow all

        # Check if title matches filter (case-insensitive regex)
        try:
            if re.search(title_filter, title, re.IGNORECASE):
                self._log(f"Title filter MATCH: '{title[:50]}...' matches '{title_filter}'")
                return True
            else:
                self._log(f"Title filter SKIP: '{title[:50]}...' does not match '{title_filter}'")
                return False
        except re.error as e:
            self._log_error(f"Invalid title filter regex '{title_filter}': {e}")
            return True  # On regex error, allow the stream

    def _get_next_subchannel_number(self, base_number: int, settings: Dict[str, Any]) -> float:
        """Get the next available sub-channel number for a base (e.g., 90.1, 90.2, etc.)"""
        # Get all channels in this base range [base, base+1)
        existing_subchannels = []

        # Check tracked_streams
        tracked_streams = settings.get("tracked_streams", {})
        for stream_data in tracked_streams.values():
            ch_num = stream_data.get("channel_number")
            if ch_num is not None:
                try:
                    ch_float = float(ch_num)
                    if base_number <= ch_float < base_number + 1:
                        existing_subchannels.append(ch_float)
                except (TypeError, ValueError):
                    pass

        # Also check actual Dispatcharr channels
        group_name = settings.get("channel_group_name", self._channel_group_name)
        try:
            group = ChannelGroup.objects.get(name=group_name)
            for ch_num in Channel.objects.filter(channel_group=group).values_list('channel_number', flat=True):
                if ch_num is not None:
                    try:
                        ch_float = float(ch_num)
                        if base_number <= ch_float < base_number + 1:
                            existing_subchannels.append(ch_float)
                    except (TypeError, ValueError):
                        pass
        except ChannelGroup.DoesNotExist:
            pass

        # Remove duplicates
        existing_subchannels = list(set(existing_subchannels))

        if not existing_subchannels:
            # First stream for this base - use .1
            return float(f"{base_number}.1")

        # Find the next available sub-channel
        max_sub = max(existing_subchannels)
        # Extract the decimal part and increment
        decimal_part = int(round((max_sub - base_number) * 10))
        next_decimal = decimal_part + 1

        # Handle .9 -> .10, .11, etc.
        return float(f"{base_number}.{next_decimal}")

    def _get_next_unmapped_base_number(self, settings: Dict[str, Any]) -> int:
        """Get the next available base channel number for unmapped YouTube channels."""
        starting_number = settings.get("starting_channel_number", self._starting_channel_number)
        increment = settings.get("channel_number_increment", 1)

        try:
            starting_number = int(starting_number)
            increment = int(increment)
        except (TypeError, ValueError):
            starting_number = self._starting_channel_number
            increment = 1

        # Get all mapped base numbers (mapping values are now dicts with "base" key)
        mapping = self._parse_channel_number_mapping(settings)
        mapped_bases = set(entry["base"] for entry in mapping.values())

        # Get all used base numbers from tracked_streams
        tracked_streams = settings.get("tracked_streams", {})
        used_bases = set()
        for stream_data in tracked_streams.values():
            ch_num = stream_data.get("channel_number")
            if ch_num is not None:
                try:
                    used_bases.add(int(float(ch_num)))
                except (TypeError, ValueError):
                    pass

        # Also check actual Dispatcharr channels
        group_name = settings.get("channel_group_name", self._channel_group_name)
        try:
            group = ChannelGroup.objects.get(name=group_name)
            for ch_num in Channel.objects.filter(channel_group=group).values_list('channel_number', flat=True):
                if ch_num is not None:
                    try:
                        used_bases.add(int(float(ch_num)))
                    except (TypeError, ValueError):
                        pass
        except ChannelGroup.DoesNotExist:
            pass

        # Combine mapped and used bases
        all_used = mapped_bases | used_bases

        # Find next available base starting from starting_number
        next_base = starting_number
        while next_base in all_used:
            next_base += increment

        return next_base

    def _get_channel_number_for_stream(self, youtube_channel_name: str, settings: Dict[str, Any], youtube_channel_id: str = "") -> float:
        """Get channel number for a stream, using sub-channel mapping if configured.

        Args:
            youtube_channel_name: Display name from yt-dlp (e.g., "Ryan Hall, Y'all")
            settings: Plugin settings dict
            youtube_channel_id: YouTube channel ID (UC...) for reliable @handle matching

        Returns a decimal channel number (e.g., 90.1, 90.2).
        """
        # Parse the mapping (returns channel_id or lowercase name → base_number)
        mapping = self._parse_channel_number_mapping(settings)

        # Normalize the channel name for lookup
        channel_name_lower = youtube_channel_name.lower()

        # Check if this YouTube channel is mapped
        base_number = None

        # First, try matching by channel_id (most reliable for @handle mappings)
        if youtube_channel_id and youtube_channel_id in mapping:
            base_number = mapping[youtube_channel_id]["base"]
            self._log(f"Channel '{youtube_channel_name}' ({youtube_channel_id}) mapped to base {base_number}")

        # If not found by ID, try matching by display name
        if base_number is None:
            for mapped_key, mapped_entry in mapping.items():
                if mapped_key == channel_name_lower:
                    base_number = mapped_entry["base"]
                    self._log(f"Channel '{youtube_channel_name}' mapped by name to base {base_number}")
                    break

        if base_number is None:
            # Check if we've seen this channel before (in tracked_streams)
            # Check monitored_channel_id first (for sub-channels), then youtube_channel_id, then name
            tracked_streams = settings.get("tracked_streams", {})
            for stream_data in tracked_streams.values():
                # Match by monitored_channel_id (handles sub-channels/aggregated streams)
                if youtube_channel_id and stream_data.get("monitored_channel_id") == youtube_channel_id:
                    ch_num = stream_data.get("channel_number")
                    if ch_num is not None:
                        try:
                            base_number = int(float(ch_num))
                            self._log(f"Channel '{youtube_channel_name}' previously used base {base_number} (by monitored ID)")
                            break
                        except (TypeError, ValueError):
                            pass
                # Match by youtube_channel_id (stream's actual channel)
                elif youtube_channel_id and stream_data.get("youtube_channel_id") == youtube_channel_id:
                    ch_num = stream_data.get("channel_number")
                    if ch_num is not None:
                        try:
                            base_number = int(float(ch_num))
                            self._log(f"Channel '{youtube_channel_name}' previously used base {base_number} (by stream ID)")
                            break
                        except (TypeError, ValueError):
                            pass
                # Fallback to matching by name
                elif stream_data.get("youtube_channel_name", "").lower() == channel_name_lower:
                    ch_num = stream_data.get("channel_number")
                    if ch_num is not None:
                        try:
                            base_number = int(float(ch_num))
                            self._log(f"Channel '{youtube_channel_name}' previously used base {base_number} (by name)")
                            break
                        except (TypeError, ValueError):
                            pass

        if base_number is None:
            # Unmapped channel - assign a new base number
            base_number = self._get_next_unmapped_base_number(settings)
            self._log(f"Channel '{youtube_channel_name}' unmapped, assigning new base {base_number}")

        # Get next sub-channel number
        channel_number = self._get_next_subchannel_number(base_number, settings)
        self._log(f"Assigned channel number {channel_number} for '{youtube_channel_name}'")

        return channel_number

    def _get_next_youtube_channel_number(self, settings: Dict[str, Any]) -> float:
        """Legacy function - now returns float for sub-channel support.

        This is kept for backwards compatibility but new code should use
        _get_channel_number_for_stream() which handles mapping.
        """
        return float(self._get_next_unmapped_base_number(settings)) + 0.1

    def _get_stream_profile_id(self) -> int:
        """Get or find a suitable stream profile ID"""
        if self._stream_profile_id is not None:
            return self._stream_profile_id

        # Try to find "proxy" profile (as used by WeatharrStation)
        profile = (
            StreamProfile.objects.filter(name__iexact="proxy").first()
            or StreamProfile.objects.filter(name__icontains="proxy").first()
        )

        if not profile:
            profile = StreamProfile.objects.first()

        if not profile:
            raise RuntimeError("No stream profiles found. Create a stream profile in Dispatcharr.")

        self._stream_profile_id = profile.id
        return self._stream_profile_id

    # --- YouTube Data API Integration ---

    def _poll_monitored_channels(self, settings: Dict[str, Any]) -> tuple[int, int]:
        """Poll monitored channels for new/ended streams. Returns (added, ended) counts.

        Uses yt-dlp flat-playlist to detect live streams - NO YouTube API quota required!
        """
        self._log("=== Starting poll cycle (yt-dlp mode - no API quota) ===")

        # Parse monitored channels
        monitored_raw = settings.get("monitored_channels", "").strip()
        self._log(f"Raw monitored_channels value: '{monitored_raw}'")

        if not monitored_raw:
            self._log("No monitored channels configured")
            return 0, 0

        channel_ids = self._parse_channel_ids(monitored_raw)
        if not channel_ids:
            self._log("No valid channel IDs found to poll")
            return 0, 0

        self._log(f"Parsed {len(channel_ids)} channel(s) to poll: {', '.join(channel_ids[:5])}")  # Show first 5

        # Get username map for yt-dlp (needs @handles, not channel IDs)
        username_map = self._extract_username_map(monitored_raw)

        tracked_streams = settings.get("tracked_streams", {})
        added_count = 0
        ended_count = 0

        for channel_id in channel_ids:
            try:
                # Get the @username for this channel (yt-dlp works better with handles)
                username = username_map.get(channel_id)
                if not username:
                    self._log(f"No @username found for {channel_id}, skipping")
                    continue

                self._log(f"Polling channel: @{username} ({channel_id})")

                # Get live streams using yt-dlp flat-playlist (NO API quota!)
                live_streams = self._get_live_streams_via_ytdlp(username, settings)

                # Handle errors - None means error occurred, skip this channel
                if live_streams is None:
                    self._log_error(f"yt-dlp error for @{username}, skipping ended-stream check to avoid false positives")
                    continue

                self._log(f"Found {len(live_streams)} live stream(s) on @{username}")

                # Apply title filter BEFORE full extraction (saves time on channels with many streams)
                if live_streams:
                    filtered_streams = []
                    for stream_info in live_streams:
                        title = stream_info.get("title", "")
                        if self._check_title_filter(title, channel_id, settings):
                            filtered_streams.append(stream_info)

                    if len(filtered_streams) < len(live_streams):
                        self._log(f"Title filter: {len(filtered_streams)}/{len(live_streams)} streams match")
                    live_streams = filtered_streams

                # Check for new streams
                self._log(f"Checking {len(live_streams)} stream(s) against tracked_streams (currently tracking {len(tracked_streams)} streams)")
                for stream_info in live_streams:
                    video_id = stream_info.get("video_id")

                    # Check if stream is in tracked_streams
                    is_tracked = video_id in tracked_streams

                    # If tracked, verify the Dispatcharr channel still exists
                    if is_tracked:
                        channel_id_to_check = tracked_streams[video_id].get("channel_id")
                        try:
                            Channel.objects.get(id=channel_id_to_check)
                            self._log(f"Processing stream {video_id}: in_tracked=True, channel exists (#{channel_id_to_check}), skipping")
                            continue  # Channel exists, skip re-adding
                        except Channel.DoesNotExist:
                            self._log(f"Processing stream {video_id}: in_tracked=True but channel #{channel_id_to_check} was deleted, will re-add")
                            # Remove from tracked_streams so it can be re-added
                            del tracked_streams[video_id]
                            self._persist_settings({"tracked_streams": tracked_streams})
                            is_tracked = False

                    self._log(f"Processing stream {video_id}: in_tracked={is_tracked}")
                    if video_id and not is_tracked:
                        # New livestream detected
                        self._log(f"New stream detected: {video_id}, extracting metadata...")
                        quality = settings.get("stream_quality", "best")
                        metadata = self._extract_stream_metadata(video_id, quality)

                        if not metadata:
                            self._log_error(f"Failed to extract metadata for {video_id} - yt-dlp returned None")
                            continue

                        self._log(f"Metadata extracted for {video_id}: is_live={metadata.get('is_live')}, title={metadata.get('title')}")

                        if metadata.get("is_live"):
                            # Title filter already applied earlier (before metadata extraction)
                            try:
                                # Double-check that the stream wasn't just added by a concurrent poll
                                # Reload settings to get the latest tracked_streams
                                try:
                                    cfg_check = PluginConfig.objects.get(key=self._plugin_key)
                                    current_tracked = dict(cfg_check.settings or {}).get("tracked_streams", {})
                                    if video_id in current_tracked:
                                        self._log(f"Stream {video_id} was already added by another process, skipping")
                                        continue
                                except PluginConfig.DoesNotExist:
                                    pass

                                self._log(f"Creating channel for {video_id}...")
                                # Pass monitored_channel_id for mapping (stream may be from sub-channel)
                                stream, channel = self._create_stream_and_channel(metadata, settings, monitored_channel_id=channel_id)

                                tracked_streams[video_id] = {
                                    "video_id": video_id,
                                    "channel_id": channel.id,
                                    "stream_id": stream.id,
                                    "monitored_channel_id": channel_id,  # The channel being monitored (for mapping)
                                    "youtube_channel_id": metadata.get("youtube_channel_id", ""),  # Stream's actual channel
                                    "youtube_channel_name": metadata.get("youtube_channel_name", ""),
                                    "title": metadata.get("title", ""),
                                    "added_at": timezone.now().isoformat(),
                                    "last_url_refresh": timezone.now().isoformat(),
                                    "stream_url": metadata.get("stream_url", ""),
                                    "is_live": True,
                                    "channel_number": channel.channel_number,
                                }

                                # Persist immediately to prevent duplicates in concurrent polls
                                self._persist_settings({"tracked_streams": tracked_streams})

                                added_count += 1
                                self._log(f"Auto-added stream: {metadata.get('title')} (Channel #{channel.channel_number})")

                                # Send Telegram notification (use channel.uuid for Dispatcharr URL)
                                self._send_telegram_notification(settings, video_id, metadata, channel.channel_number, str(channel.uuid))

                            except Exception as exc:
                                self._log_error(f"Failed to add stream {video_id}: {exc}")
                        else:
                            self._log_error(f"Stream {video_id} is not live (is_live={metadata.get('is_live')}), skipping")

                # Check for ended streams (mark as not live)
                # yt-dlp flat-playlist gets all streams, so no truncation concerns
                current_video_ids = {s.get("video_id") for s in live_streams}
                for video_id, stream_data in list(tracked_streams.items()):
                    if stream_data.get("monitored_channel_id") == channel_id:
                        if video_id not in current_video_ids and stream_data.get("is_live"):
                            stream_data["is_live"] = False
                            ended_count += 1
                            self._log(f"Stream ended: {stream_data.get('title')}")

            except Exception as exc:
                self._log_error(f"Failed to poll channel {channel_id}: {exc}")

        # Persist updates
        self._persist_settings({
            "tracked_streams": tracked_streams,
            "last_poll_time": timezone.now().isoformat(),
        })

        return added_count, ended_count

    def _get_live_streams_for_channel(self, channel_id: str, api_key: str, settings: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Get currently live streams for a YouTube channel using direct HTTP requests.

        Args:
            channel_id: YouTube channel ID
            api_key: YouTube Data API key
            settings: Plugin settings dict (optional, used for quota tracking)

        Returns:
            Dict with keys:
              - "streams": list of stream info dicts
              - "truncated": bool indicating results were truncated due to page limit
            or None on API/network error. Returning None allows callers to distinguish
            "no streams" from "error".

        Supports pagination to fetch all live streams (up to 150 to limit API calls).
        """
        try:
            live_streams = []
            results_truncated = False
            page_token = None
            max_pages = 3  # Limit to 3 pages (150 streams) to avoid excessive API usage
            pages_fetched = 0

            for page_num in range(max_pages):
                # Build YouTube Data API v3 search URL
                params = {
                    "part": "id,snippet",
                    "channelId": channel_id,
                    "eventType": "live",
                    "type": "video",
                    "maxResults": "50",  # Max allowed by API
                    "key": api_key,
                }
                if page_token:
                    params["pageToken"] = page_token

                url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
                if page_num == 0:
                    self._log(f"YouTube API request: {url.replace(api_key, 'API_KEY_HIDDEN')}")
                else:
                    self._log(f"YouTube API request (page {page_num + 1})")

                # Make HTTP request
                request = urllib.request.Request(url)
                request.add_header("Accept", "application/json")

                with urllib.request.urlopen(request, timeout=30) as response:
                    response_text = response.read().decode()
                    data = json.loads(response_text)

                pages_fetched += 1

                # Track API quota for this page (100 units per search call)
                if settings is not None:
                    self._increment_api_quota(settings, 100)

                # Parse items from this page
                items = data.get("items", [])
                for item in items:
                    video_id = item.get("id", {}).get("videoId")
                    if video_id:
                        stream_info = {
                            "video_id": video_id,
                            "title": item.get("snippet", {}).get("title", "Unknown"),
                            "thumbnail": item.get("snippet", {}).get("thumbnails", {}).get("high", {}).get("url", ""),
                        }
                        live_streams.append(stream_info)
                        self._log(f"Found live stream: {stream_info['title']} (ID: {video_id})")

                # Check for more pages
                page_token = data.get("nextPageToken")
                if not page_token:
                    break

                # Check if there are any errors in response
                if "error" in data:
                    self._log_error(f"API error in response: {data['error']}")
                    return None

            # Log summary
            self._log(f"YouTube API response: {len(live_streams)} total live streams found")

            # Warn if results may be truncated (hit page limit with more pages available)
            if page_token:
                self._log(f"WARNING: Results may be truncated (hit {max_pages} page limit). Some streams may not be detected.")
                results_truncated = True

            return {"streams": live_streams, "truncated": results_truncated}

        except urllib.error.HTTPError as exc:
            # Read error response body for more details
            try:
                error_body = exc.read().decode()
                error_data = json.loads(error_body)
                error_message = error_data.get("error", {}).get("message", exc.reason)
                self._log_error(f"YouTube API HTTP {exc.code}: {error_message}")
            except:
                if exc.code == 403:
                    self._log_error("YouTube API quota exceeded (403)")
                elif exc.code in (400, 401):
                    self._log_error("Invalid YouTube API key (400/401)")
                else:
                    self._log_error(f"YouTube API HTTP error {exc.code}: {exc.reason}")
            return None  # Return None on error, not [] - caller must handle this
        except urllib.error.URLError as exc:
            self._log_error(f"YouTube API network error: {exc.reason}")
            return None  # Return None on error, not [] - caller must handle this
        except Exception as exc:
            self._log_error(f"YouTube API error: {exc}")
            return None  # Return None on error, not [] - caller must handle this

    def _get_live_streams_via_ytdlp(self, channel_handle: str, settings: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        """Get currently live streams for a YouTube channel using yt-dlp flat-playlist.

        This method uses NO API quota - it scrapes the channel's /streams page directly.

        Args:
            channel_handle: YouTube channel handle (e.g., "@nasa" or "nasa")
            settings: Plugin settings dict (for title filtering)

        Returns:
            List of stream info dicts with keys: video_id, title, thumbnail
            or None on error.
        """
        # Normalize handle
        if not channel_handle.startswith("@"):
            channel_handle = f"@{channel_handle}"

        streams_url = f"https://www.youtube.com/{channel_handle}/streams"
        self._log(f"Scanning {streams_url} via yt-dlp flat-playlist (no API quota)")

        try:
            yt_dlp_path = self._find_ytdlp_binary()
            if not yt_dlp_path:
                self._log_error("yt-dlp binary not found")
                return None
            cmd = [
                yt_dlp_path,
                "--flat-playlist",
                "--dump-json",
                "--no-warnings",
                "--ignore-errors",
                streams_url
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120  # 2 minute timeout for large channels
            )

            if result.returncode != 0 and not result.stdout:
                self._log_error(f"yt-dlp flat-playlist failed: {result.stderr[:200] if result.stderr else 'no output'}")
                return None

            # Parse JSON lines output
            live_streams = []
            lines = result.stdout.strip().split('\n')

            for line in lines:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    video_id = entry.get("id")
                    title = entry.get("title", "Unknown")
                    live_status = entry.get("live_status", "")

                    # Only include currently live streams
                    if live_status != "is_live":
                        continue

                    # Get thumbnail
                    thumbnail = entry.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

                    stream_info = {
                        "video_id": video_id,
                        "title": title,
                        "thumbnail": thumbnail,
                    }
                    live_streams.append(stream_info)

                except json.JSONDecodeError:
                    continue

            self._log(f"Found {len(live_streams)} live stream(s) via yt-dlp flat-playlist")
            return live_streams

        except subprocess.TimeoutExpired:
            self._log_error(f"yt-dlp flat-playlist timed out for {channel_handle}")
            return None
        except Exception as exc:
            self._log_error(f"yt-dlp flat-playlist error: {exc}")
            return None

    def _extract_username_map(self, raw: str) -> Dict[str, str]:
        """Extract mapping of channel_id -> username from monitored channels input.

        Handles combined format: @channel=90:filter - extracts just the @channel part.
        """
        username_map = {}
        parts = re.split(r'[,;\n]+', raw)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Strip off =number:filter suffix if present (combined format)
            if "=" in part:
                part = part.split("=")[0].strip()

            username = None
            if part.startswith("@"):
                username = part[1:]
            elif "youtube.com" in part:
                match = re.search(r'/@([a-zA-Z0-9_-]+)', part)
                if match:
                    username = match.group(1)

            if username:
                # Resolve to channel ID
                channel_id = self._resolve_username_to_channel_id(username)
                if channel_id:
                    username_map[channel_id] = username

        return username_map

    def _fallback_scan_username_streams(self, username: str, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback scan using @username/streams URL (finds streams on ANY related channel)"""
        try:
            limit = settings.get("fallback_scan_limit", 10)

            # Scrape the @username/streams page directly
            # This shows ALL streams associated with that handle, regardless of which channel hosts them
            channel_url = f"https://www.youtube.com/@{username}/streams"
            self._log(f"Scraping @username streams page: {channel_url}")

            cmd = [
                self._ytdlp_path,
                "--flat-playlist",
                "--dump-json",
                "--playlist-end", str(limit),
                "--no-warnings",
                channel_url
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                self._log(f"Failed to scrape {channel_url}: {result.stderr[:200]}")
                return []

            # Parse video IDs and check if they're live
            live_streams = []
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                try:
                    video_info = json.loads(line)
                    video_id = video_info.get("id")

                    if not video_id:
                        continue

                    # Check if this video is actually live
                    self._log(f"Checking if video {video_id} is live...")
                    metadata = self._extract_stream_metadata(video_id, settings.get("stream_quality", "best"))

                    if metadata and metadata.get("is_live"):
                        live_streams.append({
                            "video_id": video_id,
                            "title": metadata.get("title", "Unknown"),
                            "thumbnail": metadata.get("thumbnail", ""),
                        })
                        self._log(f"✓ Found live stream via @{username}: {metadata.get('title')} (ID: {video_id})")

                        if len(live_streams) >= limit:
                            break
                    else:
                        self._log(f"✗ Video {video_id} is not live, skipping")

                except json.JSONDecodeError:
                    continue
                except Exception as exc:
                    self._log_error(f"Error checking video: {exc}")
                    continue

            return live_streams

        except subprocess.TimeoutExpired:
            self._log_error(f"yt-dlp timeout for @{username}/streams")
            return []
        except Exception as exc:
            self._log_error(f"Fallback scan error for @{username}: {exc}")
            return []

    def _fallback_scan_channel_streams(self, channel_id: str, settings: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback method: Use yt-dlp to scrape channel's /streams or /videos page for live videos"""
        try:
            limit = settings.get("fallback_scan_limit", 10)

            # Try both /streams and /videos tabs
            # Also try @username format if we have it in the original input
            urls_to_try = [
                f"https://www.youtube.com/channel/{channel_id}/streams",
                f"https://www.youtube.com/channel/{channel_id}/videos",
            ]

            for channel_url in urls_to_try:
                self._log(f"Scraping channel page: {channel_url}")

                # Use yt-dlp to extract video IDs from the page
                cmd = [
                    self._ytdlp_path,
                    "--flat-playlist",
                    "--dump-json",
                    "--playlist-end", str(limit),
                    "--no-warnings",
                    channel_url
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if result.returncode != 0:
                    self._log(f"Failed to scrape {channel_url}: {result.stderr[:200]}")
                    continue  # Try next URL

                # Parse each line as a JSON object (yt-dlp outputs one JSON per line in flat-playlist mode)
                live_streams = []
                for line in result.stdout.strip().split('\n'):
                    if not line:
                        continue

                    try:
                        video_info = json.loads(line)
                        video_id = video_info.get("id")

                        if not video_id:
                            continue

                        # Check if this video is actually live by getting full metadata
                        self._log(f"Checking if video {video_id} is live...")
                        metadata = self._extract_stream_metadata(video_id, settings.get("stream_quality", "best"))

                        if metadata and metadata.get("is_live"):
                            live_streams.append({
                                "video_id": video_id,
                                "title": metadata.get("title", "Unknown"),
                                "thumbnail": metadata.get("thumbnail", ""),
                            })
                            self._log(f"✓ Found live stream via fallback: {metadata.get('title')} (ID: {video_id})")

                            # Stop if we've found enough
                            if len(live_streams) >= limit:
                                break
                        else:
                            self._log(f"✗ Video {video_id} is not live, skipping")

                    except json.JSONDecodeError:
                        continue
                    except Exception as exc:
                        self._log_error(f"Error checking video: {exc}")
                        continue

                # If we found live streams, return them
                if live_streams:
                    return live_streams

            # Tried all URLs, found nothing
            return []

        except subprocess.TimeoutExpired:
            self._log_error(f"yt-dlp fallback scan timed out for {channel_id}")
            return []
        except Exception as exc:
            self._log_error(f"Fallback scan error for {channel_id}: {exc}")
            return []

    def _parse_channel_ids(self, raw: str) -> List[str]:
        """Parse channel IDs from combined format string.

        Handles: @channel, @channel=90, @channel=90:filter
        Extracts just the channel part, ignoring =number:filter suffix.
        """
        # Split by common separators
        parts = re.split(r'[,;\n]+', raw)

        self._log(f"Parsing monitored channels input: {raw[:100]}")  # Show first 100 chars
        self._log(f"Split into {len(parts)} part(s): {[p.split('=')[0].strip() for p in parts if p.strip()]}")

        channel_ids = []
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # Strip off =number:filter suffix if present (combined format)
            if "=" in part:
                part = part.split("=")[0].strip()

            # Check if it's just @username (without URL)
            if part.startswith("@"):
                username = part[1:]  # Remove the @ symbol
                self._log(f"Detected @username format: @{username}")
                resolved_id = self._resolve_username_to_channel_id(username)
                if resolved_id:
                    channel_ids.append(resolved_id)
                    self._log(f"Resolved @{username} to channel ID: {resolved_id}")
                else:
                    self._log_error(f"Could not resolve @{username} to channel ID. Please use channel ID (UC...) instead.")
                continue

            # Extract channel ID from URL if needed
            if "youtube.com" in part or "youtu.be" in part:
                # Try to extract channel ID from URL formats:
                # - /channel/UC...
                # - /@username
                # - /c/channelname

                # Direct channel ID
                match = re.search(r'/channel/([a-zA-Z0-9_-]+)', part)
                if match:
                    channel_ids.append(match.group(1))
                    self._log(f"Parsed channel ID: {match.group(1)} from {part}")
                    continue

                # @username in URL - need to resolve to channel ID
                match = re.search(r'/@([a-zA-Z0-9_-]+)', part)
                if match:
                    username = match.group(1)
                    # Try to resolve @username to channel ID
                    resolved_id = self._resolve_username_to_channel_id(username)
                    if resolved_id:
                        channel_ids.append(resolved_id)
                        self._log(f"Resolved @{username} to channel ID: {resolved_id}")
                    else:
                        self._log_error(f"Could not resolve @{username} to channel ID. Please use channel ID (UC...) instead.")
                    continue

                # /c/ format
                match = re.search(r'/c/([a-zA-Z0-9_-]+)', part)
                if match:
                    channel_name = match.group(1)
                    self._log_error(f"/c/ format not supported. Please find channel ID (UC...) for: {channel_name}")
                    continue

                # Fallback: might be direct channel ID in URL
                self._log_error(f"Could not parse channel ID from URL: {part}")
            else:
                # Assume it's already a channel ID (starts with UC usually)
                if part.startswith("UC") or len(part) == 24:
                    channel_ids.append(part)
                    self._log(f"Using channel ID: {part}")
                else:
                    self._log_error(f"Invalid channel ID format: {part}. Should be 24 characters starting with UC or @username")

        return channel_ids

    def _resolve_username_to_channel_id(self, username: str) -> Optional[str]:
        """Try to resolve @username to channel ID, using cache when available.

        Cache is stored in settings['username_cache'] and persists across restarts.
        """
        # Check cache first
        try:
            cfg = PluginConfig.objects.get(key=self._plugin_key)
            settings = dict(cfg.settings or {})
            username_cache = settings.get("username_cache", {})

            if username in username_cache:
                channel_id = username_cache[username]
                self._log(f"Cache hit: @{username} -> {channel_id}")
                return channel_id
        except PluginConfig.DoesNotExist:
            username_cache = {}

        # Cache miss - scrape the channel page
        try:
            url = f"https://www.youtube.com/@{username}"

            request = urllib.request.Request(url)
            request.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

            with urllib.request.urlopen(request, timeout=10) as response:
                html = response.read().decode('utf-8', errors='ignore')

            channel_id = None

            # Look for channel ID in the HTML
            # Pattern: "channelId":"UCxxxxxxxxxxxxxxxx"
            match = re.search(r'"channelId":"(UC[a-zA-Z0-9_-]{22})"', html)
            if match:
                channel_id = match.group(1)

            # Alternative pattern: "externalId":"UCxxxxxxxxxxxxxxxx"
            if not channel_id:
                match = re.search(r'"externalId":"(UC[a-zA-Z0-9_-]{22})"', html)
                if match:
                    channel_id = match.group(1)

            # Try browse_id pattern
            if not channel_id:
                match = re.search(r'"browseId":"(UC[a-zA-Z0-9_-]{22})"', html)
                if match:
                    channel_id = match.group(1)

            if channel_id:
                self._log(f"Resolved @{username} to {channel_id}")
                # Cache the result
                username_cache[username] = channel_id
                self._persist_settings({"username_cache": username_cache})
                return channel_id

            self._log_error(f"Could not find channel ID in webpage for @{username}")
            return None

        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self._log_error(f"YouTube channel @{username} not found (404)")
            else:
                self._log_error(f"HTTP error resolving @{username}: {exc.code}")
            return None
        except Exception as exc:
            self._log_error(f"Error resolving @{username}: {exc}")
            return None

    # --- URL Refresh ---

    def _refresh_expiring_urls(self, settings: Dict[str, Any]) -> int:
        """Refresh stream URLs that are approaching expiration. Returns count of refreshed URLs"""
        tracked_streams = settings.get("tracked_streams", {})
        refresh_interval = settings.get("url_refresh_interval_seconds", 3600)
        now = datetime.now(dt_timezone.utc)
        refreshed_count = 0

        for video_id, stream_data in tracked_streams.items():
            if not stream_data.get("is_live"):
                continue

            last_refresh_str = stream_data.get("last_url_refresh")
            if not last_refresh_str:
                continue

            try:
                last_refresh = datetime.fromisoformat(last_refresh_str.replace("Z", "+00:00"))
                if isinstance(last_refresh.tzinfo, type(None)):
                    last_refresh = last_refresh.replace(tzinfo=dt_timezone.utc)

                age_seconds = (now - last_refresh).total_seconds()

                if age_seconds > refresh_interval:
                    # Refresh needed
                    quality = settings.get("stream_quality", "best")
                    metadata = self._extract_stream_metadata(video_id, quality)

                    if metadata and metadata.get("stream_url"):
                        # Update Stream object
                        try:
                            stream = Stream.objects.get(id=stream_data["stream_id"])
                            stream.url = metadata["stream_url"]
                            stream.save(update_fields=["url"])

                            # Update tracked metadata
                            stream_data["stream_url"] = metadata["stream_url"]
                            stream_data["last_url_refresh"] = now.isoformat()
                            stream_data["is_live"] = metadata.get("is_live", False)

                            refreshed_count += 1
                            self._log(f"Refreshed URL for: {stream_data.get('title')}")

                        except Stream.DoesNotExist:
                            self._log_error(f"Stream {stream_data['stream_id']} not found")

            except Exception as exc:
                self._log_error(f"Failed to refresh URL for {video_id}: {exc}")

        # Persist updates
        if refreshed_count > 0:
            self._persist_settings({"tracked_streams": tracked_streams})

        return refreshed_count

    # --- Cleanup ---

    def _cleanup_ended_streams(self, settings: Dict[str, Any], force: bool = False) -> int:
        """Remove channels for ended streams. Returns count of cleaned channels"""
        tracked_streams = settings.get("tracked_streams", {})
        auto_cleanup = settings.get("auto_cleanup", True)

        if not auto_cleanup and not force:
            return 0

        cleaned_count = 0
        to_remove = []

        for video_id, stream_data in tracked_streams.items():
            if not stream_data.get("is_live") or force:
                try:
                    # Delete Channel
                    channel_id = stream_data.get("channel_id")
                    if channel_id:
                        try:
                            channel = Channel.objects.get(id=channel_id)
                            channel.delete()
                            cleaned_count += 1
                            self._log(f"Deleted channel: {stream_data.get('title')}")
                        except Channel.DoesNotExist:
                            pass

                    # Delete Stream (if not used by other channels)
                    stream_id = stream_data.get("stream_id")
                    if stream_id:
                        try:
                            stream = Stream.objects.get(id=stream_id)
                            if not stream.channelstream_set.exists():
                                stream.delete()
                        except Stream.DoesNotExist:
                            pass

                    to_remove.append(video_id)

                except Exception as exc:
                    self._log_error(f"Cleanup failed for {video_id}: {exc}")

        # Remove from tracked streams
        for video_id in to_remove:
            del tracked_streams[video_id]

        # Persist updates
        if cleaned_count > 0:
            self._persist_settings({"tracked_streams": tracked_streams})

        return cleaned_count

    # --- Monitoring Thread ---

    def _monitoring_loop(self, plugin_key: str) -> None:
        """Background monitoring loop (runs in daemon thread)"""
        self._log("Monitoring loop started")

        while not self._monitor_stop_event.is_set():
            try:
                # Check in-memory flag first (authoritative - DB flag can be overwritten by Dispatcharr)
                if not self._monitoring_active:
                    self._log("Monitoring disabled (in-memory flag), stopping")
                    break

                # Reload settings from database
                try:
                    cfg = PluginConfig.objects.get(key=plugin_key)
                    settings = dict(cfg.settings or {})
                except PluginConfig.DoesNotExist:
                    self._log_error("Plugin config not found, stopping monitoring")
                    break

                # Re-persist monitoring_active to DB in case Dispatcharr overwrote it
                if not settings.get("monitoring_active"):
                    self._log("DB shows monitoring_active=False but in-memory flag is True, re-persisting")
                    self._persist_settings({"monitoring_active": True})

                # Check API quota
                if self._is_quota_exceeded(settings):
                    self._log("API quota exceeded, pausing monitoring")
                    self._monitor_stop_event.wait(3600)  # Wait 1 hour
                    continue

                # Poll channels
                try:
                    added, ended = self._poll_monitored_channels(settings)

                    # Refresh URLs
                    refreshed = self._refresh_expiring_urls(settings)

                    # Cleanup if enabled
                    if settings.get("auto_cleanup", True):
                        cleaned = self._cleanup_ended_streams(settings)
                    else:
                        cleaned = 0

                    # Trigger webhook if channels changed
                    if added > 0 or cleaned > 0:
                        self._trigger_webhook(settings)

                except Exception as exc:
                    self._log_error(f"Poll cycle error: {exc}")

                # Sleep for poll interval
                poll_interval = settings.get("poll_interval_minutes", 15)
                sleep_seconds = poll_interval * 60

                # Sleep in small chunks so we can respond to stop signal
                for _ in range(int(sleep_seconds)):
                    if self._monitor_stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as exc:
                self._log_error(f"Monitoring loop error: {exc}")
                time.sleep(60)  # Back off on error

        self._log("Monitoring loop stopped")

    # --- API Quota Management ---

    def _increment_api_quota(self, settings: Dict[str, Any], units: int) -> None:
        """Track API quota usage.

        Only persists quota-related keys to avoid clobbering other settings
        that may have been updated by concurrent operations.
        """
        today = datetime.now(dt_timezone.utc).date().isoformat()
        quota_date = settings.get("quota_reset_date", "")

        if quota_date != today:
            # Reset quota for new day
            self._persist_settings({
                "api_calls_today": units,
                "quota_reset_date": today
            })
            # Update local copy for caller
            settings["api_calls_today"] = units
            settings["quota_reset_date"] = today
        else:
            # Increment quota
            new_count = settings.get("api_calls_today", 0) + units
            self._persist_settings({"api_calls_today": new_count})
            # Update local copy for caller
            settings["api_calls_today"] = new_count

    def _is_quota_exceeded(self, settings: Dict[str, Any]) -> bool:
        """Check if API quota is exceeded (95% of 10,000 daily limit)"""
        api_calls = settings.get("api_calls_today", 0)
        return api_calls >= 9500

    # --- State Management ---

    def _persist_settings(self, updates: Dict[str, Any]) -> None:
        """Persist settings updates to database (thread-safe)"""
        try:
            # Use select_for_update to prevent race conditions
            with transaction.atomic():
                cfg = PluginConfig.objects.select_for_update().get(key=self._plugin_key)
                settings = dict(cfg.settings or {})
                settings.update(updates)
                cfg.settings = settings
                cfg.save(update_fields=["settings", "updated_at"])
        except PluginConfig.DoesNotExist:
            self._log_error("Plugin config not found")

    # --- Logging ---

    def _trigger_webhook(self, settings: Dict[str, Any]) -> None:
        """Trigger webhook URL when channels change (with configurable delay)"""
        webhook_url = settings.get("webhook_url", "").strip()

        if not webhook_url:
            return  # Webhook disabled

        # Get delay setting (default 5 seconds to let Dispatcharr finish processing)
        delay_seconds = settings.get("webhook_delay_seconds", 5)
        try:
            delay_seconds = int(delay_seconds)
            if delay_seconds < 0:
                delay_seconds = 0
            elif delay_seconds > 60:
                delay_seconds = 60
        except (TypeError, ValueError):
            delay_seconds = 5

        try:
            if delay_seconds > 0:
                self._log(f"Waiting {delay_seconds}s before triggering webhook...")
                time.sleep(delay_seconds)

            self._log(f"Triggering webhook: {webhook_url}")
            req = urllib.request.Request(webhook_url, method='POST')
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
                if status in [200, 204]:
                    self._log(f"Webhook triggered successfully (HTTP {status})")
                else:
                    self._log(f"Webhook returned HTTP {status}")
        except Exception as exc:
            self._log_error(f"Failed to trigger webhook: {exc}")

    def _send_telegram_notification(self, settings: Dict[str, Any], video_id: str, metadata: Dict[str, Any], channel_number: int, channel_uuid: str) -> None:
        """Send Telegram notification when a new channel is added"""
        telegram_url = settings.get("telegram_webhook_url", "").strip()

        if not telegram_url:
            return  # Telegram notifications disabled

        try:
            # Build the payload for Claudia (use channel UUID for Dispatcharr stream URL)
            base_url = settings.get("dispatcharr_base_url", "").strip().rstrip("/")
            if not base_url:
                self._log("Skipping Telegram notification: dispatcharr_base_url not configured")
                return
            dispatcharr_url = f"{base_url}/proxy/ts/stream/{channel_uuid}"
            self._log(f"DEBUG: channel_uuid={channel_uuid}, dispatcharr_url={dispatcharr_url}")
            payload = {
                "title": metadata.get("title", "YouTube Live Stream"),
                "channel": metadata.get("youtube_channel_name", "YouTube"),
                "url": dispatcharr_url,
                "description": f"Added as Dispatcharr Channel #{channel_number}",
                "timestamp": datetime.now(dt_timezone.utc).isoformat()
            }

            self._log(f"Sending Telegram notification for: {metadata.get('title', 'stream')[:60]}...")
            self._log(f"DEBUG: payload={json.dumps(payload, indent=2)}")

            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(telegram_url, data=data, method='POST')
            req.add_header('Content-Type', 'application/json')

            with urllib.request.urlopen(req, timeout=10) as response:
                status = response.status
                if status in [200, 201, 204]:
                    self._log(f"Telegram notification sent successfully (HTTP {status})")
                else:
                    self._log(f"Telegram notification returned HTTP {status}")
        except Exception as exc:
            self._log_error(f"Failed to send Telegram notification: {exc}")

    def _log(self, message: str) -> None:
        """Write log message"""
        timestamp = datetime.now().isoformat()
        log_msg = f"[{timestamp}] {message}\n"

        try:
            # Rotate log if too large
            if self._log_path.exists() and self._log_path.stat().st_size > self._log_max_bytes:
                backup = self._log_path.with_suffix(".log.old")
                if backup.exists():
                    backup.unlink()
                self._log_path.rename(backup)

            with open(self._log_path, "a") as f:
                f.write(log_msg)
        except Exception:
            pass

    def _log_error(self, message: str) -> None:
        """Write error log message"""
        self._log(f"ERROR: {message}")

    # --- Binary Finder ---

    def _find_ytdlp_binary(self) -> Optional[str]:
        """Find yt-dlp binary (bundled or system-installed)"""
        # First, check for bundled yt-dlp in plugin directory
        bundled_ytdlp = self._base_dir / "yt-dlp"
        if bundled_ytdlp.exists() and bundled_ytdlp.is_file():
            # Make sure it's executable
            try:
                bundled_ytdlp.chmod(0o755)
                # Test it works
                result = subprocess.run(
                    [str(bundled_ytdlp), "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    self._log(f"Using bundled yt-dlp: {bundled_ytdlp}")
                    return str(bundled_ytdlp)
            except Exception as exc:
                self._log_error(f"Bundled yt-dlp failed: {exc}")

        # Fall back to system-installed yt-dlp
        binary_names = ["yt-dlp", "youtube-dl"]

        for binary in binary_names:
            try:
                result = subprocess.run(
                    ["which", binary],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    path = result.stdout.strip()
                    self._log(f"Found system {binary} at: {path}")
                    return path
            except Exception:
                continue

        # Try direct execution
        for binary in binary_names:
            try:
                result = subprocess.run(
                    [binary, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    self._log(f"Found system {binary} (executable)")
                    return binary
            except Exception:
                continue

        self._log_error("yt-dlp not found. Plugin includes bundled version, but it may not be working.")
        return None
