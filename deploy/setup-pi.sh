#!/usr/bin/env bash
# CastAdhan Portable — Raspberry Pi installer
# ============================================
# Run this once on a fresh Raspberry Pi OS to build the golden image.
# After it finishes, the Pi will:
#   • Auto-start CastAdhan on every boot
#   • Be reachable at  http://castadhan.local:8786
#   • Show the first-run wizard on first visit
#
# Usage:  sudo bash setup-pi.sh
#
# Tested on:  Raspberry Pi OS Lite (Bookworm, 64-bit) — Pi 3B+ / Pi 4 / Pi Zero 2 W
set -euo pipefail

# ---- config ----------------------------------------------------------------
INSTALL_DIR=/opt/castadhan-portable
SERVICE_USER=castadhan
HOSTNAME=castadhan
SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "$0")"/.. && pwd)}"   # parent of deploy/
PYTHON_BIN=python3

# ---- helpers ---------------------------------------------------------------
say()  { printf "\n\033[1;33m▶ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;31m!\033[0m %s\n" "$*"; }

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo:  sudo bash $(basename "$0")"
    exit 1
  fi
}

require_root

say "Checking OS"
. /etc/os-release
echo "  $PRETTY_NAME"
if [ "${ID:-}" != "raspbian" ] && [ "${ID:-}" != "debian" ] && [ "${ID_LIKE:-}" != *debian* ]; then
  warn "This script is tuned for Debian/Raspbian. It may still work — proceed with care."
fi

# ---- 1. system packages ----------------------------------------------------
say "Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  ffmpeg \
  avahi-daemon \
  ca-certificates curl \
  >/dev/null
ok "python3, ffmpeg, avahi-daemon installed"

# ---- 2. user ---------------------------------------------------------------
say "Creating service user"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  ok "user '$SERVICE_USER' created"
else
  ok "user '$SERVICE_USER' already exists"
fi

# ---- 3. install dir --------------------------------------------------------
say "Installing CastAdhan files"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'venv' \
  --exclude 'backups' \
  --exclude '.DS_Store' \
  --exclude 'deploy' \
  "$SOURCE_DIR"/ "$INSTALL_DIR"/
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
ok "files copied to $INSTALL_DIR"

# ---- 4. python venv --------------------------------------------------------
say "Building Python virtualenv (this takes a couple of minutes)"
sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "venv ready"

# ---- 5. systemd unit -------------------------------------------------------
say "Installing systemd service"
install -m 0644 "$SOURCE_DIR/deploy/castadhan-portable.service" /etc/systemd/system/castadhan-portable.service
systemctl daemon-reload
systemctl enable castadhan-portable.service >/dev/null
ok "service enabled — will auto-start on boot"

# ---- 6. hostname + mDNS ----------------------------------------------------
say "Setting hostname to '$HOSTNAME' (so the Pi is reachable as $HOSTNAME.local)"
CURRENT_HOST=$(hostname)
if [ "$CURRENT_HOST" != "$HOSTNAME" ]; then
  hostnamectl set-hostname "$HOSTNAME"
  # Update /etc/hosts so sudo doesn't complain
  if grep -q "127.0.1.1" /etc/hosts; then
    sed -i "s/^127.0.1.1.*/127.0.1.1\t$HOSTNAME/" /etc/hosts
  else
    echo -e "127.0.1.1\t$HOSTNAME" >> /etc/hosts
  fi
  ok "hostname changed to $HOSTNAME"
else
  ok "hostname already $HOSTNAME"
fi

# Make sure avahi is running & enabled (auto-publishes <hostname>.local via mDNS)
systemctl enable avahi-daemon >/dev/null 2>&1 || true
systemctl restart avahi-daemon || true
ok "avahi-daemon running — $HOSTNAME.local should resolve on the LAN"

# ---- 6b. install auto-update timer + sudoers stanza -----------------------
say "Installing auto-update timer"
install -m 0644 "$SOURCE_DIR/deploy/castadhan-update.service" /etc/systemd/system/castadhan-update.service
install -m 0644 "$SOURCE_DIR/deploy/castadhan-update.timer"   /etc/systemd/system/castadhan-update.timer
if [ ! -f /etc/default/castadhan-update ]; then
  install -m 0644 "$SOURCE_DIR/deploy/castadhan-update.defaults" /etc/default/castadhan-update
fi
# Sudoers stanza — allows the castadhan service user to trigger updates from the web UI
install -m 0440 "$SOURCE_DIR/deploy/castadhan-sudoers" /etc/sudoers.d/castadhan
visudo -c -f /etc/sudoers.d/castadhan >/dev/null && ok "sudoers stanza installed" || warn "sudoers check failed — Update Now button may not work"

# `at` command is needed for the post-update cleanup of rollback dir
apt-get install -y --no-install-recommends at >/dev/null 2>&1 || true
systemctl enable --now atd >/dev/null 2>&1 || true

systemctl daemon-reload
systemctl enable castadhan-update.timer >/dev/null
systemctl start castadhan-update.timer
ok "auto-update timer enabled (daily 04:00 + 10 min after boot)"

