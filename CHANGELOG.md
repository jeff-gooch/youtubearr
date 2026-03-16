# YouTubearr Changelog

## [1.14.0] - 2026-03-15

### Fixed - EPG Not Appearing in Jellyfin

**Bug:** EPG data for YouTube streams showed correctly in Dispatcharr but not in Jellyfin.

**Root cause:** Dispatcharr reads EPG directly from the database, but Jellyfin reads from XMLTV cache files at `/app/media/cached_epg/{source_id}.tmp`. YouTubearr was creating database records but never generating the XMLTV cache file.

**Fix:** Added `_generate_xmltv_cache()` method that writes the EPG data to the XMLTV cache file before triggering the webhook. Now when streams are added:
1. EPGData/ProgramData saved to database (Dispatcharr sees it immediately)
2. XMLTV cache file generated
3. Webhook triggers Jellyfin refresh
4. Jellyfin reads updated XMLTV file

## [1.13.0] - 2026-03-15

### Added - Channel Numbering Mode (Issue #2)

**Feature:** Choose between decimal sub-channels and sequential whole numbers.

**New Setting:** Channel Numbering Mode
- **Decimal** (default): 90.1, 90.2, 90.3 - groups streams from the same YouTube channel
- **Sequential**: 2000, 2001, 2002 - for systems that don't handle decimal channels well

**Use Case:** Some IPTV players or guide systems have issues with decimal channel numbers (e.g., treating 90.10 as 90.1). Switch to Sequential mode for full compatibility.

### Added - Cookies Support

**Feature:** Paste YouTube cookies directly in the settings UI.

**New Setting:** YouTube Cookies
- Paste cookies in Netscape format (from browser extension exports)
- Cookies are used as fallback when stream extraction fails without authentication
- Helps with age-restricted content or regional restrictions

**How it works:**
1. If stream extraction fails without cookies, plugin retries with cookies
2. Cookies are stored in the plugin directory as `cookies.txt`
3. No need to manually upload files

### Added - QuickJS Runtime (Bundled)

**Feature:** Bundled QuickJS-NG binary for yt-dlp's JavaScript runtime requirements.

YouTube's PO token extraction sometimes requires a JavaScript runtime. The plugin now bundles QuickJS-NG and automatically uses it when available.

**Technical details:**
- QuickJS-NG v0.12.1 binary bundled as `qjs`
- Automatically detected and used via `--js-runtimes quickjs:/path/to/qjs`
- Falls back gracefully if QuickJS isn't working

### Fixed - EPG Programme Data Not Showing

**Bug:** EPG programmes for YouTube channels weren't appearing in the guide output.

**Root cause:** The plugin was using channel names (e.g., "NASA #1") as tvg_id, but Dispatcharr's EPG XML output uses channel numbers (e.g., "92.1") as channel IDs. The mismatch meant programmes weren't linked to channels.

**Fix:** Changed EPGData and ProgramData to use channel_number as tvg_id, matching Dispatcharr's EPG output format.

### Fixed - Code Quality Issues

- Fixed bare `except:` clause to catch specific exceptions
- Removed redundant `import re` inside function
- Fixed version number in Plugin class (was still showing 1.12.4)

## [1.12.4] - 2026-03-14

### Fixed - EPG Program Titles

**Fix:** Added ProgramData entries so the EPG guide displays the livestream title while channel names stay in `{YouTube Channel} #{N}` format.

## [1.12.3] - 2026-03-14

### Fixed - EPG Title vs Channel Name

**Fix:** EPG entries now use the **stream title** for guide display, while channel names remain in the format `{YouTube Channel} #{N}`.

**Result:** Channel names stay compact and consistent, while the EPG shows the actual livestream title.

## [1.12.2] - 2026-03-14

### Added - Auto-Create Dummy EPG

**Feature:** Plugin now automatically creates a Dummy EPG source and assigns it to YouTube channels.

**Changes:**
- EPG Source Name now defaults to "YouTube Live" instead of empty
- Automatically creates the Dummy EPG source if it doesn't exist
- Automatically creates EPG data entries for each YouTube channel
- No manual EPG setup required on fresh installs

**Result:** YouTube channels now have guide data out of the box.

## [1.12.1] - 2026-03-13

### Fixed - Removed Stale API Key Requirement

**Bug:** Plugin showed "YouTube API key required" error when starting monitoring or refreshing, even though API key was removed in v1.10.0.

**Fixes:**
- Removed API key check from `_handle_start_monitoring()`
- Removed API key check from `_handle_refresh()`
- Fixed auto-restart check to use correct settings key (`monitored_channels` instead of `youtube_channels`)

**Result:** Plugin now works immediately with zero configuration beyond adding channels to monitor.

## [1.12.0] - 2026-03-11

### Added - GitHub Release

- Packaged plugin for GitHub distribution
- Added LICENSE (Unlicense/public domain)
- Added THIRD_PARTY_NOTICES.md for yt-dlp attribution
- Added comprehensive THIRD_PARTY_LICENSES.txt for bundled yt-dlp dependencies
- Added .gitignore for clean repository
- Sanitized examples (removed personal domains/IPs)

## [1.11.3] - 2026-03-10

### Changed
- Simplified channel names: now uses "{YouTube Channel} #{stream_number}" format
- Example: "NASASpaceflight #1" instead of "NASASpaceflight - Space Coast Live: 24/7... - 2026-03-10 00:25"
- EPG still shows full video title for program details

## [1.11.2] - 2026-03-09

### Fixed
- Auto-restart monitoring after container/service restarts
- Previously, monitoring would stop when Dispatcharr was restarted and required manual restart
- Now automatically resumes when viewing the plugin page if monitoring was enabled

## [1.11.1] - 2026-03-08

### Fixed
- Removed leftover `results_truncated` variable reference from old API code
- Fixed ended-stream detection to use `monitored_channel_id` instead of `youtube_channel_id`

## [1.11.0] - 2026-03-08

