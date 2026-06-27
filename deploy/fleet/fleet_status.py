#!/usr/bin/env python3
"""
fleet_status.py — CastAdhan fleet aggregator (Telegram MVP).

Runs on ONE hub (your Mac, or a nominated Pi). Polls every family Pi over the
Tailscale tailnet, composes a single roll-call, posts it to Telegram. Stdlib
only (urllib) — no pip. Secrets + roster come from the environment / a local
gitignored file, never hardcoded (CastAdhan security fence).

Endpoints used (all confirmed in app.py):
  GET /api/version  -> {ok, version, device_id}            (liveness + identity)
  GET /api/state    -> {devices.speakers[], next_prayer}   (next prayer + speakers)
  GET /api/today    -> {played, of, problem, per_prayer}   (added for the fleet view)

Usage:
  python3 fleet_status.py                 # full daily roll-call
  python3 fleet_status.py --only-problems # silent unless something is wrong (heartbeat)

Roster (~/.castadhan-fleet.json, local, gitignored, NO secrets):
  [{"name": "aunt-pi-ghent", "host": "100.81.101.1"},
   {"name": "son-pi-hwest",  "host": "100.x.x.x"}]

Telegram creds via env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

PORT = 8786
HTTP_TIMEOUT = 6  # seconds per request; a dead Pi must not hang the whole run
GH_REPO = "sabreenaapa-coder/castadhan-portable"
ROSTER_FILE = os.path.expanduser("~/.castadhan-fleet.json")


def _get_json(url, timeout=HTTP_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "castadhan-fleet"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def latest_release_tag():
    """Newest GitHub release tag (without leading 'v'), or None if unreachable."""
    try:
        d = _get_json(f"https://api.github.com/repos/{GH_REPO}/releases/latest", timeout=8)
        return (d.get("tag_name") or "").lstrip("v") or None
    except Exception:
        return None


def poll_pi(pi, latest):
    """Status for one Pi. Never raises — a dead Pi is data, not an error."""
    base = f"http://{pi['host']}:{PORT}"
    out = {"name": pi["name"], "online": False, "version": None, "behind": False,
           "next": None, "speakers": None, "today": None, "note": ""}
    # 1) Liveness + identity + version — works with NO changes on the Pi.
    try:
        v = _get_json(f"{base}/api/version")
        out["online"] = bool(v.get("ok", True))
        out["version"] = v.get("version")
    except Exception as e:
        out["note"] = type(e).__name__  # offline: this is the headline
        return out
    if latest and out["version"] and out["version"] != latest:
        out["behind"] = True
    # 2) Next prayer + speaker count from /api/state.
    try:
        st = _get_json(f"{base}/api/state")
        nxt = st.get("next_prayer") or {}
        out["next"] = nxt.get("name") or nxt.get("prayer")  # [VERIFY exact field]
        out["speakers"] = len((st.get("devices") or {}).get("speakers") or [])
    except Exception:
        pass
    # 3) Today's per-prayer result (needs the /api/today endpoint).
    try:
        out["today"] = _get_json(f"{base}/api/today", timeout=4)
    except Exception:
        pass  # endpoint absent on older Pis -> degrade silently
    return out


def line_for(p):
    if not p["online"]:
        return f"\U0001f534 {p['name']} — OFFLINE ({p['note']})"
    bits = []
    today = p.get("today")
    if today:
        bits.append(f"{today.get('played', '?')}/{today.get('of', 5)} prayers")
    if p.get("next"):
        bits.append(f"next {p['next']}")
    if p.get("speakers") is not None:
        bits.append(f"{p['speakers']} spk")
    bits.append(f"v{p['version']}" + (" ⬆️behind" if p["behind"] else ""))
    bad = p["behind"] or (today or {}).get("problem")
    icon = "⚠️" if bad else "✅"
    return f"{icon} {p['name']} — " + " · ".join(bits)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:  # never hardcode — print instead of leaking
        print("Telegram not configured; printing:\n" + text, file=sys.stderr)
        return
    body = json.dumps({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def main():
    only_problems = "--only-problems" in sys.argv
    with open(ROSTER_FILE) as f:
        roster = json.load(f)
    latest = latest_release_tag()
    results = [poll_pi(pi, latest) for pi in roster]
    problems = [r for r in results
                if not r["online"] or r["behind"] or (r.get("today") or {}).get("problem")]
    if only_problems and not problems:
        return  # heartbeat mode: stay silent when all-clear
    header = f"\U0001f54b CastAdhan Fleet — {datetime.now(timezone.utc):%a %d %b}"
    footer = ("— all healthy ✅" if not problems
              else f"— {len(problems)} of {len(results)} need attention")
    send_telegram(header + "\n" + "\n".join(line_for(r) for r in results) + "\n" + footer)


if __name__ == "__main__":
    main()
