#!/usr/bin/env python3
"""Self-healing known_speakers.json refresher.

Why this exists: the app discovers Cast speakers by name over mDNS, but on some
hosts that live discovery is unreliable — Ubuntu boxes' CastBrowser returns 0,
and some Pis' speaker set flaps. On those units the app leans on known_hosts
(static IPs in known_speakers.json), which silently break when the router hands
the speakers new DHCP leases -> NO_SPEAKERS -> missed adhans (B-Belgium-67/68).

pychromecast.get_chromecasts() works even where CastBrowser doesn't, so this
re-finds the speakers by name every 15 min (via castadhan-refresh.timer),
rewrites known_speakers.json with their current IPs, and nudges /api/rediscover.
Net effect: automatic speaker-IP self-healing on every unit.

Filters to individual AUDIO speakers (cast_type 'audio') so displays ('cast')
and Cast groups ('group') — neither pinnable at IP:8009 — never pollute the list.
"""
import json
import os
import urllib.request

import pychromecast

ROOT = os.path.dirname(os.path.abspath(__file__))
KH = os.path.join(ROOT, "known_speakers.json")


def main():
    try:
        ccs, browser = pychromecast.get_chromecasts(timeout=12)
    except Exception as e:
        print("discovery error:", e)
        return
    found = {}
    for c in ccs:
        ci = c.cast_info
        if getattr(ci, "cast_type", None) != "audio":  # audio speakers only
            continue
        name = (ci.friendly_name or "").strip()
        host = getattr(ci, "host", None)
        if name and host:
            found[name] = host
    try:
        pychromecast.discovery.stop_discovery(browser)
    except Exception:
        pass

    if not found:
        print("no audio speakers found this pass — leaving known_speakers.json unchanged")
        return

    cur = {}
    if os.path.exists(KH):
        try:
            cur = json.load(open(KH))
        except Exception:
            cur = {}

    merged = dict(cur)
    merged.update(found)  # update IPs for known speakers; auto-add new ones
    if merged != cur:
        json.dump(merged, open(KH, "w"), indent=2)
        print("updated known_speakers.json ->", found)
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:8786/api/rediscover", data=b"", timeout=25
            )
            print("nudged app rediscovery")
        except Exception as e:
            print("rediscover nudge failed (app will catch up on its own cycle):", e)
    else:
        print("no IP changes:", found)


if __name__ == "__main__":
    main()