### Changed - Simplified Settings (Combined Format)

**Breaking Change:** Settings page simplified - removed deprecated fields and combined channel config into single field.

**Removed fields:**
- YouTube Data API Key (no longer needed - monitoring uses yt-dlp)
- Enable Fallback Scanning (deprecated - yt-dlp is now primary)
- Fallback Scan Limit (deprecated)
- Channel Number Mapping (merged into Monitored Channels)

**New combined format in "Monitored YouTube Channels":**
```
@NASA=92
@RyanHallYall=90
@OfficialYallBot=90
@VirtualRailfan=91:Horseshoe Curve|La Grange|Glendale|Anchorage
@NASASpaceFlight=93
```

**Format options:**
- `@channel` - monitor with auto-assigned channel number
- `@channel=90` - monitor with base channel 90
- `@channel=90:TitleFilter` - monitor with base 90 + title filtering

**Benefits:**
- Single field for all channel configuration
- No duplicate channel entries
- Cleaner settings page
- Backwards compatible with existing monitored_channels values

## [1.10.0] - 2026-03-08

### Changed - Zero API Quota Monitoring (Major Update!)

**Breaking Change:** Monitoring no longer uses the YouTube Data API. Instead, it uses `yt-dlp --flat-playlist` to scan channels directly.

**Why this matters:**
- **Zero API quota usage** - No more 10,000 unit daily limits
- **Faster for large channels** - VirtualRailfan (360 streams) scans in ~3 seconds
- **More reliable** - No API key required, no quota errors
- **Title filtering is faster** - Filters applied BEFORE full metadata extraction

**Performance comparison:**

| Method | Time (5 channels) | API Quota |
|--------|-------------------|-----------|
| Old (YouTube API) | ~1s | 500+ units |
| Old fallback | ~140s | 0 |
| **New (flat-playlist)** | **~21s** | **0** |

**What changed:**
- `_poll_monitored_channels()` now uses `_get_live_streams_via_ytdlp()`
- Title filter applied early (before yt-dlp metadata extraction)
- API key field marked as optional (kept for potential future use)
- Fallback scan setting deprecated (yt-dlp is now the primary method)

**Migration:** No action required. Just update the plugin and monitoring works without API key.

## [1.9.4] - 2026-03-08

### Added - Title Filter for Selective Stream Addition

**Use Case:** When monitoring channels that have many simultaneous streams (e.g., VirtualRailfan with 70+ streams), you can now filter to only add streams matching specific titles.

**New Feature:** Title filter in Channel Number Mapping

**Format:**
```
@ChannelName=BaseNumber:TitleFilter
```

**Examples:**
```
@VirtualRailfan=91:Horseshoe Curve|La Grange|Glendale
@WeatherChannel=90
@MultiStreamChannel=92:Pattern1|Pattern2
```

**How it works:**
1. Parse mapping: `@VirtualRailfan=91:Horseshoe Curve|La Grange|Glendale`
2. When monitoring finds a stream, check title against filter (regex, case-insensitive)
3. If filter matches → add stream with sub-channel under base 91
4. If filter doesn't match → skip stream (logged but not added)

**Filter Syntax:**
- Use `|` (pipe) to match multiple patterns (OR)
- Case-insensitive matching
- Supports full regex syntax
- No filter = add all streams from that channel

**Logs show:**
```
Title filter MATCH: 'Horseshoe Curve LIVE...' matches 'Horseshoe Curve|La Grange|Glendale'
Title filter SKIP: 'Some Other Location...' does not match 'Horseshoe Curve|La Grange|Glendale'
```

## [1.9.3] - 2026-03-08

### Added - Webhook Delay Setting

**Issue:** The Jellyfin webhook was triggered before Dispatcharr finished processing new channels, causing incomplete guide data.

**Fix:** Added configurable delay before triggering the webhook.

**New Setting:**
- **Webhook Delay (seconds)** - Default: 5 seconds, Range: 0-60
- Allows Dispatcharr to finish saving channel data before Jellyfin refreshes

**Logs now show:**
```
Waiting 5s before triggering webhook...
Triggering webhook: http://jellyfin:8096/...
Webhook triggered successfully (HTTP 204)
```

## [1.9.2] - 2026-03-08

### Fixed - Sub-Channel/Aggregated Stream Mapping

**Issue:** When a YouTube channel aggregates streams from sub-channels (e.g., @nasa hosting NASA TV streams), the mapping didn't work because the stream's actual channel ID differs from the monitored channel ID.

**Example:**
- You monitor `@nasa` (ID: `UC_NASA_MAIN`)
- A stream goes live from `NASA TV` sub-channel (ID: `UC_NASA_TV`)
- Mapping `@nasa=92` didn't match because stream has different ID

**Fix:** Now tracks `monitored_channel_id` separately from stream's `youtube_channel_id`:
- When monitoring finds a stream, the *monitored* channel ID is used for mapping
- This ensures all streams found via `@nasa` use the `@nasa=92` mapping, regardless of which sub-channel they're actually from

**tracked_streams now stores both:**
- `monitored_channel_id`: The channel being polled (for mapping)
- `youtube_channel_id`: The stream's actual channel (for reference)

## [1.9.1] - 2026-03-08

### Fixed - Channel Mapping Not Matching @Handles

**Issue:** Channel number mapping using `@Handle=90` didn't work because the lookup was comparing @handle against YouTube's display name (e.g., `@RyanHallYall` vs `Ryan Hall, Y'all`).

**Fix:** When parsing `@Handle` entries in the mapping, now resolves the handle to the channel ID (UC...) and matches by that instead of display name.

**How it works now:**
1. User enters `@RyanHallYall=90`
2. Plugin resolves `@RyanHallYall` → `UCBBsPuUY-8UwkSim4zLXp1w`
3. When adding a stream, matches by channel ID (reliable) instead of display name

