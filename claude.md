# YouTubearr Project Notes

## Deployment Targets

### Production
- **Server**: media-stack
- **User**: gooch
- **Path**: /opt/dispatcharr/data/plugins/youtubearr

### Test Environment
- **Server**: media-stack
- **User**: gooch
- **Path**: /home/gooch/dispatcharr-test/data/plugins/youtubearr

## Deployment Commands

### Deploy to Production
```bash
scp /home/gooch/youtubearr.zip gooch@media-stack:/tmp/youtubearr.zip
ssh gooch@media-stack "cd /tmp && rm -rf youtubearr && unzip -o youtubearr.zip && rm -rf /opt/dispatcharr/data/plugins/youtubearr && cp -r youtubearr /opt/dispatcharr/data/plugins/"
```

### Deploy to Test
```bash
scp /home/gooch/youtubearr.zip gooch@media-stack:/tmp/youtubearr.zip
ssh gooch@media-stack "cd /tmp && rm -rf youtubearr && unzip -o youtubearr.zip && rm -rf /home/gooch/dispatcharr-test/data/plugins/youtubearr && cp -r youtubearr /home/gooch/dispatcharr-test/data/plugins/"
```

## Build Commands

### Create distribution zip
```bash
cd /home/gooch
rm -f youtubearr.zip && zip -r youtubearr.zip youtubearr -x "*.pyc" -x "*__pycache__*" -x "*.log" -x "*.jsonl" -x "*.git*"
```

## Key Files
- `plugin.py` - Main plugin code (version is defined BOTH here AND in plugin.json)
- `plugin.json` - Plugin metadata
- `qjs` - Bundled QuickJS-NG binary for yt-dlp JavaScript runtime
- `yt-dlp` - Bundled yt-dlp binary

## Important Notes
- Version must be updated in BOTH `plugin.py` (class attribute) AND `plugin.json`
- The `--js-runtimes` flag is a top-level yt-dlp flag, NOT inside `--extractor-args`
- QuickJS `--help` returns exit code 1, so check for "QuickJS" string in output instead
