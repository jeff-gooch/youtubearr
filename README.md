# YouTubearr - YouTube Livestream Plugin for Dispatcharr

YouTubearr is a Dispatcharr plugin that monitors YouTube channels for livestreams and adds them as playable channels. It uses yt-dlp to detect when streams go live, creates Dispatcharr channels with proper EPG support, and cleans them up when streams end. No YouTube API quota required. I built this with Claude's help - we're all using AI now, I'm just honest about it. 🤖

## Features

- **Manual Stream Addition**: Quickly add any YouTube livestream by pasting the URL
- **Automatic Channel Monitoring**: Monitor YouTube channels and automatically add new livestreams (no API key)
- **Auto-cleanup**: Automatically remove Dispatcharr channels when YouTube streams end
- **URL Refresh**: Handles YouTube's expiring stream URLs automatically
- **Quality Selection**: Choose preferred stream quality (Best, 1080p, 720p, 480p)
- **ZERO Python Dependencies**: Includes bundled yt-dlp, no extra pip installs

## Installation

### Simple Installation (Literally ZERO Dependencies!)

1. Copy the `youtubearr` directory to your Dispatcharr plugins directory:
   ```bash
   # For Docker
   docker cp youtubearr dispatcharr:/app/data/plugins/

   # For local installation
   cp -r youtubearr /path/to/dispatcharr/data/plugins/
   ```

2. Restart Dispatcharr to load the plugin:
   ```bash
   # For Docker
   docker restart dispatcharr

   # For systemd
   sudo systemctl restart dispatcharr
   ```

3. Enable the plugin in Dispatcharr UI (Settings → Plugins → YouTubearr)

4. **Done!** Everything works immediately:
   - ✅ Bundled yt-dlp binary (3.1MB included)
   - ✅ YouTube Data API via Python's built-in `urllib`
   - ✅ Manual URL addition
   - ✅ Automatic monitoring
   - ✅ Channel management

**No pip install. No apt-get. No dependencies. It just works.**

## No API Key Required

YouTubearr uses yt-dlp to detect live streams and does not require a YouTube Data API key or quota.

## Configuration

### Optional Settings

- **Monitored YouTube Channels**: One per line, using the combined format:
  - `@handle` (auto-assign channel numbers)
  - `@handle=BaseNumber` (pin a base channel number)
  - `@handle=BaseNumber:TitleFilter` (regex filter for multi-stream channels)
  - Example:
    ```
    @NASA=92
    @RyanHallYall=90
    @VirtualRailfan=91:Horseshoe Curve|La Grange
    ```
- **Poll Interval**: How often to check for new/ended streams (5-60 minutes, default: 15)
- **Auto-cleanup**: Automatically remove channels when streams end (default: enabled)
- **URL Refresh Interval**: How often to refresh stream URLs (default: 3600 seconds)
- **Channel Group**: Group name for created channels (default: "YouTube Live")
- **Stream Quality**: Preferred quality for ingested streams (default: Best Available)
- **Starting Channel Number**: First channel number to assign (default: 2000)
  - Example: Set to 3000 to start YouTube streams at channel 3000
- **Channel Number Increment**: How much to increment for each new stream (default: 1)
  - Example: Set to 10 to assign channels 2000, 2010, 2020, etc.
