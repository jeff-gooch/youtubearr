# PLAN — v1.5 "Replay" support (planning only, no implementation)

Status: **planning document**. Nothing in this file has been implemented. No
replay code, settings fields, lifecycle states, or tests exist yet in
`plugin.py` / `tests/test_plugin.py` as of this writing.

**Release line**: `v1.5` here is a *proposed* future feature release line —
nothing in this plan has a version number assigned yet, and none of it ships
until implemented and reviewed on its own. The *current* released line is
`v1.40.0` (Streamlink playback routing plus the cookie persistence/validation
work it depends on, see `CHANGELOG.md`), which is unrelated in scope and must
not be conflated with this plan. `manifest_version`/`capabilities` (plugins-v3
compatibility prep, tracked as a separate future `v2.0` line) are **not**
declared as of `v1.40.0` and are also unrelated to this plan.

## 1. Goal

When a tracked YouTube livestream ends, instead of immediately deleting its
Dispatcharr channel (current behavior — see §4), keep the channel alive for a
configurable retention window so viewers can watch the stream's YouTube VOD
replay/archive through the same channel, then expire and clean it up
automatically once the window elapses or the archive turns out to be
unavailable.

## 2. Non-goals

- No change to live-stream detection, numbering, or the existing cookie/auth
  workflow (`cookies_content`, `_sync_cookies_sidecar`, `Clear Cookies` — see
  §11). Replay readiness and cookie validity are orthogonal; a replay-eligible
  stream still needs valid cookies for `--http-cookies-file` exactly like a
  live one does.
- No change to the Streamlink-vs-Proxy stream-profile selection mechanism
  itself (`_profile_name_is_streamlink`, `_get_playback_url`,
  `plugin.py:2331-2367`) beyond feeding it the same canonical watch URL it
  already uses for live streams (§7).
- No support for downloading/storing/transcoding VOD content on the
  Dispatcharr host. This is playback-through-YouTube only — YouTube remains
  the source of truth and the only place video bytes are ever fetched from.
- No change to `manifest_version`/`capabilities` in `plugin.json` — as of
  `v1.40.0` none are declared yet (see `CHANGELOG.md`); that's separate,
  unreleased `v2.0` plugins-v3 compatibility prep, and this plan does not
  require or depend on it.
- No `data_dir` migration. That's also deferred to a future release;
  replay state continues to live in `tracked_streams` inside plugin settings,
  not a new sidecar file.
- Not attempting perfect archive-availability detection. Some ended streams
  never get a VOD (age-restricted takedowns, streamer deletes it, memberships-
  only replay, DMCA); the design must fail closed to "expire it" rather than
  hang in `replay_pending` forever (see retention default and §9).

## 3. Lifecycle

```
live -> replay_pending -> replay_available -> expired
  \                                          ^
   \-------------------(direct expire)------/
```

- **`live`**: current behavior, unchanged. `stream_data["is_live"] = True`.
- **`replay_pending`**: entered the instant a previously-live stream is
  detected as ended (today this is exactly the trigger that currently calls
  `_cleanup_ended_streams` / deletes the channel — see §4). The channel is
  *not* deleted. Instead the plugin records `replay_pending_since` and starts
  polling yt-dlp/YouTube to check whether the VOD has finished processing
  ("archive readiness", §8). Retention clock starts here, not at
  `replay_available`, so a stream whose archive never becomes available still
  expires on schedule instead of lingering indefinitely.
- **`replay_available`**: archive readiness check confirms the VOD is watchable
  (yt-dlp can extract a non-live format list for the same `video_id`, or
  reports a definite non-live, non-error state). Title/EPG/notification
  updates fire once here (§10). Playback URL is unchanged — still the
  canonical `https://www.youtube.com/watch?v={video_id}` watch URL (§7);
  YouTube itself resolves the same URL to live-or-VOD depending on stream
  state, so no URL rewrite is needed on transition.
- **`expired`**: retention window (`replay_retention_hours`, default 24,
  §3.1) elapsed since `replay_pending_since`, or the archive-readiness check
  came back with a terminal negative (private/deleted/geo-blocked — §9).
  Terminal state: this is where today's `_cleanup_ended_streams` deletion
  path (channel delete, stream delete, tracked_streams entry removal) actually
  runs. No new "expired" tracked_streams entries persist past this point —
  same as today, ended streams don't stay in `tracked_streams` forever.

