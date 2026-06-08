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

# B-Belgium-24: consume the manual-update request flag (if any) FIRST, so the
# castadhan-update.path unit that triggered us doesn't immediately re-fire in a
# loop. Harmless no-op for the nightly timer path (flag won't exist).
rm -f "${INSTALL_DIR}/.update-requested" 2>/dev/null || true

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
# B-Belgium-43 (v1.9.6): stage in /var/tmp, not /tmp. Pi OS Lite mounts /tmp
# as tmpfs sized at ~half of available RAM (453 MB on a Pi 3B+ with 1 GB
# RAM, ~209 MB on smaller variants). The release tarball is ~273 MB
# compressed and ~550 MB once extracted — won't fit in tmpfs on smaller
# Pis, so `tar -xzf` hits "No space left on device" partway through and
# aborts. Two full auto-update cycles silently failed on masood-pi today
# (v1.9.3 attempt at 04:41 + v1.9.5 attempt at 22:00) before this was
# diagnosed. /var/tmp lives on rootfs (50+ GB free on every Pi in the
# fleet) so it can absorb any reasonable release. The trap still cleans up
# on exit, and a leftover stage from a crashed run is just a hidden dir
# under /var/tmp that survives reboots (which is fine — it's < 600 MB).
STAGE=$(mktemp -d /var/tmp/castadhan-update.XXXXXX)
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

# B-Belgium-25: preserve runtime state that lives in the install dir but is NOT
# shipped in releases. Without this, every update wiped it — destroying the
# prayer-fired audit trail and resetting speaker enable/volume to defaults:
#   • play_history.jsonl     — the "did each prayer fire?" audit trail
#   • ui_state.json(.lock)   — per-speaker enable flags + volumes
#   • known_speakers.json    — discovered cast hosts (saves a re-discovery)
for f in play_history.jsonl ui_state.json ui_state.json.lock known_speakers.json; do
  [ -f "$PREV_DIR/$f" ] && cp -a "$PREV_DIR/$f" "$INSTALL_DIR/$f"
done

# Keep ALL audio files from the previous install (new releases don't ship audio)
if [ -d "$PREV_DIR/audio" ]; then
  rm -rf "$INSTALL_DIR/audio" 2>/dev/null
  cp -a "$PREV_DIR/audio" "$INSTALL_DIR/audio"
fi

chown -R castadhan:castadhan "$INSTALL_DIR"

# v1.9.0: keep system-side install artefacts in sync with the new release.
# These files live OUTSIDE the swap directory (in /etc/) so they don't ride
# the tarball; copy them across so a fresh release lands its full bundle.
if [ -d /etc/polkit-1/rules.d ] \
   && [ -f "$INSTALL_DIR/deploy/castadhan-nm.rules" ]; then
  install -m 0644 "$INSTALL_DIR/deploy/castadhan-nm.rules" \
                  /etc/polkit-1/rules.d/50-castadhan-nm.rules
  log "polkit rule for NetworkManager refreshed (WiFi wizard)"
fi

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

rollback() {
  local reason="${1:-unknown}"
  log "❌ Rolling back ($reason)…"
  systemctl stop "$SERVICE" || true
  rm -rf "$INSTALL_DIR"
  mv "$PREV_DIR" "$INSTALL_DIR"
  systemctl start "$SERVICE"
  log "Rollback complete. Still on $CURRENT."
  exit 1
}

if ! systemctl is-active --quiet "$SERVICE"; then
  rollback "service failed to start on new version"
fi

# ---- 5b. Sanity test (v1.7.0): regression net against documented bug --------
# classes. Lives at $INSTALL_DIR/sanity_test.py, derived from
# INCIDENT_REPORT_2026-05-22.md. Exit code semantics:
#     0 — all checks passed
#     1 — at least one CRITICAL fail → ROLL BACK (release is broken)
#     2 — only HIGH/MEDIUM/LOW fails → warn but ship (regression to patch
#         in next release, not bad enough to undo this one)
#     3 — internal test error
#
# Why this exists: the v1.6.5 Eid-Fajr-no-fire incident was caused by a
# dashboard UI regression that an automated check (L6 include-input-default-
# empty) would have caught instantly. Releases ship from now on only if the
# sanity test says they're OK. Lesson 41 turned from documented-only into
# enforced.
SANITY="$INSTALL_DIR/sanity_test.py"
if [ -f "$SANITY" ]; then
  log "Running sanity_test.py (regression net for past bugs)…"
  # Up to 60s for the suite (avahi-browse + aladhan call dominate)
  if timeout 60 python3 "$SANITY" --quiet 2>&1 | tee -a /var/log/castadhan-update.log; then
    rc=${PIPESTATUS[0]}
  else
    rc=${PIPESTATUS[0]:-3}
  fi
  case "$rc" in
    0) log "✅ Sanity test PASSED — all checks green" ;;
    1) rollback "sanity test reported CRITICAL failures" ;;
    2) log "⚠️  Sanity test had HIGH/MEDIUM/LOW failures (release shipped; patch in next release)" ;;
    *) log "⚠️  Sanity test internal error (rc=$rc) — release shipped; check log" ;;
  esac
else
  log "ℹ️  sanity_test.py not present (older install layout) — skipping"
fi

log "✅ Update successful. Now on $LATEST_VERSION."
# B-Belgium-26: do NOT delete $PREV_DIR here — that immediate rm defeated the
# 24h rollback window the next two lines promise (and destroyed the backup we
# could otherwise restore runtime state from). Cleanup is the scheduled `at`
# job; failing that, the next update's pre-backup rm clears it.
log "Previous version retained at $PREV_DIR for 24 hours (rollback window)"
echo "rm -rf '$PREV_DIR'" | at now + 24 hours 2>/dev/null || true
exit 0