- **Channel Number Mapping**: Optional mapping of YouTube channels to base channel numbers for sub-channel grouping. See [Sub-Channel Numbering](#sub-channel-numbering) below.
- **Manual URL**: Paste a YouTube livestream URL for quick manual addition
- **Dispatcharr Base URL**: Base URL for stream links in notifications (e.g., https://tv.example.com)

## Usage

### Adding a Stream Manually

1. Copy a YouTube livestream URL (e.g., `https://www.youtube.com/watch?v=VIDEO_ID`)
2. Open YouTubearr plugin settings in Dispatcharr
3. Paste the URL into the **Manual YouTube URL** field
4. Click the **Add Stream** button
5. The stream will appear as a new channel in your Dispatcharr feed

### Monitoring YouTube Channels

1. Add YouTube handles to **Monitored YouTube Channels** (see format above)
2. Set your preferred **Poll Interval** (how often to check for streams)
3. Click **Start Monitoring**
5. YouTubearr will automatically:
   - Check for new livestreams on monitored channels
   - Add new livestreams as Dispatcharr channels
   - Remove channels when streams end (if auto-cleanup is enabled)
   - Refresh stream URLs to prevent expiration

### Manual Actions

- **Add Stream**: Add a single stream from the Manual URL field
- **Start Monitoring**: Begin automatic monitoring of configured channels
- **Stop Monitoring**: Stop automatic monitoring
- **Refresh Now**: Immediately check for new/ended livestreams (bypasses poll interval)
- **Cleanup**: Manually remove all channels for ended streams

## Sub-Channel Numbering

YouTubearr supports decimal sub-channels (e.g., 90.1, 90.2) to group streams from the same YouTube channel together in your guide.

### Automatic Grouping (Default)

Without any configuration, streams are automatically grouped by YouTube channel:
- First stream from Channel A → 2000.1
- Second stream from Channel A → 2000.2
- First stream from Channel B → 2001.1

### Custom Base Number Mapping (Optional)

Use the **Channel Number Mapping** setting to assign specific base numbers:
```
@WeatherChannel=90
@SpaceChannel=91
@NewsChannel=92
@RelatedNewsChannel=92
```

**Result:**
- WeatherChannel streams → 90.1, 90.2, 90.3...
- SpaceChannel streams → 91.1, 91.2, 91.3...
- NewsChannel + RelatedNewsChannel streams → 92.1, 92.2, 92.3... (grouped together!)

**Format:** `@ChannelName=BaseNumber` (one per line)

**Tips:**
- Multiple YouTube channels can share the same base number to group related content
- Unmapped channels automatically get assigned the next available base number
- Sub-channels continue beyond .9 (e.g., .10, .11, .12)

### Title Filtering (For Channels with Many Streams)

Some YouTube channels (like VirtualRailfan) have 70+ simultaneous streams. Use title filtering to selectively add only the streams you want:

```
@VirtualRailfan=91:Horseshoe Curve|La Grange|Glendale
@WeatherChannel=90
```

**Extended Format:** `@ChannelName=BaseNumber:TitleFilter`

**Result:**
- Only VirtualRailfan streams with titles matching "Horseshoe Curve", "La Grange", or "Glendale" are added
- All WeatherChannel streams are added (no filter)

**Filter Syntax:**
- Use `|` (pipe) to match multiple patterns (OR logic)
- Case-insensitive matching
- Supports full regex: `Horseshoe.*Curve` matches "Horseshoe Main Curve"
- No filter after `:` = add all streams

## Supported Channel Formats

**For monitoring channels**, use @handles in the combined format:

| Format | Example | Notes |
|--------|---------|-------|
| @handle | `@NASA` | Auto-assigns channel numbers |
| @handle=Base | `@NASA=92` | Pins base channel number |
| @handle=Base:Filter | `@VirtualRailfan=91:Horseshoe Curve` | Regex filter for multi-stream channels |

**For manual stream URLs**, you can use:

| Format | Example |
|--------|---------|
| Watch URL | `https://www.youtube.com/watch?v=VIDEO_ID` |
| Short URL | `https://youtu.be/VIDEO_ID` |
| Live URL | `https://www.youtube.com/live/VIDEO_ID` |

## Troubleshooting

### Streams not appearing in Dispatcharr

- Verify the YouTube stream is actually live (not a premiere or scheduled stream)
- Check the youtubearr.log file for error messages
- Try adding the stream manually first to verify yt-dlp is working

### Stream playback issues

- YouTube stream URLs expire after ~6 hours
- YouTubearr automatically refreshes URLs every hour
- If a stream stops playing, try the **Refresh Now** action

### Orphaned channels

- Use the **Cleanup** action to remove channels for ended streams
- This can happen if monitoring was stopped while streams were active

## Technical Details

- **yt-dlp**: Used for extracting streamable URLs from YouTube
- **YouTube Data API v3**: Used for monitoring channels and detecting livestreams
- **Stream URL Refresh**: Automatic refresh every 60 minutes to prevent expiration
- **Channel Numbering**: Auto-assigned starting from 2000 to avoid conflicts
- **Thread Safety**: Uses Django's select_for_update() to prevent race conditions

## Logs

Runtime logs are stored in: `/app/data/plugins/youtubearr/youtubearr.log`

View logs to troubleshoot issues:
```bash
tail -f /app/data/plugins/youtubearr/youtubearr.log
```

## Support

- GitHub Issues: [Report bugs or request features]
- Dispatcharr Documentation: https://github.com/Dispatcharr/Dispatcharr

## License

This plugin follows Dispatcharr's licensing terms.
