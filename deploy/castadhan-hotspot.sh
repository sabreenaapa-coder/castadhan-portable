#!/usr/bin/env bash
# CastAdhan Portable — WiFi onboarding hotspot (captive-portal fallback)
# =====================================================================
# Runs on every boot as castadhan-hotspot.service. Decides ONE thing:
# does this Pi have a working network yet?
#
#   • YES  (Ethernet cable, OR a pre-baked / previously-configured WiFi):
#          do nothing, exit. Normal operation.
#   • NO, AND this is a fresh un-configured unit:
#          bring up an OPEN WiFi access point called "CastAdhan Setup" so a
#          non-technical recipient can join it from their phone and type in
#          their home WiFi — no Ethernet cable, no app, no typing an IP.
#
# A tiny captive responder on :80 (castadhan-captive.py) + NetworkManager's
# shared-mode dnsmasq (pointed at us) make the setup page auto-pop on
# iOS/Android. Once the recipient's WiFi connects, the AP + responder are
# torn down and this exits.
#
# SAFETY: this ONLY starts the AP on a genuinely fresh unit — no saved WiFi
# profile AND the first-run wizard not yet completed. A configured, in-use box
# that is merely offline for a moment (router reboot) NEVER drops into setup
# mode; it just waits for its own network to return. Any error exits cleanly
# without touching the existing network config.
set -uo pipefail

AP_SSID="CastAdhan Setup"
AP_CON="castadhan-setup-ap"        # NetworkManager connection name for our AP
AP_IP="10.42.0.1"                  # NM shared-mode gateway (NM's fixed default)
WLAN=wlan0
CONFIG=/opt/castadhan-portable/config.yaml
SCAN_CACHE=/var/lib/castadhan/wifi-scan.json
CAPTIVE_PY=/opt/castadhan-portable/deploy/castadhan-captive.py
BOOT_GRACE=75                      # seconds to wait for a real network first
POLL=5

log() { printf '%s castadhan-hotspot: %s\n' "$(date '+%H:%M:%S')" "$*"; }

# Hard preconditions — bail quietly (exit 0) if this board can't do WiFi AP.
command -v nmcli >/dev/null 2>&1 || { log "nmcli absent — cannot run hotspot fallback"; exit 0; }
ip link show "$WLAN" >/dev/null 2>&1 || { log "no $WLAN interface (Ethernet-only board) — skipping"; exit 0; }

# --- helpers ---------------------------------------------------------------

# has_network: eth0 connected with an IP, OR wlan0 on a real (non-AP) network.
has_network() {
  if nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
       | grep -q '^eth0:ethernet:connected'; then
    return 0
  fi
  local wstate wconn
  wstate=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$WLAN" '$1==d{print $2}')
  wconn=$(nmcli -t -f DEVICE,CONNECTION device status 2>/dev/null | awk -F: -v d="$WLAN" '$1==d{print $2}')
  [ "$wstate" = "connected" ] && [ -n "$wconn" ] && [ "$wconn" != "$AP_CON" ]
}

# has_saved_wifi: a real saved WiFi profile exists (pre-baked or configured).
# Our own AP profile is excluded so a half-finished onboarding still counts as
# "fresh" and can retry.
has_saved_wifi() {
  nmcli -t -f NAME,TYPE connection show 2>/dev/null \
    | awk -F: -v ap="$AP_CON" '$2=="802-11-wireless" && $1!=ap {found=1} END{exit !found}'
}

# setup_complete: the first-run wizard has been finished on this box.
setup_complete() {
  grep -qiE '^[[:space:]]*setup_complete:[[:space:]]*true' "$CONFIG" 2>/dev/null
}

teardown_ap() {
  log "tearing down setup AP"
  nmcli connection down "$AP_CON"  >/dev/null 2>&1 || true
  nmcli connection delete "$AP_CON" >/dev/null 2>&1 || true
  pkill -f "$(basename "$CAPTIVE_PY")" >/dev/null 2>&1 || true
}

# --- 1. give a real network a chance first ---------------------------------
log "waiting up to ${BOOT_GRACE}s for a real network (Ethernet or known WiFi)"
_end=$((SECONDS + BOOT_GRACE))
while [ "$SECONDS" -lt "$_end" ]; do
  if has_network; then
    log "network is up — no hotspot needed"
    exit 0
  fi
  sleep "$POLL"
done

# --- 2. only fresh, unconfigured units may broadcast the setup AP ----------
if has_saved_wifi; then
  log "a saved WiFi profile exists — configured unit, just offline; not starting AP"
  exit 0
fi
if setup_complete; then
  log "first-run wizard already completed — not starting AP (will reconnect when network returns)"
  exit 0
fi

log "fresh unit with no network — starting onboarding hotspot"

# --- 3. pre-scan nearby WiFi (a single radio can't scan once it's an AP) ----
mkdir -p "$(dirname "$SCAN_CACHE")"
nmcli device wifi rescan >/dev/null 2>&1 || true
sleep 3
nmcli -t -f SSID,SIGNAL,SECURITY,FREQ device wifi list 2>/dev/null \
  | python3 -c '
import sys, json
seen=set(); out=[]
for ln in sys.stdin:
    p=ln.rstrip("\n").split(":")
    if len(p)<4: continue
    ssid=p[0].strip()
    if not ssid or ssid in seen: continue
    seen.add(ssid)
    try: sig=int(p[1])
    except ValueError: sig=0
    try: freq=int(p[3])
    except ValueError: freq=0
    out.append({"ssid":ssid,"signal":sig,"security":(p[2] or "open"),
                "band":"5 GHz" if freq>=5000 else "2.4 GHz"})
out.sort(key=lambda n:n["signal"], reverse=True)
json.dump(out, open("'"$SCAN_CACHE"'","w"))
' 2>/dev/null || echo "[]" > "$SCAN_CACHE"
chmod 644 "$SCAN_CACHE" 2>/dev/null || true
log "cached $(python3 -c 'import json;print(len(json.load(open("'"$SCAN_CACHE"'"))))' 2>/dev/null || echo '?') nearby networks for the setup page"

# --- 4. bring up an OPEN AP (no password to join — friendliest for onboarding)
nmcli connection delete "$AP_CON" >/dev/null 2>&1 || true
if ! nmcli connection add type wifi ifname "$WLAN" con-name "$AP_CON" \
       autoconnect no ssid "$AP_SSID" >/dev/null 2>&1; then
  log "could not create AP connection — exiting without changing anything"
  exit 0
fi
nmcli connection modify "$AP_CON" \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  ipv4.method shared \
  ipv6.method ignore >/dev/null 2>&1 || true
if ! nmcli connection up "$AP_CON" >/dev/null 2>&1; then
  log "failed to start AP — cleaning up"
  nmcli connection delete "$AP_CON" >/dev/null 2>&1 || true
  exit 0
fi
log "AP '$AP_SSID' is up — setup page at http://$AP_IP:8786/wifi-setup"

# --- 5. captive responder on :80 (auto-pops the setup page on phones) -------
if [ -f "$CAPTIVE_PY" ]; then
  setsid python3 "$CAPTIVE_PY" >/dev/null 2>&1 &
fi

trap teardown_ap EXIT INT TERM

# --- 6. wait until the recipient's WiFi connects, then clean up -------------
log "waiting for the recipient to choose their WiFi…"
while true; do
  if has_network; then
    log "recipient WiFi connected — onboarding complete, tearing down AP"
    exit 0        # trap runs teardown_ap
  fi
  sleep "$POLL"
done