**Logs now show:**
```
Mapping: @RyanHallYall (UCBBsPuUY-8UwkSim4zLXp1w) → base 90
Channel 'Ryan Hall, Y'all' (UCBBsPuUY-8UwkSim4zLXp1w) mapped to base 90
```

## [1.9.0] - 2026-03-08

### Added - Sub-Channel Numbering

**Feature:** Group streams from the same YouTube channel using decimal sub-channels (e.g., 90.1, 90.2, 90.3).

**Automatic grouping (no config needed):**
- First stream from Channel A → 2000.1
- Second stream from Channel A → 2000.2
- First stream from Channel B → 2001.1

**Optional: Channel Number Mapping setting**
```
@WeatherChannel=90
@SpaceChannel=91
@NewsChannel=92
@RelatedNews=92
```

Format: `@ChannelName=BaseNumber` (one per line)

**Behavior with mapping:**
- WeatherChannel streams → 90.1, 90.2, 90.3...
- NewsChannel + RelatedNews → 92.1, 92.2... (grouped together!)
- Unmapped channels → auto-assigned next available base

**Benefits:**
- Related streams grouped together in the guide
- Multiple YouTube channels can share the same base number
- Sub-channels work beyond .9 (e.g., .10, .11, .12)
- Unmapped channels automatically get their own base number

**Technical details:**
- Queries existing channels to find next available sub-number
- Stores `youtube_channel_name` in tracked_streams for grouping

## [1.8.4] - 2026-03-07

### Fixed - Monitoring Loop Immediately Stopping

**Issue:** Monitoring would start, then immediately stop within milliseconds. The logs showed:
```
Monitoring loop started
Monitoring disabled, stopping  (4ms later!)
Monitoring loop stopped
```

**Root cause:** Race condition with Dispatcharr's form save mechanism.
1. Plugin sets `monitoring_active: True` in DB
2. Plugin starts background thread
3. Dispatcharr saves form data to DB (overwrites settings, sets `monitoring_active` to False)
4. Thread reads settings → sees `monitoring_active = False` → stops

**Fix:** Added in-memory `_monitoring_active` flag that is authoritative.
- Set BEFORE starting thread
- Thread checks in-memory flag first, not DB
- If DB shows False but in-memory is True, re-persists True to DB
- Prevents Dispatcharr form saves from accidentally stopping monitoring

## [1.8.3] - 2026-03-07

### Fixed - Ended Stream Detection + Handle Mapping

**Fixes:**
- If YouTube API returns an error payload (HTTP 200 with `"error"`), now treated as an error and skipped to avoid false "stream ended" detections.
- If API results are truncated (hit pagination limit), ended-stream detection is skipped for that channel to prevent false negatives.
- Handle mapping now supports full `https://youtube.com/@handle` entries for fallback scanning.

### Docs - Settings + Quota Clarifications

- Documented **URL Refresh Interval** and **Dispatcharr Base URL** settings.
- Clarified API quota reset as **midnight UTC** and noted pagination can increase quota usage.

## [1.8.2] - 2026-03-07

### Added - URL Refresh Interval Setting

**Issue:** The URL refresh interval (how often stream URLs are refreshed to prevent expiration) was hardcoded to 3600 seconds (1 hour).

**Fix:** Exposed `url_refresh_interval_seconds` as a configurable setting in the UI.

**New Setting:**
- **URL Refresh Interval (seconds)** - Default: 3600 (1 hour)
- Range: 300 (5 min) to 21600 (6 hours)
- YouTube URLs typically expire after ~6 hours, so default is safe

**Use case:** Adjust if you experience URL expiration issues or want to reduce yt-dlp calls.

## [1.8.1] - 2026-03-07

### Added - Username Resolution Caching

**Issue:** Every poll cycle scraped YouTube to resolve @usernames to channel IDs, causing unnecessary network requests and potential rate limiting.

**Fix:** Added persistent cache for username → channel ID mappings.

**Changes:**
- Cache stored in `settings['username_cache']`
- Persists across plugin restarts
- Cache hit logs: `Cache hit: @username -> UCxxxxxxxx`
- Only scrapes YouTube on first resolution

**Benefits:**
- Faster poll cycles after first resolution
- Reduced network requests
- Less likely to be rate-limited by YouTube

## [1.8.0] - 2026-03-07

### Added - API Pagination Support

**Issue:** YouTube API was limited to 5 results with no pagination, missing streams on channels with many concurrent live streams (e.g., VirtualRailfan with 79 streams).

**Fix:** Added pagination support to fetch up to 150 live streams per channel.

**Changes:**
- Increased `maxResults` from 5 to 50 (API maximum)
- Added pagination loop to fetch up to 3 pages (150 streams total)
- Added truncation warning if more pages are available
- Quota tracking now accounts for each API page (100 units per page)

**Example output:**
```
YouTube API request: https://www.googleapis.com/youtube/v3/search...
YouTube API request (page 2)
YouTube API response: 79 total live streams found
```

**If truncated:**
```
WARNING: Results may be truncated (hit 3 page limit). Some streams may not be detected.
```

## [1.7.9] - 2026-03-07

### Added - Configurable Dispatcharr Base URL

**Issue:** The Dispatcharr URL in Telegram notifications was hardcoded to a specific domain.

**Fix:** Added new setting `dispatcharr_base_url` to configure the base URL for stream links.

**New Setting:**
- **Dispatcharr Base URL** - Base URL for stream links (e.g., `https://tv.example.com`)
- Stream URLs are built as `{base_url}/proxy/ts/stream/{uuid}`
- If not configured, Telegram notifications are skipped with a log message

## [1.7.8] - 2026-03-07

### Fixed - Settings Clobber Risk in Quota Tracking

**Issue:** `_increment_api_quota()` passed the entire `settings` dict to `_persist_settings()`, risking data loss from concurrent operations.

**Scenario:**
1. Thread A reads settings with `tracked_streams: {video1}`
2. Thread B adds video2, persists `tracked_streams: {video1, video2}`
3. Thread A calls `_increment_api_quota(settings, 100)` with stale settings
4. `_persist_settings(settings)` overwrites DB → video2 is lost