### 3.1 Retention setting

New settings field (style-matched to existing fields, e.g. `auto_cleanup` at
`plugin.py:99-104`, `url_refresh_interval_seconds` at `plugin.py:105-...`):

```python
{
    "id": "replay_retention_hours",
    "label": "Replay Retention (hours)",
    "type": "number",
    "default": 0,
    "help_text": "How long to keep a channel alive after a livestream ends so "
                 "viewers can watch the YouTube VOD replay before the channel "
                 "is removed. Set to 0 to disable replay and clean up ended "
                 "streams immediately (current v1.40.0 behavior).",
}
```

**Shipped default is `0`, not the eventual product target.** The proposed
product default for this feature is 24 hours — that is the value replay is
designed around and what §9's caveats (long streams, processing time) are
evaluated against. But the *implementation rollout* default, i.e. what ships
in the initial v1.5 release and what any operator gets who upgrades without
touching this field, must be `0` until the canary acceptance criteria in §12
pass. `0` is both the rollback lever (§13) and the safe initial state: it
routes every ended stream through the existing, already-proven immediate-
cleanup path, so replay code ships dark by default and operators opt in
explicitly. Only after §12's canary criteria are met should a follow-up
release flip the shipped default to `24`; until then `24` is something an
operator can set manually, not what they get out of the box.

## 4. Exact current code to inspect/touch

All line numbers are against `plugin.py` on `feat/streamlink-playback-wrapper`
as of this planning pass; re-check before implementation since v1.40.0 changes
shifted some of these.

| Area | Location | Why it matters |
|---|---|---|
| `_cleanup_ended_streams` | `plugin.py:3103-3216` | Today: deletes the channel/Stream and drops the `tracked_streams` entry the instant `is_live` is falsy (or `force=True`). This is the single chokepoint that must grow the `replay_pending`/`replay_available`/`expired` branch instead of going straight to delete. The existing orphan-check and stale-EPG-verification sub-branches (`plugin.py:3149-3206`) already do a `_verify_video_is_live` re-check before declaring a stream dead — that verification call is reusable as (part of) the archive-readiness gate. |
| `_verify_video_is_live` | `plugin.py:2639-2662` | Existing yt-dlp-based liveness re-check (fails safe: assumes still-live on error). Archive readiness needs a parallel/extended check — see §8 — likely a sibling method rather than overloading this one, since "not live" and "VOD ready" are different questions. |
| `_poll_monitored_channels` | `plugin.py:2373` onward, ended-stream detection around `plugin.py:2437-2620` | This is where a channel's tracked stream transitions from "seen in current scan" to "not seen" — i.e. where `live -> replay_pending` must be triggered instead of whatever currently signals end-of-stream to `_cleanup_ended_streams`. Needs read-through to confirm exactly which branch marks `is_live = False` vs. removes the tracked entry outright. |
| `_extract_stream_metadata` | `plugin.py:1494-1600ish`, watch URL built at `plugin.py:1559` | Confirms the `https://www.youtube.com/watch?v={video_id}` URL construction already used for extraction; archive readiness checks should reuse the exact same URL builder, not hand-roll a second one. |
| `_get_playback_url` | `plugin.py:2343-2367` | Canonical watch-URL-for-Streamlink logic. Must confirm this continues to fire (unchanged) for `replay_available` streams — no branch currently keys off replay state, and none should need to, since the watch URL is state-agnostic (§7). |
| `_monitoring_loop` | `plugin.py:3226` onward | Background loop cadence; replay-pending/archive-readiness polling needs to hang off the same loop (or a lower-frequency sub-check inside it) rather than spawning a second thread. Needs read-through for how it paces `_poll_monitored_channels` vs. `_refresh_stream_urls`/cleanup calls today. |
| `_refresh_epg_times` | `plugin.py:3044-3099` | EPG programme end-time refresh for still-live entries. Replay-pending/available entries need an analogous EPG programme (§10) so the guide doesn't show a dead time slot; likely reuses this pattern rather than inventing a new one. |
| Settings fields list | `plugin.py:~80-230` | Where `replay_retention_hours` (§3.1) is added; match existing field dict shape and help-text tone exactly. |
| `TestCleanupEndedStreams` (or equivalent) in `tests/test_plugin.py` | search for `_cleanup_ended_streams` in tests | Existing coverage of immediate-delete behavior that must keep passing when `replay_retention_hours == 0`, plus new coverage for the retained-window path. |

