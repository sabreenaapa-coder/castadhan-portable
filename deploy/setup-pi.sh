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