**Fix:** Changed `_increment_api_quota()` to only persist quota-related keys (`api_calls_today`, `quota_reset_date`) instead of the entire settings dict.

## [1.7.7] - 2026-03-07

### Fixed - False "Stream Ended" Detection on API Errors

**Issue:** Streams were being incorrectly marked as "ended" when YouTube API errors occurred (quota exceeded, network issues, invalid key).

**Root cause:**
- `_get_live_streams_for_channel()` returned `[]` (empty list) on any error
- Calling code couldn't distinguish between "no live streams" and "API error"
- When API failed, all tracked streams for that channel were marked as `is_live=False`
- This caused streams to appear ended and potentially be auto-cleaned

**Fix:**
- Changed `_get_live_streams_for_channel()` to return `None` on error instead of `[]`
- Updated `_poll_monitored_channels()` to detect `None` and skip that channel
- Streams are no longer falsely marked as ended during API outages

**Before:**
```
YouTube API quota exceeded (403)
Stream ended: Horseshoe Curve – Altoona...  ← FALSE POSITIVE
```

**After:**
```
YouTube API quota exceeded (403)
API error for channel UCxxx, skipping ended-stream check to avoid false positives
```

**Impact:** This fix prevents channels from disappearing when the API temporarily fails.

## [1.7.6] - 2026-03-06

### Fixed - Channel Avatar/Logo

**Issue:** yt-dlp doesn't provide `channel_thumbnail` or `uploader_thumbnail` in video metadata, so channels were getting the video thumbnail instead of the channel avatar.

**Fix:** Added `_fetch_channel_avatar()` method that scrapes the YouTube channel page to extract the channel's profile picture/avatar URL.

**Technical details:**
- Fetches channel page HTML using urllib
- Uses regex patterns to find avatar URL (yt3.ggpht.com URLs)
- Falls back to video thumbnail if avatar can't be found
- No API quota cost (uses page scraping instead of API)

## [1.7.5] - 2026-03-06

### Fixed - EPG Assignment Bug

**Issue:** EPG assignment wasn't working because the wrong model name was used.

**Fix:** Changed from `EPGSource` to `EPGData` to match Dispatcharr's actual model name.

**Debug:** Added logging to show what `channel_thumbnail` yt-dlp returns (to debug logo issue).

## [1.7.4] - 2026-03-06

### Added - Logo and EPG Auto-Assignment

**Channel Logo:** Fixed logo assignment by properly creating Logo objects via Dispatcharr's Logo model.

**EPG Auto-Assignment:** New setting to automatically assign a Dummy EPG source to created channels.

**New Settings:**
- **EPG Source Name** - Name of the Dummy EPG to assign to new channels (e.g., "YouTube Live")

**Technical details:**
- Imported `Logo` model from `apps.channels.models`
- Imported `EPGSource` model from `apps.epg.models`
- Creates Logo objects using `Logo.objects.get_or_create(url=..., defaults={"name": ...})`
- Assigns logo to channel via `logo=logo` parameter
- Looks up EPG source by name and assigns to `channel.epg_data`
- Reuses existing logos with same URL to avoid duplicates

## [1.7.3] - 2026-03-06

### Improved - Channel Naming and Logo Support

**Channel Name Format:** Changed to `{channel_name} - {title} - {timestamp}` for better Dummy EPG pattern matching.

**Example:**
- Before: `Horseshoe Curve – Altoona, Pennsylvania, USA | LIVE Train Camera (PTZ) 2026-03-05 18:36`
- After: `VirtualRailfan - Horseshoe Curve – Altoona, Pennsylvania, USA | LIVE Train Camera (PTZ) - 2026-03-06 12:30`

**Channel Logo:** Now extracts YouTube channel avatar (`channel_thumbnail`) from yt-dlp and attempts to set it as the channel logo in Dispatcharr.

**Technical details:**
- Added `channel_thumbnail` field to metadata extraction
- Channel name now includes YouTube channel name as prefix
- Timestamp formatted as `YYYY-MM-DD HH:MM`
- Logo setting wrapped in try/except for compatibility

## [1.7.2] - 2026-03-05

### Fixed - Telegram Notification URL (Final Fix)

**Issue:** Telegram notifications were using `stream.id` which returned the integer database ID (e.g., 112799) instead of the UUID needed for Dispatcharr stream URLs.

**Root cause:** The UUID is on the `channel` object, not the `stream` object.

**Fix:** Changed to use `channel.uuid` for building Dispatcharr stream URLs: `https://tv.example.com/proxy/ts/stream/{channel_uuid}`

**Technical details:**
- Changed from `str(stream.pk)` to `str(channel.uuid)`
- Channel model has the UUID field needed for `/proxy/ts/stream/` URLs
- Notifications now link directly to correct Dispatcharr stream for immediate playback

## [1.7.1] - 2026-03-05

### Fixed - Telegram Notification URL (Incomplete)

**Issue:** Telegram notifications were linking to YouTube URLs instead of Dispatcharr stream URLs.

**Attempted fix:** Tried using `str(stream.pk)` but this returned integer ID instead of UUID.

**Note:** This version was superseded by 1.7.2 which correctly uses `channel.uuid`.

## [1.7.0] - 2026-03-05

### Added - Telegram Notifications

**Feature:** Send Telegram notifications via webhook when new YouTube streams are added.

**New Setting:**
- **Telegram Notification URL** - URL for a webhook endpoint
- Example: `https://example.com/webhook/notify`
- Leave empty to disable Telegram notifications

**When notifications are sent:**
- When monitoring detects and adds a new live stream
- When manual URLs are added via "Add Streams" button
- Notifications include: title, YouTube channel name, Dispatcharr stream URL, channel number, timestamp

