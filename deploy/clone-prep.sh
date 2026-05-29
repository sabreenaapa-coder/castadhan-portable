#!/usr/bin/env bash
# CastAdhan Portable — Clone Prep
# ================================
# Run this on a working Pi BEFORE imaging the SD card for cloning.
# Wipes all per-installation state so the next person to boot the SD card
# gets a fresh first-run wizard, no leftover WiFi, no logs, no history.
#
# Usage:  sudo bash clone-prep.sh
#
# After this finishes, power off cleanly (sudo poweroff) and image the card.

set -euo pipefail

INSTALL_DIR=/opt/castadhan-portable
SERVICE=castadhan-portable.service

say()  { printf "\n\033[1;33m▶ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;31m!\033[0m %s\n" "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash $(basename "$0")"; exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
  echo "$INSTALL_DIR not found — is CastAdhan installed?"; exit 1
fi

# ---- 1. stop the service so nothing is mid-write ---------------------------
say "Stopping CastAdhan"
systemctl stop "$SERVICE" 2>/dev/null || true
ok "service stopped"

# ---- 2. reset the first-run wizard so the new user gets it ------------------
say "Resetting first-run wizard flag"
python3 - <<EOF
import yaml, os
p = "$INSTALL_DIR/config.yaml"
c = yaml.safe_load(open(p)) or {}
c.setdefault("rules", {})["setup_complete"] = False
# Blank out auto-detected location so the new owner's location takes over
c.setdefault("app", {}).setdefault("location", {}).update({
    "city": "", "country": "", "latitude": 0.0, "longitude": 0.0
})
# Forget per-Pi twilight cache state (rules are kept)
open(p, "w").write(yaml.dump(c, default_flow_style=False, indent=2))
print("  config.yaml reset")
EOF
ok "wizard will run for the next owner"

# ---- 3. clean per-Pi state files -------------------------------------------
say "Cleaning per-Pi state"
rm -f "$INSTALL_DIR/castadhan.log"  "$INSTALL_DIR/castadhan.log."*
rm -f "$INSTALL_DIR/ui_state.json"  "$INSTALL_DIR/ui_state.json.lock"
rm -f "$INSTALL_DIR/prayer_times_cache.json"
rm -rf "$INSTALL_DIR/__pycache__"
rm -rf "$INSTALL_DIR/backups"
ok "state files cleared"

# ---- 4. forget WiFi (handled separately by wifi-prebake before imaging) ----
say "Wiping WiFi credentials"
# Raspberry Pi OS Bookworm uses NetworkManager keyfiles
rm -f /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null || true
# Older releases use wpa_supplicant
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
  cat > /etc/wpa_supplicant/wpa_supplicant.conf <<'WPA'
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=GB
WPA
fi
ok "WiFi credentials removed (wifi-prebake.sh will install the recipient's)"

# ---- 5. SSH host keys regenerate on next boot -------------------------------
say "Removing SSH host keys (regenerated on next boot)"
rm -f /etc/ssh/ssh_host_* 2>/dev/null || true
# Drop a one-shot service to regenerate them on next boot
cat > /etc/systemd/system/regenerate-ssh-host-keys.service <<'UNIT'
[Unit]
Description=Regenerate SSH host keys (post-clone)
ConditionPathExists=!/etc/ssh/ssh_host_rsa_key
Before=ssh.service

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A
ExecStartPost=/bin/systemctl disable regenerate-ssh-host-keys.service

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable regenerate-ssh-host-keys.service >/dev/null
ok "SSH host keys removed"

# ---- 5b. first-boot auto-setup (zero-touch Tailscale enrol on gift Pis) -----
# So a flashed gift card needs NO manual `sudo bash setup-pi.sh`: on first boot
# this oneshot runs the installer once (which picks up the SD-card auth key,
# enrols on the tailnet, shreds the key), then disables itself. Lets you "flash,
# drop the auth-key file on /boot, post the SD card" — recipient just powers on.
say "Enabling first-boot auto-setup"
if [ -f "$INSTALL_DIR/deploy/castadhan-firstboot.service" ]; then
  install -m 0644 "$INSTALL_DIR/deploy/castadhan-firstboot.service" /etc/systemd/system/castadhan-firstboot.service
  systemctl daemon-reload
  systemctl enable castadhan-firstboot.service >/dev/null
  ok "first-boot setup will run once on the next owner's first boot"
else
  warn "castadhan-firstboot.service not found in $INSTALL_DIR/deploy — skipping (update the install first)"
fi

# ---- 6. shell history + sensitive logs --------------------------------------
say "Clearing shell history & logs"
for user_home in /root /home/*; do
  rm -f "$user_home/.bash_history" "$user_home/.python_history" "$user_home/.zsh_history" 2>/dev/null
done
truncate -s 0 /var/log/wtmp /var/log/btmp 2>/dev/null || true
rm -f /var/log/lastlog 2>/dev/null || true
journalctl --rotate --vacuum-time=1s >/dev/null 2>&1 || true
ok "shell history & journal cleared"

# ---- 7. cap machine-id so each clone gets unique one ------------------------
say "Resetting machine-id"
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id 2>/dev/null
ln -sf /etc/machine-id /var/lib/dbus/machine-id
ok "machine-id will regenerate on first boot"

# ---- 8. summary ------------------------------------------------------------
chown -R castadhan:castadhan "$INSTALL_DIR"

cat <<EOF


╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   🧹  Clone prep complete.                                        ║
║                                                                  ║
║   Next steps:                                                    ║
║                                                                  ║
║   1. Optionally bake the recipient's WiFi into this SD card      ║
║      first (recommended for zero-friction gifts):                ║
║          sudo bash deploy/wifi-prebake.sh "<SSID>" "<password>"  ║
║                                                                  ║
║   2. Shut down cleanly:                                          ║
║          sudo poweroff                                           ║
║                                                                  ║
║   3. Pull the SD card and image it on your laptop:               ║
║          sudo dd if=/dev/diskN of=~/castadhan-golden.img bs=4M   ║
║                                                                  ║
║   4. Use that .img as the source for every future gift.          ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

EOF