## 5. `tracked_streams` / state schema changes

Current per-entry shape (inferred from `plugin.py:530-590`, `2460-2600`,
`3103-3216`): `video_id` key mapping to a dict with at least `channel_id`,
`stream_id`, `title`, `is_live`, `added_at`, `youtube_channel_id`,
`youtube_channel_name`.

Proposed additive fields (no removal/rename of existing keys — old entries
without these fields must be treated as `replay_state` implicitly absent ==
"pre-v1.5, immediate-cleanup" for backward compatibility on upgrade):

```python
stream_data["replay_state"] = "live" | "replay_pending" | "replay_available" | "expired"
stream_data["replay_pending_since"] = "<iso8601 utc>"   # set once, on live -> replay_pending
stream_data["replay_available_since"] = "<iso8601 utc>" # set once, on pending -> available
stream_data["replay_check_attempts"] = 0                 # incremented each readiness poll, for backoff/logging
```

Migration note: on load, any entry with `is_live is False` and no
`replay_state` key (i.e. written by a pre-v1.5 plugin version, or by v1.5 with
`replay_retention_hours == 0`) should be treated as already `expired` and go
straight through the existing immediate-cleanup path — this is what keeps
`replay_retention_hours = 0` byte-for-byte equivalent to current behavior
(§13 rollback).

## 6. Archive readiness checks

Needed: a way to ask "has YouTube finished processing this stream into a
watchable VOD, or is watching it currently broken/impossible" without
guessing based on wall-clock time alone (processing time varies with stream
length and YouTube's queue).

Candidate approach (needs validation against real yt-dlp behavior during
implementation, not assumed here):
- Re-run `_extract_stream_metadata`-style yt-dlp extraction (or a lighter
  `--dump-json` probe) against the same watch URL used for live extraction.
- Inspect the result for a definite `live_status` value distinct from
  `is_live`/`was_live` ambiguity — yt-dlp typically reports
  `live_status: "was_live"` or `"post_live"` while YouTube is still
  processing, transitioning to a normal VOD state once ready. The exact
  field/value mapping must be confirmed empirically (see Tests §12) since
  yt-dlp's live-status vocabulary has changed across versions before (this
  repo already tracks a bundled yt-dlp version, currently 2026.08.19 per
  `CHANGELOG.md`).
- Distinguish **terminal negative** (private, deleted, geo-blocked in the
  region the Dispatcharr host queries from, members-only restricted) from
  **transient/processing** (still finalizing) so terminal-negative can
  short-circuit straight to `expired` instead of waiting out the full
  retention window for no reason (§9).