**Payload format sent to Claudia:**
```json
{
  "title": "🔴 LIVE - Stream Title",
  "channel": "YouTube Channel Name",
  "url": "https://tv.example.com/proxy/ts/stream/UUID",
  "description": "Added as Dispatcharr Channel #200",
  "timestamp": "2026-03-05T14:00:00Z"
}
```

**Telegram message displays:**
- 📺 YouTube Channel Name
- 🔔 [clickable stream title linking to Dispatcharr stream]
- Channel number
- Timestamp in ET

**Use case:** Get instant Telegram notifications when new streams go live on your monitored channels.

**Technical notes:**
- Uses urllib for zero dependencies
- 10 second timeout
- Failures are logged but don't stop stream addition
- Works alongside existing Jellyfin webhook

## [1.6.6] - 2026-03-05

### Fixed - Cleanup Button Deleted All Channels

**Issue:** "Cleanup Orphaned Channels" button was deleting **ALL** channels (including live streams), not just ended/orphaned ones.

**Root cause:**
- Button called `_cleanup_ended_streams(settings, force=True)`
- `force=True` meant "delete everything regardless of live status"
- Caused duplicates when users re-added manual URLs after cleanup

**What it did before:**
- Deleted ALL tracked channels (live and ended)
- Cleared ALL entries from tracked_streams
- User re-adds manual URLs → duplicates created

**What it does now:**
- Only removes channels for ended streams (`is_live=False`)
- Additionally cleans up orphaned tracked_streams entries (where channel was manually deleted)
- Preserves live streams and their tracked_streams data
- Reports: "Cleaned up X ended stream(s), removed Y orphaned entry(ies)"

**Button updates:**
- Label: "Cleanup Orphaned Channels" → "Cleanup Ended Streams"
- Description: Now explicitly states live streams are NOT affected
- Confirmation: Updated to clarify only ended streams are removed

**Use case:** Click this button to remove channels for streams that ended, without affecting currently live streams.

## [1.6.5] - 2026-03-05

### Fixed - Duplicate Channel Numbers After Fresh Install

**Issue:** When installing plugin fresh or clearing tracked_streams, channel numbering would restart at 200 even if channels already existed, causing duplicates.

**Scenario that triggered bug:**
1. Add manual URLs → Creates channels #200-203
2. Click Refresh → Sees empty `tracked_streams`, assigns #200 again
3. Result: Multiple channels with #200

**Root cause:**
- `_get_next_youtube_channel_number()` only looked at `tracked_streams` to find max channel number
- If `tracked_streams` was empty (fresh install), it returned starting_number (200)
- Didn't check existing Dispatcharr channels in the same group

**Fix:**
- Now queries actual Dispatcharr channels in the YouTube Live group
- Combines channel numbers from both `tracked_streams` AND existing channels
- Finds true maximum across all sources
- Prevents duplicate numbering even with empty `tracked_streams`

**Before:**
```
Manual add → #200-203 created
Refresh → tracked_streams empty → assigns #200 again → DUPLICATE
```

**After:**
```
Manual add → #200-203 created
Refresh → checks existing channels → sees max is 203 → assigns #204 ✓
```

## [1.6.4] - 2026-03-05

### Reverted - Channel Naming

**Change:** Reverted back to using video title as channel name (original behavior).

**Reason:** Using YouTube channel name broke EPG/guide data display in Jellyfin. Video titles work better with Dispatcharr's dummy XMLTV EPG generation.

**Current behavior:**
- Channel Name: Video title (e.g., "🔴 LIVE - WATER RESCUES...")
- Stream Name: Video title (same)
- EPG/guide data displays correctly

**This matches the original behavior that worked well for Virtual Railfan channels.**

## [1.6.3] - 2026-03-05

### Fixed - Monitoring Stops When Clicking Refresh

**Issue:** Clicking "Refresh Now" button caused monitoring to stop automatically.

**Root cause:**
- Dispatcharr passes settings from UI form to action handlers
- UI form doesn't include `monitoring_active` flag
- Monitoring loop reads updated settings and sees `monitoring_active` as missing/False
- Monitoring stops itself with "Monitoring disabled, stopping"

**Fix:**
- `_handle_refresh` now reads settings directly from database instead of using `context.get("settings")`
- Preserves `monitoring_active` flag so monitoring continues running
- Prevents accidental monitoring shutdown when using Refresh button

**Before:** Refresh → Monitoring stops → Manual restart required
**After:** Refresh → Monitoring continues running

## [1.6.2] - 2026-03-05

### Improved - Channel Name Extraction

**Enhancement:** Better channel name extraction with fallback options and improved logging.

**Changes:**
- Try multiple metadata fields for channel name: `channel`, `uploader`, `channel_name`
- Fallback to "YouTube" if all fields are empty
- Added debug logging: `Metadata: title='...', channel='...'`

**Helps diagnose channel naming issues and ensures channel names are always populated.**

## [1.6.1] - 2026-03-05

### Changed - Channel Naming

**Improvement:** Use YouTube channel name as Dispatcharr channel name instead of video title.

**Before:**
- Channel Name: "🔴 LIVE - SEVERE STORMS BATTER DALLAS..."
- Stream Name: "🔴 LIVE - SEVERE STORMS BATTER DALLAS..."

**After:**
- Channel Name: "Ryan Hall Y'all" (YouTube channel name)
- Stream Name: "🔴 LIVE - SEVERE STORMS BATTER DALLAS..." (video title)

**Benefits:**
- Cleaner channel names in guide
- Easier to identify which YouTube channel is streaming
- Video title still visible in EPG/program data
- Better organization when multiple streams from same channel

**Technical details:**
- Uses `info.get("channel")` from yt-dlp metadata
- Falls back to "YouTube" if channel name not available
- Channel name extracted for all streams (manual and monitored)

**Example:**
- VirtualRailfan streams → Channel name: "Virtual Railfan"
- NASA streams → Channel name: "NASA"
- Ryan Hall Y'all → Channel name: "Ryan Hall Y'all"