# ---- 6c. Tailscale auto-enrolment (v1.3.0, O17v3) -------------------------
# Goal: every fresh-flashed CastAdhan Pi joins the maintainer's tailnet on
# first boot so future bug fixes can be applied remotely. Without this, every
# gift Pi at a remote household enters the "we can't reach it" state from day
# one (see Lesson 34, son's Pi in Haverfordwest).
#
# Auth-key delivery (in priority order):
#   1. Env var TS_AUTHKEY=tskey-auth-...   (passed inline to setup-pi.sh)
#   2. File /etc/default/castadhan-tailscale   (line: TS_AUTHKEY="tskey-...")
#   3. File /boot/castadhan-tailscale.env   (placed on SD card before flash —
#      survives Pi Imager's first-boot setup, easy to bake per-gift)
#
# If no key found anywhere, the step is skipped gracefully (log line only).
# The Pi still installs fine, just without a remote-support path. The
# maintainer can enrol later via one-shot SSH (same recovery flow as
# son's Pi).
say "Tailscale auto-enrolment"
TS_AUTHKEY="${TS_AUTHKEY:-}"
TS_HOSTNAME="${TS_HOSTNAME:-$(hostname)}"
TS_EXTRA_ARGS="${TS_EXTRA_ARGS:---ssh}"

# Source candidate files (if env var not already set)
if [ -z "$TS_AUTHKEY" ] && [ -f /etc/default/castadhan-tailscale ]; then
  # shellcheck disable=SC1091
  . /etc/default/castadhan-tailscale
fi
if [ -z "$TS_AUTHKEY" ] && [ -f /boot/castadhan-tailscale.env ]; then
  # shellcheck disable=SC1091
  . /boot/castadhan-tailscale.env
fi
if [ -z "$TS_AUTHKEY" ] && [ -f /boot/firmware/castadhan-tailscale.env ]; then
  # Pi OS Bookworm moved /boot to /boot/firmware
  # shellcheck disable=SC1091
  . /boot/firmware/castadhan-tailscale.env
fi

if [ -z "$TS_AUTHKEY" ]; then
  warn "No Tailscale auth key found (env TS_AUTHKEY / /etc/default/castadhan-tailscale / /boot[/firmware]/castadhan-tailscale.env)"
  warn "Pi will install fine, but you'll need to enrol it via SSH later for remote support."
  warn "See deploy/castadhan-tailscale.defaults.template for the expected format."
else
  if ! command -v tailscale >/dev/null 2>&1; then
    say "Installing Tailscale (this can take 30-90 seconds on a Pi)"
    if curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1; then
      ok "Tailscale installed"
    else
      warn "Tailscale install failed — continuing without remote-support path"
      TS_AUTHKEY=""
    fi
  else
    ok "Tailscale already installed"
  fi

  if [ -n "$TS_AUTHKEY" ]; then
    # Idempotent: skip enrolment if already on the tailnet
    if tailscale status --json 2>/dev/null | grep -q '"Self"'; then
      EXISTING_TAILNET=$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json; print(json.load(sys.stdin).get("CurrentTailnet",{}).get("Name","unknown"))' 2>/dev/null || echo "unknown")
      ok "Tailscale already enrolled (tailnet: $EXISTING_TAILNET) — skipping"
    else
      say "Enrolling on tailnet as hostname '$TS_HOSTNAME'"
      # shellcheck disable=SC2086
      if tailscale up --auth-key="$TS_AUTHKEY" --hostname="$TS_HOSTNAME" $TS_EXTRA_ARGS 2>&1 | tail -5; then
        TS_IP=$(tailscale ip -4 2>/dev/null | head -1 || echo "(pending)")
        ok "Tailscale enrolled — reachable from your tailnet at $TS_IP"
        # If the auth key came from /boot[/firmware]/, remove it now that
        # it's been consumed (one-shot keys leak risk on the SD card)
        for f in /boot/castadhan-tailscale.env /boot/firmware/castadhan-tailscale.env; do
          if [ -f "$f" ]; then
            shred -u "$f" 2>/dev/null || rm -f "$f"
            ok "Removed consumed auth-key file: $f"
          fi
        done
      else
        warn "Tailscale enrolment failed — Pi still installs, but no remote support"
      fi
    fi
  fi
fi

# ---- 7. start service ------------------------------------------------------
say "Starting CastAdhan"
systemctl restart castadhan-portable.service
sleep 4
if systemctl is-active --quiet castadhan-portable.service; then
  ok "service is active"
else
  warn "service failed to start; check:   sudo journalctl -u castadhan-portable -n 50"
  exit 1
fi

# ---- 8. summary ------------------------------------------------------------
IP=$(hostname -I | awk '{print $1}')
cat <<EOF


╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   🕌  CastAdhan Portable is up and running.                      ║
║                                                                  ║
║   Open in any browser on your home network:                      ║
║                                                                  ║
║      ▸  http://castadhan.local:8786                              ║
║      ▸  http://${IP}:8786$(printf '%*s' $((33 - ${#IP})) '')║
║                                                                  ║
║   You'll see the first-run setup wizard. Follow it once.         ║
║                                                                  ║
║   To check status:   sudo systemctl status castadhan-portable    ║
║   To see logs:       sudo journalctl -u castadhan-portable -f    ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

EOF