- Reuse `_verify_video_is_live`'s fail-safe philosophy but invert the default:
  where liveness-check fails safe by assuming "still live" on error (so a
  transient error doesn't kill a working live channel), archive-readiness
  should fail safe by assuming "not yet ready, keep waiting within the
  retention window" on transient error — never assume ready and never
  immediately expire on a single error blip.

## 7. Canonical watch URL / Streamlink from-the-start behavior

`_get_playback_url` (`plugin.py:2343-2367`) already stores
`https://www.youtube.com/watch?v={video_id}` for Streamlink profiles instead
of the short-lived extracted googlevideo URL, specifically so Streamlink
re-resolves playback itself. This is the same URL a VOD replay should be
played through — **no URL change is required at the `replay_available`
transition** as long as the existing Streamlink profile invocation
(`streamlink {streamUrl} ... best --stdout`, documented in `README.md`) plays
from the beginning of the given URL by default rather than seeking to
"live edge."

This needs explicit verification during implementation: Streamlink's default
behavior against a YouTube VOD URL (as opposed to a live URL) is to start
from the beginning of the video, which is what "replay" means here — but the
plugin should not assume this without confirming against the bundled
Streamlink version, because if the profile's parameters or Streamlink's
plugin-specific default ever add a live-edge/seek offset, replay playback
would start mid-video instead of from the start. If verification shows a gap,
the fix belongs in the Stream Profile's documented parameters (README), not
in plugin code, since the plugin doesn't control the Streamlink command line
beyond the stored `{streamUrl}` value.

## 8. Title / EPG / notification changes

- **Title**: on `replay_pending -> replay_available`, prepend/suffix a marker
  (e.g. `"{title} [Replay]"`) so viewers browsing the guide can tell a replay
  channel from a live one at a glance. Exact placeholder mechanics should
  reuse the existing `{title}`/`{channel}` EPG-source-name placeholder
  pattern already supported (`plugin.py:200-205`) rather than inventing new
  templating syntax.
- **EPG**: `_refresh_epg_times` (`plugin.py:3044-3099`) currently extends the
  programme end-time for still-live entries. A `replay_available` entry needs
  an equivalent — likely a fixed-duration or actual-VOD-duration programme
  window (if yt-dlp reports `duration` on the VOD, prefer that over a fixed
  guess) that also gets renewed/extended as long as `replay_state` remains
  `replay_available`, so the guide doesn't show a stale/ended time slot for a
  channel that's still playable.
- **Notifications**: existing webhook config (`_get_notification_webhook_config`,
  `plugin.py:3640`) fires on new/ended stream events today. Needs a new
  event kind (or reuse of the existing "ended" event with an added replay-
  availability flag in the payload) for `replay_available`, and — separately —
  for `expired`, so downstream consumers (e.g. a Discord/Slack integration)
  can distinguish "stream ended, replay incoming" from "stream ended, no
  replay, channel about to disappear" from "channel now gone." Payload fields
  must stay consistent with the existing no-secret-leakage discipline (no
  cookie/token values — this is unrelated to cookies but the same review bar
  applies to any new webhook payload).

## 9. Caveats requiring explicit handling

- **Private streams**: a stream can be unlisted/made private by the uploader
  immediately after ending. Archive-readiness probe will get an extraction
  error indistinguishable at the transport level from "processing" unless
  yt-dlp surfaces a specific error class/message for private/removed videos —
  must special-case on yt-dlp's actual stderr/exit behavior for this case
  (needs empirical check, not assumed) so it maps to terminal-negative
  (§6) rather than retrying for the full retention window.
- **Deleted videos**: same as private, but check exact yt-dlp error signature
  (likely a distinct message like "Video unavailable") — should short-circuit
  the same way.
- **Geo-restricted replays**: a stream that was viewable live from the
  Dispatcharr host's egress region may have its VOD replay geo-blocked
  differently than the live broadcast was (rights windows can differ for
  replay vs. live). Must not assume "was live here" implies "VOD is
  available here."
- **Long streams (24h+)**: YouTube VOD processing time scales with stream
  duration; a 12-hour stream may take a long time to finish processing into
  a seekable VOD. The default 24h retention window must be validated against
  real long-running channels this plugin already tracks (VirtualRailfan is
  called out elsewhere in this repo's docs/changelog as a many-simultaneous-
  streams example) to confirm 24h isn't systematically too short for that
  category, or whether retention should scale with observed stream duration
  rather than being a flat constant.
- **Archive still processing at expiry**: if the retention window elapses
  while the archive-readiness check is still returning "processing" (neither
  ready nor terminal-negative), current default behavior should be to expire
  anyway (bounded worst case — see rollback/acceptance criteria) rather than
  auto-extend, to avoid unbounded channel lifetime for a straggler. This
  should be configurable-in-spirit but the *default* must be bounded.

## 10. Tests to add (planning-level list, not written yet)

- `_cleanup_ended_streams` with `replay_retention_hours = 0` behaves
  byte-identical to current v1.40.0 tests (regression guard for rollback path).
- Transition unit tests for each lifecycle edge: `live -> replay_pending`,
  `replay_pending -> replay_available` (readiness confirmed),
  `replay_pending -> expired` (readiness terminal-negative before window
  elapses), `replay_pending -> expired` (window elapses with readiness still
  "processing"), `replay_available -> expired` (window elapses).
  `-> expired` (window elapses).
- Backward-compatible load of a `tracked_streams` entry with no `replay_state`
  key (pre-v1.5 upgrade path) — confirm it's treated as `expired`/immediate-
  cleanup, not stuck.
- Archive-readiness probe: mocked yt-dlp outputs for processing / ready /
  private / deleted / geo-blocked, asserting correct state-machine transition
  for each.
- EPG programme window created/extended correctly for `replay_available`
  entries; confirm no regression to existing `_refresh_epg_times` live-entry
  coverage.
- Notification/webhook payload tests for the new replay-available/expired
  events, including the existing no-secret-leakage assertions applied to any
  new payload fields.
- Title-marker formatting test (e.g. `"{title} [Replay]"`) including
  placeholder interaction with existing `{title}`/`{channel}` EPG-name
  templating.

## 11. Explicit separation from cookie auth (v1.40.0)

Replay support and the v1.40.0 pasted-cookie workflow are unrelated concerns
that happen to share the same playback path:

- Cookies (`cookies_content`, `_sync_cookies_sidecar`,
  `_cookies_sidecar_path`, `Clear Cookies` action) control **authentication**
  to YouTube — needed for age-restricted/members-only extraction and,
  optionally, for Streamlink via `--http-cookies-file`. This plan does not
  touch any of that code.
- Replay controls **channel lifecycle** after a stream ends — an entirely
  separate axis. A replay-eligible stream still needs the same cookie
  configuration a live one does if the content requires authentication; v1.5
  must not introduce a second, parallel cookie/auth path for VOD playback.
- No new settings field in this plan reads or writes `cookies_content`, and
  no new code path in this plan calls `_write_cookies_sidecar_text` or
  `_validate_cookies_text`. If implementation discovers replay genuinely
  needs new cookie behavior (it shouldn't), that is out of scope for v1.5 and
  belongs in a dedicated cookie-focused change reviewed on its own.

## 12. Canary acceptance criteria (before wider rollout)

- `replay_retention_hours = 0` regression suite passes with zero behavior
  diff from v1.40.0 (existing `_cleanup_ended_streams` tests untouched and
  green).
- On at least one real ended stream from a channel already tracked by this
  plugin instance (per `[[project_overview]]`-style operational testing, not
  a mocked unit test), confirm: channel remains after stream ends, title
  gains the replay marker once `replay_available`, Streamlink plays back
  from the start of the VOD (not live-edge) per §7, and the channel is
  actually removed once `replay_retention_hours` elapses.
- At least one canary run against a channel/video known to go private or be
  deleted shortly after a live stream ends, confirming terminal-negative
  detection expires the channel promptly instead of holding it for the full
  retention window.
- No secret/cookie values appear in any new log line, diagnostics field, or
  webhook payload introduced by this feature (same bar as
  `test_diagnostics_does_not_expose_cookies_content` /
  `TestHandleClearCookies` in the current suite).
- `python3 -m unittest discover tests -v` green, plus manual/canary
  verification above, before promoting `replay_retention_hours`'s default
  from `0` (safe, off) to `24` (on) in a follow-up release — i.e. v1.5 should
  ship with replay *implemented* but the acceptance bar for flipping the
  *default* on is a separate, later gate.

## 13. Rollback

- Primary rollback lever: set `replay_retention_hours = 0`, which (per §3.1
  and §5) routes every ended stream through the exact same immediate-cleanup
  path that exists today — no code path unique to replay executes.
- If a bug is found post-release, reverting `plugin.py`/`plugin.json` to the
  pre-v1.5 commit is safe with respect to `tracked_streams` data: old code
  ignores the additive `replay_state`/`replay_pending_since`/
  `replay_available_since`/`replay_check_attempts` keys entirely (they're
  new keys, not renamed/removed ones), so downgrading the plugin doesn't
  corrupt or require migrating settings data. Entries left mid-`replay_pending`
  at rollback time simply get treated by the old code as already-ended,
  is_live-false streams and cleaned up on the next poll — acceptable (matches
  "worst case: replay didn't happen, stream cleaned up," not data loss or a
  crash).
- No database schema changes are anticipated (this plan stores all new state
  inside the existing `tracked_streams` JSON blob in plugin settings), so
  rollback never requires a migration step.