## [1.6.0] - 2026-03-05

### Added - Webhook Integration

**Feature:** Trigger external webhooks when channels are added or removed.

**Use case:** Automatically refresh Jellyfin LiveTV when YouTubearr adds/removes streams.

**New Settings:**
- **Webhook URL** - URL to POST when channels change
- Leave empty to disable webhooks
- Example: `http://jellyfin:8096/ScheduledTasks/Running/TASK_ID?api_key=KEY`

**When webhook triggers:**
- After monitoring adds new streams
- After manual URL addition
- After cleanup removes ended streams
- After "Refresh Now" action (if changes detected)

**How it works:**
```python
# Plugin sends POST request to webhook URL
# No request body needed
# Logs success/failure in youtubearr.log
```

**Setup for Jellyfin:**
1. Get your Jellyfin "Refresh Guide" task ID:
   ```bash
   curl "http://jellyfin:8096/ScheduledTasks?api_key=YOUR_KEY" | grep RefreshGuide
   ```
2. Set Webhook URL to:
   ```
   http://jellyfin:8096/ScheduledTasks/Running/TASK_ID?api_key=YOUR_KEY
   ```
3. Plugin will trigger Jellyfin refresh automatically

**Error handling:**
- Webhook failures are logged but don't stop plugin operation
- 10 second timeout prevents hanging
- Uses urllib (zero dependencies)

## [1.5.6] - 2026-03-05

### Fixed - Multiple Manual URLs Getting Same Channel Number

