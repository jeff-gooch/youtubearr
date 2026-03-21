# Playlist-as-Live Feature Design Document

## Overview

Create a "fake live stream" from YouTube channel videos/playlists that plays continuously
like a traditional TV channel.

## User Request

> "Take a channel that isn't live and bring a playlist of videos from the channel in as a 'live stream.'"

## How It Works

1. Fetch video list from YouTube channel/playlist via yt-dlp
2. Extract direct stream URLs for each video
3. Use ffmpeg to concatenate videos into continuous MPEG-TS stream
4. Serve stream via HTTP server on localhost
5. Create Dispatcharr channel pointing to localhost stream
6. On-demand: ffmpeg starts when client connects, stops after grace period

## Recommended Approach: Separate Plugin (Playlistarr)

### Rationale

- Clean separation of concerns (live streams vs playlist streams)
- YouTubearr stays focused and maintainable
- Easier to test/debug independently
- Can be merged later if desired

### Architecture

```
playlistarr/
├── plugin.py (~1,500 lines)
│   ├── HTTP server (port 5962)
│   ├── ffmpeg process management
│   ├── yt-dlp video extraction
│   ├── URL refresh manager (YouTube URLs expire ~6 hours)
│   ├── Ring buffer streaming
│   └── Dispatcharr channel creation
├── plugin.json
├── ffmpeg (bundled or system)
└── yt-dlp (bundled, copy from YouTubearr)
```

### Settings (plugin.json fields)

```json
{
  "fields": [
    {
      "id": "youtube_url",
      "label": "YouTube Channel/Playlist URL",
      "type": "string",
      "help_text": "URL of YouTube channel or playlist"
    },
    {
      "id": "mode",
      "label": "Playback Mode",
      "type": "select",
      "options": [
        {"value": "sequential", "label": "Sequential"},
        {"value": "shuffle", "label": "Shuffle"},
        {"value": "loop", "label": "Loop (restart when finished)"}
      ]
    },
    {
      "id": "max_videos",
      "label": "Maximum Videos",
      "type": "number",
      "default": 50,
      "help_text": "Limit videos to fetch (0 = unlimited)"
    },
    {
      "id": "channel_name",
      "label": "Channel Name",
      "type": "string",
      "default": "Playlist TV"
    },
    {
      "id": "channel_number",
      "label": "Channel Number",
      "type": "number",
      "default": 998
    },
    {
      "id": "grace_period",
      "label": "Grace Period (seconds)",
      "type": "number",
      "default": 30,
      "help_text": "How long to keep ffmpeg running after last client disconnects"
    }
  ]
}
```

### Core Components

#### 1. Video Extraction (~200 lines)

```python
def _fetch_playlist_videos(self, url: str, max_videos: int) -> list[dict]:
    """Fetch video list from YouTube channel/playlist"""
    cmd = [
        self._yt_dlp_path,
        "--flat-playlist",
        "--print", "%(id)s",
        "--print", "%(title)s",
        "--print", "%(duration)s",
        url
    ]
    # Parse output, return list of {id, title, duration}

def _get_video_stream_url(self, video_id: str) -> str:
    """Get direct stream URL for video (expires in ~6 hours)"""
    cmd = [
        self._yt_dlp_path,
        "-f", "best[ext=mp4]/best",
        "-g",  # Print URL only
        f"https://www.youtube.com/watch?v={video_id}"
    ]
    # Return direct URL
```

#### 2. HTTP Server (~200 lines)

```python
import http.server
import threading

class PlaylistStreamHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/playlist.ts":
            self._stream_playlist()
        elif self.path == "/status":
            self._send_status()

    def _stream_playlist(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/MP2T")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # Register client, start ffmpeg if needed
        plugin.register_client(self)

        # Stream from ring buffer until client disconnects
        while True:
            chunk = plugin.get_stream_chunk()
            if not chunk:
                break
            self.wfile.write(chunk)
```

#### 3. ffmpeg Process Management (~300 lines)

