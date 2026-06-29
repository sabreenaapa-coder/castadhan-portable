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
  python3 fleet_status.py --list          # print the resolved roster and exit (no poll)

Roster — ZERO upkeep at fleet scale:
  By default the roster is auto-discovered from Tailscale: every tailnet node whose
  hostname starts with "castadhan-" (how gift units self-enrol — castadhan-<serial>,
  see setup-pi.sh) is polled automatically. A Pi that enrols today is in tomorrow's
  roll-call with no edits. Requires the `tailscale` CLI on this hub (the macOS app
  path is found automatically).

  An optional local file (~/.castadhan-fleet.json, gitignored, NO secrets) is MERGED
  on top — use it for friendly labels or boxes NOT named castadhan-* (e.g. the
  original named fleet). File entries win by name:
    [{"name": "aunt-pi-ghent", "host": "100.81.101.1"},
     {"name": "son-pi-hwest",  "host": "100.x.x.x"}]

Telegram creds via env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

PORT = 8786
HTTP_TIMEOUT = 6  # seconds per request; a dead Pi must not hang the whole run
GH_REPO = "sabreenaapa-coder/castadhan-portable"
ROSTER_FILE = os.path.expanduser("~/.castadhan-fleet.json")
TS_PREFIX = "castadhan"  # gift units self-enrol as castadhan-<serial> (setup-pi.sh)
TS_BINS = ["tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
           "/usr/local/bin/tailscale", "/usr/bin/tailscale"]


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


def _tailscale_status_json():
    """`tailscale status --json` as a dict, or None if the CLI isn't reachable.
    Tries PATH first, then the macOS Tailscale.app bundle path."""
    for binp in TS_BINS:
        try:
            out = subprocess.run([binp, "status", "--json"],
                                 capture_output=True, text=True, timeout=15)
        except (FileNotFoundError, OSError):
            continue  # this binary path isn't present — try the next
        except Exception:
            return None
        if out.returncode == 0 and out.stdout.strip():
            try:
                return json.loads(out.stdout)
            except Exception:
                return None
    return None


def roster_from_tailscale(prefix=TS_PREFIX):
    """Every tailnet node whose hostname starts with `prefix` -> {name, host}.
    Zero upkeep: a freshly-enrolled castadhan-<serial> Pi appears in the roll-call
    automatically, so monitoring grows with the fleet without editing any file."""
    data = _tailscale_status_json()
    if not data:
        return []
    nodes = []
    if data.get("Self"):
        nodes.append(data["Self"])
    nodes.extend((data.get("Peer") or {}).values())
    roster = []
    for n in nodes:
        host = (n.get("HostName") or "").strip()
        if not host.lower().startswith(prefix.lower()):
            continue
        ips = n.get("TailscaleIPs") or []
        ip = next((i for i in ips if ":" not in i), ips[0] if ips else None)  # prefer IPv4
        if ip:
            roster.append({"name": host, "host": ip})
    return roster


def load_roster():
    """Tailscale auto-discovery (castadhan-* nodes) MERGED with the optional local
    file. File entries win by name — they add friendly labels or boxes not on the
    tailnet (e.g. the original named fleet). Deduped, name-sorted for stable output."""
    by_name = {p["name"]: p for p in roster_from_tailscale()}
    if os.path.exists(ROSTER_FILE):
        try:
            with open(ROSTER_FILE) as f:
                for p in json.load(f):
                    by_name[p["name"]] = p  # file overrides / augments auto-discovery
        except Exception as e:
            print(f"roster file {ROSTER_FILE} unreadable ({type(e).__name__}); "
                  "using Tailscale auto-discovery only", file=sys.stderr)
    return [by_name[k] for k in sorted(by_name)]


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
    roster = load_roster()
    if not roster:
        print("No Pis found. Enrol gift units on Tailscale as castadhan-<serial> "
              "(automatic via setup-pi.sh), make sure the `tailscale` CLI is on this "
              f"hub, or list boxes manually in {ROSTER_FILE}.", file=sys.stderr)
        sys.exit(1)
    if "--list" in sys.argv:  # show what got discovered, no polling / no Telegram
        print(f"{len(roster)} CastAdhan(s):")
        for p in roster:
            print(f"  {p['name']:<28} {p['host']}")
        return
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
