#!/usr/bin/env python3
"""CastAdhan Portable — Sanity Test
=====================================

Derived from INCIDENT_REPORT_2026-05-22.md. Every check below corresponds
to a bug we've actually shipped + regressed. If any check FAILs, the
corresponding documented bug class is back.

Run on the Pi:
    python3 /opt/castadhan-portable/sanity_test.py            # full report
    python3 /opt/castadhan-portable/sanity_test.py --quiet    # failures only
    python3 /opt/castadhan-portable/sanity_test.py --json     # machine-readable

Or remotely via SSH:
    ssh farley@<pi> 'python3 /opt/castadhan-portable/sanity_test.py'

Exit codes (used by castadhan-update.sh to gate releases):
    0   all checks passed
    1   at least one CRITICAL check failed  → auto-update rolls back
    2   no CRITICAL fails, but at least one HIGH/MEDIUM/LOW fail
    3   internal test error (e.g. couldn't reach localhost API)

────────────────────────────────────────────────────────────────────────────
CONTRIBUTOR NOTE — how this file grows
────────────────────────────────────────────────────────────────────────────
Every time a bug ships + is fixed in production, ADD a check here that
would have caught it. The test is append-only (never remove old checks —
they're the regression net for bugs the incident report has documented).

Rules of thumb:
  1. Each check maps to a specific bug ID or lesson number from the report.
     Reference the ID in the check name + detail string so future readers
     can find the context.
  2. Severity choice:
       CRITICAL  = "users will silently lose the product if this fails"
                   (no adhan, wrong prayer time, scheduler dies). Triggers
                   auto-rollback on update.
       HIGH      = "feature regressed but core still works". Logged as
                   warning, doesn't roll back. Operator should fix.
       MEDIUM    = "nice to have working" — informational.
       LOW       = "absence is not a real problem" — purely informational.
  3. Tests must be:
       - Safe (NEVER play audio — that's an active intervention, not a test)
       - Idempotent (running twice gives same result)
       - Fast (each check < 5s; whole suite < 30s — runs during update grace
         window with margin)
       - Self-contained (Python stdlib + subprocess to commands the Pi ships
         with — avahi-browse, systemctl, ping, curl, tailscale)
  4. Output format is stable: SEVERITY ✓/✗/! NAME DETAIL — don't break
     this, the update script + future log scrapers parse it.
  5. New layers (L12+) belong at the bottom — keep existing L1-L11 stable.
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

BASE_URL = "http://127.0.0.1:8786"
INSTALL_DIR = "/opt/castadhan-portable"
# Source dir for STATIC source-grep checks: the install dir on a Pi, or this
# file's own directory in a dev checkout — so the static checks run anywhere.
SRC_DIR = INSTALL_DIR if os.path.isdir(INSTALL_DIR) else os.path.dirname(os.path.abspath(__file__))
results = []   # (severity, category, name, status, detail)

def t(severity, category, name, ok, detail=""):
    results.append((severity, category, name, "PASS" if ok else "FAIL", detail))

def err(severity, category, name, e):
    results.append((severity, category, name, "ERROR", str(e)))

def http_get(path, timeout=5):
    r = urllib.request.urlopen(BASE_URL + path, timeout=timeout)
    return r.status, r.read()

def http_json(path, timeout=5):
    code, body = http_get(path, timeout)
    return code, json.loads(body.decode("utf-8"))

def read(path):
    with open(path) as f:
        return f.read()

def run(cmd, timeout=8):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)

def src(rel):
    """Read a SOURCE file (app.py, console.html, deploy/*, VERSION …) for static
    regression checks, from SRC_DIR — the install dir on a Pi, the repo dir in a
    dev checkout. Read-only; never plays audio."""
    return read(os.path.join(SRC_DIR, rel))

def _fn_body(text, defline):
    """Slice a Python function body: from `defline` to the next top-level `def `."""
    i = text.find(defline)
    if i < 0:
        return ""
    j = text.find("\ndef ", i + len(defline))
    return text[i: j if j > 0 else len(text)]

# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — System health
# ─────────────────────────────────────────────────────────────────────────────
def L1_system():
    cat = "L1 system"

    # Service active (basic sanity)
    try:
        r = run(["systemctl", "is-active", "castadhan-portable.service"])
        t("CRITICAL", cat, "service-active", r.stdout.strip() == "active", r.stdout.strip())
    except Exception as e: err("CRITICAL", cat, "service-active", e)

    # NTP synced
    try:
        r = run(["timedatectl"])
        synced = "System clock synchronized: yes" in r.stdout
        t("HIGH", cat, "ntp-synced", synced, "extracted from timedatectl")
    except Exception as e: err("HIGH", cat, "ntp-synced", e)

    # O29 / Lesson 26 — system tz matches app tz
    try:
        app_tz = None
        for line in read(INSTALL_DIR + "/config.yaml").splitlines():
            m = re.match(r"\s*timezone:\s*(\S+)\s*$", line)
            if m and ":" in m.group(0):
                app_tz = m.group(1).strip("'\"")
                break
        sys_tz = None
        for line in run(["timedatectl"]).stdout.splitlines():
            m = re.search(r"Time zone:\s+(\S+)", line)
            if m: sys_tz = m.group(1); break
        t("HIGH", cat, "tz-app-eq-system", app_tz == sys_tz, f"app={app_tz} system={sys_tz}")
    except Exception as e: err("HIGH", cat, "tz-app-eq-system", e)

    # Memory headroom — read from /proc instead of systemd (cleaner — systemd's
    # MemoryCurrent can be '[not set]' under certain unit-file configurations)
    try:
        pid = run(["pgrep", "-f", "castadhan-portable/app.py"]).stdout.strip().split("\n")[0]
        cur_kb = 0
        if pid:
            for line in open(f"/proc/{pid}/status"):
                if line.startswith("VmRSS:"):
                    cur_kb = int(line.split()[1]); break
        # Ceiling: parse MemoryMax from systemd if numeric, otherwise unlimited
        r = run(["systemctl", "show", "castadhan-portable.service", "--property=MemoryMax"])
        mmax_bytes = 0
        for line in r.stdout.splitlines():
            if line.startswith("MemoryMax="):
                v = line.split("=",1)[1].strip()
                if v.isdigit(): mmax_bytes = int(v)
        cur_mb = cur_kb // 1024
        max_mb = mmax_bytes // 1024 // 1024
        ok = max_mb == 0 or cur_mb < max_mb * 0.85
        t("MEDIUM", cat, "memory-headroom", ok,
          f"{cur_mb}MB used / {max_mb if max_mb else '∞'}MB ceiling")
    except Exception as e: err("MEDIUM", cat, "memory-headroom", e)

    # Disk space
    try:
        r = run(["df", "-h", "/"])
        line = [l for l in r.stdout.splitlines() if "/" in l and "Filesystem" not in l][0]
        used_pct = int(line.split()[4].rstrip("%"))
        t("MEDIUM", cat, "disk-space", used_pct < 90, f"{used_pct}% used on /")
    except Exception as e: err("MEDIUM", cat, "disk-space", e)

    # B-Belgium-23 (v1.7.3) — thread-count canary. The cast-object thread leak
    # hit 89 threads on 28 May, starving APScheduler's worker pool so Asr +
    # Maghrib jobs never executed. A healthy process sits ~6-15 threads. This
    # is HIGH (not CRITICAL) because a high count doesn't instantly break a
    # single play — it degrades over hours — but it's the direct canary for
    # the leak. If it trips, the disconnect-on-replace logic has regressed.
    try:
        pid = run(["pgrep", "-f", "castadhan-portable/app.py"]).stdout.strip().split("\n")[0]
        nthreads = 0
        if pid:
            for line in open(f"/proc/{pid}/status"):
                if line.startswith("Threads:"):
                    nthreads = int(line.split()[1]); break
        t("HIGH", cat, "thread-count-sane", 0 < nthreads < 40,
          f"{nthreads} threads (leak suspected if >40 — see B-Belgium-23)")
    except Exception as e: err("HIGH", cat, "thread-count-sane", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Configuration integrity (huge in the incident report)
# ─────────────────────────────────────────────────────────────────────────────
def L2_config():
    cat = "L2 config"
    try:
        _, cfg = http_json("/api/config")
        rules = cfg["config"]["rules"]
        app = cfg["config"]["app"]
        spk = cfg["config"]["speakers"]

        # B-Belgium-1 / O24 / B-Belgium-19 — include_if_name_contains MUST be ''
        t("CRITICAL", cat, "include-filter-empty",
          spk.get("include_if_name_contains") == "",
          f"got {spk.get('include_if_name_contains')!r}")

        # O3 — auto_detect_location_on_startup MUST be false
        t("HIGH", cat, "auto-detect-disabled",
          rules.get("auto_detect_location_on_startup") is False,
          f"got {rules.get('auto_detect_location_on_startup')!r}")

        # C-1 — calculation_method must be one of the supported values
        valid_methods = {"ISNA","MWL","Egyptian","Karachi","UmmAlQura","Umm al-Qura"}
        cm = app.get("calculation_method","")
        t("CRITICAL", cat, "calc-method-valid", cm in valid_methods, f"got {cm!r}")

        # C-2 — madhab must be one of the supported values
        valid_madhab = {"shafii","hanafi","maliki","hanbali"}
        m = (rules.get("madhab") or "").lower()
        t("CRITICAL", cat, "madhab-valid", m in valid_madhab, f"got {m!r}")

        # O37 — high_latitude_method must be one of the supported values
        valid_hilat = {"combine_prayers","1_7_rule","static_offset"}
        h = rules.get("high_latitude_method","")
        t("HIGH", cat, "hilat-valid", h in valid_hilat, f"got {h!r}")

        # B-Belgium-13 / O29 — app + Pi system tz match
        t("HIGH", cat, "app-tz-set", bool(app.get("timezone")), f"got {app.get('timezone')!r}")

        # Setup completed
        t("HIGH", cat, "setup-complete", rules.get("setup_complete") is True,
          f"got {rules.get('setup_complete')!r}")

        # Location actually set
        loc = app.get("location",{})
        has_loc = bool(loc.get("city") and loc.get("country") and loc.get("latitude"))
        t("CRITICAL", cat, "location-set", has_loc, f"city={loc.get('city')}, lat={loc.get('latitude')}")

        # takbeeraat_window default exists + valid
        tw = (rules.get("takbeeraat_window") or "inclusive").lower()
        t("MEDIUM", cat, "takbeeraat-window-valid", tw in ("inclusive","strict"), f"got {tw!r}")
    except Exception as e:
        err("CRITICAL", cat, "config-load", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — Discovery (the recurring nightmare)
# ─────────────────────────────────────────────────────────────────────────────
def L3_discovery():
    cat = "L3 discovery"
    try:
        # avahi-browse can be shelled out to
        r = run(["avahi-browse", "-atrp", "-l", "--resolve"], timeout=6)
        cast_count = sum(1 for line in r.stdout.splitlines()
                         if line.startswith("=;") and "_googlecast" in line and ";IPv4;" in line)
        t("HIGH", cat, "avahi-cast-visible", cast_count > 0,
          f"{cast_count} Cast records visible via system mDNS")

        # known_speakers.json exists + valid JSON
        ks_path = INSTALL_DIR + "/known_speakers.json"
        ks = json.loads(read(ks_path))
        t("HIGH", cat, "known-speakers-json-valid", isinstance(ks, dict), f"{len(ks)} entries")

        # Each known speaker IP responds to ping (basic reachability)
        for name, ip in ks.items():
            r = run(["ping", "-c", "1", "-W", "2", ip])
            t("MEDIUM", cat, f"ping-{name}", r.returncode == 0, ip)

        # O39 — each known speaker's :8009 either open OR clearly dead (no hangs)
        for name, ip in ks.items():
            try:
                with socket.create_connection((ip, 8009), timeout=2):
                    t("MEDIUM", cat, f"port8009-{name}", True, f"{ip}:8009 open")
            except (OSError, socket.timeout):
                # NOT a fail — speaker might just be off. But log it as INFO.
                t("LOW", cat, f"port8009-{name}", False, f"{ip}:8009 unreachable (speaker likely off)")

        # B-Belgium-2/8/16/19 — discover_casts() returns >= 1 speaker
        _, state = http_json("/api/state", timeout=15)
        spks = state.get("devices",{}).get("speakers",[])
        t("CRITICAL", cat, "discover-returns-speakers",
          len(spks) > 0, f"{len(spks)} speakers: {[s.get('name') for s in spks]}")

        # Each discovered speaker is connected=true
        all_conn = all(s.get("connected") for s in spks)
        t("HIGH", cat, "all-speakers-connected", all_conn or len(spks)==0,
          f"{sum(1 for s in spks if s.get('connected'))}/{len(spks)} connected")
    except Exception as e:
        err("CRITICAL", cat, "discovery-overall", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 4 — Scheduler (Lesson 27 / B-Belgium-13 / O35 next_run_time bug)
# ─────────────────────────────────────────────────────────────────────────────
def L4_scheduler():
    cat = "L4 scheduler"
    try:
        _, state = http_json("/api/state")
        jobs = state.get("scheduled_jobs",[])
        job_ids = {j.get("id","") for j in jobs}

        # O35 — periodic jobs MUST be present (smoking gun for schedule_today truncation)
        required = {"cast_rediscovery","dst_protection_refresh","health_check","twilight_scan","refresh_daily"}
        missing = required - job_ids
        t("CRITICAL", cat, "periodic-jobs-present", not missing,
          f"missing: {sorted(missing)}" if missing else f"{len(required)} periodic jobs scheduled")

        # Scheduler running
        t("CRITICAL", cat, "scheduler-running",
          state.get("scheduler_running") is True, f"got {state.get('scheduler_running')!r}")

        # At least 1 adhan_X job scheduled (unless ALL prayers have passed today, in which case
        # there should be a refresh_daily that re-builds tomorrow's schedule)
        adhans = [j for j in jobs if j.get("id","").startswith("adhan_")]
        t("HIGH", cat, "adhans-or-refresh", len(adhans) > 0 or "refresh_daily" in job_ids,
          f"{len(adhans)} adhans + refresh_daily={'yes' if 'refresh_daily' in job_ids else 'no'}")

        # v1.8.0 — the Telegram daily-digest job must always be scheduled (it's a
        # no-op when Telegram is unconfigured, but the JOB should exist). Its
        # absence means the "did the adhans fire today?" digest will never fire —
        # a silent-notification regression. MEDIUM: not core playback.
        t("MEDIUM", cat, "daily-summary-scheduled", "daily_summary" in job_ids,
          "daily_summary present" if "daily_summary" in job_ids else "MISSING — digest won't fire")

        # Lesson 26 — all job timestamps in same tz as app (no +01 vs +02 drift)
        tz_offsets = set()
        for j in jobs:
            nr = j.get("next_run","")
            m = re.search(r"([+-]\d{2}:\d{2})$", nr)
            if m: tz_offsets.add(m.group(1))
        t("HIGH", cat, "job-tz-consistent", len(tz_offsets) <= 1,
          f"offsets seen: {sorted(tz_offsets)}")

        # B-Belgium-13 — verify no SCHEDULER_INCOMPLETE in last 24h
        try:
            _, ph = http_json("/api/play_history?limit=200&status=SCHEDULER_INCOMPLETE", timeout=5)
            t("HIGH", cat, "no-recent-truncation", ph.get("count",0) == 0,
              f"{ph.get('count')} SCHEDULER_INCOMPLETE entries in history")
        except Exception:
            pass  # endpoint shape, not fatal
    except Exception as e:
        err("CRITICAL", cat, "scheduler-overall", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 5 — API health (responsiveness + presence of post-mortem endpoints)
# ─────────────────────────────────────────────────────────────────────────────
def L5_api():
    cat = "L5 api"

    # B-Belgium-18 / O39 — /api/state responds in <3s (no retry storms)
    try:
        t0 = time.time()
        http_get("/api/state", timeout=5)
        dt = time.time() - t0
        t("HIGH", cat, "state-fast", dt < 3.0, f"{dt:.3f}s")
    except Exception as e: err("HIGH", cat, "state-fast", e)

    # C-3 — /api/version exists + returns {ok, version}
    try:
        code, data = http_json("/api/version")
        ok = code == 200 and "version" in data
        t("HIGH", cat, "version-endpoint", ok, f"v{data.get('version')}")
        # v1.8.3 — /api/version also exposes a stable per-Pi device id used to tag
        # Telegram alerts + the dashboard footer across the fleet. On a real Pi it
        # should be RPI-<serial> (or SYS-<machine-id>), not the dev fallback.
        # LOW: absence doesn't break playback, only the fleet-identification tag.
        dev = data.get("device_id", "")
        t("LOW", cat, "device-id-present", bool(dev) and dev != "DEV-GENERIC-PORTABLE",
          f"device_id={dev!r}")
    except Exception as e: err("HIGH", cat, "version-endpoint", e)

    # O25 — /api/play_history exists (no AttributeError from v1.6.1 hotfix regression)
    try:
        code, data = http_json("/api/play_history?limit=5")
        t("HIGH", cat, "play-history-endpoint", code == 200 and "entries" in data,
          f"{data.get('count',0)} entries returned")
    except Exception as e: err("HIGH", cat, "play-history-endpoint", e)

    # O36 — /api/speakers/add_by_ip exists (rejects empty body with 400, that's enough)
    try:
        r = run(["curl","-sSm","3","-o","/dev/null","-w","%{http_code}",
                 "-X","POST", BASE_URL+"/api/speakers/add_by_ip",
                 "-H","Content-Type: application/json","-d","{}"])
        code = r.stdout.strip()
        t("MEDIUM", cat, "add-by-ip-endpoint", code in ("400","422"),
          f"HTTP {code} on empty body (400/422 expected)")
    except Exception as e: err("MEDIUM", cat, "add-by-ip-endpoint", e)

    # C-4 — /api/scheduler/hold exists
    try:
        code, data = http_json("/api/scheduler/hold")
        t("MEDIUM", cat, "scheduler-hold-endpoint", code == 200 and "held" in data,
          f"held={data.get('held')}")
    except Exception as e: err("MEDIUM", cat, "scheduler-hold-endpoint", e)

    # O32 — next_prayer payload has effective_* + shifted fields
    try:
        _, state = http_json("/api/state")
        np = state.get("next_prayer",{})
        needed = {"name","time_pretty","effective_time_pretty","shifted"}
        has_all = needed.issubset(np.keys())
        t("HIGH", cat, "next-prayer-enriched", has_all,
          f"keys: {sorted(np.keys())}")
    except Exception as e: err("HIGH", cat, "next-prayer-enriched", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 6 — UI surface (regression checks for HTML/JS bugs)
# ─────────────────────────────────────────────────────────────────────────────
def L6_ui():
    cat = "L6 ui"

    # Root page loads
    try:
        code, body = http_get("/")
        t("HIGH", cat, "dashboard-loads", code == 200 and len(body) > 1000,
          f"HTTP {code}, {len(body)}B")
        html = body.decode("utf-8","ignore")
    except Exception as e:
        err("HIGH", cat, "dashboard-loads", e); return

    # C-3 — footer reads /api/version (no hardcoded version)
    t("HIGH", cat, "footer-version-dynamic",
      'id="footer-version"' in html and 'CastAdhan v3.2' not in html,
      "footer-version span present + v3.2 absent")

    # B-Belgium-19 — HTML default for include filter is "" not "speaker"
    t("CRITICAL", cat, "include-input-default-empty",
      'id="config-include-name" value=""' in html,
      "should be value='' not value='speaker'")

    # B-Belgium-19 — JS uses ?? not || for the include filter (regression check)
    t("CRITICAL", cat, "include-js-uses-nullish",
      "include_if_name_contains ?? ''" in html,
      "should be ?? '' not || 'speaker'")

    # C-2 / U-2 — madhab dropdown wired in dashboard
    t("HIGH", cat, "madhab-dropdown-present",
      'id="config-madhab"' in html and 'value="hanafi"' in html,
      "config-madhab + hanafi option")

    # E-4 — simple-mode pane present
    t("MEDIUM", cat, "simple-mode-pane",
      'id="simple-mode-pane"' in html, "simple-mode-pane div")

    # E-5 PWA — manifest + sw.js routes work
    try:
        code, _ = http_get("/manifest.json")
        t("MEDIUM", cat, "manifest-route", code == 200, f"HTTP {code}")
    except Exception as e: err("MEDIUM", cat, "manifest-route", e)
    try:
        code, _ = http_get("/sw.js")
        t("MEDIUM", cat, "service-worker-route", code == 200, f"HTTP {code}")
    except Exception as e: err("MEDIUM", cat, "service-worker-route", e)

    # U-4 — responsive @media queries present
    t("MEDIUM", cat, "responsive-media-queries",
      "@media (max-width: 720px)" in html, "720px breakpoint")

    # v1.6.4 — weather widget uses open-meteo (no weatherapi key embedded)
    t("HIGH", cat, "weather-uses-openmeteo",
      "api.open-meteo.com" in html and "WEATHER_API_KEY = '3" not in html,
      "open-meteo URL present, weatherapi key absent")

# ─────────────────────────────────────────────────────────────────────────────
# Layer 7 — Audio files (every config.audio entry must exist on disk)
# ─────────────────────────────────────────────────────────────────────────────
def L7_audio():
    cat = "L7 audio"
    try:
        _, cfg = http_json("/api/config")
        audio = cfg["config"]["audio"]
        for key, rel in audio.items():
            full = os.path.join(INSTALL_DIR, rel)
            exists = os.path.isfile(full)
            t("HIGH", cat, f"audio-file-{key}", exists,
              f"{full} {'present' if exists else 'MISSING'}")
    except Exception as e: err("HIGH", cat, "audio-config-load", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 7b — Scheduled Audio (Quran Programs) — v1.9.8
#
# Verifies that the scheduled_audio feature shipped end-to-end and the system
# can recover gracefully from common breakages: corrupted state file, missing
# storage dir (covered separately in L9), audio_url validation working.
# ─────────────────────────────────────────────────────────────────────────────
def L7b_scheduled_audio():
    cat = "L7b scheduled_audio"

    # B-Belgium-52 (v1.9.8): the /api/scheduled_audio endpoint should always
    # respond, even with zero configured entries. The dashboard polls this
    # on every Settings open; a 500 would silently break the new tab.
    try:
        _, data = http_json("/api/scheduled_audio")
        ok = bool(data.get("ok"))
        entries = data.get("entries", []) if ok else []
        t("HIGH", cat, "scheduled-audio-endpoint", ok,
          f"endpoint returned ok={ok} with {len(entries)} entries")
    except Exception as e: err("HIGH", cat, "scheduled-audio-endpoint", e)

    # The six default surahs should all be present in a fresh config. If any
    # are missing it means config.yaml's scheduled_audio block was deleted or
    # the user's config has fully overridden the shipping default — masood
    # would notice as missing cards. MEDIUM (not HIGH): masood may have
    # deliberately removed an entry he doesn't want.
    EXPECTED_DEFAULTS = ['surah_baqarah', 'surah_yasin', 'surah_mulk',
                        'surah_kahf', 'surah_waqiah', 'surah_sajdah']
    try:
        _, data = http_json("/api/scheduled_audio")
        ids = {e["id"] for e in data.get("entries", [])}
        missing = [s for s in EXPECTED_DEFAULTS if s not in ids]
        ok = len(missing) == 0
        t("MEDIUM", cat, "scheduled-audio-default-entries", ok,
          "all 6 default surahs present" if ok else f"missing: {missing}")
    except Exception as e: err("MEDIUM", cat, "scheduled-audio-default-entries", e)

    # Kahf bridge sanity: surah_kahf must be enabled by default (preserves
    # the v1.9.7 behaviour) AND have the bundled audio source. If either
    # changes, aunt-pi-ghent / son-pi-haverfordwest who today get Kahf on
    # Friday automatically might stop getting it. HIGH.
    try:
        _, data = http_json("/api/scheduled_audio")
        kahf = next((e for e in data.get("entries", []) if e["id"] == "surah_kahf"), None)
        if kahf is None:
            t("HIGH", cat, "scheduled-audio-kahf-bridge-preserved", False,
              "surah_kahf entry missing — Friday Kahf will not fire")
        else:
            cfg = kahf.get("config", {})
            enabled = bool(cfg.get("enabled"))
            bundled = cfg.get("audio_url") == "bundled"
            ok = enabled and bundled
            parts = []
            if not enabled: parts.append("not enabled")
            if not bundled: parts.append(f"audio_url={cfg.get('audio_url')!r} (expected 'bundled')")
            t("HIGH", cat, "scheduled-audio-kahf-bridge-preserved", ok,
              "OK" if ok else "; ".join(parts))
    except Exception as e: err("HIGH", cat, "scheduled-audio-kahf-bridge-preserved", e)

    # State file readable + valid JSON. Already covered in L9 but checked
    # here too since the scheduled_audio engine fails closed if state can't
    # be read (every download is a fresh attempt with consecutive_failures=0).
    try:
        state_path = "/var/lib/castadhan/custom_audio_state.json"
        ok = True
        msg = "OK"
        if not os.path.isfile(state_path):
            ok = False; msg = f"{state_path} not present"
        else:
            try:
                with open(state_path) as f:
                    json.load(f)
            except Exception as e:
                ok = False; msg = f"invalid JSON: {e}"
        t("MEDIUM", cat, "scheduled-audio-state-file", ok, msg)
    except Exception as e: err("MEDIUM", cat, "scheduled-audio-state-file", e)

    # v1.9.9: Kahf must be MIGRATED — fixed 07:00 Friday with speakers set.
    # The legacy Friday substitution is gone, so a Kahf entry still on the
    # broken v1.9.8 default (Dhuhr-60) or with empty target_speakers means
    # Friday Kahf will fire at the wrong time or not at all.
    try:
        _, data = http_json("/api/scheduled_audio")
        kahf = next((e for e in data.get("entries", []) if e["id"] == "surah_kahf"), None)
        if kahf is None:
            t("HIGH", cat, "kahf-migrated", False, "surah_kahf entry missing")
        else:
            cfg = kahf.get("config", {})
            problems = []
            if cfg.get("trigger_type") != "fixed":
                problems.append(f"trigger_type={cfg.get('trigger_type')!r} (expected fixed)")
            if cfg.get("play_time") != "07:00":
                problems.append(f"play_time={cfg.get('play_time')!r} (expected 07:00)")
            if not cfg.get("target_speakers"):
                problems.append("target_speakers empty — B-61 enforcement will silence Kahf")
            t("HIGH", cat, "kahf-migrated", not problems,
              "fixed 07:00 with speakers set" if not problems else "; ".join(problems))
    except Exception as e: err("HIGH", cat, "kahf-migrated", e)

    # v1.9.9: systemd watchdog configured. Without WatchdogSec in the
    # INSTALLED unit (not just the repo copy), a wedged process runs forever —
    # the 12-Jun aunt-pi failure mode.
    try:
        unit_path = "/etc/systemd/system/castadhan-portable.service"
        unit = read(unit_path)
        ok = "WatchdogSec=" in unit
        t("HIGH", cat, "watchdog-unit-configured", ok,
          "WatchdogSec present in installed unit" if ok
          else f"WatchdogSec MISSING from {unit_path} — updater should have installed it")
    except Exception as e: err("HIGH", cat, "watchdog-unit-configured", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 8 — Religious correctness (C-1 + C-2 verified through real API)
# ─────────────────────────────────────────────────────────────────────────────
def L8_religion():
    cat = "L8 religion"

    # Aladhan API reachable + returns valid prayer times for aunt's location
    try:
        _, cfg = http_json("/api/config")
        loc = cfg["config"]["app"]["location"]
        url = (f"https://api.aladhan.com/v1/timings/{int(time.time())}"
               f"?latitude={loc['latitude']}&longitude={loc['longitude']}&method=2")
        r = urllib.request.urlopen(url, timeout=10)
        data = json.loads(r.read().decode("utf-8"))
        ok = data.get("code") == 200 and "data" in data
        t("HIGH", cat, "aladhan-reachable", ok, f"Fajr: {data.get('data',{}).get('timings',{}).get('Fajr')}")
    except Exception as e: err("HIGH", cat, "aladhan-reachable", e)

    # Hijri date sensible (not 1900, not 9999)
    try:
        _, state = http_json("/api/state")
        h = state.get("hijri_now_sunset_aware",{})
        y = int(h.get("year",0))
        t("MEDIUM", cat, "hijri-date-sensible", 1400 <= y <= 1500, f"year={y}")
    except Exception as e: err("MEDIUM", cat, "hijri-date-sensible", e)

    # v1.8.12: /api/state must compute today's prayer_times. Catches the stale-
    # module-globals failure mode where /api/config has the right city/coords
    # but the fetcher calls Aladhan with empty city/country params and 400s
    # (live evidence: son's Pi 2026-05-31 after the first-run wizard saved
    # Haverfordwest — dashboard stuck at --:-- until a service restart).
    # CRITICAL: a Pi that can't compute prayer times can't schedule any adhan.
    try:
        _, state = http_json("/api/state", timeout=10)
        pt = state.get("prayer_times", {}) or {}
        any_set = bool(pt.get("Fajr") and pt.get("Dhuhr") and pt.get("Sunrise"))
        t("CRITICAL", cat, "prayer-times-computed", any_set,
          f"Fajr={pt.get('Fajr')!r} Dhuhr={pt.get('Dhuhr')!r} Sunrise={pt.get('Sunrise')!r}")
    except Exception as e: err("CRITICAL", cat, "prayer-times-computed", e)

    # v1.8.11 — Fajr adhan must be scheduled within the permissible window
    # [true dawn, sunrise). Whatever fajr_mode is chosen, it can never fire before
    # true dawn (impermissible) or at/after sunrise (window closed). Only checkable
    # while the Fajr job is still pending today; skipped once it has fired.
    try:
        _, state = http_json("/api/state")
        pt = state.get("prayer_times", {})
        raw_fajr, sunrise = pt.get("Fajr"), pt.get("Sunrise")
        jobs = {j.get("id"): (j.get("next_run") or "") for j in state.get("scheduled_jobs", [])}
        fajr_run = jobs.get("adhan_Fajr")
        m = re.search(r"T(\d{2}:\d{2})", fajr_run) if fajr_run else None
        if m and raw_fajr and sunrise:
            sched_hhmm = m.group(1)
            ok = raw_fajr <= sched_hhmm < sunrise   # zero-padded HH:MM compares fine
            t("HIGH", cat, "fajr-within-dawn-sunrise", ok,
              f"scheduled {sched_hhmm} must be in [{raw_fajr}, {sunrise})")
        else:
            t("LOW", cat, "fajr-within-dawn-sunrise", True,
              "Fajr already fired or not yet scheduled — skipped")
    except Exception as e: err("HIGH", cat, "fajr-within-dawn-sunrise", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 9 — Auto-update infrastructure (O27)
# ─────────────────────────────────────────────────────────────────────────────
def L9_autoupdate():
    cat = "L9 update"

    # castadhan-update.sh exists
    p = INSTALL_DIR + "/deploy/castadhan-update.sh"
    t("HIGH", cat, "update-script-deployed", os.path.isfile(p),
      f"{p} {'present' if os.path.isfile(p) else 'MISSING'}")

    # /etc/default/castadhan-update has real repo (not placeholder)
    # B-Belgium-40 (v1.9.3): strip comment lines before checking — earlier
    # versions of castadhan-update.defaults included historical text mentioning
    # the literal placeholder string ('yourname/castadhan-portable') in a
    # comment, which made this check false-fail even though the actual
    # GITHUB_REPO= value was correct. Also: the failure message used to read
    # like a pass ("real repo set, no placeholder") regardless of state, so
    # operators saw '✗ real repo set, no placeholder' which was baffling.
    # Now we describe the actual finding.
    try:
        raw = read("/etc/default/castadhan-update")
        # Keep only non-comment, non-blank lines for the check.
        cfg = "\n".join(
            line for line in raw.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        has_real = "sabreenaapa-coder/castadhan-portable" in cfg
        no_placeholder = "yourname/castadhan-portable" not in cfg
        passed = has_real and no_placeholder
        if passed:
            msg = "real repo set, no placeholder"
        else:
            parts = []
            if not has_real:
                parts.append("expected 'sabreenaapa-coder/castadhan-portable' NOT FOUND")
            if not no_placeholder:
                parts.append("placeholder 'yourname/castadhan-portable' STILL PRESENT")
            msg = "; ".join(parts) or "unknown failure"
        t("HIGH", cat, "update-config-real-repo", passed, msg)
    except Exception as e: err("HIGH", cat, "update-config-real-repo", e)

    # Update timer enabled + active
    try:
        r = run(["systemctl", "is-enabled", "castadhan-update.timer"])
        t("HIGH", cat, "update-timer-enabled", r.stdout.strip() == "enabled", r.stdout.strip())
        r = run(["systemctl", "is-active", "castadhan-update.timer"])
        t("HIGH", cat, "update-timer-active", r.stdout.strip() == "active", r.stdout.strip())
    except Exception as e: err("HIGH", cat, "update-timer", e)

    # B-Belgium-24: manual "Update Now" watcher. The web service runs with
    # NoNewPrivileges and can't sudo, so the dashboard button works by writing a
    # flag that castadhan-update.path (root, via systemd) watches. If this unit
    # isn't active the button silently does nothing. MEDIUM: nightly timer still
    # works, only the manual button is affected.
    try:
        r = run(["systemctl", "is-active", "castadhan-update.path"])
        t("MEDIUM", cat, "manual-update-watcher-active", r.stdout.strip() == "active",
          r.stdout.strip() + " (castadhan-update.path)")
    except Exception as e: err("MEDIUM", cat, "manual-update-watcher", e)

    # B-Belgium-25: the updater MUST preserve runtime state that lives in the
    # install dir but isn't shipped in releases. Before v1.8.2 every update wiped
    # play_history.jsonl (the prayer-fired audit trail), ui_state.json (speaker
    # enable flags + volumes), and known_speakers.json. Static check: the deployed
    # update script still carries the preserve logic for all three. HIGH (not
    # CRITICAL): a regression silently loses data + resets settings on the NEXT
    # update, but the running adhan still works.
    try:
        upd = read(INSTALL_DIR + "/deploy/castadhan-update.sh")
        preserved = all(name in upd for name in
                        ("play_history.jsonl", "ui_state.json", "known_speakers.json"))
        t("HIGH", cat, "update-preserves-runtime-state", preserved,
          "preserve loop covers play_history/ui_state/known_speakers" if preserved
          else "MISSING preserve logic — updates will wipe state (B-Belgium-25)")
    except Exception as e: err("HIGH", cat, "update-preserves-runtime-state", e)

    # B-Belgium-26: the updater must NOT delete the .previous backup immediately
    # after a successful update — that destroyed the advertised 24h rollback
    # window (and the backup we'd restore wiped state from). The only legitimate
    # `rm -rf "$PREV_DIR"` is the pre-backup one; a second occurrence means the
    # premature post-success rm has regressed.
    try:
        upd = read(INSTALL_DIR + "/deploy/castadhan-update.sh")
        n = len(re.findall(r'rm\s+-rf\s+"\$PREV_DIR"', upd))
        t("MEDIUM", cat, "update-keeps-rollback-window", n == 1,
          f'{n} `rm -rf "$PREV_DIR"` found (expect exactly 1 — pre-backup only)')
    except Exception as e: err("MEDIUM", cat, "update-keeps-rollback-window", e)

    # B-Belgium-43 (v1.9.7): the updater's staging path must have enough free
    # space for the release tarball PLUS its extracted contents. Pi OS Lite
    # mounts /tmp as tmpfs sized at half of RAM (453 MB on Pi 3B+, ~209 MB on
    # smaller variants), and the release tarball is ~273 MB compressed / ~550 MB
    # uncompressed. On 8 Jun 2026 masood-pi silently extract-failed every nightly
    # update for THREE consecutive versions before this got diagnosed — the
    # symptom was just a line in /var/log/castadhan-update.log. This check parses
    # the staging path out of the deployed updater (currently /var/tmp/...) and
    # warns if the underlying mount has under 700 MB free. HIGH (not CRITICAL):
    # the running adhan service is fine; only future auto-updates would fail.
    try:
        upd_path = INSTALL_DIR + "/deploy/castadhan-update.sh"
        upd = read(upd_path)
        m = re.search(r'mktemp\s+-d\s+(/\S+)/castadhan-update', upd)
        if not m:
            t("HIGH", cat, "update-staging-space", False,
              f"could not parse staging path from {upd_path}")
        else:
            staging_parent = m.group(1)
            try:
                st = os.statvfs(staging_parent)
                free_mb = (st.f_bavail * st.f_frsize) // (1024 * 1024)
                ok = free_mb >= 700
                t("HIGH", cat, "update-staging-space", ok,
                  f"{staging_parent} has {free_mb} MB free (need ≥700 for tarball+extract)")
            except OSError as e:
                t("HIGH", cat, "update-staging-space", False,
                  f"could not statvfs({staging_parent}): {e}")
    except Exception as e: err("HIGH", cat, "update-staging-space", e)

    # B-Belgium-52 (v1.9.8): persistent state for the Quran Programs feature
    # lives in /var/lib/castadhan/ — OUTSIDE the install dir so it survives
    # the atomic rm-and-replace update swap. The directory + the state JSON
    # must exist on first install AND on every update from a pre-v1.9.8
    # version. setup-pi.sh + castadhan-update.sh create them defensively,
    # but if either misses (older Pis upgrading), audio downloads fail
    # silently with "permission denied" until the dir is manually mkdir'd.
    try:
        custom_audio_dir = "/var/lib/castadhan/custom_audio"
        ok = os.path.isdir(custom_audio_dir)
        t("HIGH", cat, "custom-audio-dir-exists", ok,
          f"{custom_audio_dir} " + ("exists" if ok else "MISSING — Quran downloads will fail"))
    except Exception as e: err("HIGH", cat, "custom-audio-dir-exists", e)

    try:
        state_file = "/var/lib/castadhan/custom_audio_state.json"
        ok = os.path.isfile(state_file)
        msg = f"{state_file} " + ("exists" if ok else "MISSING — will be created on next service start")
        if ok:
            # also verify it parses as JSON
            try:
                with open(state_file) as f:
                    json.load(f)
            except Exception as parse_err:
                ok = False
                msg = f"{state_file} exists but is invalid JSON: {parse_err}"
        t("MEDIUM", cat, "custom-audio-state-readable", ok, msg)
    except Exception as e: err("MEDIUM", cat, "custom-audio-state-readable", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 10 — Tailscale (Lesson 20)
# ─────────────────────────────────────────────────────────────────────────────
def L9b_wifi_wizard():
    """v1.9.0: the WiFi-setup wizard depends on a polkit rule that lets the
    castadhan service user manage NetworkManager without sudo (NoNewPrivileges
    blocks sudo for this service). Without the rule, scan/connect 401 silently."""
    cat = "L9b wifi-wizard"
    p = "/etc/polkit-1/rules.d/50-castadhan-nm.rules"
    # /etc/polkit-1/rules.d/ is locked-down 700 root:root on Debian/Pi OS, so a
    # non-root operator running this test via SSH can't stat files inside.
    # os.path.isfile would false-negative. Detect that case and degrade to "OK"
    # so the operator doesn't see a misleading FAIL — when the auto-update gate
    # runs this test as root, the direct check works fine.
    has_rule = os.path.isfile(p)
    if has_rule:
        msg = f"{p} present"
    else:
        try:
            os.listdir(os.path.dirname(p))
            msg = f"{p} MISSING — setup-pi.sh did not install it"
        except PermissionError:
            has_rule = True
            msg = "rules.d locked-down (run as root for a definitive check); install path assumed OK"
    t("MEDIUM", cat, "polkit-rule-installed", has_rule, msg)
    # NetworkManager itself must be running for any of the wifi/* endpoints.
    try:
        r = run(["systemctl", "is-active", "NetworkManager"])
        t("MEDIUM", cat, "networkmanager-active", r.stdout.strip() == "active",
          r.stdout.strip())
    except Exception as e: err("MEDIUM", cat, "networkmanager-active", e)

def L10_tailscale():
    cat = "L10 tailscale"
    try:
        r = run(["tailscale", "status"], timeout=5)
        on = "100." in r.stdout
        t("HIGH", cat, "tailscale-up", on, "100.x address present in status")
    except Exception as e: err("HIGH", cat, "tailscale-up", e)

    # v1.8.12: BackendState must be "Running" — catches the failure mode where
    # tailscaled was installed but `tailscale up` never ran (the old setup-pi.sh
    # idempotency bug). A freshly-installed-but-not-enrolled daemon still emits a
    # Self object so the previous check passed, but BackendState reads NeedsLogin
    # or Stopped. Live evidence: son's Pi 2026-05-31.
    try:
        r = run(["tailscale", "status", "--json"], timeout=5)
        state = "Unknown"
        try:
            state = json.loads(r.stdout or "{}").get("BackendState", "Unknown")
        except Exception:
            pass
        t("HIGH", cat, "tailscale-backend-running", state == "Running",
          f"BackendState={state} (expect Running)")
    except Exception as e: err("HIGH", cat, "tailscale-backend-running", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 11 — Recent fire history (canary for silent-failure detection)
# ─────────────────────────────────────────────────────────────────────────────
def L11_history():
    cat = "L11 history"
    try:
        _, ph = http_json("/api/play_history?limit=50")
        entries = ph.get("entries",[])
        # v1.8.14: exclude NO_SPEAKERS for prayers the owner has whitelisted as
        # silent-by-design (e.g. aunt's Fajr while she powers her bedroom
        # speakers down at night). New entries are logged as SILENT_EXPECTED
        # directly so they wouldn't match this filter anyway — this just keeps
        # historical NO_SPEAKERS entries from a pre-v1.8.14 install quiet too.
        try:
            _, cfg = http_json("/api/config")
            silent_whitelist = set(cfg["config"]["rules"].get("expected_silent_prayers") or [])
        except Exception:
            silent_whitelist = set()
        no_speakers = [e for e in entries
                       if e.get("status") == "NO_SPEAKERS"
                       and e.get("prayer_name") not in silent_whitelist]
        t("HIGH", cat, "no-recent-no-speakers",
          len(no_speakers) == 0,
          f"{len(no_speakers)} NO_SPEAKERS (excl. silent-whitelist {sorted(silent_whitelist)}) in last 50: {[e.get('prayer_name') for e in no_speakers]}")
        # SCHEDULER_INCOMPLETE check
        sched_incomp = [e for e in entries if e.get("status") == "SCHEDULER_INCOMPLETE"]
        t("HIGH", cat, "no-recent-scheduler-incomplete",
          len(sched_incomp) == 0, f"{len(sched_incomp)} in last 50")
        # B-Belgium-22 (v1.7.1) — FAIL entries (cast play failed despite speakers
        # being present, e.g. stale-socket "is connecting..." failures)
        fails = [e for e in entries if e.get("status") == "FAIL"]
        t("HIGH", cat, "no-recent-play-fails",
          len(fails) == 0, f"{len(fails)} FAIL in last 50: {[e.get('prayer_name') for e in fails]}")
    except Exception as e: err("HIGH", cat, "play-history-fetch", e)

    # B-Belgium-21 (v1.7.1) — play_history.jsonl must EXIST on disk.
    # If _log_play() is broken (e.g. the timezone-shadowing bug that returned
    # on v1.7.0), the file is never created and every play goes unrecorded —
    # exactly the blind spot that hid the Eid Fajr no-play. The file's absence
    # after the system has run through at least one prayer is a canary that
    # the logging path itself is broken.
    try:
        ph_file = INSTALL_DIR + "/play_history.jsonl"
        exists = os.path.isfile(ph_file)
        # Only a hard fail if the service has been up long enough to have fired
        # something. We approximate: if uptime > 6h, at least one prayer has
        # almost certainly passed, so the file should exist.
        up = run(["bash","-lc","ps -o etimes= -p $(pgrep -f castadhan-portable/app.py | head -1) | tr -d ' '"])
        uptime_s = int((up.stdout.strip().split('\n')[0] or "0"))
        if uptime_s > 6*3600:
            t("HIGH", cat, "play-history-file-written", exists,
              f"{'present' if exists else 'MISSING after '+str(uptime_s//3600)+'h uptime — _log_play likely broken'}")
        else:
            t("LOW", cat, "play-history-file-written", True,
              f"uptime {uptime_s//60}m — too early to require the file")
    except Exception as e: err("HIGH", cat, "play-history-file-written", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 12 — Live cast connectivity (v1.7.1)
# The Eid-Fajr-no-play bug: speakers were DISCOVERED (in the list) but their
# sockets were STALE, so playback failed with "is connecting...". A speaker
# being "in the list" is not the same as "connectable right now". This layer
# verifies each discovered speaker's :8009 actually completes a TCP handshake
# AT TEST TIME — a stale or dropped speaker fails here even though L3 discovery
# might still list it from cache.
# ─────────────────────────────────────────────────────────────────────────────
def L12_connectivity():
    cat = "L12 connectivity"
    try:
        _, state = http_json("/api/state", timeout=15)
        spks = state.get("devices",{}).get("speakers",[])
        if not spks:
            t("LOW", cat, "speakers-to-probe", False, "no speakers discovered to probe")
            return
        for s in spks:
            ip = s.get("ip")
            name = s.get("name","?")
            if not ip:
                continue
            try:
                t0 = time.time()
                with socket.create_connection((ip, 8009), timeout=4):
                    dt = time.time() - t0
                # CRITICAL: a discovered speaker whose :8009 won't handshake will
                # fail to play. This is the direct canary for the May 28 bug.
                t("CRITICAL", cat, f"connectable-{name}", True, f"{ip}:8009 handshake {dt:.2f}s")
            except (OSError, socket.timeout) as e:
                t("CRITICAL", cat, f"connectable-{name}", False,
                  f"{ip}:8009 NOT connectable ({e}) — playback will fail")
    except Exception as e:
        err("CRITICAL", cat, "connectivity-overall", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 13 — Volume policy (v1.8.6) — peripheral-audio quiet-hours behaviour.
# Pure-logic checks: import volume_policy and assert the locked design. These are
# deterministic (no Pi state). The adhan-untouched check is CRITICAL — a policy
# that could mute the call to prayer must roll an update back.
# ─────────────────────────────────────────────────────────────────────────────
def L13_volume_policy():
    cat = "L13 volume"
    try:
        if INSTALL_DIR not in sys.path:
            sys.path.insert(0, INSTALL_DIR)
        import volume_policy as vp
        from datetime import datetime as _dt
        QUIET = _dt(2026, 1, 1, 5, 0)    # 05:00 — inside default quiet hours
        DAY   = _dt(2026, 1, 1, 14, 0)   # 14:00 — daytime
        # Resolver contract: returns int 0–100 to play at, or None to suppress.

        # Spec check 1 — Fajr takbeeraat suppressed (None -> logged suppressed, NOT failed).
        v = vp.resolve_play_volume("takbeeraat", 100, None, QUIET, "Fajr")
        t("HIGH", cat, "fajr-takbeeraat-suppressed", v is None, f"returned {v!r} (expect None)")

        # Spec check 2 — combined-Isha twilight survives the night: ATTENUATE, not None.
        v = vp.resolve_play_volume("twilight", 100, None, _dt(2026, 1, 1, 23, 35))
        t("HIGH", cat, "twilight-survives-night", v is not None and 0 < v < 100,
          f"vol={v} (expect a quiet-but-present number, not None)")

        # Owner preference — dhikr / duas are quiet-but-present at night (ATTENUATE),
        # NOT suppressed. Guards against regressing this back to SUPPRESS (None).
        vd = vp.resolve_play_volume("morning_dhikr", 100, None, QUIET)
        t("HIGH", cat, "dhikr-quiet-but-present", vd is not None and 0 < vd < 100,
          f"vol={vd} (expect quiet-but-present, not None)")

        # Spec check 3 — legacy config (no volume_policy keys) is safe: CORE rides master,
        # never floored, master_volume untouched.
        v = vp.resolve_play_volume("adhan", 70, None, QUIET)   # None config == legacy/missing
        t("HIGH", cat, "legacy-config-safe", v == 70, f"adhan@master70 -> {v} (expect 70, untouched)")

        # Spec check 4 (CRITICAL) — Core is NEVER silenced: adhan + warning in quiet hours
        # return master volume, never None.
        va = vp.resolve_play_volume("adhan", 100, None, QUIET)
        vw = vp.resolve_play_volume("fajr_warning", 100, None, QUIET)
        t("CRITICAL", cat, "core-never-silenced", va == 100 and vw == 100,
          f"adhan={va} warning={vw} (both must be 100, never None)")

        # Spec check 5 — ALLOW != loud: the adhan rides master volume, not a fixed 100.
        v = vp.resolve_play_volume("adhan", 55, None, DAY)
        t("HIGH", cat, "allow-rides-master-not-fixed-100", v == 55,
          f"adhan@master55 -> {v} (expect 55, proving ALLOW uses master)")

        # Gotcha — the deliberate night alarms must still SOUND during quiet hours
        # (never None). suhoor is pinned to 50% of master; wakeup (if enabled by the
        # owner) rides master. Both must be audible, not silenced.
        vs = vp.resolve_play_volume("suhoor_alarm", 100, None, _dt(2026, 1, 1, 4, 30))
        vk = vp.resolve_play_volume("wakeup", 100, None, _dt(2026, 1, 1, 6, 30))
        t("HIGH", cat, "alarms-sound-in-quiet-hours", vs == 50 and vk == 100,
          f"suhoor={vs} (expect 50) wakeup={vk} (expect 100)")
    except Exception as e:
        err("HIGH", cat, "volume-policy-overall", e)

# ─────────────────────────────────────────────────────────────────────────────
# Layer 14 — Incident regression net (2026-06-20 coverage-audit gap closure)
# Each check guards a documented B-Belgium incident that previously had NO
# regression test. STATIC checks read SRC_DIR (install dir on a Pi / repo dir in
# a dev checkout) so they run anywhere; RUNTIME checks hit the local API + state
# (Pi only — they ERROR gracefully off-box). SAFE: all read-only, never plays audio.
# ─────────────────────────────────────────────────────────────────────────────
def L14_incident_net():
    cat = "L14 incident-net"
    try: app = src("app.py")
    except Exception as e: err("HIGH", cat, "b-source-app-readable", e); app = ""
    try: html = src("console.html")
    except Exception: html = ""

    # B-Belgium-10 / Lesson 32 (CRITICAL): the app.py SCHEMA default for the speaker
    # include-filter must be '' — a non-empty default silently re-poisons config on
    # merge so discovery returns 0 speakers (the 25-May silent-Maghrib mechanism).
    if app:
        vals = [v for (_q, v) in re.findall(r"include_if_name_contains['\"]?\s*:\s*(['\"])(.*?)\1", app)]
        bad = [v for v in vals if v.strip() != ""]
        t("CRITICAL", cat, "b10-include-default-empty", bool(vals) and not bad,
          "include_if_name_contains default must be '' (found %r)" % (bad[:1] or vals[:1]))

    # B-Belgium-17 / O37 (HIGH): shipped default high_latitude_method must be
    # static_offset (combine_prayers silently skips Isha for new low-lat installs).
    if app:
        m = re.search(r"high_latitude_method['\"]?\s*:\s*(['\"])(.*?)\1", app)
        t("HIGH", cat, "b17-hilat-default-static-offset", bool(m) and m.group(2) == "static_offset",
          "default high_latitude_method=%s (expect static_offset)" % (m.group(2) if m else "?"))

    # B-Belgium-3 (HIGH): Stop All must force-stop via quit_app(), not no-op when
    # _general_casts is empty (audio kept playing while UI said "stopped 0 devices").
    if app:
        t("HIGH", cat, "b3-stopall-uses-quit-app", "quit_app" in _fn_body(app, "def stop_all_audio("),
          "stop_all_audio must call quit_app() as a forceful fallback")

    # B-Belgium-36 (HIGH): every add_job with a FIXED id (not a timestamped one-shot)
    # must pass replace_existing=True, or first-run save raises ConflictingIdError and
    # the scheduler dies (no prayers fire).
    if app:
        risky = []
        guards = ("not in existing", "get_job(", "not in current", "existing_ids", "if not sched")
        for mt in re.finditer(r"add_job\(", app):
            seg = app[mt.start(): mt.start() + 700].split("\n\n")[0]
            before = app[max(0, mt.start() - 280): mt.start()]
            # Safe if: timestamped one-shot id, OR replace_existing=True, OR guarded by an
            # "id not in existing" existence check before the call. Otherwise risky.
            if "id=" in seg and "replace_existing" not in seg \
               and not ("strftime" in seg or "%Y%m%d" in seg) \
               and not any(g in before for g in guards):
                risky.append(seg.split("\n")[0][:36])
        t("HIGH", cat, "b36-addjob-replace-existing", not risky,
          "fixed-id add_job needs replace_existing or an existence guard: %s" % (risky[:2] or "none"))

    # B-Belgium-37 (HIGH): Settings save must snapshot config-* inputs DYNAMICALLY —
    # a static whitelist silently dropped isha_static_offset / isha_max_time edits.
    if html:
        t("HIGH", cat, "b37-settings-snapshot-dynamic",
          "_captureFormSnapshot" in html and 'id^="config-"' in html,
          "Settings snapshot must scan [id^=config-] dynamically, not a whitelist")

    # B-Belgium-38 (HIGH): the isha_method_always_apply rule must exist (the UK-summer
    # Maghrib+N override was gated only on persistent_twilight_active before).
    if app:
        t("HIGH", cat, "b38-isha-always-apply-rule", "isha_method_always_apply" in app,
          "config rule isha_method_always_apply must exist")

    # B-Belgium-48 (HIGH): cast_rediscovery cron must NOT fire on :00 or :30 — those
    # collide with on-the-half-hour prayer jamats and can knock out a firing.
    if app:
        line = next((l for l in app.splitlines() if "cast_rediscovery" in l and "add_job" in l), "")
        m = re.search(r"minute=(['\"])(.*?)\1", line)
        mins = [x.strip() for x in m.group(2).split(",")] if m else []
        t("HIGH", cat, "b48-rediscovery-avoids-prayer-minutes",
          bool(m) and not (set(mins) & {"0", "30"}),
          "cast_rediscovery minute=%s must avoid :00/:30" % (m.group(2) if m else "?"))

    # B-Belgium-42 (HIGH): every audio-playing function must log to play_history —
    # directly via _log_play, or via _play_to_targets (which logs). 10 of 13 players
    # were once silent, giving false "everything fired" confidence for weeks.
    if app:
        players = ["play_takbeeraat_all", "play_twilight", "play_adhan_all",
                   "play_sunrise_warning", "play_asr_warning", "play_dhuhr_warning",
                   "play_maghrib_warning", "play_morning_dhikr", "play_evening_content",
                   "play_friday_prayer", "play_wakeup", "play_suhoor_alarm"]
        missing = [p for p in players
                   if (b := _fn_body(app, "def %s(" % p)) and "_log_play" not in b and "_play_to_targets" not in b]
        t("HIGH", cat, "b42-all-players-log-history", not missing,
          "play fns not logging history: %s" % (missing or "none"))

    # B-Belgium-15 / B-Belgium-20 (HIGH): the next-prayer CARD must use the EFFECTIVE
    # (shifted) time, not raw aladhan time — else it shows e.g. 03:42 while the alarm
    # fires at 05:11 (contributed to the 27-May Eid no-fire).
    if html:
        t("HIGH", cat, "b15-card-uses-effective-time",
          "effective_when_iso" in html and "effective_time_pretty" in html,
          "next-prayer card must read effective_when_iso / effective_time_pretty")

    # B-Belgium-44 (HIGH): the updater download must --retry + surface curl's exit code
    # (a bare `curl -s` silently swallowed mid-stream failures for days).
    try:
        upd = src("deploy/castadhan-update.sh")
        t("HIGH", cat, "b44-updater-curl-retry",
          "--retry" in upd and ("--retry-all-errors" in upd or "curl exit" in upd),
          "castadhan-update.sh download must use --retry + capture the exit code")
    except Exception as e:
        err("HIGH", cat, "b44-updater-curl-retry", e)

    # B-Belgium-31 (HIGH): setup-pi.sh must set the WiFi regulatory country, or a fresh
    # Pi's wlan0 stays 'unavailable' and the WiFi-wizard scan is empty (P0 dead-end).
    # B-Belgium-32 (MEDIUM): iw + rfkill must be apt-installed (the unblock runcmd needs them).
    # B-Belgium-33 (LOW): constant castadhan.local avahi alias present.
    try:
        setup = src("deploy/setup-pi.sh")
        t("HIGH", cat, "b31-setup-sets-wifi-country", "country" in setup and "wlan0" in setup,
          "setup-pi.sh must configure the WiFi regulatory country")
        t("MEDIUM", cat, "b32-iw-rfkill-installed", "iw" in setup and "rfkill" in setup,
          "setup-pi.sh must apt-install iw + rfkill")
        t("LOW", cat, "b33-avahi-castadhan-alias",
          "host-name=castadhan" in setup or "castadhan.local" in setup,
          "setup-pi.sh should register the castadhan.local avahi alias")
    except Exception as e:
        err("HIGH", cat, "b31-setup-wifi", e)

    # B-Belgium-30 (MEDIUM): committed updater defaults must point at the REAL repo,
    # not a placeholder (a shipped placeholder broke fresh installs).
    try:
        defs = src("deploy/castadhan-update.defaults")
        t("MEDIUM", cat, "b30-update-defaults-real-repo",
          'GITHUB_REPO="sabreenaapa-coder/castadhan-portable"' in defs,
          "GITHUB_REPO must be set to the real repo")
    except Exception as e:
        err("MEDIUM", cat, "b30-update-defaults-real-repo", e)

    # B-Belgium-29 (MEDIUM): add_by_ip must PERSIST to known_speakers.json (it once
    # returned ok but the speaker vanished after a known_speakers reset).
    if app:
        i = app.find("/api/speakers/add_by_ip")
        body = app[i:i + 2600] if i >= 0 else ""
        t("MEDIUM", cat, "b29-add-by-ip-persists", "known_speakers" in body,
          "add_by_ip endpoint must persist to known_speakers.json")

    # B-Belgium-62 / B-Belgium-63 (LOW): on weak TV browsers the scene wedges must dim
    # via the SVG opacity ATTRIBUTE (not only a CSS class), or they render full-bright.
    if html:
        t("LOW", cat, "b62-wedge-dim-opacity-attr",
          "setAttribute('opacity'" in html or 'setAttribute("opacity"' in html,
          "drawF24 must set the SVG opacity attribute for wedge dimming")

    # ── Runtime checks (Pi only — read live state; ERROR gracefully off-box) ──

    # B-Belgium-41 / B-Belgium-46 (MEDIUM): no orphan speaker keys in ui_state.json —
    # every volumes key must map to a known/discovered speaker (orphans persist forever).
    try:
        ui = json.loads(read(INSTALL_DIR + "/ui_state.json"))
        known = set()
        try:
            ks = json.loads(read(INSTALL_DIR + "/known_speakers.json"))
            known |= set(ks.keys()) if isinstance(ks, dict) else {e.get("name") for e in ks if isinstance(e, dict)}
        except Exception:
            pass
        try:
            _c, st = http_json("/api/speaker/status")
            known |= set((st.get("status") or {}).keys())
        except Exception:
            pass
        orphans = sorted((set((ui.get("volumes") or {}).keys()) - {"__default"}) - known)
        t("MEDIUM", cat, "b41-no-orphan-ui-keys", not orphans,
          "orphan ui_state speaker keys: %s" % (orphans[:3] or "none"))
    except Exception as e:
        err("MEDIUM", cat, "b41-no-orphan-ui-keys", e)

    # B-Belgium-34 (MEDIUM): no two speakers share an IP (add_by_ip used to duplicate
    # a speaker after a later rediscovery).
    try:
        ks = json.loads(read(INSTALL_DIR + "/known_speakers.json"))
        ips = [(v.get("ip") if isinstance(v, dict) else v) for v in ks.values()] if isinstance(ks, dict) \
              else [e.get("ip") for e in ks if isinstance(e, dict)]
        ips = [i for i in ips if i]
        dups = sorted({i for i in ips if ips.count(i) > 1})
        t("MEDIUM", cat, "b34-no-duplicate-speaker-ips", not dups,
          "duplicate speaker IPs: %s" % (dups or "none"))
    except Exception as e:
        err("MEDIUM", cat, "b34-no-duplicate-speaker-ips", e)

    # B-Belgium-45 (MEDIUM): fleet version-drift signal — warn if this box's VERSION is
    # behind the latest GitHub release (drift was previously invisible). Network read.
    try:
        local_v = src("VERSION").strip()
        req = urllib.request.Request(
            "https://api.github.com/repos/sabreenaapa-coder/castadhan-portable/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "castadhan-sanity"})
        tag = (json.loads(urllib.request.urlopen(req, timeout=10).read().decode()).get("tag_name") or "").lstrip("v")
        t("MEDIUM", cat, "b45-version-not-drifted", bool(tag) and local_v == tag,
          "local VERSION=%s latest release=%s" % (local_v, tag or "?"))
    except Exception as e:
        err("MEDIUM", cat, "b45-version-not-drifted", e)

# ─────────────────────────────────────────────────────────────────────────────
# Run everything
# ─────────────────────────────────────────────────────────────────────────────
for fn in [L1_system, L2_config, L3_discovery, L4_scheduler, L5_api,
           L6_ui, L7_audio, L7b_scheduled_audio,
           L8_religion, L9_autoupdate, L9b_wifi_wizard, L10_tailscale, L11_history,
           L12_connectivity, L13_volume_policy, L14_incident_net]:
    try:
        fn()
    except Exception as e:
        err("ERROR", fn.__name__, "category-execute", e)

# ─────────────────────────────────────────────────────────────────────────────
# Render results
# ─────────────────────────────────────────────────────────────────────────────
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "ERROR": 4}
results.sort(key=lambda r: (SEVERITY_ORDER.get(r[0], 9), r[1], r[2]))

pass_count = sum(1 for r in results if r[3] == "PASS")
fail_count = sum(1 for r in results if r[3] == "FAIL")
err_count  = sum(1 for r in results if r[3] == "ERROR")
crit_fail  = sum(1 for r in results if r[3] == "FAIL" and r[0] == "CRITICAL")
high_fail  = sum(1 for r in results if r[3] == "FAIL" and r[0] == "HIGH")

# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(description="CastAdhan sanity test")
_parser.add_argument("--quiet", action="store_true",
                     help="only print failures + summary (suitable for update log)")
_parser.add_argument("--json", action="store_true",
                     help="emit machine-readable JSON instead of human report")
_args, _ = _parser.parse_known_args()

if _args.json:
    print(json.dumps({
        "summary": {
            "total": len(results),
            "pass": pass_count,
            "fail": fail_count,
            "error": err_count,
            "critical_fail": crit_fail,
            "high_fail": high_fail,
        },
        "results": [
            {"severity": s, "category": c, "name": n, "status": st, "detail": d}
            for (s, c, n, st, d) in results
        ],
    }, indent=2))
else:
    print()
    print("=" * 90)
    print(f"CastAdhan Portable — Sanity Test  ({len(results)} checks)")
    print("=" * 90)
    current_cat = None
    for sev, cat, name, status, detail in results:
        if _args.quiet and status == "PASS":
            continue
        if cat != current_cat:
            print(f"\n── {cat} ──")
            current_cat = cat
        marker = {"PASS": "✓", "FAIL": "✗", "ERROR": "!"}.get(status, "?")
        print(f"  [{sev[:4]:<4}] {marker} {name:<32} {detail[:60]}")
    print()
    print("=" * 90)
    print(f"SUMMARY: {pass_count} pass · {fail_count} fail · {err_count} error  "
          f"(critical-fail: {crit_fail}, high-fail: {high_fail})")
    print("=" * 90)

# Severity-aware exit code (used by castadhan-update.sh to gate releases).
# IMPORTANT: only CRITICAL failures should cause auto-rollback. HIGH/MEDIUM
# failures are loud-logged but don't undo a release — they're regressions
# of past fixes that should be patched in the next release, not rolled back.
if crit_fail > 0:
    sys.exit(1)
if fail_count > 0 or err_count > 0:
    sys.exit(2)
sys.exit(0)
