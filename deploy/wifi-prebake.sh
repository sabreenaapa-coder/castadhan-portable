#!/usr/bin/env bash
# CastAdhan Portable — WiFi Pre-bake
# ===================================
# Bakes the recipient's WiFi credentials into the running Pi (or SD card
# mounted on a Pi). The next boot will auto-join their network with zero
# action from them.
#
# Usage:  sudo bash wifi-prebake.sh "SSID" "password" [country_code]
#         country_code defaults to BE for Belgium; common values: GB, DE, FR, NL, US
#
# Example for your aunt in Belgium:
#   sudo bash wifi-prebake.sh "VOO-12345" "supersecret123" BE
#
# Run this AFTER clone-prep.sh, BEFORE you image the SD card.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash $(basename "$0") \"SSID\" \"password\" [country_code]"; exit 1
fi

SSID="${1:-}"
PASS="${2:-}"
COUNTRY="${3:-BE}"

if [ -z "$SSID" ] || [ -z "$PASS" ]; then
  cat <<EOF
Usage: sudo bash $(basename "$0") "SSID" "password" [country_code]

Examples:
  sudo bash wifi-prebake.sh "MyHome-5G" "letmein2026" GB
  sudo bash wifi-prebake.sh "VOO-12345" "supersecret" BE

Country code (2 letters):
  GB = United Kingdom    BE = Belgium    NL = Netherlands
  DE = Germany           FR = France     IE = Ireland
  US = United States     PK = Pakistan   TR = Turkey
EOF
  exit 1
fi

say()  { printf "\n\033[1;33m▶ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }

say "Pre-baking WiFi: SSID='$SSID', country=$COUNTRY"

# Set the WiFi regulatory domain — important for 5 GHz channel availability
raspi-config nonint do_wifi_country "$COUNTRY" 2>/dev/null || \
  echo "country=$COUNTRY" > /etc/wpa_supplicant/wpa_supplicant.conf.country

# Detect which network stack the OS uses
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
  # Raspberry Pi OS Bookworm (default since late 2023) uses NetworkManager
  say "Using NetworkManager (modern Raspberry Pi OS)"

  # Remove any existing connection profiles
  rm -f /etc/NetworkManager/system-connections/*.nmconnection

  # Write a fresh connection profile
  CONN_FILE="/etc/NetworkManager/system-connections/${SSID}.nmconnection"
  cat > "$CONN_FILE" <<EOF
[connection]
id=$SSID
type=wifi
autoconnect=true
permissions=

[wifi]
mode=infrastructure
ssid=$SSID

[wifi-security]
key-mgmt=wpa-psk
psk=$PASS

[ipv4]
method=auto

[ipv6]
method=auto
addr-gen-mode=default
EOF
  chmod 600 "$CONN_FILE"
  ok "NetworkManager profile written: $CONN_FILE"

else
  # Older Pi OS releases use wpa_supplicant directly
  say "Using wpa_supplicant (older Raspberry Pi OS)"

  WPA_FILE=/etc/wpa_supplicant/wpa_supplicant.conf
  cat > "$WPA_FILE" <<EOF
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=$COUNTRY

network={
    ssid="$SSID"
    psk="$PASS"
    key_mgmt=WPA-PSK
    priority=1
}
EOF
  chmod 600 "$WPA_FILE"
  ok "wpa_supplicant.conf written: $WPA_FILE"
fi

cat <<EOF


╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   📶  WiFi pre-baked.                                             ║
║                                                                  ║
║   On first boot at the recipient's home, the Pi will auto-join: ║
║       SSID:    $SSID
║       Country: $COUNTRY
║                                                                  ║
║   Power off and image the SD card now:                          ║
║       sudo poweroff                                              ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

EOF