**Issue:** When adding multiple manual URLs at once, all streams were assigned the same channel number (#200).

**Root cause:**
- `_handle_add_manual` persisted `tracked_streams` only AFTER all streams were added
- Each call to `_create_stream_and_channel` read `cfg.settings` which had the OLD `tracked_streams`
- `_get_next_youtube_channel_number` saw the same "max assigned number" for all streams
- Result: All streams got #200 instead of #200, #201, #202, etc.

**Fix:**
- Persist `tracked_streams` **immediately after each stream** (not at the end)
- `select_for_update()` ensures next iteration reads fresh data
- Each stream now sees the updated channel numbers

**Before fix:**
```
Added stream: Horseshoe Curve... (Channel #200)
Added stream: La Grange... (Channel #200)  ← duplicate!
Added stream: Glendale... (Channel #200)   ← duplicate!
```

**After fix:**
```
Added stream: Horseshoe Curve... (Channel #200)
Added stream: La Grange... (Channel #201)
Added stream: Glendale... (Channel #202)
```

### Fixed - Manual URLs Not Re-adding After Deletion

**Issue:** Manual URLs had same problem as monitored streams - wouldn't re-add after channel deletion.

**Fix:**
- Added channel existence check to `_handle_add_manual` (same as monitoring)
- If channel was deleted, remove from `tracked_streams` and allow re-add

**Behavior:**
```
Stream ssuM6NJQ2no tracked but channel #201 was deleted, will re-add
Added stream: Horseshoe Curve... (Channel #201)
```

## [1.5.5] - 2026-03-05

### Fixed - Deleted Channels Not Re-adding on Refresh

**Issue:** When you manually deleted a Dispatcharr channel, the stream would not be re-added on the next refresh.

**Root cause:**
- Stream remained in `tracked_streams` dictionary after manual channel deletion
- Plugin checked `if video_id not in tracked_streams` and skipped the stream
- No validation that the Dispatcharr channel still existed

**Fix:**
- Added channel existence check: `Channel.objects.get(id=channel_id)`
- If channel was deleted, remove from `tracked_streams` and allow re-add
- Logs now show: "in_tracked=True but channel #200 was deleted, will re-add"

**Behavior before fix:**
```
Processing stream ezp-7eLXBVs: in_tracked=True
(stream is skipped even though channel #200 was deleted)
```

**Behavior after fix:**
```
Processing stream ezp-7eLXBVs: in_tracked=True but channel #200 was deleted, will re-add
New stream detected: ezp-7eLXBVs, extracting metadata...
Auto-added stream: ... (Channel #200)
```

**Use case:**
- Delete a channel manually to test or fix issues
- Click "Refresh Now" to re-add the stream
- Stream is automatically re-added with fresh metadata

## [1.5.4] - 2026-03-04

### Added - Multiple Manual URL Support

**Requested feature:** Add multiple streams at once via manual URL field.

**What changed:**
- "Manual YouTube URL" field is now a multi-line textarea
- Supports adding multiple streams in one action
- Accepts newline-separated OR comma-separated URLs
- Returns detailed status: added count, skipped count, error count

**How to use:**
1. Paste multiple YouTube livestream URLs into "Manual YouTube URLs" field
2. Separate URLs with newlines OR commas (or both)
3. Click "Add Streams" button
4. Plugin will process all URLs and report results

**Example input:**
```
https://www.youtube.com/watch?v=WMSiw_Hyac8
https://www.youtube.com/watch?v=abc123def45
https://www.youtube.com/watch?v=xyz789ghi01
```

**Example output:**
```
Successfully added 2 streams, skipped 1 (already tracked), 0 errors
```

**Use cases:**
- Add 2-3 VirtualRailfan streams at once
- Quickly populate multiple 24/7 news channels
- Bulk import from a list of URLs

### Technical Details - URL Refresh for 24/7 Streams

**Question:** Do 24/7 continuous streams expire like other channels?

**Answer:** Yes, ALL YouTube stream URLs expire after ~6 hours, but YouTubearr handles this automatically.

**How it works:**
- Plugin tracks `last_url_refresh` timestamp for every stream
- During each monitoring cycle, `_refresh_expiring_urls()` runs
- Any stream with URL older than 3600 seconds (1 hour) gets refreshed
- Fresh URL is fetched via yt-dlp and saved to database
- Happens automatically for ALL tracked streams (including 24/7 streams)

**Configuration:**
- Default refresh interval: 3600 seconds (1 hour)
- Configurable via `url_refresh_interval_seconds` setting
- YouTube URLs typically expire after ~6 hours, so 1-hour refresh is safe

**Result:** 24/7 continuous streams stay live indefinitely with no manual intervention.

## [1.5.0] - 2026-03-05

### Added - Fallback Stream Scanning

**Major feature for channels with complex setups!**

YouTube's Data API often misses live streams on channels that have:
- Multiple simultaneous live streams (e.g., VirtualRailfan with 79 streams)
- Streams on related/legacy channel IDs (e.g., NASA ISS stream)
- Complex channel structures

**New Settings:**
- **Enable Fallback Stream Scanning** - When API finds 0 streams, use yt-dlp to scrape the channel's `/streams` page
- **Fallback Scan Limit** - Maximum streams to check (1-50, default 10)

**How it works:**
1. Poll channel via YouTube Data API (fast, low quota)
2. If API returns 0 results AND fallback is enabled:
   - Use yt-dlp to scrape `youtube.com/channel/ID/streams`
   - Extract video IDs from the streams page
   - Check each video with yt-dlp to verify it's actually live
   - Add confirmed live streams to monitoring

**Performance notes:**
- Disabled by default (keeps monitoring fast)
- Enable for channels known to have issues
- Limit controls how many videos to check per channel
- Much slower than API but finds ALL visible streams

**Example use case:**
- VirtualRailfan has 79 live streams but API returns 0
- Enable fallback scanning with limit=20
- Plugin will scrape streams page and check up to 20 videos
- Adds all confirmed live streams to Dispatcharr

## [1.4.0] - 2026-03-04

### Fixed - Multi-line Channel Input

Changed "Monitored YouTube Channels" field from `type: "string"` (single-line) to `type: "text"` (multi-line textarea).

**Before:** Only first channel was being stored when using comma-separated list

**After:** All channels are properly stored and monitored

## [1.3.6] - 2026-03-04

### Fixed - Duplicate Stream Prevention

**Critical bug fix:** Streams were being added multiple times due to race conditions.

**Root cause:**
- `tracked_streams` was only persisted at the end of polling all channels
- If monitoring was triggered multiple times (e.g., clicking "Refresh Now" multiple times), concurrent polls would both think a stream was new
- This caused duplicate Dispatcharr channels for the same YouTube stream

**Fixes:**
1. **Immediate persistence** - Now persists `tracked_streams` immediately after adding each stream
2. **Double-check before creation** - Reloads settings before creating channel to check if another process just added it
3. **Thread-safe settings updates** - `_persist_settings()` now uses `select_for_update()` to prevent concurrent write conflicts

**Additional improvements:**
- Added logging to show how many channels were parsed from the monitored list
- Shows first 5 channel IDs being polled for debugging

**Behavior before fix:**
```
Auto-added stream: ... (Channel #200)
Auto-added stream: ... (Channel #201)  ← duplicate!
```

**Behavior after fix:**
```
Auto-added stream: ... (Channel #200)
Stream ezp-7eLXBVs was already added by another process, skipping
```

## [1.3.5] - 2026-03-04

### Added - Enhanced Diagnostic Logging

Added comprehensive logging to diagnose why detected livestreams might not be automatically added to Dispatcharr.

**New log messages:**
- When a new stream is detected from the YouTube API
- yt-dlp execution status (success/failure with detailed error messages)
- Detailed live status information from yt-dlp:
  - `is_live` field value
  - `live_status` field value
  - Computed `is_live` result
- Metadata extraction results
- Channel creation attempts
- Clear error messages when streams are skipped

**What to look for in logs:**

1. **If stream is detected but not added**, you'll see:
   ```
   Found live stream: ... (ID: VIDEO_ID)
   New stream detected: VIDEO_ID, extracting metadata...
   ```

2. **If yt-dlp fails**, you'll see:
   ```
   yt-dlp failed for VIDEO_ID (returncode=X)
   yt-dlp stderr: ERROR: ...
   Failed to extract metadata for VIDEO_ID - yt-dlp returned None
   ```

3. **If yt-dlp says stream isn't live**, you'll see:
   ```
   yt-dlp live status for VIDEO_ID: is_live=False, live_status=not_live, computed_is_live=False
   Stream VIDEO_ID is not live (is_live=False), skipping
   ```

4. **If successful**, you'll see:
   ```
   yt-dlp succeeded for VIDEO_ID, parsing JSON output...
   yt-dlp live status for VIDEO_ID: is_live=True, live_status=is_live, computed_is_live=True
   Metadata extracted for VIDEO_ID: is_live=True, title=...
   Creating channel for VIDEO_ID...
   Auto-added stream: ... (Channel #2000)
   ```

## [1.3.4] - 2026-03-04

### Added - Simple @username Support

- Added support for using `@username` directly in monitored channels field
- No longer requires full URL like `https://www.youtube.com/@username`
- Can now use: `@nasa`, `@PBSNewsHour`, `@RyanHallYall`

## [1.3.3] - 2026-03-04

### Added - Enhanced API Response Logging

- Added detailed YouTube API response logging
- Shows number of items returned from API
- Logs pageInfo details when no results found
- Helps diagnose why channels show 0 live streams

## [1.3.2] - 2026-03-04

### Changed - @username Resolution via HTML Scraping

- Replaced yt-dlp channel resolution with direct HTML scraping
- Fetches YouTube channel page and extracts channel ID from HTML
- Supports multiple patterns: channelId, externalId, browseId
- More reliable than yt-dlp for @username resolution

## [1.3.1] - 2026-03-04

### Added - Channel ID Resolution

- Added support for @username format in monitored channels
- Automatically resolves @username to channel ID (UC... format)
- Works with URLs like `https://www.youtube.com/@username`

## [1.3.0] - 2026-03-04

### Changed
- **MAJOR**: Removed google-api-python-client dependency - now uses Python's built-in `urllib`!
- **Plugin now has ZERO external dependencies** - fully self-contained
- Replaced Google API client library with direct HTTP requests to YouTube Data API v3
- Simplified error messages for API errors (quota, invalid key, network)

### Added
- Direct HTTP implementation of YouTube Data API v3 search endpoint
- JSON response parsing with urllib

### Removed
- google-api-python-client dependency
- All imports and checks for googleapiclient library

### Technical Details
- Uses `urllib.request` for YouTube API HTTP calls
- Constructs YouTube search URLs manually with proper parameter encoding
- Handles HTTP 403 (quota), 400/401 (bad key) errors directly
- Plugin is now 100% self-contained with bundled yt-dlp + Python stdlib only

### Benefits
- **Zero dependencies**: No pip install needed, ever
- **Faster deployment**: Copy plugin and restart - that's it
- **More reliable**: No version conflicts with other Python packages
- **Simpler troubleshooting**: Either it works or it doesn't
- **Smaller attack surface**: Fewer external libraries

---

## [1.2.0] - 2026-03-04

### Changed
- **BREAKING**: Removed auto-install functionality (caused permission issues in Docker)
- **NEW**: Bundled yt-dlp binary (3.1MB) - plugin now works with ZERO dependencies!
- Switched from Python yt-dlp library to subprocess calls to bundled binary
- Plugin now checks for bundled yt-dlp first, then falls back to system installation
- Simplified installation process - just copy and restart!

### Added
- `install_dependencies.sh` - Optional script to install google-api-python-client for monitoring
- Bundled yt-dlp binary version (latest stable release)

### Removed
- Auto-install code (_ensure_dependencies, _install_dependencies methods)
- Python yt-dlp import dependency
- Complex dependency management logic

### Fixed
- "Plugin is installing dependencies" error that never completed
- Permission issues in Docker containers
- Simplified error messages with clear installation instructions

### Technical Details
- Plugin now uses `subprocess.run()` to call yt-dlp binary
- JSON output parsing via `--dump-json` flag
- Bundled binary checked first at plugin startup
- Manual URL addition works immediately with no setup
- Monitoring still requires google-api-python-client (optional)

---

## [1.1.0] - 2026-03-04

### Added
- **Configurable Channel Numbering** - New settings to control channel number assignment:
  - `starting_channel_number` - Configure where channel numbering begins (default: 2000)
  - `channel_number_increment` - Configure spacing between channels (default: 1)
- Smart channel number assignment that:
  - Tracks the highest assigned number
  - Auto-increments based on user settings
  - Prevents conflicts with existing channels
  - Falls back to safe defaults if conflicts occur

### Changed
- Channel creation logic now uses `_get_next_youtube_channel_number()` instead of Django's default
- Updated documentation to explain channel numbering options

### Example Use Cases
- **Sequential numbering**: Start: 2000, Increment: 1 → 2000, 2001, 2002...
- **Spaced numbering**: Start: 3000, Increment: 10 → 3000, 3010, 3020...
- **Custom range**: Start: 5000, Increment: 5 → 5000, 5005, 5010...

---

## [1.0.0] - 2026-03-04

### Added
- **Self-Installing Plugin** - Automatically installs dependencies on first use
  - Auto-installs yt-dlp for YouTube stream extraction
  - Auto-installs google-api-python-client for channel monitoring
  - Thread-safe installation with locking mechanism

- **Manual Stream Addition**
  - Add YouTube livestreams by pasting URL
  - Supports multiple URL formats (watch, live, youtu.be)
  - Quality selection (Best, 1080p, 720p, 480p)
  - Video ID extraction with regex patterns

- **Automatic Channel Monitoring**
  - Monitor multiple YouTube channels for new livestreams
  - Background daemon thread with configurable poll interval
  - YouTube Data API v3 integration
  - API quota tracking and management (10,000 units/day)
  - Auto-add new livestreams as Dispatcharr channels
  - Auto-detect when streams end

- **Stream Management**
  - URL refresh every 60 minutes (prevents YouTube URL expiration)
  - Auto-cleanup of ended streams (configurable)
  - Manual cleanup action for ended streams
  - Live status tracking for all streams

- **Error Handling & Logging**
  - Comprehensive error logging with automatic rotation (5MB limit)
  - Network failure recovery
  - API quota exceeded detection
  - Invalid API key detection
  - Thread-safe operations with Django's select_for_update()

- **Configuration Options**
  - YouTube Data API Key
  - Monitored YouTube Channels (comma/newline separated)
  - Poll Interval (5-60 minutes)
  - Stream Quality selection
  - Auto-cleanup toggle
  - Channel Group name
  - Manual URL field

- **Actions**
  - Add Stream - Add single stream from manual URL
  - Start Monitoring - Begin automatic channel monitoring
  - Stop Monitoring - Stop monitoring with confirmation
  - Refresh Now - Force immediate refresh cycle
  - Cleanup - Manual cleanup of ended streams

### Technical Details
- Built on Dispatcharr plugin architecture
- Django model integration (Channel, Stream, ChannelStream, ChannelGroup)
- Uses PluginConfig.settings for state persistence
- Thread-safe channel creation (no duplicates)
- Daemon thread for background monitoring
- 1,100+ lines of Python code
- Comprehensive documentation (README, DEPLOYMENT, QUICK_START)

### Dependencies (Auto-Installed)
- yt-dlp >= 2024.3.10
- google-api-python-client >= 2.100.0
- google-auth >= 2.23.0
- google-auth-httplib2 >= 0.1.1
- google-auth-oauthlib >= 1.1.0
- isodate >= 0.6.1
- urllib3 >= 2.0.0

### Files Included
- plugin.py - Main plugin code
- plugin.json - Plugin metadata
- requirements.txt - Dependency list
- README.md - Full user documentation
- DEPLOYMENT.md - Deployment guide
- QUICK_START.md - Quick reference
- test_url_extraction.py - Test script
- verify_plugin.py - Pre-deployment verification