```python
def _start_ffmpeg(self, video_urls: list[str]):
    """Start ffmpeg with concat input"""
    # Create concat file
    concat_file = self._create_concat_file(video_urls)

    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",  # No re-encoding
        "-f", "mpegts",
        "-"  # Output to stdout
    ]

    self._ffmpeg_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Start thread to read stdout into ring buffer
    self._reader_thread = threading.Thread(
        target=self._read_ffmpeg_output,
        daemon=True
    )
    self._reader_thread.start()

def _create_concat_file(self, urls: list[str]) -> str:
    """Create ffmpeg concat demuxer file"""
    lines = ["ffconcat version 1.0"]
    for url in urls:
        lines.append(f"file '{url}'")

    concat_path = f"{self._plugin_dir}/concat.txt"
    with open(concat_path, "w") as f:
        f.write("\n".join(lines))
    return concat_path
```

#### 4. Ring Buffer (~150 lines)

```python
class RingBuffer:
    """Thread-safe ring buffer for streaming"""

    def __init__(self, size: int = 10 * 1024 * 1024):  # 10MB
        self._buffer = bytearray(size)
        self._size = size
        self._write_pos = 0
        self._lock = threading.Lock()
        self._data_available = threading.Event()

    def write(self, data: bytes):
        with self._lock:
            # Write data to buffer, wrap around if needed
            ...
            self._data_available.set()

    def read(self, size: int) -> bytes:
        self._data_available.wait(timeout=5.0)
        with self._lock:
            # Read from buffer
            ...
```

#### 5. URL Refresh Manager (~150 lines)

```python
def _url_refresh_loop(self):
    """Background thread to refresh expiring URLs"""
    while self._running:
        time.sleep(60)  # Check every minute

        # URLs expire in ~6 hours, refresh at 5 hours
        if time.time() - self._urls_fetched_at > 5 * 3600:
            self._log("Refreshing video URLs...")
            new_urls = self._fetch_video_urls()

            # Update concat file
            self._update_concat_file(new_urls)

            # Note: Active ffmpeg will finish current video,
            # then read updated concat file for next video
```

#### 6. Client Management (~100 lines)

```python
def register_client(self, handler):
    """Called when client connects"""
    with self._client_lock:
        self._clients.append(handler)
        if len(self._clients) == 1:
            self._start_ffmpeg(self._video_urls)

def unregister_client(self, handler):
    """Called when client disconnects"""
    with self._client_lock:
        self._clients.remove(handler)
        if len(self._clients) == 0:
            self._start_grace_period()

def _start_grace_period(self):
    """Stop ffmpeg after grace period if no clients reconnect"""
    def check_and_stop():
        time.sleep(self._grace_period)
        with self._client_lock:
            if len(self._clients) == 0:
                self._stop_ffmpeg()

    threading.Thread(target=check_and_stop, daemon=True).start()
```

### Dispatcharr Integration

```python
def _create_dispatcharr_channel(self, settings):
    """Create channel pointing to local stream"""
    from apps.channels.models import Channel, Stream

    stream, _ = Stream.objects.update_or_create(
        url=f"http://127.0.0.1:5962/playlist.ts",
        defaults={"name": f"Playlistarr: {settings['channel_name']}"}
    )

    Channel.objects.update_or_create(
        channel_number=settings["channel_number"],
        defaults={
            "name": settings["channel_name"],
            "stream": stream
        }
    )
```

## Code Estimate

| Component | Lines |
|-----------|-------|
| Video extraction | ~200 |
| HTTP server | ~200 |
| ffmpeg management | ~300 |
| Ring buffer | ~150 |
| URL refresh | ~150 |
| Client management | ~100 |
| Settings/actions | ~150 |
| Dispatcharr integration | ~150 |
| Logging/utilities | ~100 |
| **Total** | **~1,500** |

## Reference Implementation

See `/home/gooch/multiview/plugin.py` for:
- HTTP server pattern (lines ~200-350)
- ffmpeg process management (lines ~400-600)
- Ring buffer implementation (lines ~100-200)
- Client connection handling (lines ~350-400)

## Future Enhancements

1. **EPG Support**: Generate programme guide from video titles/durations
2. **Multiple Playlists**: Support multiple playlist channels
3. **Resume Position**: Remember playback position across restarts
4. **Skip/Previous**: Web UI to control playback
5. **Auto-fallback**: If YouTubearr detects no live stream, activate playlist mode

## Getting Started

1. Create `/home/gooch/playlistarr/` directory
2. Copy `yt-dlp` binary from YouTubearr
3. Create `plugin.json` with settings above
4. Implement `plugin.py` following component breakdown
5. Test locally before deploying to Dispatcharr
