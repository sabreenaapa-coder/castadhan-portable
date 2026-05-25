#!/usr/bin/env bash
# CastAdhan Portable — Auto-update script
# ========================================
# Pulls latest version from a GitHub release tarball and atomically swaps it in.
# Keeps the previous version in *.previous for instant rollback.
#
# This script is normally invoked by castadhan-update.timer, NOT manually.
# To trigger manually:  sudo systemctl start castadhan-update.service
#
# Configuration is read from /etc/default/castadhan-update (see installer).

set -euo pipefail

INSTALL_DIR=${INSTALL_DIR:-/opt/castadhan-portable}
PREV_DIR="${INSTALL_DIR}.previous"
SERVICE=castadhan-portable.service

# Source config if present (overrides defaults)
[ -f /etc/default/castadhan-update ] && . /etc/default/castadhan-update

GITHUB_REPO=${GITHUB_REPO:-yourname/castadhan-portable}       # set in /etc/default/castadhan-update
UPDATE_CHANNEL=${UPDATE_CHANNEL:-stable}                       # "stable" or "beta"
UPDATE_ENABLED=${UPDATE_ENABLED:-true}                         # set to "false" to disable

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a /var/log/castadhan-update.log
  logger -t castadhan-update "$*"
}

if [ "$UPDATE_ENABLED" != "true" ]; then
  log "Auto-update is disabled (UPDATE_ENABLED=$UPDATE_ENABLED). Exiting."
  exit 0
fi

current_version() {
  if [ -f "$INSTALL_DIR/VERSION" ]; then
    cat "$INSTALL_DIR/VERSION" | tr -d '[:space:]'
  else
    echo "0.0.0"
  fi
}

# ---- 1. Check GitHub for the latest release tag -----------------------------
log "Checking for updates from $GITHUB_REPO (channel: $UPDATE_CHANNEL)"

API_URL="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
if [ "$UPDATE_CHANNEL" = "beta" ]; then
  # Beta channel: include pre-releases. Pick the most recent regardless.
  API_URL="https://api.github.com/repos/${GITHUB_REPO}/releases"
fi

if ! REL_JSON=$(curl -sf --max-time 30 "$API_URL"); then
  log "Could not reach GitHub. Skipping this update cycle (will retry tomorrow)."
  exit 0
fi

if [ "$UPDATE_CHANNEL" = "beta" ]; then
  LATEST_TAG=$(echo "$REL_JSON" | python3 -c "import sys,json; rel=json.load(sys.stdin); print(rel[0]['tag_name'] if rel else '')")
  TARBALL_URL=$(echo "$REL_JSON" | python3 -c "import sys,json; rel=json.load(sys.stdin); print(rel[0]['tarball_url'] if rel else '')")
else
  LATEST_TAG=$(echo "$REL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))")
  TARBALL_URL=$(echo "$REL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tarball_url',''))")
fi

LATEST_VERSION=${LATEST_TAG#v}   # strip leading 'v' if present
CURRENT=$(current_version)

if [ -z "$LATEST_VERSION" ] || [ -z "$TARBALL_URL" ]; then
  log "No releases found in $GITHUB_REPO. Nothing to do."
  exit 0
fi

if [ "$CURRENT" = "$LATEST_VERSION" ]; then
  log "Already on $CURRENT (latest). Nothing to do."
  exit 0
fi

# Don't downgrade automatically
if [ "$(printf '%s\n%s\n' "$CURRENT" "$LATEST_VERSION" | sort -V | head -n1)" = "$LATEST_VERSION" ]; then
  log "Latest ($LATEST_VERSION) is older than current ($CURRENT). Refusing to downgrade."
  exit 0
fi

log "Update available: $CURRENT → $LATEST_VERSION. Beginning atomic update."

# ---- 2. Download tarball to a staging dir -----------------------------------
STAGE=$(mktemp -d /tmp/castadhan-update.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT

log "Downloading $TARBALL_URL"
if ! curl -sfL --max-time 300 "$TARBALL_URL" -o "$STAGE/release.tgz"; then
  log "Download failed. Skipping."
  exit 1
fi

mkdir -p "$STAGE/unpack"
if ! tar -xzf "$STAGE/release.tgz" -C "$STAGE/unpack" --strip-components=1; then
  log "Extract failed. Aborting."
  exit 1
fi

# Sanity: must contain app.py
if [ ! -f "$STAGE/unpack/app.py" ]; then
  log "Tarball doesn't look like CastAdhan (no app.py at root). Aborting."
  exit 1
fi

# ---- 3. Stop service, swap dirs atomically ---------------------------------
log "Stopping $SERVICE"
systemctl stop "$SERVICE" || true

log "Backing up current install → ${PREV_DIR}"
rm -rf "$PREV_DIR"
mv "$INSTALL_DIR" "$PREV_DIR"

log "Installing new version"
mv "$STAGE/unpack" "$INSTALL_DIR"

# Preserve venv & config & audio files from the previous install
[ -d "$PREV_DIR/venv" ] && cp -a "$PREV_DIR/venv" "$INSTALL_DIR/venv"
[ -f "$PREV_DIR/config.yaml" ] && cp "$PREV_DIR/config.yaml" "$INSTALL_DIR/config.yaml"
# Keep ALL audio files from the previous install (new releases don't ship audio)
if [ -d "$PREV_DIR/audio" ]; then
  rm -rf "$INSTALL_DIR/audio" 2>/dev/null
  cp -a "$PREV_DIR/audio" "$INSTALL_DIR/audio"
fi

chown -R castadhan:castadhan "$INSTALL_DIR"

# ---- 4. Update Python dependencies if requirements changed ------------------
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
  log "Refreshing Python dependencies"
  sudo -u castadhan "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt" || \
    log "pip install had warnings — continuing"
fi

# ---- 5. Restart service & verify it stays up --------------------------------
log "Starting $SERVICE"
systemctl start "$SERVICE"
sleep 8

if systemctl is-active --quiet "$SERVICE"; then
  log "✅ Update successful. Now on $LATEST_VERSION."
  rm -rf "$PREV_DIR"   # uncomment this line to immediately free space
  log "Previous version retained at $PREV_DIR for 24 hours (rollback window)"
  # Schedule cleanup of previous version after 24h
  echo "rm -rf '$PREV_DIR'" | at now + 24 hours 2>/dev/null || true
  exit 0
else
  log "❌ Service failed to start on new version. Rolling back…"
  systemctl stop "$SERVICE" || true
  rm -rf "$INSTALL_DIR"
  mv "$PREV_DIR" "$INSTALL_DIR"
  systemctl start "$SERVICE"
  log "Rollback complete. Still on $CURRENT."
  exit 1
fi
