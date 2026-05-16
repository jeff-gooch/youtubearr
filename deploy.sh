#!/usr/bin/env bash
# deploy.sh — Deploy YouTubearr to a Dispatcharr instance
#
# Usage:
#   ./deploy.sh test          Deploy dev branch to dispatcharr-test
#   ./deploy.sh production    Deploy to production dispatcharr
#   ./deploy.sh publish       Submit version bump PR to dispatcharr/Plugins
#   ./deploy.sh setup-test    First-time test environment setup

set -e

TARGET="${1:-}"
RUN_INTEGRATION=false
[[ "${2:-}" == "--integration" ]] && RUN_INTEGRATION=true

HOST="gooch@192.168.20.7"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Config ───────────────────────────────────────────────────────────────────

case "$TARGET" in
  test)
    PLUGIN_PATH="/opt/dispatchar-test/data/plugins/youtubearr"
    CONTAINER="dispatcharr-test"
    COMPOSE_DIR="/opt/dispatchar-test"
    ;;
  production)
    PLUGIN_PATH="/opt/dispatcharr/data/plugins/youtubearr"
    CONTAINER="dispatcharr"
    ;;
  publish|setup-test)
    ;;
  *)
    echo "Usage: $0 [test|production|publish|setup-test] [--integration]"
    exit 1
    ;;
esac

# ── Helpers ──────────────────────────────────────────────────────────────────

run_tests() {
  echo "Running unit tests..."
  cd "$SCRIPT_DIR"
  nix-shell -p python3 --run "python3 -m unittest discover tests/ -v -p 'test_plugin.py'" 2>/dev/null \
    || python -m unittest discover tests/ -v -p "test_plugin.py"
  echo "Unit tests passed."

  if $RUN_INTEGRATION; then
    echo "Running integration tests (requires internet + real yt-dlp, ~60s)..."
    nix-shell -p python3 --run "python3 -m unittest tests/test_integration.py -v" 2>/dev/null \
      || python -m unittest tests/test_integration.py -v
    echo "Integration tests passed."
  fi
}

deploy_files() {
  echo "Deploying to $CONTAINER..."
  for f in plugin.py plugin.json yt-dlp qjs; do
    [[ -f "$SCRIPT_DIR/$f" ]] || { echo "ERROR: Missing $f"; exit 1; }
  done
  ssh "$HOST" "mkdir -p $PLUGIN_PATH"
  scp "$SCRIPT_DIR/plugin.py" "$SCRIPT_DIR/plugin.json" \
      "$SCRIPT_DIR/yt-dlp" "$SCRIPT_DIR/qjs" \
      "$HOST:$PLUGIN_PATH/"
  echo "Files copied."
}

restart_and_verify() {
  echo "Restarting $CONTAINER..."
  ssh "$HOST" "docker restart $CONTAINER"
  echo "Waiting for startup..."
  sleep 15
  VERSION=$(ssh "$HOST" "grep -m1 'version = ' $PLUGIN_PATH/plugin.py | tr -d ' '")
  echo "Deployed: $VERSION"
  LOG=$(ssh "$HOST" "find /opt/$(echo $PLUGIN_PATH | cut -d/ -f3)/data/plugins -name 'youtubearr.log' 2>/dev/null | head -1")
  echo "To tail logs: ssh $HOST 'tail -f $LOG'"
}

# ── Commands ─────────────────────────────────────────────────────────────────

case "$TARGET" in
  test)
    run_tests
    deploy_files
    restart_and_verify
    ;;

  production)
    echo "Deploying to PRODUCTION. Tests must pass first."
    run_tests
    read -rp "Deploy to production? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
    deploy_files
    restart_and_verify
    ;;

  publish)
    VERSION=$(nix-shell -p python3 --run "python3 -c \"import json; print(json.load(open('$SCRIPT_DIR/plugin.json'))['version'])\"" 2>/dev/null \
              || python -c "import json; print(json.load(open('$SCRIPT_DIR/plugin.json'))['version'])")
    PLUGINS_DIR="/home/gooch/dispatcharr-plugins"
    BRANCH="youtubearr/v${VERSION}"

    echo "Publishing v${VERSION} to dispatcharr/Plugins..."

    cd "$PLUGINS_DIR"
    git fetch upstream
    git checkout main
    git merge upstream/main --ff-only
    git push origin main

    git checkout -b "$BRANCH" 2>/dev/null || git checkout "$BRANCH"

    cp "$SCRIPT_DIR/plugin.py"   plugins/youtubearr/plugin.py
    cp "$SCRIPT_DIR/plugin.json" plugins/youtubearr/plugin.json
    cp "$SCRIPT_DIR/yt-dlp"     plugins/youtubearr/yt-dlp
    cp "$SCRIPT_DIR/qjs"        plugins/youtubearr/qjs

    git add plugins/youtubearr/
    git commit -m "[youtubearr] Bump version to ${VERSION}" || { echo "Nothing to commit."; exit 0; }
    git push origin "$BRANCH"

    echo ""
    echo "Branch pushed. Open this URL to create the PR:"
    echo "  https://github.com/jeff-gooch/plugins/pull/new/${BRANCH}"
    echo "PR title: [youtubearr] Bump version to ${VERSION}"
    echo "Base:     Dispatcharr/Plugins → main"
    ;;

  setup-test)
    # Creates a fresh dispatcharr-test environment on media-stack.
    # Requires /opt/dispatchar-test to already exist (needs sudo on host):
    #   sudo mkdir -p /opt/dispatchar-test/data
    #   sudo chown -R gooch:gooch /opt/dispatchar-test

    echo "Setting up dispatcharr-test environment..."

    # Write compose file
    ssh "$HOST" "cat > /opt/dispatchar-test/docker-compose.yml" << 'COMPOSE'
services:
  dispatcharr-test:
    image: ghcr.io/dispatcharr/dispatcharr:latest
    container_name: dispatcharr-test
    restart: unless-stopped
    environment:
      - DISPATCHARR_LOG_LEVEL=info
      - DISPATCHARR_ENV=aio
      - REDIS_HOST=localhost
      - CELERY_BROKER_URL=redis://localhost:6379/0
    volumes:
      - /opt/dispatchar-test/data:/data
    ports:
      - "9292:9191"
    devices:
      - /dev/dri:/dev/dri
    group_add:
      - "993"
    security_opt:
      - no-new-privileges:true
    networks:
      - media-stack

networks:
  media-stack:
    external: true
COMPOSE

    # Deploy plugin files
    PLUGIN_PATH="/opt/dispatchar-test/data/plugins/youtubearr"
    CONTAINER="dispatcharr-test"
    deploy_files

    # Start container
    ssh "$HOST" "cd /opt/dispatchar-test && docker compose up -d"
    echo "Waiting for startup..."
    sleep 15
    ssh "$HOST" "docker ps | grep dispatcharr-test"
    echo ""
    echo "Test instance running at http://192.168.20.7:9292"
    echo "To tear down: ssh $HOST 'cd /opt/dispatchar-test && docker compose down -v'"
    ;;
esac
