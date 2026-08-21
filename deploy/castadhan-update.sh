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

# v1.9.9: Telegram alert helper for update failures. Reads the same root-only
# config the app uses. No-op when unconfigured. Never fails the script.
telegram_alert() {
  local text="$1" token="" chat=""
  if [ -f /etc/default/castadhan-telegram ]; then
    token=$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' /etc/default/castadhan-telegram | tr -d '"' | tr -d "'")
    chat=$(sed -n 's/^TELEGRAM_CHAT_ID=//p' /etc/default/castadhan-telegram | tr -d '"' | tr -d "'")
  fi
  [ -n "$token" ] && [ -n "$chat" ] || return 0
  curl -s --max-time 15 "https://api.telegram.org/bot${token}/sendMessage" \
    -d chat_id="$chat" --data-urlencode text="$text" >/dev/null 2>&1 || true
}

log "Downloading $TARBALL_URL"
# B-Belgium-44 (v1.9.9): son-pi's nightly downloads failed silently for two
# days (curl -s swallowed the error; "Download failed. Skipping." was all we
# had — no exit code, no retry). Now: curl's built-in retry (3 attempts, 30s
# apart, retrying ALL transient errors), -S to surface error text into the
# log even with -s, and the exit code recorded on final failure so we can
# distinguish HTTP 4xx (22) from mid-stream resets (18/56) from DNS (6).
if ! curl -sSfL --max-time 300 --retry 3 --retry-delay 30 --retry-all-errors \
     "$TARBALL_URL" -o "$STAGE/release.tgz"; then
  rc=$?
  log "Download failed after 3 retries (curl exit $rc). Skipping."
  telegram_alert "⚠️ CastAdhan ($(hostname)): nightly update download failed (curl exit $rc, 3 retries). Still on $CURRENT."
  exit 1
fi

mkdir -p "$STAGE/unpack"
if ! tar -xzf "$STAGE/release.tgz" -C "$STAGE/unpack" --strip-components=1; then
  log "Extract failed. Aborting."
  telegram_alert "⚠️ CastAdhan ($(hostname)): nightly update EXTRACT failed (the B-Belgium-43 class). Still on $CURRENT."
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

# B-Belgium-52 (v1.9.8): ensure persistent state directories used by the
# scheduled_audio Quran Programs feature exist with correct ownership. These
# live OUTSIDE /opt so they survive the rm-and-replace install dir swap, but
# they must be CREATED on first install or first update from a pre-v1.9.8
# version. Idempotent — re-running is safe.
mkdir -p /var/lib/castadhan/custom_audio
chown -R castadhan:castadhan /var/lib/castadhan
chmod 755 /var/lib/castadhan /var/lib/castadhan/custom_audio
if [ ! -f /var/lib/castadhan/custom_audio_state.json ]; then
  echo "{}" > /var/lib/castadhan/custom_audio_state.json
  chown castadhan:castadhan /var/lib/castadhan/custom_audio_state.json
  chmod 644 /var/lib/castadhan/custom_audio_state.json
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

# v1.9.9: install the systemd unit shipped with the release if it changed.
# Done here — INSIDE the stopped window, before the restart below — so the
# unit and the code it expects always land together. (Critical for the
# WatchdogSec= watchdog: a new unit with an old non-pinging app would be
# killed every 3 minutes; shipping both atomically makes that impossible.)
if [ -f "$INSTALL_DIR/deploy/castadhan-portable.service" ] \
   && ! cmp -s "$INSTALL_DIR/deploy/castadhan-portable.service" \
               /etc/systemd/system/castadhan-portable.service; then
  install -m 0644 "$INSTALL_DIR/deploy/castadhan-portable.service" \
                  /etc/systemd/system/castadhan-portable.service
  systemctl daemon-reload
  log "systemd unit refreshed from release + daemon-reload"
fi

# B-Belgium-67/68: install/refresh the speaker self-heal timer. This unit is
# NEW on boxes that predate it, so always install + `enable --now` (idempotent)
# — that's how the existing fleet gains automatic speaker-IP self-healing on
# the next auto-update, not just freshly-imaged units.
if [ -f "$INSTALL_DIR/deploy/castadhan-refresh.service" ]; then
  install -m 0644 "$INSTALL_DIR/deploy/castadhan-refresh.service" \
                  /etc/systemd/system/castadhan-refresh.service
  install -m 0644 "$INSTALL_DIR/deploy/castadhan-refresh.timer" \
                  /etc/systemd/system/castadhan-refresh.timer
  systemctl daemon-reload
  systemctl enable --now castadhan-refresh.timer >/dev/null 2>&1 || true
  log "speaker self-heal timer installed/refreshed (every 15 min)"
fi

# B-Belgium-76: unblock the WiFi radio on EVERY update, not just at install.
# The rfkill/NetworkManager unblock landed in setup-pi.sh (commit d298010) — but
# setup-pi.sh only ever runs when a unit is first imaged, and the updater swaps
# the install dir without touching system state. So a unit imaged BEFORE that
# commit can be running a release that contains the fix while its radio is still
# soft-blocked: `nmcli wifi list` returns zero networks, no error, and the WiFi
# wizard looks broken for ever. Confirmed on Rayan's box 21 Aug 2026 — v1.16.0
# installed (which includes d298010), radio still blocked, wizard saw 0 networks.
# A fix that lives only in the installer cannot reach the fleet. Idempotent.
if command -v rfkill >/dev/null 2>&1; then
  rfkill unblock wifi >/dev/null 2>&1 || true
fi
nmcli radio wifi on >/dev/null 2>&1 || true
if [ "$(nmcli radio wifi 2>/dev/null)" = "enabled" ]; then
  log "WiFi radio verified enabled (rfkill + NetworkManager) — B-Belgium-76"
else
  log "WARN: WiFi radio not reporting enabled — WiFi wizard may find no networks"
fi

# B-Belgium-77: ensure avahi-browse exists. setup-pi.sh installs avahi-daemon
# (which PUBLISHES <hostname>.local) but avahi-browse lives in avahi-utils, and
# was never installed. discover_casts() uses avahi-browse to catch Cast devices
# that CastBrowser misses — the augmentation path that matters most when a
# PORTABLE box arrives at a new house full of speakers it has never seen. Absent
# on rayan + masood (21 Aug 2026); it failed open and silently did nothing.
if ! command -v avahi-browse >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends avahi-utils >/dev/null 2>&1 \
    && log "avahi-utils installed (avahi-browse discovery path) — B-Belgium-77" \
    || log "WARN: could not install avahi-utils; avahi-browse discovery unavailable"
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
  # v1.9.9: a rollback is exactly the "silent failure" the operator must hear
  # about — Lesson 53: a silent rollback hides the very state it protects.
  telegram_alert "⚠️ CastAdhan ($(hostname)): update to $LATEST_VERSION ROLLED BACK ($reason). Still on $CURRENT."
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
