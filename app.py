"""
CastAdhan - Prayer Time Script with Google Cast Integration
Master Portable Edition v3.1 - Complete Speaker Management & Configuration Console
Added: Speaker management UI, audio routing matrix, emergency stop, stale lock cleanup, memory optimization
"""

import os
import sys
import json
import time
import logging
import logging.handlers
import subprocess
import threading
import signal
import socket
import traceback
import math
import fcntl
import queue
import shutil
import tempfile
import urllib.request
import urllib.error
from datetime import datetime, timedelta, date, timezone
from urllib.parse import quote, urlparse
from typing import List, Dict, Optional, Tuple, Any, Union
from pathlib import Path

import volume_policy  # peripheral-audio volume + quiet-hours policy (pure, fail-safe)

# Third-party imports
try:
    import yaml
    from pytz import timezone, utc, NonExistentTimeError, AmbiguousTimeError
    import requests
    from flask import Flask, send_from_directory, jsonify, abort, request, redirect, Response
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    import pychromecast
except ImportError as e:
    print(f"Missing required package: {e}")
    print("Please install: pip install flask apscheduler requests pychromecast PyYAML")
    sys.exit(1)

# ---- LITE MODE: skip heavy audio conversion ----
# Set CASTADHAN_LITE=1 in /etc/default/castadhan-portable to enable.
# On low-RAM devices (Pi 3 A+, Pi Zero), pydub's AudioSegment.from_file loads
# entire mp3s into RAM and triggers OOM kills. In lite mode we skip the
# "compatible" file generation entirely and rely on the source mp3 files
# (modern Cast devices accept mono 44.1 kHz mp3 fine).
LITE_MODE = os.environ.get("CASTADHAN_LITE", "").lower() in ("1", "true", "yes")
AudioSegment = None  # type: ignore  # lazily imported only when needed
CouldntDecodeError = Exception
def _import_pydub():
    """Import pydub on demand. Returns True if available."""
    global AudioSegment, CouldntDecodeError
    if AudioSegment is not None:
        return True
    try:
        from pydub import AudioSegment as _AS
        from pydub.exceptions import CouldntDecodeError as _CDE
        AudioSegment = _AS
        CouldntDecodeError = _CDE
        return True
    except ImportError:
        return False

# ---------------- Global Process Lock (Kernel-Enforced Singleton) ----------------
LOCKFILE = "/tmp/castadhan.lock"
_global_lock_f = None

def acquire_global_lock():
    """Acquire kernel-level process lock to prevent multiple instances"""
    global _global_lock_f
    try:
        # Check if lock file is stale (process no longer exists)
        if os.path.exists(LOCKFILE):
            try:
                with open(LOCKFILE, 'r') as f:
                    old_pid_str = f.read().strip()
                    if old_pid_str:
                        old_pid = int(old_pid_str)
                        # Check if process exists (this will raise if process doesn't exist)
                        os.kill(old_pid, 0)
                        # Process exists, lock is valid
                        print(f"FATAL: Another CastAdhan instance is already running (PID: {old_pid})")
                        print(f"Lock file: {LOCKFILE}")
                        print("If you're sure no other instance is running, manually remove the lock file:")
                        print(f"  sudo rm -f {LOCKFILE}")
                        sys.exit(1)
            except (ProcessLookupError, ValueError, OSError):
                # Process doesn't exist or PID invalid - remove stale lock
                print(f"Removing stale lock file from PID {old_pid_str if 'old_pid_str' in locals() else 'unknown'}")
                try:
                    os.unlink(LOCKFILE)
                except:
                    pass
            except Exception as e:
                print(f"Error checking lock file: {e}")
        
        # Acquire fresh lock
        _global_lock_f = open(LOCKFILE, 'w')
        fcntl.flock(_global_lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _global_lock_f.write(str(os.getpid()))
        _global_lock_f.flush()
        print(f"Acquired global lock: {LOCKFILE} (PID: {os.getpid()})")
    except (IOError, OSError) as e:
        print(f"FATAL: Another CastAdhan instance is already running (lock held)")
        print(f"Lock file: {LOCKFILE}")
        print("If you're sure no other instance is running, manually remove the lock file:")
        print(f"  sudo rm -f {LOCKFILE}")
        sys.exit(1)

# ---------------- Gunicorn Bootstrap Guard (must be at top) ----------------
# Check worker count early to warn about misconfiguration
if os.environ.get("USE_GUNICORN", "").lower() == "true":
    # Gunicorn doesn't set this automatically, but we check for documentation
    worker_count = os.environ.get("GUNICORN_WORKERS", "1")
    if worker_count != "1":
        print(f"WARNING: CastAdhan running with {worker_count} workers. Must run with 1 worker only.")
        print("This will cause duplicate Adhans and scheduler corruption.")

# ---------------- Production Constants ----------------
REQUEST_TIMEOUT = 10  # Seconds for all API calls
STARTUP_TIME = datetime.now(utc)
MIN_PLAY_INTERVAL_SECONDS = 5  # Prevent rapid manual triggers
MAX_HIJRI_CACHE_SIZE = 5  # Limit memory growth
TWILIGHT_CACHE_DAYS = 7  # How often to refresh twilight detection
BINARY_SEARCH_STEPS = 12  # Number of samples for binary search (covers full year)

# ---------------- Paths (defined early so logging can use them) ----------------
ROOT = os.path.abspath(os.path.dirname(__file__))
CFG_PATH = os.path.join(ROOT, "config.yaml")

# ---------------- Enhanced Logging ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(os.path.join(ROOT, 'castadhan.log'), maxBytes=5*1024*1024, backupCount=3),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("castadhan")

# Default config with high-latitude combined prayer support and audio routing
DEFAULT_CONFIG = {
    'app': {
        'host': '0.0.0.0',
        'port': 8786,
        'timezone': 'Europe/London',
        'location': {
            'city': '',
            'country': '',
            'latitude': 51.5074,
            'longitude': -0.1278
        },
        'calculation_method': 'ISNA'
    },
    'audio': {
        'adhan': 'audio/adhan.mp3',
        'adhan_compatible': 'audio/adhan_compatible.mp3',
        'takbeeraat': 'audio/takbeeraat.mp3',
        'morning_dhikr': 'audio/morning_dhikr.mp3',
        'evening_dhikr': 'audio/evening_dhikr.mp3',
        'surah_kahf': 'audio/surah_kahf.mp3',
        'wakeup': 'audio/wakey_wakey.mp3',
        'suhoor_alarm': 'audio/suhoor_alarm.mp3',
        'twilight': 'audio/twilight.mp3',  # Plays after Maghrib during persistent twilight
        'fajr_warning': 'audio/fajr_warning.mp3',  # Plays 5 minutes before sunrise (end-of-Fajr reminder)
        'dhuhr_warning': 'audio/dhuhr_warning.mp3',  # Plays 10 minutes before Asr (end of Dhuhr time)
        'asr_warning': 'audio/asr_warning.mp3',      # Plays 10 minutes before Maghrib (end of Asr time)
        'maghrib_warning': 'audio/maghrib_warning.mp3'  # Plays 10 minutes before Isha (end of Maghrib time)
    },
    'speakers': {
        'general_volume': 0.7,
        # O24 (v1.2.0, Tue 26 May 2026) — ROOT CAUSE of config drift:
        # Previously this default was 'speaker' (English-only assumption).
        # During Belgium deployment on 25 May we found this kept silently
        # overwriting the user's empty-string override after restarts, because
        # config-load merges schema defaults INTO the loaded YAML for any
        # missing fields, and an empty string in YAML can be misread as
        # "missing" by some merge implementations. Forcing to "" here means
        # the schema and the shipped config.yaml agree; even if a merge does
        # happen, the result is correct. Filter for excluding displays/hubs
        # is sufficient on its own — see B-Belgium-1 (Dutch "Huiskamer").
        'include_if_name_contains': '',
        'exclude_if_name_contains_any': ['display', 'hub', 'nest hub'],
        'suhoor_exclude_names': [],
        'audio_routing': {}  # Per-speaker audio routing: {"speaker_name": {"adhan": true, "morning_dhikr": false, ...}}
    },
    # Peripheral-audio volume + quiet-hours policy (v1.8.6). Apartment-friendly
    # defaults: peripheral audio (dhikr/takbeeraat/duas) plays quieter, and quiet
    # hours suppress/soften it so it can't disturb neighbours. The full per-type
    # map + category ratios live in volume_policy.py; these top-level knobs are the
    # ones an owner is most likely to tune. Owner can opt "up" (e.g. enabled:false
    # for a house, or widen the quiet window). The adhan + prayer warnings are
    # always CORE+ALLOW and are never touched by this.
    'volume_policy': {
        'enabled': True,
        'quiet_hours': {'strategy': 'clock', 'start': '22:00', 'end': '07:00'},
    },
    'rules': {
        'morning_dhikr_time': '07:00',
        # v1.8.8: wakey-wakey alarm is OFF by default on every portable CastAdhan.
        # It's a personal alarm clock, not a prayer feature — the owner opts in via
        # the console. (Time/weekday settings are kept so enabling is one click.)
        'wakeup_enabled': False,
        'wakeup_time': '06:30',
        'wakeup_weekdays_only': True,
        'evening_after_maghrib_minutes': 30,
        'evening_dhikr_cutoff_time': '20:00',  # Suppress dhikr if it would end after this time
        # O3 (v1.2.0): default flipped True → False. Auto-detect via ip-api.com
        # returns the ISP's POP, not the customer's actual city — TalkTalk users
        # in the UK get "Birkenhead" or "Chester" instead of their real town,
        # which then breaks prayer times. Manual Search & Set is the reliable
        # path; auto-detect remains available as an explicit button.
        'auto_detect_location_on_startup': False,
        'skip_isha_during_persistent_twilight': True,  # Skip Isha when astronomy says no real night
        'setup_complete': False,  # First-run wizard sets this True when finished
        # Note: legacy `skip_isha_between` is no longer in defaults.
        # If a user-supplied config still has it, code paths in compute_current_next
        # and schedule_today honour it as a static manual override.
        'suhoor_lead_minutes': 30,
        # v1.8.11: Fajr adhan timing mode (owner-selectable, always clamped to the
        # permissible window [true dawn, sunrise)):
        #   'raw' (default) = fire at the true astronomical dawn, every day.
        #   'before_sunrise' = fire `fajr_minutes_before_sunrise` minutes before sunrise.
        # (Ramadan always uses raw dawn regardless of this setting.)
        'fajr_mode': 'raw',
        'fajr_minutes_before_sunrise': 30,
        # Legacy (pre-v1.8.11 weekday/weekend rule); kept for back-compat, now
        # superseded by fajr_mode above.
        'fajr_workday_cap': '07:00',
        'fajr_weekend_offset_minutes': -30,
        # v1.8.14: prayers whose NO_SPEAKERS is treated as silent-by-design
        # (downgraded to status SILENT_EXPECTED): no instant Telegram alert, no
        # daily-digest warning, no L11 sanity HIGH-fail. A genuine FAIL (cast
        # timeout etc.) for the same prayer still alerts. Use case: aunt powers
        # her bedroom speakers off at night, so the 03:40 raw-dawn Fajr never
        # finds any speakers — that's intentional, not a bug.
        'expected_silent_prayers': [],
        # v1.8.14: when True (the new global default), the 23:15 Telegram digest
        # is sent ONLY on days with problems — silent on all-green days. Instant
        # failure alerts continue regardless. Owner explicitly chose this so the
        # phone only buzzes when something genuinely needs attention.
        'telegram_only_on_failure': True,
        'enable_eid_takbeeraat': True,
        # v1.6.2: 'inclusive' (default) = Fajr 9 → Asr 13 Dhū al-Hijjah
        # 'strict' = 10-12 only (the pre-v1.6.2 behaviour, narrower scholarly view).
        # Doesn't affect Eid al-Fitr — that's always 1 Shawwal except Maghrib.
        'takbeeraat_window': 'inclusive',
        # High-latitude settings
        # O37 / Lesson 31 (v1.2.0): default flipped combine_prayers → static_offset.
        # combine_prayers SKIPS Isha and SHIFTS Fajr to sunrise-30 — a conservative
        # interpretation that's not what most UK/EU/NL/BE/DE mosques actually use
        # in practice. static_offset keeps Fajr at the calculated time and sets
        # Isha = Maghrib + 90 min — closer to most published mosque timetables.
        # Users can change via wizard step 2 (lat>=45°) or Settings → Advanced.
        'high_latitude_method': 'static_offset',  # options: 'combine_prayers', '1_7_rule', 'static_offset'
        # C-2 (v1.5.0): Madhab/fiqh selector for Asr shadow factor.
        # 'shafii' → shadow factor 1 (default — Aladhan's own default, used by
        # most North African / Arab / Indonesian / Malay Muslims). 'hanafi' →
        # shadow factor 2 (later Asr by 30-60 min, used by most South Asian /
        # Turkish / Balkan / Central Asian Muslims, and ~1.3 billion globally).
        # Maliki and Hanbali use Shafi'i shadow factor — set to 'shafii'.
        # Before v1.5.0 this parameter was never sent to Aladhan, so every
        # Hanafi user globally got Shafi'i Asr daily.
        'madhab': 'shafii',  # options: 'shafii', 'hanafi', 'maliki', 'hanbali'
        'isha_static_offset_minutes': 90,  # Used if method = 'static_offset'
        'isha_max_time': '',  # Optional HH:MM cap (e.g. "22:00"); empty disables (recommended for travel)
        # B-Belgium-38 (v1.9.3): UK users routinely want the high-latitude
        # Isha rule to fire YEAR-ROUND, not only during the strict persistent-
        # twilight window. At 51.6°N (Swansea / London / Cardiff) the sun
        # technically dips far enough for ISNA to compute an Isha angle for
        # most of June, but the resulting time is 23:20+ — well past most
        # mosque timetables. When this flag is True, apply_high_latitude_
        # overrides() runs the configured method regardless of the persistent-
        # twilight cache state. Defaults to False to preserve previous
        # behaviour on every existing Pi.
        'isha_method_always_apply': False,
        'fajr_at_start_when_isha_capped': True,  # When Isha cap fires today, play Fajr at raw API time
        'twilight_scan_frequency_days': 7,
        # B-Belgium-64: opt-in, per box. Cast audio from the PUBLIC internet URL
        # (GitHub) instead of the Pi's local HTTP server. For boxes whose router
        # blocks the speaker from reaching the Pi over the LAN (AP / client
        # isolation) — the speaker streams the adhan over the internet instead.
        # Default False everywhere = local serving, fully offline-capable.
        'cast_media_from_internet': False
    },
    # v1.9.8.1: Quran Programs defaults. _deep_merge_defaults() will inject this
    # block on any existing install where config.yaml was preserved across the
    # v1.9.7 → v1.9.8 update (which is every fleet Pi — the updater preserves
    # user config so they don't lose customisations). Without this hot-fix, the
    # dashboard's new Quran cards would show "no entries" on every existing Pi
    # until the operator hand-edited config.yaml.
    #
    # The merge is non-destructive: if a user has manually disabled or retimed
    # any of these entries, those edits are preserved across future restarts.
    # The defaults only fill MISSING keys.
    'scheduled_audio': {
        'surah_baqarah': {
            'name': 'Surah al-Baqarah',
            'category': 'Quran',
            'enabled': False,
            'trigger_type': 'fixed',
            'play_time': '10:00',
            'relative_prayer_anchor': 'none',
            'offset_minutes': 0,
            'days': [0, 1, 2, 3, 4, 5, 6],
            'audio_url': 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/surah_baqarah.mp3',
            'target_speakers': [],
            'max_duration_minutes': 150,
        },
        'surah_yasin': {
            'name': 'Surah Yasin',
            'category': 'Quran',
            'enabled': False,
            'trigger_type': 'relative_to_prayer',
            'play_time': '',
            'relative_prayer_anchor': 'Maghrib',
            'offset_minutes': 15,
            'days': [3],   # Thursday
            'audio_url': 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/surah_yasin.mp3',
            'target_speakers': [],
            'max_duration_minutes': 60,
        },
        'surah_mulk': {
            'name': 'Surah al-Mulk',
            'category': 'Quran',
            'enabled': False,
            'trigger_type': 'fixed',
            'play_time': '22:00',
            'relative_prayer_anchor': 'none',
            'offset_minutes': 0,
            'days': [0, 1, 2, 3, 4, 5, 6],
            'audio_url': 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/surah_mulk.mp3',
            'target_speakers': [],
            'max_duration_minutes': 35,
        },
        'surah_kahf': {
            'name': 'Surah al-Kahf',
            'category': 'Quran',
            'enabled': True,   # preserves current behaviour fleet-wide
            # v1.9.9: fixed 07:00 Friday — the time the legacy substitution
            # actually used (the v1.9.8 Dhuhr-60 default was wrong and caused
            # masood's 12-Jun dual-fire). ["__all__"] = all speakers, matching
            # the legacy play-everywhere behaviour.
            'trigger_type': 'fixed',
            'play_time': '07:00',
            'relative_prayer_anchor': 'none',
            'offset_minutes': 0,
            'days': [4],   # Friday
            'audio_url': 'bundled',
            'target_speakers': ['__all__'],
            'max_duration_minutes': 35,
        },
        'surah_waqiah': {
            'name': "Surah al-Waqi'ah",
            'category': 'Quran',
            'enabled': False,
            'trigger_type': 'fixed',
            'play_time': '17:00',
            'relative_prayer_anchor': 'none',
            'offset_minutes': 0,
            'days': [0, 1, 2, 3, 4, 5, 6],
            'audio_url': 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/surah_waqiah.mp3',
            'target_speakers': [],
            'max_duration_minutes': 20,
        },
        'surah_sajdah': {
            'name': 'Surah as-Sajdah',
            'category': 'Quran',
            'enabled': False,
            'trigger_type': 'relative_to_prayer',
            'play_time': '',
            'relative_prayer_anchor': 'Fajr',
            'offset_minutes': 15,
            'days': [4],   # Friday
            'audio_url': 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/surah_sajdah.mp3',
            'target_speakers': [],
            'max_duration_minutes': 10,
        },
    }
}

def _deep_merge_defaults(defaults: dict, current: Optional[dict]) -> dict:
    """Recursively merge defaults without overwriting existing values"""
    if current is None:
        current = {}
    out = dict(current)
    for k, v in defaults.items():
        if isinstance(v, dict):
            out[k] = _deep_merge_defaults(v, out.get(k))
        else:
            if k not in out:
                out[k] = v
    return out

def migrate_old_config(cfg: dict) -> dict:
    """Migrate old config keys to new portable format"""
    changes = False
    
    # Migrate loft_wakeup_time to wakeup_time
    if 'rules' in cfg:
        if 'loft_wakeup_time' in cfg['rules'] and 'wakeup_time' not in cfg['rules']:
            cfg['rules']['wakeup_time'] = cfg['rules']['loft_wakeup_time']
            log.warning("Migrated 'loft_wakeup_time' to 'wakeup_time' - please update config.yaml")
            changes = True
        
        if 'loft_wakeup_weekdays_only' in cfg['rules'] and 'wakeup_weekdays_only' not in cfg['rules']:
            cfg['rules']['wakeup_weekdays_only'] = cfg['rules']['loft_wakeup_weekdays_only']
            log.warning("Migrated 'loft_wakeup_weekdays_only' to 'wakeup_weekdays_only'")
            changes = True
        
        # Migrate persistent_twilight_logic to high_latitude_method
        if 'persistent_twilight_logic' in cfg['rules'] and 'high_latitude_method' not in cfg['rules']:
            old_val = cfg['rules']['persistent_twilight_logic']
            if old_val == '1_7_rule':
                cfg['rules']['high_latitude_method'] = '1_7_rule'
            elif old_val == 'static_offset':
                cfg['rules']['high_latitude_method'] = 'static_offset'
            else:
                cfg['rules']['high_latitude_method'] = 'combine_prayers'
            log.warning(f"Migrated 'persistent_twilight_logic' to 'high_latitude_method' = {cfg['rules']['high_latitude_method']}")
            changes = True
        
        # Migrate twilight_reminder audio to twilight
        if 'audio' in cfg:
            if 'twilight_reminder' in cfg['audio'] and 'twilight' not in cfg['audio']:
                cfg['audio']['twilight'] = cfg['audio']['twilight_reminder']
                log.warning("Migrated 'twilight_reminder' audio to 'twilight'")
                changes = True
    
    # Migrate audio file
    if 'audio' in cfg:
        if 'loft_wakeup' in cfg['audio'] and 'wakeup' not in cfg['audio']:
            cfg['audio']['wakeup'] = cfg['audio']['loft_wakeup']
            log.warning("Migrated 'loft_wakeup' audio to 'wakeup'")
            changes = True
    
    return cfg, changes

# C-1 + C-2 (v1.5.0): Aladhan parameter maps. Must be defined BEFORE
# validate_config() because validate_config is called at module-load time
# (line ~400) and uses these constants for input validation. Previous
# location near fetch_prayer_times() caused a NameError on startup.
ALADHAN_METHOD_MAP = {
    "ISNA":        2,   # Islamic Society of North America — Fajr 15°, Isha 15°
    "MWL":         3,   # Muslim World League — Fajr 18°, Isha 17°
    "EGYPTIAN":    5,   # Egyptian General Authority — Fajr 19.5°, Isha 17.5°
    "KARACHI":     1,   # University of Islamic Sciences, Karachi — Fajr 18°, Isha 18°
    "UMM AL-QURA": 4,   # Umm al-Qura, Makkah — Fajr 18.5°, Isha = Maghrib + 90 min
    # Aliases / variants likely to occur in user-written config.yaml
    "UMM_AL_QURA": 4,
    "UMMALQURA":   4,
    "ISLAMIC SOCIETY OF NORTH AMERICA": 2,
    "MUSLIM WORLD LEAGUE": 3,
}
ALADHAN_SCHOOL_MAP = {
    "SHAFII":  0,   # default — shadow factor 1
    "SHAFI'I": 0,
    "MALIKI":  0,   # Maliki/Hanbali use Shafi'i shadow factor too
    "HANBALI": 0,
    "HANAFI":  1,   # shadow factor 2 — Asr ~30-60 min later than Shafi'i
}

def validate_config(cfg: dict) -> Tuple[bool, str]:
    """Validate critical config values to prevent silent failures"""
    try:
        # App section validation
        port = cfg.get('app', {}).get('port')
        if port and not isinstance(port, int):
            return False, f"app.port must be integer, got {type(port)}"
        
        tz = cfg.get('app', {}).get('timezone')
        if tz:
            try:
                timezone(tz)
            except Exception:
                return False, f"Invalid timezone: {tz}"
        
        # Location validation
        lat = cfg.get('app', {}).get('location', {}).get('latitude')
        if lat is not None and not isinstance(lat, (int, float)):
            return False, f"latitude must be number, got {type(lat)}"
        
        lon = cfg.get('app', {}).get('location', {}).get('longitude')
        if lon is not None and not isinstance(lon, (int, float)):
            return False, f"longitude must be number, got {type(lon)}"
        
        # High latitude method validation
        method = cfg.get('rules', {}).get('high_latitude_method', 'combine_prayers')
        if method not in ['combine_prayers', '1_7_rule', 'static_offset']:
            return False, f"high_latitude_method must be one of: 'combine_prayers', '1_7_rule', 'static_offset'"

        # v1.8.11: Fajr timing mode validation
        fmode = cfg.get('rules', {}).get('fajr_mode', 'raw')
        if fmode not in ['raw', 'before_sunrise']:
            return False, f"fajr_mode must be 'raw' or 'before_sunrise'; got {fmode!r}"
        fmins = cfg.get('rules', {}).get('fajr_minutes_before_sunrise')
        if fmins is not None and (not isinstance(fmins, int) or not (0 <= fmins <= 120)):
            return False, f"fajr_minutes_before_sunrise must be an int 0–120; got {fmins!r}"

        # C-2 (v1.5.0): madhab validation
        madhab = cfg.get('rules', {}).get('madhab', 'shafii')
        if str(madhab).lower() not in ['shafii', 'hanafi', 'maliki', 'hanbali']:
            return False, f"madhab must be one of: 'shafii', 'hanafi', 'maliki', 'hanbali'; got {madhab!r}"

        # C-1 (v1.5.0): calculation_method validation
        calc_method = cfg.get('app', {}).get('calculation_method', 'ISNA')
        if calc_method.upper() not in ALADHAN_METHOD_MAP:
            return False, f"calculation_method must be one of: {sorted(set(ALADHAN_METHOD_MAP.keys()) - {'ISLAMIC SOCIETY OF NORTH AMERICA', 'MUSLIM WORLD LEAGUE', 'UMM_AL_QURA', 'UMMALQURA'})}; got {calc_method!r}"

        # fajr_at_start_when_isha_capped validation (must be bool if present)
        fajr_couple = cfg.get('rules', {}).get('fajr_at_start_when_isha_capped')
        if fajr_couple is not None and not isinstance(fajr_couple, bool):
            return False, f"fajr_at_start_when_isha_capped must be true/false, got {type(fajr_couple).__name__}"

        # evening_dhikr_cutoff_time validation (optional HH:MM)
        evening_cutoff = cfg.get('rules', {}).get('evening_dhikr_cutoff_time')
        if evening_cutoff not in (None, ''):
            if not isinstance(evening_cutoff, str):
                return False, f"evening_dhikr_cutoff_time must be HH:MM string, got {type(evening_cutoff).__name__}"
            try:
                parts = evening_cutoff.split(':')
                if len(parts) != 2:
                    raise ValueError()
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    return False, f"evening_dhikr_cutoff_time '{evening_cutoff}' out of range"
            except Exception:
                return False, f"evening_dhikr_cutoff_time '{evening_cutoff}' must be HH:MM format"

        # auto_detect_location_on_startup validation
        adl = cfg.get('rules', {}).get('auto_detect_location_on_startup')
        if adl is not None and not isinstance(adl, bool):
            return False, f"auto_detect_location_on_startup must be true/false, got {type(adl).__name__}"

        # skip_isha_during_persistent_twilight validation
        sipt = cfg.get('rules', {}).get('skip_isha_during_persistent_twilight')
        if sipt is not None and not isinstance(sipt, bool):
            return False, f"skip_isha_during_persistent_twilight must be true/false, got {type(sipt).__name__}"

        # isha_max_time validation (optional; HH:MM string, or empty/None to disable cap)
        isha_max = cfg.get('rules', {}).get('isha_max_time')
        if isha_max not in (None, ''):
            if not isinstance(isha_max, str):
                return False, f"isha_max_time must be HH:MM string, got {type(isha_max).__name__}"
            try:
                parts = isha_max.split(':')
                if len(parts) != 2:
                    raise ValueError("must be HH:MM")
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    return False, f"isha_max_time '{isha_max}' out of range (00:00–23:59)"
            except Exception:
                return False, f"isha_max_time '{isha_max}' must be HH:MM format"

        return True, "Config valid"
    except Exception as e:
        return False, f"Config validation error: {e}"

# Load or create config
try:
    with open(CFG_PATH, "r") as f:
        CFG = yaml.safe_load(f)
except FileNotFoundError:
    log.warning(f"Config file not found at {CFG_PATH}, creating default config")
    CFG = DEFAULT_CONFIG
    try:
        with open(CFG_PATH, "w") as f:
            yaml.dump(CFG, f, default_flow_style=False, indent=2)
        log.info(f"Created default config at {CFG_PATH}")
    except Exception as e:
        log.error(f"Failed to create config file: {e}")

# Migrate old config keys
CFG, migrated = migrate_old_config(CFG)

# Merge with defaults
CFG = _deep_merge_defaults(DEFAULT_CONFIG, CFG)

# Validate config
valid, msg = validate_config(CFG)
if not valid:
    log.error(f"Config validation failed: {msg}")
    log.error("Please fix config.yaml and restart")
    sys.exit(1)

# Extract config values
HOST = CFG["app"]["host"]
PORT = int(CFG["app"]["port"])

TZ = CFG["app"]["timezone"]
LOCAL_TZ = timezone(TZ)
CITY = CFG["app"]["location"]["city"]
COUNTRY = CFG["app"]["location"]["country"]
LATITUDE = CFG["app"]["location"]["latitude"]
LONGITUDE = CFG["app"]["location"]["longitude"]
METHOD = CFG["app"]["calculation_method"]

def _refresh_location_globals_from_cfg():
    """v1.8.12: pull CITY / COUNTRY / LATITUDE / LONGITUDE / TZ from live CFG so
    callers reading the module globals (notably the Aladhan fetcher) see the
    just-saved values without needing a service restart. Used by
    api_set_config() and the auto-detect handler — keeps both code paths
    consistent so a future wizard variant can't reintroduce stale-globals."""
    global CITY, COUNTRY, LATITUDE, LONGITUDE, TZ, LOCAL_TZ
    loc = (CFG.get("app", {}) or {}).get("location", {}) or {}
    CITY      = loc.get("city", "") or ""
    COUNTRY   = loc.get("country", "") or ""
    LATITUDE  = loc.get("latitude")
    LONGITUDE = loc.get("longitude")
    new_tz = (CFG.get("app", {}) or {}).get("timezone")
    if new_tz and new_tz != TZ:
        TZ = new_tz
        try:
            LOCAL_TZ = timezone(TZ)
        except Exception as e:
            log.warning(f"refresh_location_globals: pytz lookup failed for {new_tz!r}: {e}")

AUDIO = CFG["audio"]
SPK = CFG["speakers"]
RULES = CFG["rules"]

# ---------------- Audio Routing Configuration ----------------
# Initialize audio routing if not present
if 'audio_routing' not in SPK:
    SPK['audio_routing'] = {}

def get_speaker_audio_routing(speaker_name: str) -> dict:
    """Get audio routing for a specific speaker, with defaults for missing audio types"""
    routing = SPK['audio_routing'].get(speaker_name, {})
    
    # Default all audio types to True if not specified
    default_routing = {}
    for audio_type in AUDIO.keys():
        if not audio_type.endswith('_compatible'):
            default_routing[audio_type] = routing.get(audio_type, True)
    
    return default_routing

def set_speaker_audio_routing(speaker_name: str, routing: dict):
    """Set audio routing for a specific speaker"""
    if speaker_name not in SPK['audio_routing']:
        SPK['audio_routing'][speaker_name] = {}
    
    # Update only provided keys
    for audio_type, enabled in routing.items():
        if audio_type in AUDIO and not audio_type.endswith('_compatible'):
            SPK['audio_routing'][speaker_name][audio_type] = enabled
    
    # Save to disk
    try:
        with open(CFG_PATH, "w") as f:
            yaml.dump(CFG, f, default_flow_style=False, indent=2)
    except Exception as e:
        log.error(f"Failed to save audio routing: {e}")

# ---------------- Helper for timezone-aware datetime (DST-safe) ----------------
def ensure_aware(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware"""
    if dt.tzinfo is None:
        return LOCAL_TZ.localize(dt)
    return dt

def safe_localize(dt: datetime) -> datetime:
    """Safely localize a datetime, handling DST gaps and ambiguities"""
    try:
        return LOCAL_TZ.localize(dt, is_dst=None)
    except (NonExistentTimeError, AmbiguousTimeError):
        # During DST transitions, fall back to a safe default
        try:
            return LOCAL_TZ.localize(dt, is_dst=False)
        except:
            # Ultimate fallback - use UTC conversion
            return dt.replace(tzinfo=LOCAL_TZ)

def now_local():
    """Get current datetime with timezone"""
    return datetime.now(LOCAL_TZ)

# ---------------- Circuit Breaker Cache ----------------
_last_successful_times = {"date": None, "times": None}
_last_successful_hijri = {"date": None, "hijri": None}
_last_successful_sunrise_sunset = {"date": None, "data": None}

# ---------------- Twilight Detection Cache ----------------
_twilight_cache = {
    "last_scan": None,
    "persistent_twilight_active": False,
    "persistent_start": None,
    "persistent_end": None,
    "high_latitude_method": RULES.get('high_latitude_method', 'combine_prayers')
}
_twilight_lock = threading.Lock()

# ---------------- Audio Overlap Guard ----------------
_last_play_timestamp = 0
_play_lock = threading.Lock()

# ---------------- Port Management ----------------
def check_port_availability(port: int) -> bool:
    """Check if a port is available"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('localhost', port))
            return True
    except OSError:
        return False

def kill_process_on_port(port: int) -> bool:
    """Kill any process using the specified port"""
    try:
        result = subprocess.run(
            ['lsof', '-ti', f':{port}'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0 and result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                try:
                    subprocess.run(['kill', '-9', pid], timeout=5)
                    log.info(f"Killed process {pid} on port {port}")
                except Exception as e:
                    log.error(f"Failed to kill process {pid}: {e}")
            return True
        return True  # No process found, port is free
    except Exception as e:
        log.error(f"Error checking/killing process on port {port}: {e}")
        return False

# ---------------- Audio Compatibility (Memory Optimized) ----------------
def ensure_audio_directory():
    """Ensure audio directory exists and create compatible audio files.
    In LITE_MODE, skip the compatible-file generation entirely — it's the
    main OOM cause on low-RAM Pis. The source mp3s play fine on modern Cast
    devices without conversion."""
    audio_dir = Path(ROOT) / "audio"
    audio_dir.mkdir(exist_ok=True)

    if LITE_MODE:
        log.info("LITE_MODE: skipping audio compatibility conversion")
        return

    # Create compatible versions of audio files
    for audio_type, rel_path in AUDIO.items():
        if audio_type.endswith('_compatible'):
            continue

        original_file = Path(ROOT) / rel_path
        compatible_file = Path(ROOT) / rel_path.replace('.mp3', '_compatible.mp3')

        if original_file.exists() and not compatible_file.exists():
            try:
                create_compatible_audio_file(str(original_file), str(compatible_file))
            except Exception as e:
                log.error(f"Failed to create compatible audio file for {audio_type}: {e}")

def create_compatible_audio_file(input_file: str, output_file: str):
    """Create a compatible audio file using ffmpeg (streaming, low memory).
    Falls back to pydub if ffmpeg subprocess fails."""
    try:
        log.info(f"Creating compatible audio file: {output_file}")

        # Prefer ffmpeg directly — streams audio without loading into RAM
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", input_file,
             "-ac", "2", "-ar", "44100", "-b:a", "128k", "-y", output_file],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            log.info(f"Successfully created compatible audio file (ffmpeg): {output_file}")
            return True
        log.warning(f"ffmpeg failed for {output_file}: {result.stderr.strip()[:200]}")
    except FileNotFoundError:
        log.warning("ffmpeg not on PATH; falling back to pydub (uses more RAM)")
    except subprocess.TimeoutExpired:
        log.error(f"ffmpeg timed out converting {input_file}")
        return False
    except Exception as e:
        log.warning(f"ffmpeg attempt failed ({e}); falling back to pydub")

    # Fallback: pydub (memory-heavy, only used if ffmpeg unavailable)
    if not _import_pydub():
        log.error("Neither ffmpeg nor pydub available; cannot convert audio")
        return False
    try:
        audio = AudioSegment.from_file(input_file)
        audio = audio.set_frame_rate(44100).set_channels(2)
        audio.export(output_file, format="mp3", bitrate="128k", parameters=["-ar", "44100"])
        log.info(f"Successfully created compatible audio file (pydub fallback): {output_file}")
        return True
    except Exception as e:
        log.error(f"Error creating compatible audio file: {e}")
        return False

def verify_audio_integrity() -> bool:
    """Verify audio files exist and are not empty - memory optimized (doesn't load files)"""
    all_good = True
    for audio_type, rel_path in AUDIO.items():
        if audio_type.endswith('_compatible'):
            continue
        try:
            abs_path = abs_audio_path(rel_path)
            if os.path.exists(abs_path):
                # Just check file exists and is not too small
                file_size = os.path.getsize(abs_path)
                if file_size < 1000:  # Less than 1KB is probably invalid
                    log.error(f"Audio file too small (likely corrupted): {audio_type} at {rel_path} ({file_size} bytes)")
                    all_good = False
                else:
                    log.info(f"✓ Audio verified: {audio_type} ({file_size} bytes)")
            else:
                if audio_type == 'twilight':
                    log.warning(f"Twilight audio missing (optional): {rel_path}")
                else:
                    log.warning(f"Audio file missing: {audio_type} at {rel_path}")
                    all_good = False
        except Exception as e:
            log.error(f"Audio verification failed for {audio_type}: {e}")
            all_good = False
    return all_good

# ---------------- File Locking with Lockfile Pattern ----------------
def _locked_file_write(filepath: str, data: dict):
    """Write JSON data to file with proper lockfile synchronization"""
    tmp_path = filepath + ".tmp"
    lock_path = filepath + ".lock"
    
    try:
        # Write to temp file first
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        # Acquire exclusive lock on lockfile (using 'a' to avoid truncation race)
        with open(lock_path, 'a') as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            
            # Atomically replace target file
            os.replace(tmp_path, filepath)
            
            # Lock released automatically on context exit
            
    except Exception as e:
        log.error(f"Error writing locked file {filepath}: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

def _locked_file_read(filepath: str) -> Optional[dict]:
    """Read JSON data from file with proper lockfile synchronization"""
    lock_path = filepath + ".lock"
    
    try:
        # Acquire shared lock on lockfile
        with open(lock_path, 'a') as lock_f:  # 'a' ensures file exists without truncating
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_SH)
            
            # Read the actual file while holding lock
            if os.path.exists(filepath):
                with open(filepath, 'r') as f:
                    data = json.load(f)
            else:
                data = None
            
            # Lock released automatically on context exit
            
        return data
        
    except FileNotFoundError:
        return None
    except Exception as e:
        log.error(f"Error reading locked file {filepath}: {e}")
        return None

# ---------------- UI State (enhanced with file locking) ----------------
STATE_PATH = os.path.join(ROOT, "ui_state.json")
_state_lock = threading.RLock()
_default_volume = int(round(float(SPK.get("general_volume", 0.7)) * 100))
shutdown_event = threading.Event()
shutdown_complete = threading.Event()

def _load_state():
    # Only use locked read - if it fails, return defaults
    data = _locked_file_read(STATE_PATH)
    if data is not None:
        return data
    
    # No valid state file, return defaults
    log.info("No valid state file found, using defaults")
    return {"enabled": {"global": True, "speakers": {}}, "volumes": {"__default": _default_volume}}

def _save_state(s):
    # Use locked write
    _locked_file_write(STATE_PATH, s)

UI = _load_state()

# ---------------- Enhanced Flask App ----------------
app = Flask(__name__)

def abs_audio_path(relpath: str) -> str:
    """Get absolute path for audio file, with fallback to compatible version"""
    path = os.path.join(ROOT, relpath)
    if not os.path.isfile(path):
        # Try compatible version
        compatible_path = path.replace('.mp3', '_compatible.mp3')
        if os.path.isfile(compatible_path):
            log.info(f"Using compatible audio: {compatible_path}")
            return compatible_path
        log.error("Audio missing: %s", path)
    return path

# E-5 / U-1 (v1.6.0): PWA routes — manifest, service worker, icons.
# Service worker MUST be served from the same path you want it to scope, so
# we serve sw.js from the root rather than under /static/. The scope is the
# whole site so a tap on the installed icon opens the dashboard.
@app.route("/manifest.json")
def pwa_manifest():
    return send_from_directory(ROOT, "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def pwa_service_worker():
    # Service-Worker-Allowed lets the SW scope to the entire site even though
    # served from /. Cache-Control:no-cache so users get SW updates promptly.
    response = send_from_directory(ROOT, "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

@app.route("/icon-192.png")
@app.route("/icon-512.png")
@app.route("/icon-maskable.png")
def pwa_icon():
    """Serve PWA icons. We don't ship raster files in v1.6.0 (would bloat the
    repo by ~30KB) — instead generate a minimal SVG-based placeholder PNG via
    a 1×1 transparent fallback if the actual icon files don't exist on disk.
    Real icons can be added by dropping icon-192.png / icon-512.png /
    icon-maskable.png next to console.html. The browser tolerates a placeholder
    fine for the PWA install flow."""
    filename = request.path.lstrip("/")
    icon_path = os.path.join(ROOT, filename)
    if os.path.exists(icon_path):
        return send_from_directory(ROOT, filename, mimetype="image/png")
    # 1x1 transparent PNG fallback (43 bytes). Sufficient for the PWA manifest
    # to validate even before real icons are added.
    import base64 as _b64
    tiny_png = _b64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    )
    return Response(tiny_png, mimetype="image/png")

@app.route("/")
def console_page():
    """Serve the console HTML page.
    If setup hasn't been completed yet, redirect to the first-run wizard.
    """
    if not RULES.get("setup_complete", False):
        return redirect("/setup")
    console_path = os.path.join(ROOT, "console.html")
    if os.path.exists(console_path):
        return send_from_directory(ROOT, "console.html")
    else:
        # Return a simple status page if console.html doesn't exist
        twilight_status = "Active" if _twilight_cache["persistent_twilight_active"] else "Inactive"
        method = _twilight_cache["high_latitude_method"]
        return f"""
        <!DOCTYPE html>
        <html>
        <head><title>CastAdhan Status</title></head>
        <body>
            <h1>CastAdhan Status</h1>
            <p>Location: {CITY}, {COUNTRY}</p>
            <p>Timezone: {TZ}</p>
            <p>Status: Running</p>
            <p>Persistent Twilight: {twilight_status}</p>
            <p>High Latitude Method: {method}</p>
            <p><a href="/api/state">View API State</a></p>
            <p><a href="/health">Health Check</a></p>
            <p><a href="/metrics">Metrics</a></p>
        </body>
        </html>
        """

@app.route("/console")
def console_redirect():
    """Redirect /console to /"""
    return redirect("/")

@app.route("/setup")
def setup_wizard():
    """Serve the first-run setup wizard."""
    path = os.path.join(ROOT, "setup.html")
    if os.path.exists(path):
        return send_from_directory(ROOT, "setup.html")
    return ("setup.html missing — please reinstall CastAdhan.", 500)

@app.route("/api/setup/complete", methods=["POST"])
def api_setup_complete():
    """Mark setup as complete and persist."""
    try:
        CFG.setdefault("rules", {})["setup_complete"] = True
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w") as f:
            yaml.dump(CFG, f, default_flow_style=False, indent=2)
        os.replace(tmp, CFG_PATH)
        log.info("✅ First-run setup completed by user")
        try:
            schedule_today()
        except Exception as e:
            log.warning(f"Post-setup schedule_today failed: {e}")
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Failed to mark setup complete: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/setup/reset", methods=["POST"])
def api_setup_reset():
    """Re-enter the setup wizard (e.g. after moving house). Doesn't wipe config."""
    try:
        CFG.setdefault("rules", {})["setup_complete"] = False
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w") as f:
            yaml.dump(CFG, f, default_flow_style=False, indent=2)
        os.replace(tmp, CFG_PATH)
        log.info("Setup reset — wizard will run on next visit")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/setup/status")
def api_setup_status():
    """Returns whether the wizard has been completed."""
    return jsonify({
        "ok": True,
        "setup_complete": bool(RULES.get("setup_complete", False)),
    })

# ============================================================================
# AUTO-UPDATE ENDPOINTS
# ============================================================================

def _read_version_file():
    """Read VERSION file shipped with the install."""
    try:
        with open(os.path.join(ROOT, "VERSION")) as f:
            return f.read().strip()
    except Exception:
        return "unknown"

_CACHED_DEVICE_ID = None

def get_device_id() -> str:
    """Stable per-device identifier so a fleet of gifted Pis is distinguishable in
    alerts and on the dashboard. Read once and cached. Tries, in order:
      1. the Raspberry Pi hardware serial from /proc/cpuinfo  -> "RPI-<serial>"
      2. the systemd machine-id                               -> "SYS-<id16>"
      3. a generic fallback (non-Pi / dev hosts)
    All sources are read-only and OS/hardware-managed — nothing to persist across
    updates (which is why the updater does NOT touch machine-id)."""
    global _CACHED_DEVICE_ID
    if _CACHED_DEVICE_ID is not None:
        return _CACHED_DEVICE_ID
    try:
        if os.path.exists("/proc/cpuinfo"):
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("Serial"):
                        serial = line.split(":")[-1].strip()
                        if serial and set(serial) != {"0"}:
                            _CACHED_DEVICE_ID = "RPI-" + serial.upper()
                            return _CACHED_DEVICE_ID
    except Exception as e:
        log.error(f"Device-id: /proc/cpuinfo read failed: {e}")
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            if os.path.exists(p):
                with open(p) as f:
                    mid = f.read().strip()
                if mid:
                    _CACHED_DEVICE_ID = "SYS-" + mid[:16].upper()
                    return _CACHED_DEVICE_ID
        except Exception:
            continue
    _CACHED_DEVICE_ID = "DEV-GENERIC-PORTABLE"
    return _CACHED_DEVICE_ID

def _site_label() -> str:
    """Human location + stable device id, e.g. 'Ghent · RPI-10000000A1B2C3D4'.
    Used to tag every outbound Telegram message so the maintainer knows which Pi
    in the fleet it came from."""
    return f"{CITY} · {get_device_id()}" if CITY else get_device_id()

_CACHED_TAILSCALE_IP: Optional[str] = None
_CACHED_TAILSCALE_IP_AT: float = 0.0

def _get_tailscale_ip() -> Optional[str]:
    """v1.8.14: best-effort Tailscale IPv4 of this Pi, cached for 5 min so each
    Telegram alert doesn't shell out. Returned in failure alerts so the owner
    can tap straight through to the faulty Pi's dashboard without looking it up.
    Returns None on any error / if Tailscale isn't installed — alerts still work,
    just without the URL line."""
    global _CACHED_TAILSCALE_IP, _CACHED_TAILSCALE_IP_AT
    now = time.time()
    if _CACHED_TAILSCALE_IP and now - _CACHED_TAILSCALE_IP_AT < 300:
        return _CACHED_TAILSCALE_IP
    try:
        import subprocess
        r = subprocess.run(["tailscale", "ip", "-4"],
                           capture_output=True, text=True, timeout=3)
        ip = (r.stdout or "").strip().splitlines()[0] if r.stdout else ""
        if ip.startswith("100."):
            _CACHED_TAILSCALE_IP = ip
            _CACHED_TAILSCALE_IP_AT = now
            return ip
    except Exception as e:
        log.debug(f"_get_tailscale_ip failed: {e}")
    _CACHED_TAILSCALE_IP_AT = now   # avoid hammering on a slow/failing tailscale
    return None

@app.route("/api/version")
def api_version():
    """Return the running version + device id of CastAdhan."""
    return jsonify({"ok": True, "version": _read_version_file(), "device_id": get_device_id()})

@app.route("/api/notify/test", methods=["POST"])
def api_notify_test():
    """v1.8.0: send a test Telegram message to verify the integration."""
    try:
        token, chat_id = _telegram_config()
        if not token or not chat_id:
            return jsonify({"ok": False, "error": "No Telegram token/chat_id configured on this Pi"}), 400
        ok = _telegram_send(f"🔔 CastAdhan ({_site_label()}) test message — Telegram notifications are working.")
        return jsonify({"ok": ok, "message": "sent" if ok else "send failed"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/notify/detect_chat", methods=["POST"])
def api_notify_detect_chat():
    """v1.8.0: call Telegram getUpdates to find the chat_id of whoever last
    messaged the bot. Used once during setup so the operator doesn't have to
    look up their numeric chat id. Requires the token to already be set; the
    operator messages the bot first, then calls this."""
    try:
        token, _ = _telegram_config()
        if not token:
            return jsonify({"ok": False, "error": "No TELEGRAM_BOT_TOKEN configured yet"}), 400
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=REQUEST_TIMEOUT)
        data = r.json()
        chats = []
        for upd in data.get("result", []):
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("id"):
                chats.append({
                    "chat_id": chat.get("id"),
                    "name": chat.get("first_name") or chat.get("title") or "",
                    "username": chat.get("username"),
                    "last_text": (msg.get("text") or "")[:40],
                })
        # dedupe by chat_id, keep most recent
        seen = {}
        for c in chats:
            seen[c["chat_id"]] = c
        return jsonify({"ok": True, "chats": list(seen.values())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ────────────────────────────────────────────────────────────────────────────
# WiFi Setup wizard (v1.9.0)
# ────────────────────────────────────────────────────────────────────────────
# For the "post a Pi to a friend" gift workflow: friend plugs Pi into Ethernet
# on first boot -> Pi joins the tailnet -> owner opens the dashboard remotely
# over Tailscale -> owner uses these endpoints to scan + connect the friend's
# WiFi. eth0 is never touched here; NetworkManager keeps both interfaces up so
# the Pi stays reachable for a retry if the password's wrong. Once wlan0 has
# an IP, the dashboard shows "safe to unplug Ethernet."
#
# All nmcli calls run as the castadhan service user via the polkit rule
# installed at /etc/polkit-1/rules.d/50-castadhan-nm.rules (no sudo, no
# NoNewPrivileges fight). Passwords are NEVER logged.

def _get_wifi_status() -> dict:
    """Read current eth0 + wlan0 state from NetworkManager. Defensive: any
    nmcli failure resolves to 'unknown' rather than crashing the endpoint."""
    import subprocess
    status = {
        "eth0":  {"state": "unknown", "ip": None},
        "wlan0": {"state": "unknown", "ip": None, "ssid": None},
        "safe_to_unplug_ethernet": False,
        "wlan0_present": False,
    }
    try:
        r = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
            capture_output=True, text=True, timeout=5)
        for line in (r.stdout or "").strip().split("\n"):
            parts = line.split(":")
            if len(parts) < 4:
                continue
            device, _dtype, state, connection = parts[0], parts[1], parts[2], parts[3]
            if device == "eth0":
                status["eth0"]["state"] = state
            elif device == "wlan0":
                status["wlan0"]["state"] = state
                status["wlan0"]["ssid"] = connection or None
                status["wlan0_present"] = True
        for iface in ("eth0", "wlan0"):
            try:
                ipres = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", iface],
                    capture_output=True, text=True, timeout=5)
                for ln in (ipres.stdout or "").strip().split("\n"):
                    if "IP4.ADDRESS" in ln and ":" in ln:
                        val = ln.split(":", 1)[1].split("/")[0].strip()
                        if val and val != "--":
                            status[iface]["ip"] = val
                            break
            except Exception:
                pass
        status["safe_to_unplug_ethernet"] = (
            status["wlan0"]["state"] == "connected"
            and status["wlan0"]["ip"] is not None)
    except Exception as e:
        log.error(f"_get_wifi_status error: {e}")
    return status

@app.route("/api/wifi/status")
def api_wifi_status():
    """v1.9.0: current state of eth0 + wlan0, plus 'safe to unplug Ethernet'."""
    try:
        return jsonify({"ok": True, "status": _get_wifi_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/wifi/scan", methods=["POST"])
def api_wifi_scan():
    """v1.9.0: trigger a rescan + return nearby networks (deduped, sorted by
    signal strength)."""
    import subprocess
    try:
        # Best-effort rescan. NetworkManager rate-limits this, so a recent
        # rescan may no-op — that's fine, the cached list is what 'list' returns.
        try:
            subprocess.run(["nmcli", "device", "wifi", "rescan"],
                           capture_output=True, timeout=8)
        except Exception:
            pass
        time.sleep(2)
        r = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,FREQ", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=10)
        networks = []
        seen = set()
        for line in (r.stdout or "").strip().split("\n"):
            parts = line.split(":")
            if len(parts) < 4:
                continue
            ssid, signal, security, freq = parts[0], parts[1], parts[2], parts[3]
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            try:
                signal_i = int(signal)
            except ValueError:
                signal_i = 0
            try:
                freq_i = int(freq)
            except ValueError:
                freq_i = 0
            networks.append({
                "ssid": ssid,
                "signal": signal_i,
                "security": security or "open",
                "frequency_mhz": freq_i,
                "band": "5 GHz" if freq_i >= 5000 else "2.4 GHz",
            })
        networks.sort(key=lambda n: n["signal"], reverse=True)
        return jsonify({"ok": True, "networks": networks})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "WiFi scan timed out"}), 504
    except Exception as e:
        log.error(f"WiFi scan endpoint error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/wifi/connect", methods=["POST"])
def api_wifi_connect():
    """v1.9.0: connect wlan0 to a chosen SSID. Doesn't touch eth0; leaves both
    interfaces up so the Pi stays reachable for a retry if the password's wrong.
    Cleans up the failed connection profile on auth failure so the SSID list
    doesn't accumulate broken entries."""
    import subprocess
    try:
        body = request.get_json(force=True, silent=True) or {}
        ssid = (body.get("ssid") or "").strip()
        password = body.get("password") or ""
        if not ssid:
            return jsonify({"ok": False, "error": "ssid is required"}), 400
        # Confirm wlan0 exists (Pi Zero W has no eth0; Pi Zero or original Pi 3
        # might lack WiFi on some kernels — defensive check either way).
        r = subprocess.run(["nmcli", "-t", "-f", "DEVICE", "device"],
                           capture_output=True, text=True, timeout=5)
        if "wlan0" not in (r.stdout or ""):
            return jsonify({"ok": False, "error": "No wlan0 interface on this Pi"}), 400

        # DO NOT log the password.
        log.info(f"WiFi connect attempt: ssid={ssid!r} (password not logged)")

        cmd = ["nmcli", "device", "wifi", "connect", ssid, "ifname", "wlan0"]
        if password:
            cmd.extend(["password", password])

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if r.returncode != 0:
            err_msg = (r.stderr or r.stdout or "").strip()
            log.warning(f"WiFi connect to {ssid!r} failed: {err_msg}")
            # Clean up the partial connection profile so the SSID list doesn't
            # accumulate broken entries on repeated wrong-password attempts.
            try:
                subprocess.run(["nmcli", "connection", "delete", ssid],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            # Translate the most common cases for the UI.
            hint = "Wrong password or network out of range" if "password" in err_msg.lower() or "secrets" in err_msg.lower() else err_msg
            return jsonify({"ok": False, "error": hint}), 400

        time.sleep(2)
        status = _get_wifi_status()
        return jsonify({
            "ok": True,
            "message": f"Connected to {ssid}",
            "status": status,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Connection attempt timed out (network out of range?)"}), 504
    except Exception as e:
        log.error(f"WiFi connect endpoint error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/update/status")
def api_update_status():
    """Report auto-update state: enabled, channel, last attempt, last log lines."""
    info = {
        "ok": True,
        "version": _read_version_file(),
        "auto_update_enabled": True,
        "channel": "stable",
        "repo": "yourname/castadhan-portable",
        "last_check": None,
        "last_result": None,
        "last_log": [],
    }
    # Read /etc/default/castadhan-update if present
    try:
        if os.path.exists("/etc/default/castadhan-update"):
            with open("/etc/default/castadhan-update") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("UPDATE_ENABLED="):
                        info["auto_update_enabled"] = line.split("=", 1)[1].strip('"').lower() == "true"
                    elif line.startswith("UPDATE_CHANNEL="):
                        info["channel"] = line.split("=", 1)[1].strip('"')
                    elif line.startswith("GITHUB_REPO="):
                        info["repo"] = line.split("=", 1)[1].strip('"')
    except Exception as e:
        log.warning(f"Could not read update config: {e}")
    # Read last 30 lines of update log
    try:
        if os.path.exists("/var/log/castadhan-update.log"):
            with open("/var/log/castadhan-update.log") as f:
                lines = f.readlines()[-30:]
                info["last_log"] = [l.rstrip() for l in lines]
                if lines:
                    info["last_check"] = lines[-1][:21] if len(lines[-1]) > 21 else None
    except Exception:
        pass
    return jsonify(info)

@app.route("/api/update/run", methods=["POST"])
def api_update_run():
    """Request a manual update (privilege-safe).

    B-Belgium-24: the web service runs with NoNewPrivileges=yes (see
    castadhan-portable.service), so it CANNOT sudo to start the updater — a
    fire-and-forget `sudo systemctl start ...` fails silently and the old code
    returned ok:true regardless, so the button looked like it worked when it
    never did. Instead the app writes a flag file inside its own ReadWritePaths
    (/opt/castadhan-portable), and a root-owned systemd path unit
    (castadhan-update.path) watches that flag and starts castadhan-update.service.
    This keeps the NoNewPrivileges hardening intact and actually works.
    """
    import subprocess
    flag = os.path.join(ROOT, ".update-requested")
    try:
        # Verify the privilege-safe trigger is installed + watching; otherwise the
        # flag would sit unread and the update would silently never run. Querying
        # a unit's state is read-only and needs no privileges.
        try:
            active = subprocess.run(
                ["systemctl", "is-active", "castadhan-update.path"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            active = "unknown"
        if active != "active":
            log.warning("Manual update requested but castadhan-update.path is '%s' (not active) — refusing to pretend it worked", active)
            return jsonify({
                "ok": False,
                "error": ("Manual update trigger is not installed on this Pi "
                          "(castadhan-update.path is not active). Install it once as "
                          "root (see deploy/setup-pi.sh), or wait for the nightly "
                          "auto-update."),
            }), 503
        with open(flag, "w") as f:
            f.write(datetime.now(utc).isoformat() + "\n")
        log.info("Manual update requested via web console (flag: %s)", flag)
        return jsonify({"ok": True, "message": "Update requested — the updater will run within a few seconds. Watch the update status/logs."})
    except Exception as e:
        log.error(f"Manual update request failed: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/update/config", methods=["POST"])
def api_update_config():
    """Update the auto-update settings (channel, enabled). Writes /etc/default/castadhan-update."""
    import subprocess
    try:
        body = request.get_json(force=True) or {}
        channel = body.get("channel", "stable")
        enabled = bool(body.get("enabled", True))
        if channel not in ("stable", "beta"):
            return jsonify({"ok": False, "error": "channel must be 'stable' or 'beta'"}), 400

        # Read existing, update keys, write back
        path = "/etc/default/castadhan-update"
        if not os.path.exists(path):
            return jsonify({"ok": False, "error": f"{path} not found — was the installer run?"}), 500

        with open(path) as f:
            lines = f.readlines()
        new_lines = []
        seen_channel = seen_enabled = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("UPDATE_CHANNEL="):
                new_lines.append(f'UPDATE_CHANNEL="{channel}"\n'); seen_channel = True
            elif stripped.startswith("UPDATE_ENABLED="):
                new_lines.append(f'UPDATE_ENABLED="{str(enabled).lower()}"\n'); seen_enabled = True
            else:
                new_lines.append(line)
        if not seen_channel:
            new_lines.append(f'UPDATE_CHANNEL="{channel}"\n')
        if not seen_enabled:
            new_lines.append(f'UPDATE_ENABLED="{str(enabled).lower()}"\n')

        # Atomic write via tmp file + sudo cp (we need root for /etc/default)
        tmp = "/tmp/.castadhan-update.defaults.new"
        with open(tmp, "w") as f:
            f.writelines(new_lines)
        subprocess.run(["sudo", "-n", "cp", tmp, path], check=True)
        os.remove(tmp)
        log.info(f"Auto-update config updated: channel={channel}, enabled={enabled}")
        return jsonify({"ok": True})
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": "sudo cp failed (sudoers entry missing?)"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/media/<path:rel>")
def media(rel):
    rel = rel.strip("/")
    safe_base = ROOT
    full = os.path.abspath(os.path.join(ROOT, rel))
    if not full.startswith(safe_base):
        abort(403)
    if not os.path.isfile(full):
        # Try compatible version
        compatible_full = full.replace('.mp3', '_compatible.mp3')
        if os.path.isfile(compatible_full):
            full = compatible_full
        else:
            abort(404)
    directory = os.path.dirname(full)
    filename = os.path.basename(full)
    return send_from_directory(directory, filename, as_attachment=False)


@app.route("/media/custom/<filename>")
def media_custom(filename):
    """v1.9.8: serve downloaded scheduled_audio files from /var/lib/castadhan/custom_audio/.
    Cast devices fetch from here at play time. Path-traversal safe: filename is
    validated against the safe character set and the resolved path must stay
    under _CUSTOM_AUDIO_DIR."""
    # Allow only [A-Za-z0-9_.-] in the filename — no slashes, no .., no nulls
    if not filename or not all(c.isalnum() or c in "._-" for c in filename):
        abort(400)
    full = os.path.realpath(os.path.join(_CUSTOM_AUDIO_DIR, filename))
    expected_root = os.path.realpath(_CUSTOM_AUDIO_DIR)
    if not full.startswith(expected_root + os.sep):
        abort(403)
    if not os.path.isfile(full):
        abort(404)
    return send_from_directory(_CUSTOM_AUDIO_DIR, filename, as_attachment=False)


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "now": now_local().isoformat(),
        "location": f"{CITY}, {COUNTRY}",
        "timezone": TZ,
        "uptime_seconds": (datetime.now(utc) - STARTUP_TIME).total_seconds(),
        "persistent_twilight": _twilight_cache["persistent_twilight_active"],
        "high_latitude_method": _twilight_cache["high_latitude_method"],
        "scheduler_running": _scheduler_started
    })

@app.route("/metrics")
def metrics():
    """Observability endpoint for monitoring"""
    try:
        with _cast_lock:
            num_speakers = len(_general_casts)
        
        # Use cached hijri to avoid API calls in metrics
        h_now = hijri_now_sunset_aware(now_local()) if '_hijri_cache' in globals() else None
        
        return jsonify({
            "uptime_seconds": (datetime.now(utc) - STARTUP_TIME).total_seconds(),
            "num_scheduled_jobs": len(sched.get_jobs()) if '_scheduler_started' in globals() and _scheduler_started else 0,
            "num_speakers": num_speakers,
            "ramadan_active": is_ramadan_today() if '_ramadan_cache' in globals() else False,
            "eid_takbeeraat_enabled": RULES.get('enable_eid_takbeeraat', True),
            "fajr_workday_cap": RULES.get('fajr_workday_cap', '07:00'),
            "hijri_now": h_now,
            "last_api_success": _last_successful_times["date"] is not None,
            "prayer_cache_date": str(_prayer_cache["date"]) if _prayer_cache["date"] else None,
            "persistent_twilight_active": _twilight_cache["persistent_twilight_active"],
            "high_latitude_method": _twilight_cache["high_latitude_method"],
            "twilight_last_scan": _twilight_cache["last_scan"].isoformat() if _twilight_cache["last_scan"] else None,
            "scheduler_running": _scheduler_started
        })
    except Exception as e:
        log.error(f"Error generating metrics: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------------- Enhanced Cast Discovery (True Portable) ----------------
_cast_lock = threading.Lock()
_general_casts = []  # All speakers in portable mode (no special loft)
_cast_browser = None
_speaker_playback_status = {}  # Track which speakers are currently playing

def _avahi_browse_cast_devices(timeout_s: float = 4.0):
    """v1.6.2 (Tue 26 May 2026): system-level mDNS discovery via `avahi-browse`.
    Returns list of {name, ip} dicts for every Google Cast device the system's
    avahi-daemon currently knows about.

    Why this exists: pychromecast's CastBrowser inside a long-running Flask
    process intermittently MISSES Cast devices that the system's mDNS layer
    sees just fine (B-Belgium-8 + the Slaap re-discovery problem on 26 May).
    Threading conflict between Werkzeug's request handlers and zeroconf's
    multicast listener — diagnosed in v1.2.0 incident report.

    This helper bypasses pychromecast's mDNS entirely and just shells out to
    avahi-browse, which we know works reliably. discover_casts() then folds
    the results into known_speakers.json + the direct-IP fallback path.

    Returns [] on any failure (avahi-browse missing, parse error, timeout).
    Never raises.
    """
    try:
        import subprocess as _subp
        result = _subp.run(
            ["avahi-browse", "-atrp", "-l", "--resolve"],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if result.returncode != 0:
            return []
        out = []
        for line in result.stdout.splitlines():
            # Resolved-record lines start with '='. Format (semicolon-separated):
            # =;<iface>;<proto>;<name>;_googlecast._tcp;local;<host>;<ip>;<port>;<txt>
            if not line.startswith("=;"):
                continue
            if "_googlecast._tcp" not in line:
                continue
            parts = line.split(";")
            if len(parts) < 9:
                continue
            proto = parts[2]
            if proto != "IPv4":
                continue  # only need one record per device; IPv4 is sufficient
            ip = parts[7]
            # Parse "fn=<friendly_name>" out of the TXT record (the last field)
            txt = parts[-1] if len(parts) >= 10 else ""
            import re as _re
            m = _re.search(r'"fn=([^"]+)"', txt)
            friendly = m.group(1) if m else parts[3]
            if not ip or not friendly:
                continue
            out.append({"name": friendly, "ip": ip})
        return out
    except Exception as e:
        log.debug(f"avahi-browse helper failed (non-fatal): {e}")
        return []

def _is_cast_port_alive(host: str, port: int = 8009, timeout: float = 1.5) -> bool:
    """O39 (v1.4.0, Tue 26 May 2026): quick TCP-probe a (host, port=8009) to
    determine whether a known speaker is currently reachable BEFORE handing
    it to pychromecast.get_chromecast_from_host().

    Why this exists:
        pychromecast.get_chromecast_from_host() succeeds immediately even when
        the speaker is offline, because it spawns a background socket_client
        that retries the connection every 5s on its own thread. Those retries
        accumulate, hold a `_cast_lock`, and end up blocking unrelated calls
        (most visibly /api/state which times out — that's exactly what
        happened with aunt's `Slaap` Nest Mini this morning when it was
        powered off).

    With this probe we treat a known-host IP that doesn't ACK on :8009 within
    a short timeout as "speaker offline for now, skip silently". The speaker
    will be picked up again on the next discovery cycle once it's back on
    the network — no manual `known_speakers.json` editing required.

    Returns True if the port answers within `timeout`, False otherwise.
    Never raises — designed to be safe to call from inside discovery loops.
    """
    import socket as _socket
    try:
        with _socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, _socket.timeout, _socket.gaierror):
        return False
    except Exception:
        return False

def _resolve_host(cast):
    """Resolve cast device host with timeout"""
    try:
        cast.wait(timeout=8)
    except Exception as e:
        log.warning(f"Timeout resolving host for {getattr(cast, 'name', 'unknown')}: {e}")

    host = None
    try:
        host = getattr(getattr(cast, "socket_client", None), "host", None) or getattr(cast, "host", None)
    except Exception:
        host = None
    return host or "unknown"

def _cast_info(cast):
    """Get cast device information including playback status"""
    if not cast:
        return None
    model = getattr(cast, "model_name", None)
    uuid = str(getattr(cast, "uuid", "")) if getattr(cast, "uuid", None) else None
    ip = _resolve_host(cast)
    
    # Check if currently playing
    is_playing = False
    try:
        mc = cast.media_controller
        if mc.status and mc.status.player_state == "PLAYING":
            is_playing = True
    except:
        pass
    
    return {
        "name": cast.name, 
        "ip": ip, 
        "uuid": uuid, 
        "model": model,
        "playing": is_playing,
        "connected": True
    }

def discover_casts():
    """Enhanced cast discovery — uses MODERN CastBrowser API.

    BUG FIX B-Belgium-2 (2026-05-25): the legacy `pychromecast.get_chromecasts(timeout=15)`
    has been DEPRECATED since June 2024 and in current pychromecast 14.x is unreliable —
    on aunt's Belgian network it consistently returned 0 even though `avahi-browse` and the
    modern `CastBrowser` API both find Huiskamer perfectly. **This caused Maghrib to silently
    fail to play** ("NO SPEAKERS AVAILABLE FOR ADHAN") on 25 May at 21:42 Brussels time.

    New behaviour: use `CastBrowser` with `known_hosts=[...]` (IPs of previously-discovered
    speakers, persisted to disk). The known_hosts hint dramatically improves first-discovery
    reliability — pychromecast doesn't have to wait for mDNS to fire, it queries the IPs
    directly. We also still do mDNS discovery (CastBrowser does it implicitly) so brand-new
    speakers will be picked up.

    Persistent known_hosts file: /opt/castadhan-portable/known_speakers.json
    Format: {"<friendly_name>": "<ip>", ...}
    Auto-updated every successful discovery — speakers found via mDNS get added.
    """
    global _general_casts, _cast_browser

    if shutdown_event.is_set():
        return

    log.info("Discovering Google Cast devices (CastBrowser + known_hosts)...")

    # Load known speaker IPs from disk (B-Belgium-2 fix: hint for CastBrowser)
    KNOWN_HOSTS_FILE = os.path.join(ROOT, "known_speakers.json")
    known_hosts = []
    known_map = {}
    try:
        if os.path.exists(KNOWN_HOSTS_FILE):
            with open(KNOWN_HOSTS_FILE) as f:
                known_map = json.load(f)
            known_hosts = list(set(known_map.values()))
            log.info(f"Loaded {len(known_hosts)} known speaker IPs: {known_hosts}")
    except Exception as e:
        log.warning(f"Could not load known_speakers.json: {e}")

    try:
        from pychromecast.discovery import CastBrowser, SimpleCastListener
        import time as _time

        # IMPORTANT: stop any previous browser before creating a new one.
        # Otherwise multiple CastBrowser instances accumulate, fighting over the
        # same multicast/zeroconf state, and discovery silently returns 0.
        # (This was the root cause of "manual works, Flask doesn't" on aunt's Pi.)
        if _cast_browser is not None:
            try:
                _cast_browser.stop_discovery()
                log.debug("Stopped previous CastBrowser before fresh discovery")
            except Exception as e:
                log.debug(f"Could not stop previous _cast_browser: {e}")
            _cast_browser = None

        listener = SimpleCastListener(lambda u, s: None, lambda u, s: None, lambda u, s: None)
        browser = CastBrowser(listener, known_hosts=known_hosts if known_hosts else None)
        _cast_browser = browser  # keep zeroconf alive for next time

        browser.start_discovery()
        # Wait long enough for both mDNS + known_hosts probes to complete.
        # 8 seconds is what we found reliable in manual testing on aunt's network.
        _time.sleep(8)

        include_token = (SPK.get("include_if_name_contains") or "").lower().strip()
        excludes = [s.lower() for s in (SPK.get("exclude_if_name_contains_any") or [])]

        found_general = []
        new_known_map = dict(known_map)  # start from existing so we don't lose entries
        found_names = set()  # track names we've already added (avoid duplicates)

        for uuid, info in browser.devices.items():
            if shutdown_event.is_set():
                break

            name = (info.friendly_name or "").strip()
            lname = name.lower()

            if include_token and include_token not in lname:
                continue
            if any(x in lname for x in excludes):
                log.info(f"Excluding device (matches exclusion rule): {name}")
                continue

            try:
                cast = pychromecast.get_chromecast_from_cast_info(info, browser.zc)
                _resolve_host(cast)
                found_general.append(cast)
                found_names.add(name)
                if info.host:
                    new_known_map[name] = info.host
            except Exception as e:
                log.warning(f"Failed to construct cast for {name}: {e}")

        # v1.6.2 (Tue 26 May 2026 — surfaced by user: "Slaap isn't being picked
        # up" after it came back online despite being visible to system mDNS):
        # Before deciding "missing from known_speakers means skip", query
        # `avahi-browse` (system-level mDNS) for any Cast device the OS knows
        # about. Any device the system sees but our CastBrowser missed gets
        # merged into known_map so the direct-IP fallback below can pick it
        # up — AND gets persisted to known_speakers.json so next discovery
        # is faster. Closes the "Slaap re-appears, we don't notice" failure.
        try:
            avahi_seen = _avahi_browse_cast_devices()
            for entry in avahi_seen:
                aname, aip = entry["name"], entry["ip"]
                if aname not in new_known_map:
                    log.info(f"📡 avahi-browse found Cast device not in known_speakers: '{aname}' @ {aip} — adding")
                    # Add ONLY to new_known_map (the working copy that will be
                    # persisted to disk). Leave the original known_map alone so
                    # the diff-check `new_known_map != known_map` below correctly
                    # detects this as a change and saves the file.
                    new_known_map[aname] = aip
        except Exception as e:
            log.debug(f"avahi-browse augmentation skipped: {e}")

        # FALLBACK (B-Belgium-2 v2 — 2026-05-25): CastBrowser inside Flask sometimes returns 0
        # even when manual standalone tests find the speaker. Diagnosed as threading conflict
        # with Werkzeug dev server's request handlers vs zeroconf. As a belt-and-braces:
        # for any known_speakers entry NOT picked up by CastBrowser, construct the Chromecast
        # directly from the cached IP using get_chromecast_from_host(). Bypasses zeroconf entirely.
        # v1.6.2: iterate new_known_map (not known_map) so any speakers freshly
        # added by the avahi-browse augmentation above are also tried via the
        # direct-IP fallback path. Without this, Slaap would be in known_map AND
        # known_speakers.json after the next discovery but wouldn't actually be
        # connected to during THIS discovery cycle.
        from pychromecast.discovery import HostServiceInfo
        for name, host in new_known_map.items():
            if name in found_names:
                continue   # already discovered via browser
            lname = name.lower()
            if include_token and include_token not in lname:
                continue
            if any(x in lname for x in excludes):
                continue
            # O39 (v1.4.0): probe TCP :8009 with a short timeout BEFORE
            # handing the host to pychromecast. If unreachable, skip silently;
            # otherwise pychromecast's socket_client spins up a forever-retrying
            # background thread that blocks downstream /api/state calls (see
            # aunt's `Slaap` Nest Mini incident, Tue 26 May 2026).
            if not _is_cast_port_alive(host):
                log.info(f"⊝ Skipping known speaker '{name}' @ {host} — TCP :8009 unreachable (speaker likely offline). Will retry on next discovery cycle.")
                continue
            try:
                # Use the lower-level get_chromecast_from_host with HostServiceInfo
                # Tuple form: (host, port, uuid, model_name, friendly_name)
                # This synthesizes the cast info without any mDNS at all.
                host_tuple = (host, 8009, name, "Google Cast", name)
                cast = pychromecast.get_chromecast_from_host(host_tuple)
                _resolve_host(cast)
                found_general.append(cast)
                found_names.add(name)
                log.info(f"✅ Fallback: constructed cast for '{name}' from cached IP {host}")
            except Exception as e:
                log.warning(f"Fallback construction failed for {name} @ {host}: {e}")

        # Save updated known_hosts to disk for next discovery
        try:
            if new_known_map != known_map:
                with open(KNOWN_HOSTS_FILE, "w") as f:
                    json.dump(new_known_map, f, indent=2)
                log.info(f"Updated known_speakers.json with {len(new_known_map)} entries")
        except Exception as e:
            log.warning(f"Could not save known_speakers.json: {e}")

        with _cast_lock:
            # v1.7.3 CRITICAL: disconnect the OLD cast objects before replacing
            # them. discover_casts() runs every 30 min + 3 min before each
            # prayer (prewarm). Each call creates brand-new Chromecast objects
            # via get_chromecast_from_host / get_chromecast_from_cast_info, each
            # spawning a socket_client thread. Without disconnecting the old set,
            # threads accumulate every cycle — the 28 May 89-thread leak that
            # starved APScheduler and killed Asr + Maghrib (B-Belgium-23).
            # Only disconnect old objects that are NOT carried over by identity.
            old_casts = list(_general_casts)
            new_ids = {id(c) for c in found_general}
            _general_casts = found_general
            for c in found_general:
                if c.name not in _speaker_playback_status:
                    _speaker_playback_status[c.name] = False
            stale_to_drop = [c for c in old_casts if id(c) not in new_ids]

        # Disconnect stale objects OUTSIDE the lock (B-Belgium-49 v1.9.7:
        # each call hard-capped to 3 sec so a single stuck cast can't hold
        # up the whole discovery cycle — that was the 9 Jun 2026 deadlock).
        for c in stale_to_drop:
            _disconnect_cast_bounded(c, timeout_seconds=3.0)
        if stale_to_drop:
            log.info(f"Disconnected {len(stale_to_drop)} stale cast object(s) to prevent thread leak")

        with _state_lock:
            for c in found_general:
                if c and c.name not in UI["enabled"]["speakers"]:
                    UI["enabled"]["speakers"][c.name] = True
                if c and c.name not in UI["volumes"]:
                    UI["volumes"][c.name] = UI["volumes"].get("__default", _default_volume)
            # B-Belgium-46 (v1.9.7): purge orphan IP-keyed entries that
            # don't match any discovered cast name. Older add-by-IP code
            # paths used to leave these behind; they're cosmetic for
            # playback (lookup is by cast.name) but confuse operators
            # diagnosing the state. Only delete keys that look like IPs
            # AND aren't a current cast name — never touch the canonical
            # name-keyed entries or __default.
            import re as _re_b46
            _IP_RE = _re_b46.compile(r'^\d{1,3}(\.\d{1,3}){3}$')
            _names = {c.name for c in found_general}
            for _k in list(UI["enabled"]["speakers"].keys()):
                if _IP_RE.match(_k) and _k not in _names:
                    del UI["enabled"]["speakers"][_k]
                    log.info(f"B-46 cleanup: removed orphan enabled.speakers['{_k}']")
            for _k in list(UI["volumes"].keys()):
                if _k == "__default":
                    continue
                if _IP_RE.match(_k) and _k not in _names:
                    del UI["volumes"][_k]
                    log.info(f"B-46 cleanup: removed orphan volumes['{_k}']")
            _save_state(UI)

        log.info("Discovered speakers: %s", [c.name for c in found_general])

    except Exception as e:
        log.error(f"Error during cast discovery: {e}", exc_info=True)

def _known_speaker_ip(name):
    """Look up a speaker's IP from known_speakers.json by friendly name."""
    try:
        ks_path = os.path.join(ROOT, "known_speakers.json")
        if os.path.exists(ks_path):
            with open(ks_path) as f:
                return json.load(f).get(name)
    except Exception:
        pass
    return None

def _cast_ip(cast):
    """Best-effort extract a cast's current IP from its various attributes."""
    for getter in (
        lambda: getattr(getattr(cast, "socket_client", None), "host", None),
        lambda: getattr(cast, "host", None),
        lambda: getattr(getattr(cast, "cast_info", None), "host", None),
    ):
        try:
            ip = getter()
            if ip:
                return ip
        except Exception:
            continue
    return None

def ensure_connected(cast):
    """Ensure cast device is connected — self-healing.

    v1.7.1 ROOT-CAUSE FIX (Thu 28 May 2026 — Eid al-Adha day 2 Fajr no-play):
    The previous version just called cast.wait(timeout=20). When the cached
    Chromecast object's socket had gone stale (which happens within hours of
    the last successful connection — e.g. overnight), wait() would sit in the
    "connecting" state for the full 20s and then play_media would fail with
    "Chromecast <ip>:8009 is connecting...". This is exactly why playback
    worked right after every restart (fresh sockets, when I tested) but
    failed at the next morning's prayer ~17h later (stale sockets).

    New behaviour: if the cached connection isn't live after a short wait,
    RECREATE the Chromecast from its IP (fresh socket) and swap it into
    _general_casts so subsequent plays use the live one. Returns a connected
    cast object — CALLERS MUST USE THE RETURN VALUE (it may be a new object).
    """
    name = getattr(cast, "name", "unknown")

    # 1. Try the existing object with a short wait
    try:
        cast.wait(timeout=8)
        sc = getattr(cast, "socket_client", None)
        if sc is not None and getattr(sc, "is_connected", False):
            return cast
    except Exception as e:
        log.warning(f"Stale/slow connection for {name}: {e}")

    # 2. Recreate from IP (the robust path)
    ip = _cast_ip(cast) or _known_speaker_ip(name)
    if not ip:
        log.error(f"Cannot recreate {name}: no IP available — playback may fail")
        return cast
    try:
        log.info(f"♻️  Recreating stale cast connection for {name} @ {ip}")
        # v1.7.3 CRITICAL: disconnect the stale object FIRST. Without this, its
        # socket_client thread keeps retrying forever. Recreating without
        # disconnecting was a THREAD LEAK — on 28 May the process hit 89 threads,
        # starving APScheduler's worker pool, so Asr + Maghrib jobs never ran
        # (B-Belgium-23). disconnect() stops the old socket_client thread.
        _disconnect_cast_quietly(cast)
        host_tuple = (ip, 8009, name, "Google Cast", name)
        fresh = pychromecast.get_chromecast_from_host(host_tuple)
        fresh.wait(timeout=12)
        # Swap the fresh object into _general_casts so future plays reuse it
        with _cast_lock:
            for i, c in enumerate(_general_casts):
                if getattr(c, "name", None) == name:
                    _general_casts[i] = fresh
                    break
        log.info(f"✅ Fresh connection established for {name} @ {ip}")
        return fresh
    except Exception as e:
        log.error(f"Failed to recreate connection for {name} @ {ip}: {e}")
        return cast

def _disconnect_cast_quietly(cast):
    """v1.7.3: stop a Chromecast object's socket_client thread so it doesn't
    leak. pychromecast spawns a background thread per Chromecast object that
    retries the connection forever; if we drop the object without calling
    disconnect(), the thread lives on. Accumulating these starves the
    APScheduler worker pool (the 28 May Asr/Maghrib no-play). Never raises."""
    if cast is None:
        return
    try:
        cast.disconnect(blocking=False)
    except Exception:
        try:
            sc = getattr(cast, "socket_client", None)
            if sc is not None:
                sc.disconnect()
        except Exception:
            pass


def _disconnect_cast_bounded(cast, timeout_seconds: float = 3.0) -> bool:
    """Disconnect a cast object with a hard wall-clock cap.

    B-Belgium-49 (v1.9.7): _disconnect_cast_quietly() can block forever on a
    half-open TCP socket after a network blip — observed on son-pi-haverfordwest
    on 2026-06-09. A network glitch left six Cast sockets in a half-open state;
    the next discover_casts() entered its stale-cast disconnect loop and never
    returned, holding APScheduler's only discover_casts slot for HOURS. Every
    subsequent rediscovery was skipped with "maximum number of running instances
    reached (1)" and any other code path needing _cast_lock or _state_lock hung
    too (the dashboard looked frozen).

    Fix: run the disconnect in a daemon thread and join it for at most
    `timeout_seconds`. If it doesn't complete we abandon the operation — the
    daemon thread keeps running in the background (will either complete on its
    own or die at process exit), and the discovery cycle moves on. Worst case
    we orphan a handful of threads per network blip; restart clears them.

    Returns True on clean disconnect, False on timeout."""
    import threading
    if cast is None:
        return True
    name = getattr(cast, "name", "?")
    t = threading.Thread(
        target=_disconnect_cast_quietly,
        args=(cast,),
        daemon=True,
        name=f"disconnect-{name}",
    )
    t.start()
    t.join(timeout=timeout_seconds)
    if t.is_alive():
        log.warning(
            f"⚠️  Disconnect of cast '{name}' exceeded {timeout_seconds}s — "
            f"abandoning. Background thread continues (will die at process "
            f"exit). B-Belgium-49 mitigation."
        )
        return False
    return True


# ═════════════════════════════════════════════════════════════════════════════
# v1.9.8 — SCHEDULED AUDIO (QURAN PROGRAMS) FRAMEWORK
# ─────────────────────────────────────────────────────────────────────────────
# Adds a generic "play any audio at any time" capability on top of the existing
# adhan scheduler. The first six entries (Baqarah, Yasin, Mulk, Kahf, Waaqi'ah,
# Sajdah) ship in the default config. Mustafa can add more (Mulk-style daily,
# Yasin-style weekly relative-to-prayer, etc.) by adding entries to the
# `scheduled_audio` block in config.yaml — no code change needed.
#
# Architecture (per PLAN_CUSTOM_SCHEDULED_AUDIO.md Sections 13/14/16/17/18/19):
#   - Persistent storage outside install dir (survives auto-update rm-and-replace)
#   - Config (user intent) in config.yaml vs runtime state in separate JSON
#   - Downloader supports HTTPS and validated file:// schemes
#   - Sequential single-worker queue (~3 MB peak memory during downloads)
#   - Atomic temp-file then rename pattern
#   - Self-healing when files vanish (re-download, no failure-count bump)
#   - Auto-disable after 3 consecutive download failures (with reset on toggle)
#   - Plays through existing _play_to_targets pipeline (respects scheduler_hold,
#     speaker enable/disable, writes play_history with structured audio_type
#     "scheduled:<id>")
#   - max_duration_minutes one-shot threading.Timer kills playback at the cap
#   - Surah Kahf is dual-write bridged: UI writes to BOTH this new block AND
#     the legacy surah_kahf hardcoded path. Full migration in v1.9.9.
# ═════════════════════════════════════════════════════════════════════════════

_CUSTOM_AUDIO_DIR = "/var/lib/castadhan/custom_audio"
_CUSTOM_AUDIO_STATE_FILE = "/var/lib/castadhan/custom_audio_state.json"
_CUSTOM_AUDIO_STAGING_DIRS = ("/home/farley/staging", "/var/lib/castadhan/custom_audio")
_CUSTOM_AUDIO_DOWNLOAD_TIMEOUT_SEC = 600   # 10 min cap on any single download
_CUSTOM_AUDIO_CATCHUP_POLL_SEC = 5         # how often to poll while waiting for catch-up
_CUSTOM_AUDIO_CATCHUP_MAX_SEC = 120        # max total wait before skipping a fire
_CUSTOM_AUDIO_FAIL_LIMIT = 3               # consecutive failures before auto-disable

_custom_audio_state_lock = threading.RLock()
_custom_audio_download_queue: queue.Queue = queue.Queue()
_custom_audio_active_timers: Dict[str, threading.Timer] = {}  # id → max_duration timer

# Tracks ongoing background downloads so multiple callers don't enqueue the same
# entry twice. Holds the threading.Event a fire-time check can wait on.
_custom_audio_download_events: Dict[str, threading.Event] = {}
_custom_audio_download_events_lock = threading.Lock()


def _ensure_custom_audio_dirs():
    """Create /var/lib/castadhan/custom_audio + state JSON if they don't exist.
    Defensive — setup-pi.sh and castadhan-update.sh both do this, but if either
    missed (older Pi upgrading), this catches up at service start."""
    try:
        os.makedirs(_CUSTOM_AUDIO_DIR, exist_ok=True)
        if not os.path.isfile(_CUSTOM_AUDIO_STATE_FILE):
            with open(_CUSTOM_AUDIO_STATE_FILE, "w") as f:
                f.write("{}\n")
    except PermissionError as e:
        log.warning(f"Could not create {_CUSTOM_AUDIO_DIR}: {e}. Will retry on next download.")
    except Exception as e:
        log.error(f"_ensure_custom_audio_dirs failed: {e}")


def _load_custom_audio_state() -> dict:
    """Read the state JSON, returning {} on any failure (file missing, invalid)."""
    try:
        with open(_CUSTOM_AUDIO_STATE_FILE) as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"_load_custom_audio_state: corrupted state file ({e}), returning empty")
        return {}


def _save_custom_audio_state(state: dict):
    """Atomic write of the state JSON: write to temp file, rename over the live file.
    Held under a lock so concurrent updates (download worker + scheduler + dashboard
    save) serialise instead of clobbering."""
    with _custom_audio_state_lock:
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".custom_audio_state.",
                suffix=".tmp",
                dir=os.path.dirname(_CUSTOM_AUDIO_STATE_FILE) or "."
            )
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(state, f, indent=2, sort_keys=True)
                os.replace(tmp_path, _CUSTOM_AUDIO_STATE_FILE)
            except Exception:
                try: os.remove(tmp_path)
                except Exception: pass
                raise
        except Exception as e:
            log.error(f"_save_custom_audio_state failed: {e}")


def _get_custom_audio_state_entry(audio_id: str) -> dict:
    """Return the runtime state dict for one entry, with sensible defaults."""
    defaults = {
        "download_status": "NOT_STARTED",   # NOT_STARTED / IN_PROGRESS / COMPLETED / FAILED / FAILED_DISABLED
        "consecutive_failures": 0,
        "last_error": None,
        "last_played_at": None,
        "last_play_status": None,
        "skip_until_date": None,            # ISO date string (YYYY-MM-DD)
        "file_size_bytes": 0,
    }
    state = _load_custom_audio_state()
    entry = state.get(audio_id) or {}
    out = {**defaults, **entry}
    return out


def _update_custom_audio_state(audio_id: str, **updates):
    """Partial update of one entry's runtime state. Writes atomically."""
    with _custom_audio_state_lock:
        state = _load_custom_audio_state()
        entry = state.get(audio_id) or {}
        entry.update(updates)
        state[audio_id] = entry
        _save_custom_audio_state(state)


def _custom_audio_file_path(audio_id: str) -> str:
    """Canonical local path for a scheduled_audio entry's downloaded mp3."""
    return os.path.join(_CUSTOM_AUDIO_DIR, f"{audio_id}.mp3")


def _classify_audio_url(url: str) -> Tuple[str, Optional[str]]:
    """Return (kind, source) where kind is one of:
       - "http"     : standard HTTP/HTTPS download
       - "file"     : local-file copy from validated staging dir
       - "bundled"  : Surah Kahf — use the shipped audio/surah_kahf.mp3
       - "invalid"  : malformed or disallowed; source is the rejection reason
       - "empty"    : empty URL (entry not yet configured)
    source is the actual URL string (http kind), the resolved absolute path
    (file kind), or the reason string (invalid kind)."""
    if not url:
        return ("empty", None)
    if url == "bundled":
        return ("bundled", None)
    try:
        parsed = urlparse(url)
    except Exception as e:
        return ("invalid", f"malformed URL: {e}")
    if parsed.scheme in ("http", "https"):
        return ("http", url)
    if parsed.scheme == "file":
        local_path = parsed.path
        # Allow-list check — must live under one of the allowed staging dirs
        abs_path = os.path.realpath(local_path)
        for allowed in _CUSTOM_AUDIO_STAGING_DIRS:
            allowed_real = os.path.realpath(allowed)
            if abs_path.startswith(allowed_real + os.sep) or abs_path == allowed_real:
                if not os.path.isfile(abs_path):
                    return ("invalid", f"file:// path does not exist: {abs_path}")
                if not abs_path.lower().endswith(".mp3"):
                    return ("invalid", f"only .mp3 allowed, got: {abs_path}")
                return ("file", abs_path)
        return ("invalid",
                f"file:// path not in allowed staging dir: {abs_path}. "
                f"Allowed: {', '.join(_CUSTOM_AUDIO_STAGING_DIRS)}")
    return ("invalid", f"unsupported scheme: {parsed.scheme!r}")


def _custom_audio_download_worker():
    """Single-threaded worker that processes the download queue serially.
    Sequential to keep peak memory low on Pi 3B+ AND to avoid competing for
    limited home-WiFi bandwidth. Runs as a daemon thread."""
    log.info("Custom audio download worker started")
    while not shutdown_event.is_set():
        try:
            try:
                job = _custom_audio_download_queue.get(timeout=2.0)
            except queue.Empty:
                continue
            audio_id, audio_url = job
            try:
                _do_custom_audio_download(audio_id, audio_url)
            except Exception as e:
                log.error(f"Download worker crashed on {audio_id}: {e}", exc_info=True)
            finally:
                _custom_audio_download_queue.task_done()
                # Signal anyone waiting on this download
                with _custom_audio_download_events_lock:
                    ev = _custom_audio_download_events.pop(audio_id, None)
                if ev is not None:
                    ev.set()
        except Exception as e:
            log.error(f"Download worker loop error: {e}", exc_info=True)


def _do_custom_audio_download(audio_id: str, audio_url: str):
    """Perform one download/copy. Atomic via temp-file + rename. Updates
    state JSON on every state transition. Never propagates exceptions —
    failures are recorded as FAILED + consecutive_failures incremented."""
    kind, source = _classify_audio_url(audio_url)
    if kind == "empty":
        log.info(f"_do_custom_audio_download({audio_id}): no URL configured, skipping")
        return
    if kind == "invalid":
        log.warning(f"_do_custom_audio_download({audio_id}): invalid URL — {source}")
        _bump_failure(audio_id, f"invalid URL: {source}")
        return
    if kind == "bundled":
        # Bundled audio lives in the install dir under audio/<id>.mp3.
        # Symlink or copy it into custom_audio so the standard playback
        # path can find it without special-casing.
        src = os.path.join(ROOT, "audio", f"{audio_id}.mp3")
        if not os.path.isfile(src):
            _bump_failure(audio_id, f"bundled audio missing: {src}")
            return
        dst = _custom_audio_file_path(audio_id)
        try:
            shutil.copy2(src, dst)
            size = os.path.getsize(dst)
            _update_custom_audio_state(
                audio_id,
                download_status="COMPLETED",
                consecutive_failures=0,
                last_error=None,
                file_size_bytes=size,
            )
            log.info(f"Bundled audio ready: {audio_id} ({size:,} bytes)")
        except Exception as e:
            _bump_failure(audio_id, f"bundled copy failed: {e}")
        return

    # http or file — both end up as a copy into custom_audio dir
    dst = _custom_audio_file_path(audio_id)
    tmp = dst + ".tmp"
    _update_custom_audio_state(audio_id, download_status="IN_PROGRESS", last_error=None)
    try:
        if kind == "file":
            # Local file — straight copy + size verify
            shutil.copy2(source, tmp)
        else:
            # HTTP/HTTPS — stream in 8 KB chunks (low peak memory on Pi 3B+)
            req = urllib.request.Request(source, headers={"User-Agent": "castadhan/1.9.8"})
            with urllib.request.urlopen(req, timeout=_CUSTOM_AUDIO_DOWNLOAD_TIMEOUT_SEC) as resp:
                if resp.status != 200:
                    raise IOError(f"HTTP {resp.status} from {source}")
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        if shutdown_event.is_set():
                            raise IOError("shutdown during download")

        # Verify what we got
        size = os.path.getsize(tmp)
        if size < 1024:
            raise IOError(f"downloaded file suspiciously small ({size} bytes)")

        # Atomic rename into place
        os.replace(tmp, dst)
        _update_custom_audio_state(
            audio_id,
            download_status="COMPLETED",
            consecutive_failures=0,
            last_error=None,
            file_size_bytes=size,
        )
        log.info(f"Downloaded {audio_id}: {size:,} bytes from {source}")
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        _bump_failure(audio_id, str(e))


def _bump_failure(audio_id: str, error_msg: str):
    """Record a download failure. Auto-disables the schedule after 3 strikes
    (writes config.yaml back with enabled=false). The user must fix the URL
    and toggle ON again to retry — which also resets the failure counter."""
    log.error(f"Custom audio download failed for {audio_id}: {error_msg}")
    state = _load_custom_audio_state()
    entry = state.get(audio_id) or {}
    fails = int(entry.get("consecutive_failures", 0)) + 1
    auto_disable = fails >= _CUSTOM_AUDIO_FAIL_LIMIT
    entry.update({
        "consecutive_failures": fails,
        "last_error": error_msg,
        "download_status": "FAILED_DISABLED" if auto_disable else "FAILED",
    })
    state[audio_id] = entry
    _save_custom_audio_state(state)
    if auto_disable:
        log.critical(
            f"⚠️  Auto-disabling scheduled_audio.{audio_id} after "
            f"{_CUSTOM_AUDIO_FAIL_LIMIT} consecutive download failures. "
            f"Fix the audio_url and re-enable to retry."
        )
        try:
            with _state_lock:
                sa = CFG.setdefault("scheduled_audio", {})
                if audio_id in sa:
                    sa[audio_id]["enabled"] = False
                    _save_config_yaml(CFG)
        except Exception as e:
            log.error(f"Could not auto-disable in config.yaml: {e}")


def _enqueue_custom_audio_download(audio_id: str, audio_url: str) -> threading.Event:
    """Add a download job to the queue. Returns an Event that callers can wait
    on if they want to catch up on a missing-at-fire-time file. Idempotent —
    if a download is already queued/running for this id, returns the existing
    Event instead of enqueuing twice."""
    with _custom_audio_download_events_lock:
        ev = _custom_audio_download_events.get(audio_id)
        if ev is not None:
            return ev
        ev = threading.Event()
        _custom_audio_download_events[audio_id] = ev
    _custom_audio_download_queue.put((audio_id, audio_url))
    return ev


def _save_config_yaml(config_dict):
    """Re-serialise config dict back to config.yaml. Used by the auto-disable
    path and by dashboard save operations. Atomic via temp + rename."""
    try:
        import yaml as _yaml
        cfg_path = os.path.join(ROOT, "config.yaml")
        tmp_path = cfg_path + ".tmp"
        with open(tmp_path, "w") as f:
            _yaml.safe_dump(config_dict, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, cfg_path)
    except Exception as e:
        log.error(f"_save_config_yaml failed: {e}")


def _start_custom_audio_download_worker():
    """Start the single-threaded download worker. Called once at app startup."""
    _ensure_custom_audio_dirs()
    t = threading.Thread(
        target=_custom_audio_download_worker,
        name="custom_audio_download_worker",
        daemon=True,
    )
    t.start()


def _reschedule_one_custom_audio(audio_id: str):
    """v1.9.9 (schedule-churn fix): re-register ONE scheduled_audio job after a
    dashboard edit, instead of re-running the entire schedule_today() rebuild.
    masood's journal on 12 Jun showed 8 full scheduler rebuilds in 17 minutes
    of card-clicking — harmless (replace_existing=True) but each rebuild also
    re-ran discover_casts and removed/re-added every prayer job. This touches
    only the edited entry."""
    job_id = f"scheduled_audio_{audio_id}"
    try:
        sched.remove_job(job_id)
    except Exception:
        pass
    with _state_lock:
        entry = (CFG.get("scheduled_audio") or {}).get(audio_id)
    if not entry or not entry.get("enabled"):
        return
    try:
        times = get_times_for(date.today())
        fire_dt = _compute_custom_audio_run_time(entry, times, date.today())
        if fire_dt and fire_dt > now_local():
            sched.add_job(_play_custom_audio, DateTrigger(run_date=fire_dt),
                          args=[audio_id], id=job_id, replace_existing=True)
            log.info(f"Rescheduled {entry.get('name', audio_id)} @ {fire_dt:%H:%M:%S}")
    except Exception as e:
        log.error(f"_reschedule_one_custom_audio({audio_id}): {e}")


def _migrate_kahf_to_scheduled_audio():
    """v1.9.9: complete the Kahf bridge migration promised in the v1.9.8 plan.

    History: legacy Kahf was a Friday SUBSTITUTION inside play_morning_dhikr
    (07:00, instead of dhikr). v1.9.8 shipped scheduled_audio.surah_kahf with
    a WRONG default (Dhuhr-60) which dual-fired alongside the legacy path —
    masood heard Kahf 3 times on 12 Jun (hotfixed in v1.9.8.2 by skipping the
    new path). v1.9.9 removes the legacy path entirely, so this migration:

      1. Rewrites any surah_kahf entry still carrying the bad v1.9.8 default
         (relative_to_prayer / Dhuhr / -60) to fixed 07:00 Friday — the time
         every fleet family is actually used to. User-customised entries are
         left alone.
      2. Populates target_speakers from known_speakers.json (legacy played on
         all speakers; B-61 enforcement would otherwise silence Kahf because
         the shipped default was []). Falls back to the ["__all__"] sentinel
         when no speakers are known yet.

    Idempotent; persists to config.yaml only when something changed."""
    try:
        with _state_lock:
            kahf = (CFG.get("scheduled_audio") or {}).get("surah_kahf")
            if not kahf:
                return
            changed = False
            if (kahf.get("trigger_type") == "relative_to_prayer"
                    and str(kahf.get("relative_prayer_anchor", "")).lower() == "dhuhr"
                    and int(kahf.get("offset_minutes") or 0) == -60):
                kahf["trigger_type"] = "fixed"
                kahf["play_time"] = "07:00"
                kahf["relative_prayer_anchor"] = "none"
                kahf["offset_minutes"] = 0
                kahf["days"] = [4]
                changed = True
                log.info("Kahf migration: rewrote v1.9.8 default (Dhuhr-60) "
                         "to legacy-equivalent fixed 07:00 Friday")
            if not kahf.get("target_speakers"):
                names = []
                try:
                    with open(os.path.join(ROOT, "known_speakers.json")) as f:
                        names = list((json.load(f) or {}).keys())
                except Exception:
                    pass
                kahf["target_speakers"] = names if names else ["__all__"]
                changed = True
                log.info(f"Kahf migration: target_speakers ← "
                         f"{kahf['target_speakers']}")
            if changed:
                _save_config_yaml(CFG)
    except Exception as e:
        log.error(f"Kahf migration failed (legacy behaviour may be affected): {e}")


def _play_custom_audio(audio_id: str, force: bool = False):
    """Fire one scheduled_audio entry. Called by APScheduler at the computed
    absolute time, OR by the dashboard's "Play Now" button (force=True).
    force=True ignores skip_until_date but still respects scheduler_hold."""
    if shutdown_event.is_set():
        return

    # Look up the entry's config
    with _state_lock:
        entry = (CFG.get("scheduled_audio") or {}).get(audio_id)
    if not entry:
        log.warning(f"_play_custom_audio({audio_id}): entry not found in config")
        return

    name = entry.get("name", audio_id)
    structured_type = f"scheduled:{audio_id}"

    # Honour scheduler_hold (Emergency Stop)
    if _scheduler_held():
        log.warning(f"⏸️  {name} skipped: scheduler is on hold")
        _log_play(structured_type, audio_id, "SKIPPED_HOLD", speakers_count=0)
        return

    # Honour skip_until_date unless forced
    state = _get_custom_audio_state_entry(audio_id)
    if not force and state.get("skip_until_date"):
        try:
            skip_until = date.fromisoformat(state["skip_until_date"])
            if date.today() <= skip_until:
                log.info(f"{name} skipped: skip_until_date={state['skip_until_date']}")
                _log_play(structured_type, audio_id, "SKIPPED_USER", speakers_count=0)
                # Clear the skip flag once the date passes
                if date.today() == skip_until:
                    _update_custom_audio_state(audio_id, skip_until_date=None)
                return
        except ValueError:
            pass  # bad date string — ignore and play anyway

    # File check + self-healing re-download if missing
    local_path = _custom_audio_file_path(audio_id)
    if not os.path.isfile(local_path):
        audio_url = entry.get("audio_url") or ""
        if not audio_url:
            log.warning(f"{name}: no audio_url configured, cannot play")
            _log_play(structured_type, audio_id, "FAIL", speakers_count=0, error="no audio_url")
            return
        log.warning(f"{name}: file missing at fire time, kicking off catch-up download")
        ev = _enqueue_custom_audio_download(audio_id, audio_url)
        # Poll up to CATCHUP_MAX_SEC for the download to complete
        waited = 0
        while waited < _CUSTOM_AUDIO_CATCHUP_MAX_SEC:
            if ev.wait(_CUSTOM_AUDIO_CATCHUP_POLL_SEC):
                break
            waited += _CUSTOM_AUDIO_CATCHUP_POLL_SEC
        if not os.path.isfile(local_path):
            log.warning(f"{name}: download did not complete within {_CUSTOM_AUDIO_CATCHUP_MAX_SEC}s, skipping")
            _log_play(structured_type, audio_id, "DOWNLOAD_IN_PROGRESS", speakers_count=0)
            return
        log.info(f"{name}: catch-up download completed in {waited}s, playing now")

    # Play through the normal pipeline so routing + volume policy + history all apply
    log.info(f"🕌 Playing {name} (audio_id={audio_id})")

    casts = _ensure_speakers(structured_type, audio_id)
    if not casts:
        return  # _ensure_speakers already logged

    # v1.9.9 (B-Belgium-61): honour target_speakers. v1.9.8 shipped the
    # per-card speaker checkboxes in the UI but the backend ignored them —
    # on a multi-speaker Pi an enabled surah would play EVERYWHERE including
    # children's bedrooms, defeating the safe-defaults design entirely.
    # Semantics: [] = nothing selected = don't play (matches the UI warning
    # banner); ["__all__"] = play on every discovered speaker (used by the
    # Kahf migration to preserve legacy fleet behaviour).
    targets = entry.get("target_speakers") or []
    if "__all__" not in targets:
        casts = [c for c in casts if c.name in targets]
    if not casts:
        log.warning(f"{name}: no target speakers selected/online — not playing")
        _log_play(structured_type, audio_id, "NO_SPEAKERS_SELECTED", speakers_count=0)
        return

    play_logged = False
    try:
        url = _custom_audio_local_url(audio_id)
        played = 0
        for cast in casts:
            try:
                play_on_cast(cast, url, _speaker_volume(cast.name),
                             structured_type, audio_id)
                played += 1
            except Exception as e:
                log.error(f"play_on_cast failed for {cast.name}: {e}")
        _log_play(structured_type, audio_id,
                  "PASS" if played else "NO_SPEAKERS",
                  speakers_count=played)
        if played:
            # v1.9.9: async verification (10s) — scheduled:* types qualify
            _verify_playback_async(casts, structured_type, audio_id)
        play_logged = True   # v1.9.8.2: gate the outer except so we don't
                              # write a duplicate FAIL entry if the play
                              # itself succeeded and a later side-effect
                              # (_update_custom_audio_state / _arm_max_duration_timer)
                              # raises. Without this, every successful play got
                              # a phantom FAIL speakers=0 partner in play_history.
        if played:
            try:
                _update_custom_audio_state(
                    audio_id,
                    last_played_at=datetime.now(utc).isoformat(),
                    last_play_status="PASS",
                )
            except Exception as e:
                log.error(f"_update_custom_audio_state({audio_id}) post-play error: {e}")
            try:
                # Arm the max-duration safety timer
                max_min = int(entry.get("max_duration_minutes", 60))
                _arm_max_duration_timer(audio_id, max_min, casts)
            except Exception as e:
                log.error(f"_arm_max_duration_timer({audio_id}) error: {e}")
    except Exception as e:
        log.error(f"_play_custom_audio({audio_id}) error: {e}", exc_info=True)
        if not play_logged:
            _log_play(structured_type, audio_id, "FAIL", speakers_count=0, error=e)


def _custom_audio_local_url(audio_id: str) -> str:
    """Build the URL the Cast device will fetch — served by /media/custom/<id>.mp3.

    B-Belgium-64: when cast_media_from_internet is set, hand the speaker the
    scheduled-audio's PUBLIC source URL (the surahs' release asset) — or, for the
    bundled Surah Kahf, its GitHub raw URL — so it streams over the internet
    instead of fetching from the Pi (fixes router AP / client isolation)."""
    if RULES.get("cast_media_from_internet"):
        ent = (CFG.get("scheduled_audio") or {}).get(audio_id) or {}
        au = (ent.get("audio_url") or "").strip()
        if au.startswith("http://") or au.startswith("https://"):
            return au
        bp = (AUDIO.get(audio_id) or "").lstrip("/")   # 'bundled' (Surah Kahf) lives in the repo audio/ tree
        if bp and os.path.isfile(os.path.join(ROOT, bp)):
            return "https://raw.githubusercontent.com/sabreenaapa-coder/castadhan-portable/main/" + quote(bp)
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    return f"http://{ip}:{PORT}/media/custom/{audio_id}.mp3"


def _arm_max_duration_timer(audio_id: str, max_minutes: int, casts: list):
    """Schedule a one-shot stop of these casts after max_minutes. Used as a
    safety net against stream-drop-leaving-cast-in-zombie-playing state."""
    # Cancel any existing timer for this id (overlapping fire)
    existing = _custom_audio_active_timers.pop(audio_id, None)
    if existing is not None:
        try: existing.cancel()
        except Exception: pass

    def stop_callback():
        log.info(f"Max duration ({max_minutes} min) reached for {audio_id} — stopping casts")
        for cast in casts:
            try:
                cast.media_controller.stop()
            except Exception as e:
                log.debug(f"Stop {cast.name} on max-duration: {e}")
        _custom_audio_active_timers.pop(audio_id, None)

    timer = threading.Timer(max_minutes * 60, stop_callback)
    timer.daemon = True
    timer.start()
    _custom_audio_active_timers[audio_id] = timer


def _quiet_test_custom_audio(audio_id: str):
    """10-second test — plays then stops cleanly. Triggered by the dashboard's
    "Quiet test 10s" button. Avoids the volume-restoration mistake from
    9 Jun by using stop() rather than vol=0."""
    if shutdown_event.is_set():
        return
    with _state_lock:
        entry = (CFG.get("scheduled_audio") or {}).get(audio_id)
    if not entry:
        return
    local_path = _custom_audio_file_path(audio_id)
    if not os.path.isfile(local_path):
        log.warning(f"quiet_test: {audio_id} not downloaded yet")
        _log_play(f"quiet_test:{audio_id}", audio_id, "FAIL",
                  speakers_count=0, error="file not downloaded")
        return
    casts = _ensure_speakers(f"quiet_test:{audio_id}", audio_id)
    if not casts:
        return

    # v1.9.9 (B-Belgium-61): quiet test targets the same speakers the real
    # schedule would — so the test actually tests the configured behaviour.
    targets = entry.get("target_speakers") or []
    if "__all__" not in targets:
        casts = [c for c in casts if c.name in targets]
    if not casts:
        log.warning(f"quiet_test {audio_id}: no target speakers selected")
        _log_play(f"quiet_test:{audio_id}", audio_id, "NO_SPEAKERS_SELECTED",
                  speakers_count=0)
        return

    url = _custom_audio_local_url(audio_id)
    played = 0
    for cast in casts:
        try:
            play_on_cast(cast, url, _speaker_volume(cast.name),
                         f"quiet_test:{audio_id}", audio_id)
            played += 1
        except Exception as e:
            log.error(f"quiet_test play failed for {cast.name}: {e}")

    def stop_after_10s():
        log.info(f"Quiet test 10s elapsed for {audio_id}, stopping")
        for cast in casts:
            try: cast.media_controller.stop()
            except Exception: pass
        _log_play(f"quiet_test:{audio_id}", audio_id, "QUIET_TEST_COMPLETED",
                  speakers_count=played)

    t = threading.Timer(10.0, stop_after_10s)
    t.daemon = True
    t.start()
    log.info(f"Quiet test started for {audio_id}, will stop in 10s")


# ═════════════════════════════════════════════════════════════════════════════
# End v1.9.8 scheduled_audio backend (continued in scheduler integration
# section near schedule_today() and API endpoints near other @app.routes)
# ═════════════════════════════════════════════════════════════════════════════


def local_media_url(relpath: str) -> str:
    """Get local media URL with better IP detection.

    B-Belgium-64: when cast_media_from_internet is set (opt-in, per box), hand the
    Cast device the PUBLIC https URL on GitHub for repo-bundled audio instead of
    the Pi's local server — so the speaker streams the file over the internet and
    never needs to reach the Pi. Fixes boxes whose router blocks the speaker->Pi
    path (AP / client isolation). Only used for files present in the repo tree."""
    rel = relpath.lstrip("/")

    if RULES.get("cast_media_from_internet") and os.path.isfile(os.path.join(ROOT, rel)):
        return "https://raw.githubusercontent.com/sabreenaapa-coder/castadhan-portable/main/" + quote(rel)

    ip = "127.0.0.1"
    try:
        # Try to get the actual network IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass

    # Check if compatible version should be used
    if not relpath.endswith('_compatible.mp3'):
        compatible_rel = rel.replace('.mp3', '_compatible.mp3')
        compatible_path = os.path.join(ROOT, compatible_rel)
        if os.path.isfile(compatible_path):
            rel = compatible_rel

    return f"http://{ip}:{PORT}/media/{quote(rel)}"

def _speaker_enabled(name: str) -> bool:
    """Check if speaker is enabled"""
    with _state_lock:
        if not UI["enabled"]["global"]:
            return False
        return UI["enabled"]["speakers"].get(name, True)

def _speaker_volume(name: str) -> float:
    """Get speaker volume as float (0.0-1.0)"""
    with _state_lock:
        v = UI["volumes"].get(name, UI["volumes"].get("__default", _default_volume))
    v = max(0, min(int(v), 100))
    return v / 100.0

def _should_play_on_speaker(speaker_name: str, audio_type: str) -> bool:
    """Check if a specific audio type should play on this speaker based on routing rules"""
    # First check global enable
    if not _speaker_enabled(speaker_name):
        return False
    
    # Special case for suhoor exclusions
    if audio_type == "suhoor_alarm":
        exclude_names = [name.lower().strip() for name in SPK.get('suhoor_exclude_names', [])]
        if speaker_name.lower().strip() in exclude_names:
            return False
    
    # Check audio routing configuration
    routing = get_speaker_audio_routing(speaker_name)
    return routing.get(audio_type, True)

def _all_casts():
    """Get all cast devices (no loft in portable mode)"""
    with _cast_lock:
        return list(_general_casts)

def _cast_by_name(name: str):
    """Find cast device by name"""
    for c in _all_casts():
        if c.name == name:
            return c
    return None

def emergency_stop_all():
    """Emergency stop all audio on all speakers - called from UI.

    C-4 (v1.5.0, Tue 26 May 2026): pre-v1.5.0, this just delegated to
    stop_all_audio() — making it functionally identical to the "Stop All"
    button next to it. The UI presented two visually-distinct buttons (one
    red, one big-red) with no meaningful behavioural difference. For the
    "6am snoozer" persona who's frantically trying to silence a misfiring
    system, that's user-hostile.

    Post-v1.5.0:
      - "Stop All" (api/test/stop) just stops current playback.
      - "Emergency Stop" stops playback PLUS pauses the scheduler for 60
        minutes — so a misfiring scheduled job that you just silenced won't
        immediately re-fire 30 seconds later. The user can re-enable via the
        same button or via /api/scheduler/resume.

    The pause is persisted to disk so a service restart doesn't lose it.
    """
    log.warning("🚨 EMERGENCY STOP ACTIVATED - Stopping all audio + scheduler hold 60 min")
    stopped = stop_all_audio()
    try:
        _set_scheduler_hold(minutes=60, reason="emergency_stop")
    except Exception as e:
        log.error(f"Could not enact scheduler hold during emergency stop: {e}")
    return stopped

_SCHEDULER_HOLD_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_hold.json")
_scheduler_hold = {"until": None, "reason": None}

def _set_scheduler_hold(minutes: int, reason: str):
    """Pause the scheduler for `minutes` minutes. Persists to disk so a
    service restart preserves the hold (otherwise an emergency stop would be
    undone by any subsequent restart, defeating its purpose)."""
    global _scheduler_hold
    until = (datetime.now(utc) + timedelta(minutes=minutes)).isoformat()
    _scheduler_hold = {"until": until, "reason": reason, "set_at_local": now_local().isoformat()}
    try:
        with open(_SCHEDULER_HOLD_FILE, "w") as f:
            json.dump(_scheduler_hold, f)
    except Exception as e:
        log.error(f"Could not persist scheduler hold: {e}")
    log.warning(f"⏸️  Scheduler held until {until} (reason: {reason})")

def _clear_scheduler_hold():
    """Lift any active scheduler hold."""
    global _scheduler_hold
    _scheduler_hold = {"until": None, "reason": None}
    try:
        if os.path.exists(_SCHEDULER_HOLD_FILE):
            os.remove(_SCHEDULER_HOLD_FILE)
    except Exception as e:
        log.error(f"Could not remove scheduler hold file: {e}")
    log.info("▶  Scheduler hold cleared")

def _load_scheduler_hold():
    """Read any persisted hold from disk on startup."""
    global _scheduler_hold
    try:
        if os.path.exists(_SCHEDULER_HOLD_FILE):
            with open(_SCHEDULER_HOLD_FILE) as f:
                _scheduler_hold = json.load(f)
            if _scheduler_hold.get("until"):
                until = datetime.fromisoformat(_scheduler_hold["until"].replace("Z", "+00:00"))
                if until <= datetime.now(utc):
                    _clear_scheduler_hold()
                else:
                    log.warning(f"📋 Restored scheduler hold from disk; in effect until {_scheduler_hold['until']}")
    except Exception as e:
        log.error(f"Could not load scheduler hold from disk: {e}")
        _scheduler_hold = {"until": None, "reason": None}

def _scheduler_held() -> bool:
    """Return True if a scheduler hold is currently active (i.e. all jobs
    should silently skip until the hold expires)."""
    u = _scheduler_hold.get("until")
    if not u:
        return False
    try:
        until = datetime.fromisoformat(u.replace("Z", "+00:00"))
        if until <= datetime.now(utc):
            _clear_scheduler_hold()
            return False
        return True
    except Exception:
        return False

def stop_all_audio():
    """Stop playback on all known speakers.

    BUG FIX (2026-05-25 — aunt's house, Belgium): previously this just iterated _general_casts
    and called media_controller.stop(). Two problems hit us during ethernet→WiFi failover:
    (1) _general_casts went empty after the failover, so stop did nothing and reported '0 devices'
    while the speaker kept playing the URL it had been given. (2) media_controller.stop()
    requires an "active session" — if pychromecast doesn't have a fresh handle, it raises
    'STOP command requested but no session is active'. quit_app() is more forceful — it kills
    the receiver app entirely, which always stops audio even after state loss.

    New behaviour:
      1. If _general_casts is empty, force a rediscovery first so we have handles to send to.
      2. For each cast: call BOTH stop() and quit_app() — try stop first, then quit_app as
         a forceful fallback. quit_app on its own would always work, but stop() is gentler
         (keeps the receiver app warm for the next play) so we try it first.
    """
    # B-Belgium-25 (v1.7.4): cancel any pending chained-audio jobs (takbeeraat
    # / twilight) BEFORE stopping playback. These are scheduled as delayed
    # APScheduler jobs (~3.5 min after an adhan). If the user hits Stop while
    # an adhan is playing, the pending follow-up would otherwise still fire
    # minutes later — exactly what startled aunt at 22:38 on 28 May after a
    # test adhan was stopped but its takbeeraat job survived.
    try:
        cancelled = 0
        for job in list(sched.get_jobs()):
            jid = getattr(job, "id", "") or ""
            if jid.startswith("takbeeraat_after_") or jid.startswith("twilight_after_"):
                try:
                    sched.remove_job(jid)
                    cancelled += 1
                except Exception:
                    pass
        if cancelled:
            log.info(f"🛑 Cancelled {cancelled} pending chained-audio job(s) (takbeeraat/twilight) on stop")
    except Exception as e:
        log.warning(f"Could not cancel pending chained-audio jobs: {e}")

    try:
        stopped_count = 0
        all_casts = _all_casts()

        # If state is empty (e.g. after WiFi failover), rediscover before giving up
        if not all_casts:
            log.warning("🔄 stop_all_audio: no cached casts, forcing rediscovery before stop")
            discover_casts()
            all_casts = _all_casts()
            if not all_casts:
                log.warning("Still no casts after rediscovery. Audio may be playing on speakers "
                            "we cannot currently reach. User should say 'Hey Google, stop' to "
                            "the speaker or power-cycle it.")

        for c in all_casts:
            try:
                ensure_connected(c)
                # Try gentle stop first
                try:
                    c.media_controller.stop()
                except Exception as e:
                    log.debug(f"stop() failed on {c.name} ({e}), trying quit_app as fallback")
                # Always also quit_app — guarantees audio dies even if stop() couldn't
                try:
                    c.quit_app()
                except Exception as e:
                    log.debug(f"quit_app() failed on {c.name}: {e}")
                with _cast_lock:
                    _speaker_playback_status[c.name] = False
                log.info(f"🛑 Stopped playback on {c.name}")
                stopped_count += 1
            except Exception as e:
                log.warning(f"Could not stop {c.name}: {e}")
        return stopped_count
    except Exception as e:
        log.error(f"Error stopping all audio: {e}")
        return 0

_last_suppress_log = {}  # audio_type -> "YYYYmmddHHMM": dedupe SUPPRESSED log to once/event

def play_on_cast(cast, media_url: str, volume: float, audio_type: str = None, prayer_name: str = None):
    """Enhanced play function with better error handling and routing awareness"""
    if shutdown_event.is_set():
        return

    # Check if this audio type should play on this speaker
    if audio_type and not _should_play_on_speaker(cast.name, audio_type):
        log.debug(f"Skipping {audio_type} on {cast.name} (routing disabled)")
        return

    # v1.8.6/1.8.7: peripheral-audio volume + quiet-hours policy — the single choke
    # point. Attenuates/suppresses peripheral audio (dhikr/takbeeraat/duas) so it
    # can't disturb a high-rise neighbour. CORE+ALLOW (adhan, warnings, the
    # deliberate suhoor/wakeup alarms) is never touched. resolve_play_volume()
    # returns 0–100 to play at, or None to suppress. Fail-safe: any error -> master.
    try:
        base_pct = int(round(volume * 100))
        dur = None
        # The >60s duration rule only applies to UNMAPPED types — compute duration
        # only then, so the adhan/common path never pays for an ffprobe call.
        if audio_type and audio_type not in volume_policy.DEFAULT_POLICY["types"] and audio_type in AUDIO:
            try:
                dur = _audio_duration_seconds(AUDIO[audio_type])
            except Exception:
                dur = None
        vol_pct = volume_policy.resolve_play_volume(
            audio_type or "", base_pct, CFG.get("volume_policy"), now_local(), prayer_name, dur)
    except Exception as e:
        log.error(f"volume_policy error (playing at master volume): {e}")
        vol_pct = int(round(volume * 100))
    if vol_pct is None:
        # Suppressed (HEALTHY, not a failure). Record once per (type, minute), not
        # once per speaker, so we don't spam play_history with N identical lines.
        try:
            key = now_local().strftime("%Y%m%d%H%M")
            if _last_suppress_log.get(audio_type) != key:
                _last_suppress_log[audio_type] = key
                _log_play(audio_type or "audio", prayer_name, "SUPPRESSED", speakers_count=0)
            log.info(f"🔕 {audio_type} suppressed by quiet-hours policy ({cast.name})")
        except Exception:
            pass
        return
    volume = max(0.0, min(vol_pct / 100.0, 1.0))

    try:
        # v1.7.1: ensure_connected may return a FRESH cast object if the cached
        # one had a stale socket. Use the return value, not the original.
        cast = ensure_connected(cast)
        cast.set_volume(volume)
        mc = cast.media_controller
        mc.play_media(media_url, content_type="audio/mpeg", stream_type="BUFFERED")
        mc.block_until_active(timeout=15)

        try:
            mc.play()
            with _cast_lock:
                _speaker_playback_status[cast.name] = True
        except Exception as e:
            log.warning(f"Play command failed on {cast.name}, but media may still be playing: {e}")

        log.info("Cast playback started on %s: vol=%.0f%% url=%s audio_type=%s",
                 cast.name, volume * 100, media_url, audio_type or "unknown")

    except Exception as e:
        log.error("Cast play failed on %s: %s", getattr(cast, "name", "?"), e)

def play_test_pattern():
    """Play test audio sequentially on each speaker to identify them"""
    if shutdown_event.is_set():
        return
    
    log.info("🔊 Starting speaker test pattern")
    
    def _test_sequence():
        speakers = _all_casts()
        if not speakers:
            log.warning("Test pattern: no speakers found")
            return
        for i, cast in enumerate(speakers):
            if shutdown_event.is_set():
                break
            log.info(f"Test pattern: playing on speaker {i+1}/{len(speakers)}: {cast.name}")
            try:
                url = local_media_url(AUDIO["adhan"])
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "adhan")
                time.sleep(8)  # Wait briefly before next speaker
            except Exception as e:
                log.warning(f"Test pattern failed on {cast.name}: {e}")
    
    threading.Thread(target=_test_sequence, daemon=True).start()

# ---------------- Quick Twilight Check (for startup) ----------------
def check_twilight_today() -> bool:
    """Quick check if today is in persistent twilight (no binary search)"""
    try:
        today = date.today()
        twilight = get_astronomical_twilight_times(today)
        return not twilight['has_twilight']
    except Exception as e:
        log.error(f"Error checking today's twilight: {e}")
        return False

# ---------------- Optimized Binary Search Twilight Detection ----------------
_twilight_search_cache = {}  # Cache for binary search to avoid duplicate calls

def get_astronomical_twilight_times_cached(target_date: date, use_cache: bool = True) -> dict:
    """
    Fetch astronomical twilight times with caching for binary search.
    This prevents duplicate API calls during boundary detection.
    """
    if use_cache and target_date in _twilight_search_cache:
        return _twilight_search_cache[target_date]
    
    result = get_astronomical_twilight_times(target_date)
    
    if use_cache:
        _twilight_search_cache[target_date] = result
        # Keep cache size manageable
        if len(_twilight_search_cache) > 30:
            # Remove oldest 10
            oldest = sorted(_twilight_search_cache.keys())[:10]
            for k in oldest:
                del _twilight_search_cache[k]
    
    return result

def get_astronomical_twilight_times(target_date: date) -> dict:
    """
    Fetch astronomical twilight times from sunrise-sunset.org API.
    Uses cached results to avoid duplicate calls.
    """
    global _last_successful_sunrise_sunset
    
    # Check cache first
    if _last_successful_sunrise_sunset["date"] == target_date:
        return _last_successful_sunrise_sunset["data"]
    
    try:
        date_str = target_date.strftime("%Y-%m-%d")
        url = "https://api.sunrise-sunset.org/json"
        params = {
            'lat': LATITUDE,
            'lng': LONGITUDE,
            'date': date_str,
            'formatted': 0  # Get ISO 8601 format
        }
        
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        
        if data.get('status') != 'OK':
            log.error(f"Sunrise API returned non-OK status: {data}")
            return {'begin': None, 'end': None, 'has_twilight': False}
        
        results = data.get('results', {})
        
        # Parse times - if they're missing or invalid, twilight is persistent
        begin_str = results.get('astronomical_twilight_begin')
        end_str = results.get('astronomical_twilight_end')

        # sunrise-sunset.org sentinel for "no astronomical twilight on this date"
        # is the Unix-epoch start ("1970-01-01T00:00:01+00:00"). Treat it as missing.
        def _is_valid_twilight(s):
            if not s or s == 'Invalid Date':
                return False
            if s.startswith('1970-'):  # epoch sentinel = no twilight
                return False
            return True

        # Convert to datetime for comparison
        begin = None
        end = None
        has_twilight = True

        if _is_valid_twilight(begin_str):
            begin = datetime.fromisoformat(begin_str.replace('Z', '+00:00'))
        else:
            has_twilight = False

        if _is_valid_twilight(end_str):
            end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
        else:
            has_twilight = False
        
        result = {'begin': begin, 'end': end, 'has_twilight': has_twilight}
        _last_successful_sunrise_sunset = {"date": target_date, "data": result}
        return result
        
    except requests.exceptions.RequestException as e:
        log.error(f"Sunrise API request failed: {e}")
        return {'begin': None, 'end': None, 'has_twilight': False}
    except Exception as e:
        log.error(f"Error parsing twilight times: {e}")
        return {'begin': None, 'end': None, 'has_twilight': False}

def estimate_solar_midnight(target_date: date) -> datetime:
    """
    Estimate solar midnight (middle of the night) when APIs fail.
    Used as fallback for 1/7 rule calculation.
    """
    # Solar midnight is approximately halfway between sunset and sunrise
    try:
        times = get_times_for(target_date)
        maghrib_str = times.get('Maghrib')
        fajr_next_str = None
        
        # Get next day's Fajr
        next_day = target_date + timedelta(days=1)
        next_times = get_times_for(next_day)
        fajr_next_str = next_times.get('Fajr')
        
        if maghrib_str and fajr_next_str:
            maghrib = parse_hhmm(maghrib_str, target_date)
            fajr_next = parse_hhmm(fajr_next_str, next_day)
            
            # Solar midnight is halfway
            night_seconds = (fajr_next - maghrib).total_seconds()
            return maghrib + timedelta(seconds=night_seconds/2)
    except Exception as e:
        log.error(f"Error estimating solar midnight: {e}")
    
    # Ultimate fallback: assume midnight at 00:00 local
    return safe_localize(datetime(target_date.year, target_date.month, target_date.day, 0, 0))

def binary_search_twilight_boundary(start_date: date, end_date: date, 
                                    find_start: bool = True) -> Optional[date]:
    """
    Binary search to find the exact day when twilight status changes.
    Uses cached API calls to avoid duplicates.
    """
    left = start_date
    right = end_date
    iterations = 0
    max_iterations = 15  # Binary search over 180 days needs ~8 iterations
    
    # Clear search cache before starting
    global _twilight_search_cache
    _twilight_search_cache = {}
    
    while left <= right and iterations < max_iterations:
        iterations += 1
        mid = left + (right - left) // 2
        
        # Check twilight at midpoint (cached)
        mid_twilight = get_astronomical_twilight_times_cached(mid)
        
        # Check day before to detect change (cached)
        prev_day = mid - timedelta(days=1)
        prev_twilight = get_astronomical_twilight_times_cached(prev_day)
        
        if find_start:
            # Looking for start of persistent twilight (normal -> no twilight)
            if not mid_twilight['has_twilight'] and prev_twilight['has_twilight']:
                log.info(f"Found twilight start boundary at {mid} after {iterations} iterations")
                return mid
            elif mid_twilight['has_twilight']:
                # Still in normal zone, move right
                left = mid + timedelta(days=1)
            else:
                # In persistent zone but not at boundary, move left
                right = mid - timedelta(days=1)
        else:
            # Looking for end of persistent twilight (no twilight -> normal)
            if mid_twilight['has_twilight'] and not prev_twilight['has_twilight']:
                log.info(f"Found twilight end boundary at {mid} after {iterations} iterations")
                return mid
            elif not mid_twilight['has_twilight']:
                # Still in persistent zone, move right
                left = mid + timedelta(days=1)
            else:
                # In normal zone, move left
                right = mid - timedelta(days=1)
    
    return None

def detect_persistent_twilight_period() -> Tuple[Optional[date], Optional[date]]:
    """
    Binary search algorithm to find persistent twilight boundaries.
    Uses caching to achieve ~12-15 API calls total.
    """
    today = date.today()
    
    # Scan range: 3 months before to 6 months after
    start_scan = today - timedelta(days=90)
    end_scan = today + timedelta(days=180)
    
    log.info(f"Binary scanning for persistent twilight between {start_scan} and {end_scan}")
    
    # Check current status
    current = get_astronomical_twilight_times_cached(today)
    currently_persistent = not current['has_twilight']
    
    if currently_persistent:
        log.info("Currently in persistent twilight, finding boundaries...")
        
        # Find start date (binary search backwards)
        start_date = binary_search_twilight_boundary(start_scan, today, find_start=True)
        
        # Find end date (binary search forwards)
        end_date = binary_search_twilight_boundary(today, end_scan, find_start=False)
        
        return start_date, end_date
    
    else:
        log.info("Not currently in persistent twilight")
        
        # Check if we'll enter it in the future
        # Sample a few points to detect if we need deeper search
        check_points = [
            today + timedelta(days=30),
            today + timedelta(days=60),
            today + timedelta(days=90),
            today + timedelta(days=120)
        ]
        
        for check in check_points:
            twilight = get_astronomical_twilight_times_cached(check)
            if not twilight['has_twilight']:
                log.info(f"Will enter persistent twilight around {check}")
                # Find exact start
                start_date = binary_search_twilight_boundary(today, check, find_start=True)
                if start_date:
                    # Find end
                    end_date = binary_search_twilight_boundary(start_date, end_scan, find_start=False)
                    return start_date, end_date
        
        return None, None

def scan_twilight_conditions():
    """Background job to scan for persistent twilight conditions using binary search"""
    if shutdown_event.is_set():
        return
    
    log.info("Binary scanning for persistent twilight conditions...")
    
    start_date, end_date = detect_persistent_twilight_period()
    
    today = date.today()
    currently_active = False
    
    if start_date and end_date:
        currently_active = start_date <= today <= end_date
    
    with _twilight_lock:
        _twilight_cache["last_scan"] = today
        _twilight_cache["persistent_twilight_active"] = currently_active
        _twilight_cache["persistent_start"] = start_date
        _twilight_cache["persistent_end"] = end_date
        _twilight_cache["high_latitude_method"] = RULES.get('high_latitude_method', 'combine_prayers')
    
    if currently_active:
        log.info(f"✅ PERSISTENT TWILIGHT ACTIVE: {start_date} to {end_date}")
        log.info(f"Using high latitude method: {_twilight_cache['high_latitude_method']}")
        if _twilight_cache['high_latitude_method'] == 'combine_prayers':
            log.info("Isha will be combined with Maghrib (twilight.mp3 will play)")
    else:
        log.info("No persistent twilight detected")

# ---------------- Enhanced Prayer Times with Filtering ----------------
_prayer_cache = {"date": None, "times": None}

def apply_high_latitude_overrides(raw_times: dict, target_date: date) -> dict:
    """
    Apply high latitude overrides to prayer times.
    This is the "Filtered Truth" - the scheduler only sees processed times.

    Order of operations:
      1) Method-specific overrides (combine_prayers / 1_7_rule / static_offset),
         only when persistent twilight is active.
      2) Universal `isha_max_time` cap — applies year-round, regardless of method.
         Empty string disables the cap. Useful for travel scenarios.
    """
    global _twilight_cache

    # Create a copy to avoid modifying cache
    times = raw_times.copy()

    # Check if we need to apply method-specific overrides.
    # B-Belgium-38 (v1.9.3): the previous gate fired ONLY when persistent
    # twilight was active — which excluded the UK summer use-case where the
    # sun technically dips far enough for the Aladhan angle to compute an
    # Isha time, but the result (23:20+) is too late for any practical
    # household. Now we also fire when isha_method_always_apply is True,
    # letting users opt into "always use my configured method" regardless
    # of geographic twilight ambiguity. Default flag is False so existing
    # Pis see no behaviour change.
    with _twilight_lock:
        twilight_active = _twilight_cache["persistent_twilight_active"]
        cached_method = _twilight_cache["high_latitude_method"]
    always_apply = bool(RULES.get('isha_method_always_apply', False))
    if twilight_active or always_apply:
        # Always-apply path reads the live rule (not the cache) so a config
        # change takes effect on the very next schedule_today() without
        # waiting for the periodic twilight scan to refresh the cache.
        method = RULES.get('high_latitude_method', cached_method) if always_apply else cached_method
    else:
        method = None

    if method == 'combine_prayers':
        # Isha is suppressed via should_play_isha() — leave times dict alone here
        log.info("combine_prayers active: Isha will be skipped via should_play_isha()")

    elif method == '1_7_rule':
        # Calculate Isha using 1/7 rule
        maghrib_str = times.get('Maghrib')
        if maghrib_str:
            maghrib_dt = parse_hhmm(maghrib_str, target_date)
            
            # Get next sunrise
            tomorrow = target_date + timedelta(days=1)
            tomorrow_times = get_times_for(tomorrow)
            sunrise_str = tomorrow_times.get('Sunrise')
            
            if sunrise_str:
                sunrise_h, sunrise_m = map(int, sunrise_str.split(':'))
                next_sunrise_dt = safe_localize(datetime(
                    tomorrow.year, tomorrow.month, tomorrow.day,
                    sunrise_h, sunrise_m
                ))
                
                # Calculate night duration
                night_seconds = (next_sunrise_dt - maghrib_dt).total_seconds()
                isha_offset = night_seconds / 7.0
                isha_dt = maghrib_dt + timedelta(seconds=isha_offset)
                
                # Format back to HH:MM
                times['Isha'] = isha_dt.strftime('%H:%M')
                log.info(f"Applied 1/7 rule: Isha at {times['Isha']}")
    
    elif method == 'static_offset':
        offset = RULES.get('isha_static_offset_minutes', 90)
        maghrib_str = times.get('Maghrib')
        if maghrib_str:
            maghrib_h, maghrib_m = map(int, maghrib_str.split(':'))
            total_minutes = maghrib_h * 60 + maghrib_m + offset
            # Clamp to same-day: never cross midnight (Isha must not roll over)
            if total_minutes >= 24 * 60:
                total_minutes = 23 * 60 + 59
                log.warning("static_offset would cross midnight; clamping Isha to 23:59")
            hours = total_minutes // 60
            minutes = total_minutes % 60
            times['Isha'] = f"{hours:02d}:{minutes:02d}"
            log.info(f"Applied static offset: Isha at {times['Isha']}")

    # Universal Isha cap — applies year-round, after any method-specific override.
    # Empty string / None means cap is disabled (useful when travelling).
    isha_max = RULES.get('isha_max_time')
    if isha_max and times.get('Isha'):
        try:
            cap_h, cap_m = map(int, isha_max.split(':'))
            cur_h, cur_m = map(int, times['Isha'].split(':'))
            if (cur_h, cur_m) > (cap_h, cap_m):
                log.info(f"Capping Isha {times['Isha']} -> {isha_max} (isha_max_time rule)")
                times['Isha'] = f"{cap_h:02d}:{cap_m:02d}"
        except Exception as e:
            log.error(f"Failed to apply isha_max_time cap (value={isha_max!r}): {e}")

    return times

# Note: ALADHAN_METHOD_MAP and ALADHAN_SCHOOL_MAP are defined near the top of
# the file (right before validate_config) because they're referenced during
# module-load validation. C-1 + C-2 (v1.5.0) — religious-correctness fix:
# before v1.5.0 every user got ISNA times regardless of the dropdown choice,
# and every Hanafi user got Shafi'i Asr (wrong by 30-60 min daily). Now wired
# via these maps with sane defaults if config has an unrecognised value.

def fetch_prayer_times(target_date: date) -> dict:
    """Fetch prayer times using coordinates with circuit breaker fallback"""
    global _last_successful_times

    try:
        # Use coordinate-based API for better accuracy
        timestamp = int(target_date.strftime("%s")) if hasattr(target_date, 'strftime') else int(time.mktime(target_date.timetuple()))
        url = f"https://api.aladhan.com/v1/timings/{timestamp}"

        method_id = ALADHAN_METHOD_MAP.get(METHOD.upper(), 2)
        madhab_name = (RULES.get('madhab') or 'shafii').upper()
        school_id = ALADHAN_SCHOOL_MAP.get(madhab_name, 0)

        params = {
            'latitude': LATITUDE,
            'longitude': LONGITUDE,
            'method': method_id,
            'school': school_id,
            'timezone': TZ
        }

        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        if data.get("code") != 200:
            raise RuntimeError(f"Prayer API non-200 payload: {data}")

        raw_times = data["data"]["timings"]
        log.debug(f"Raw prayer times for {target_date}: {raw_times}")
        
        # Apply high latitude overrides
        filtered_times = apply_high_latitude_overrides(raw_times, target_date)
        
        log.info(f"Filtered prayer times for {target_date}: {filtered_times}")
        
        # Update circuit breaker cache
        _last_successful_times = {"date": target_date, "times": filtered_times}
        
        return filtered_times

    except Exception as e:
        log.error(f"Error fetching prayer times: {e}")
        
        # Try fallback to city-based API
        try:
            dmy = target_date.strftime("%d-%m-%Y")
            url = (f"https://api.aladhan.com/v1/timingsByCity/{quote(dmy)}"
                   f"?city={quote(CITY)}&country={quote(COUNTRY)}&method=2")
            r = requests.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 200:
                raise RuntimeError(f"Prayer API non-200 payload: {data}")
            
            raw_times = data["data"]["timings"]
            filtered_times = apply_high_latitude_overrides(raw_times, target_date)
            
            _last_successful_times = {"date": target_date, "times": filtered_times}
            return filtered_times
            
        except Exception as e2:
            log.error(f"Fallback prayer API also failed: {e2}")
            
            # Circuit breaker: return last known good times if available
            if _last_successful_times["times"] and _last_successful_times["date"] == target_date:
                log.warning("Using cached prayer times from last successful fetch")
                return _last_successful_times["times"]
            elif _last_successful_times["times"]:
                log.warning("Using stale cached prayer times (date mismatch)")
                return _last_successful_times["times"]
            else:
                # No cache available, re-raise
                raise

def get_times_for(d: date) -> dict:
    """Get prayer times for specific date with caching"""
    if _prayer_cache["date"] != d:
        _prayer_cache["times"] = fetch_prayer_times(d)
        _prayer_cache["date"] = d
    return _prayer_cache["times"]

def parse_hhmm(hhmm: str, base_day: date) -> datetime:
    """Parse HH:MM time string to datetime - DST safe"""
    h, m = map(int, hhmm.split(":")[:2])
    dt = datetime(year=base_day.year, month=base_day.month, day=base_day.day,
                  hour=h, minute=m, second=0, microsecond=0)
    return safe_localize(dt)

def today_at(hhmm: str) -> datetime:
    """Get datetime for today at specified time - DST safe version"""
    h, m = map(int, hhmm.split(":")[:2])
    today = date.today()
    dt = datetime(year=today.year, month=today.month, day=today.day,
                  hour=h, minute=m, second=0, microsecond=0)
    return safe_localize(dt)

def is_between_mmdd(mmdd, start_mmdd, end_mmdd):
    """Check if date is between two MM-DD ranges"""
    def to_ord(s):
        m, d = map(int, s.split("-"))
        return (date(2001, m, d) - date(2001, 1, 1)).days + 1
    cur = to_ord(mmdd)
    s = to_ord(start_mmdd)
    e = to_ord(end_mmdd)
    return (s <= e and s <= cur <= e) or (s > e and (cur >= s or cur <= e))

def should_play_isha(target_date: date) -> bool:
    """Determine if Isha should be played today.

    Returns False (skip) when:
      - persistent_twilight_active is True AND (
          high_latitude_method == 'combine_prayers'
          OR `skip_isha_during_persistent_twilight` rule is True
        )

    Locations without persistent twilight get persistent_twilight_active == False
    so this always returns True there — Isha plays normally.
    """
    try:
        with _twilight_lock:
            active = _twilight_cache.get("persistent_twilight_active", False)
            method = _twilight_cache.get("high_latitude_method", "combine_prayers")
        if not active:
            return True
        if method == 'combine_prayers':
            return False
        if RULES.get("skip_isha_during_persistent_twilight", True):
            return False
        return True
    except Exception as e:
        log.error(f"Error in should_play_isha, defaulting to play: {e}")
        return True

def compute_current_next():
    """Compute current and next prayer times"""
    today = date.today()
    times = get_times_for(today)
    order = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]

    now = now_local()
    upcoming = []

    for name in order:
        # Skip Isha if it should not be played (twilight combine mode)
        if name == "Isha" and not should_play_isha(today):
            continue
            
        if name == "Isha" and RULES and RULES.get("skip_isha_between"):
            if is_between_mmdd(today.strftime("%m-%d"),
                               RULES["skip_isha_between"]["start"],
                               RULES["skip_isha_between"]["end"]):
                continue
                
        t = times.get(name)
        if not t:
            continue
        
        dt = parse_hhmm(t, today)
        upcoming.append((name, dt))

    next_name, next_dt = None, None
    for name, dt in upcoming:
        if dt >= now:
            next_name, next_dt = name, dt
            break

    if not next_dt:
        # Next prayer is tomorrow
        tom = today + timedelta(days=1)
        try:
            t2 = get_times_for(tom)
            if order[0] in t2:
                next_name, next_dt = order[0], parse_hhmm(t2[order[0]], tom)
        except Exception as e:
            log.error(f"Error getting tomorrow's prayer times: {e}")

    current = None
    for name, dt in reversed(upcoming):
        if dt <= now:
            current = {"name": name, "started_at": dt.isoformat()}
            break

    next_pretty = next_dt.strftime("%H:%M") if next_dt else None
    return {
        "current": current,
        "next": {
            "name": next_name,
            "when": next_dt.isoformat() if next_dt else None,
            "time_pretty": next_pretty
        }
    }

# ---------------- Ramadan Detection ----------------
_ramadan_cache = {"date": None, "is_ramadan": None}

def is_ramadan_today() -> bool:
    """Check if today is in Ramadan using Hijri calendar"""
    today = date.today()

    if _ramadan_cache["date"] == today:
        return _ramadan_cache["is_ramadan"]

    try:
        date_str = today.strftime("%d-%m-%Y")
        url = f"https://api.aladhan.com/v1/gToH"
        params = {
            'date': date_str,
            'latitude': LATITUDE,
            'longitude': LONGITUDE
        }

        log.info(f"Checking Ramadan status via: {url}?date={date_str}")
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        if data.get("code") == 200:
            hijri_month = int(data["data"]["hijri"]["month"]["number"])
            is_ramadan = (hijri_month == 9)

            _ramadan_cache["date"] = today
            _ramadan_cache["is_ramadan"] = is_ramadan

            log.info(f"🌙 Ramadan check: Hijri month {hijri_month} -> {'RAMADAN' if is_ramadan else 'not Ramadan'}")
            return is_ramadan
        else:
            log.error(f"Failed to get Hijri date: {data}")
            return False

    except Exception as e:
        log.error(f"Error checking Ramadan status: {e}")
        return False

# ---------------- Eid / Hijri helpers (sunset-aware) ----------------
_hijri_cache = {}  # key: "DD-MM-YYYY" -> payload dict
_audio_len_cache = {}  # key: relpath -> seconds

def _get_maghrib_dt_for_day(d: date) -> Optional[datetime]:
    """Get Maghrib datetime for a given date"""
    try:
        times = get_times_for(d)
        t = times.get("Maghrib")
        if not t:
            return None
        return parse_hhmm(t, d)
    except Exception as e:
        log.error(f"Failed to get Maghrib time for {d}: {e}")
        return None

def _hijri_from_gregorian_day(d: date) -> Optional[dict]:
    """Get Hijri date for a Gregorian civil date (midnight-based)."""
    global _last_successful_hijri
    
    key = d.strftime("%d-%m-%Y")
    
    # Limit cache size to prevent memory growth
    if len(_hijri_cache) > MAX_HIJRI_CACHE_SIZE:
        oldest = next(iter(_hijri_cache))
        del _hijri_cache[oldest]
        log.debug(f"Cleared oldest Hijri cache entry: {oldest}")
    
    if key in _hijri_cache:
        return _hijri_cache[key]
    
    try:
        url = "https://api.aladhan.com/v1/gToH"
        params = {
            "date": key,
            "latitude": LATITUDE,
            "longitude": LONGITUDE
        }
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 200:
            log.error(f"gToH non-200 payload for {key}: {data}")
            # Return cached fallback if available
            if _last_successful_hijri["date"] == d:
                return _last_successful_hijri["hijri"]
            return None
        hijri = data["data"]["hijri"]
        payload = {
            "day": int(hijri["day"]),
            "month": int(hijri["month"]["number"]),
            "year": int(hijri["year"])
        }
        _hijri_cache[key] = payload
        _last_successful_hijri = {"date": d, "hijri": payload}
        return payload
    except Exception as e:
        log.error(f"Error fetching Hijri date for {key}: {e}")
        # Return cached fallback if available
        if _last_successful_hijri["date"] == d:
            return _last_successful_hijri["hijri"]
        return None

def hijri_now_sunset_aware(dt: Optional[datetime] = None) -> Optional[dict]:
    """
    Return Hijri date where the Hijri day flips at Maghrib (sunset).
    Implementation:
      - If local time >= Maghrib today: use gToH(tomorrow)
      - Else: use gToH(today)
    """
    if dt is None:
        dt = now_local()
    d = dt.date()
    mag = _get_maghrib_dt_for_day(d)
    if mag and dt >= mag:
        return _hijri_from_gregorian_day(d + timedelta(days=1))
    return _hijri_from_gregorian_day(d)

def _audio_duration_seconds(relpath: str) -> float:
    """Return audio duration (seconds) for relpath, cached.
    Uses ffprobe (low memory) — falls back to pydub if ffprobe unavailable.
    Used by Friday-prayer scheduler to position 'Maghrib - duration - 5s'.
    """
    if relpath in _audio_len_cache:
        return _audio_len_cache[relpath]
    p = abs_audio_path(relpath)

    # Try ffprobe first (streams audio, ~5 MB RSS instead of pydub's ~hundreds of MB)
    try:
        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", p],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            sec = max(0.0, float(result.stdout.strip()))
            _audio_len_cache[relpath] = sec
            return sec
    except FileNotFoundError:
        pass  # ffprobe not installed
    except Exception as e:
        log.warning(f"ffprobe failed for {relpath}: {e}")

    # Fallback: pydub (heavy)
    if not _import_pydub():
        log.error(f"Cannot determine duration of {relpath} — neither ffprobe nor pydub available")
        _audio_len_cache[relpath] = 0.0
        return 0.0
    try:
        audio = AudioSegment.from_file(p)
        sec = max(0.0, float(len(audio)) / 1000.0)
        _audio_len_cache[relpath] = sec
        return sec
    except Exception as e:
        log.error(f"Failed to read duration for {relpath}: {e}")
        _audio_len_cache[relpath] = 0.0
        return 0.0

def should_play_takbeerat_after_adhan(prayer_name: Optional[str], when: Optional[datetime] = None) -> bool:
    """
    Eid takbeeraat scheduling.

    v1.6.2 (Tue 26 May 2026 — surfaced by user: 'takbeeraat didn't come on
    after Maghrib' the evening of 9 Dhū al-Ḥijjah, eve of Eid al-Adha):
    pre-v1.6.2 only fired on Eid al-Adha days 10-12. But the mainstream
    scholarly position across Hanafi/Shafi'i/Maliki/Hanbali schools is that
    "takbeeraat al-muqayyad" (the takbeer after each prayer) runs from
    Fajr on 9 Dhū al-Ḥijjah (Day of Arafah) through Asr on 13 Dhū al-Ḥijjah.
    Updated to that more inclusive window. Configurable via
    rules.takbeeraat_window if a user wants the stricter 10-12 view.

    Rules:
      - Eid al-Fitr (1 Shawwal): after every Adhan EXCEPT Maghrib.
      - Eid al-Adha:
          - inclusive (default): Fajr 9 Dhul Hijjah through Asr 13 Dhul Hijjah
          - strict:               10-12 Dhul Hijjah only
      - Sunset-aware Hijri day boundaries respected throughout.
    """
    if not RULES.get('enable_eid_takbeeraat', True):
        return False

    if prayer_name is None:
        prayer_name = ""

    # B-Belgium-24 (v1.7.4, Thu 28 May 2026): a TEST adhan (from the dashboard's
    # "Test Adhan" button or /api/test/play) must NEVER trigger the Eid
    # takbeeraat chain. On 28 May a 5%-volume test adhan during the Eid window
    # scheduled a takbeeraat job that then fired ~3.5 min later at full volume
    # in aunt's house at 22:38 — startling. Test plays are for checking the
    # audio path, not for performing the actual Eid ritual chain.
    if prayer_name.upper() == "TEST":
        return False

    if when is None:
        when = now_local()

    # Maghrib prayer belongs to the pre-sunset day for rule decisions.
    if prayer_name == "Maghrib":
        when_for_day = when - timedelta(minutes=1)
    else:
        when_for_day = when

    h = hijri_now_sunset_aware(when_for_day)
    if not h:
        return False

    h_day = int(h["day"])
    h_month = int(h["month"])

    # Eid al-Fitr: 1 Shawwal (month 10, day 1)
    if h_month == 10 and h_day == 1:
        if prayer_name == "Maghrib":
            return False  # explicitly not on Eid al-Fitr Maghrib
        return True

    # Eid al-Adha takbeeraat window. Default: mainstream-inclusive view.
    window = (RULES.get('takbeeraat_window') or 'inclusive').lower()
    if h_month == 12:
        if window == 'strict' and h_day in (10, 11, 12):
            # Strict: 10-12; Maghrib only on 10 & 11
            if prayer_name == "Maghrib":
                return h_day in (10, 11)
            return True
        if window != 'strict' and h_day in (9, 10, 11, 12, 13):
            # Inclusive: 9 Fajr through 13 Asr.
            # On day 9: only AFTER Fajr (i.e. all prayers from Fajr onwards).
            # On day 13: only THROUGH Asr — so Asr yes, Maghrib + Isha no.
            if h_day == 13 and prayer_name in ("Maghrib", "Isha"):
                return False
            # Day 9 Maghrib: yes (eve of Eid — most communities recite tonight).
            # Days 10/11 Maghrib: yes. Day 12 Maghrib: yes (consistent inclusive).
            return True

    return False

# ---------------- Enhanced Scheduler ----------------
sched = BackgroundScheduler(timezone=LOCAL_TZ)
_scheduler_started = False

def schedule_midnight_refresh():
    """Schedule daily refresh at midnight"""
    try:
        now = now_local()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=1, second=0, microsecond=0)
        # Check if job already exists
        existing = [job.id for job in sched.get_jobs()]
        if 'refresh_daily' not in existing:
            sched.add_job(refresh_daily, DateTrigger(run_date=tomorrow), id="refresh_daily")
            log.info("Scheduled daily refresh @ %s", tomorrow)
    except Exception as e:
        log.error(f"Error scheduling midnight refresh: {e}")

_DISCOVER_CASTS_MAX_SECONDS = 45  # 8s mDNS + 6×3s disconnects + iteration headroom


# ═════════════════════════════════════════════════════════════════════════════
# v1.9.9 — WATCHDOG & SELF-HEALING
# ─────────────────────────────────────────────────────────────────────────────
# Written the day after aunt-pi missed Dhuhr, Asr AND Maghrib (12 Jun 2026)
# while logging "discover_casts() exceeded 45s" CRITICAL every 30 minutes for
# 6+ hours. The system KNEW it was sick and did nothing but log. Two layers:
#
#   Layer 1 — systemd hardware watchdog. The unit file sets WatchdogSec=180;
#   _watchdog_health_loop() pings sd_notify(WATCHDOG=1) every 60s, but ONLY
#   after verifying the Flask thread answers a localhost /api/state probe and
#   APScheduler is alive. Wedged process → no ping → systemd kills + restarts
#   within 3 min (Restart=on-failure already in the unit).
#
#   Layer 2 — application recovery rules:
#     • 3 consecutive discover_casts timeouts (the exact aunt-pi signature)
#       → Telegram alert + WATCHDOG_RESTART history entry + self-restart
#     • zero speakers for >2h while the network is demonstrably up → same
#   Self-restarts are rate-limited to one per 6 hours via a tiny state file
#   in /var/lib/castadhan/ so a restart that doesn't fix the problem can't
#   become a restart loop.
# ═════════════════════════════════════════════════════════════════════════════

_WATCHDOG_STATE_FILE = "/var/lib/castadhan/watchdog_state.json"
_WATCHDOG_PING_INTERVAL_SEC = 60
_WATCHDOG_WARMUP_SEC = 300          # pings unconditional for first 5 min of uptime
_WATCHDOG_SELF_RESTART_COOLDOWN_SEC = 6 * 3600
_DISCOVERY_TIMEOUT_RESTART_STREAK = 3
_ZERO_SPEAKERS_RESTART_AFTER_SEC = 2 * 3600

_discovery_timeout_streak = 0       # consecutive discover_casts timeouts
_zero_speakers_since: Optional[float] = None   # time.monotonic() when count hit 0
_process_started_monotonic = time.monotonic()


def _sd_notify(message: str):
    """Minimal sd_notify(3) — no dependency on python-systemd. No-op when not
    running under systemd (NOTIFY_SOCKET unset) so dev runs are unaffected."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            if addr.startswith("@"):          # abstract namespace socket
                addr = "\0" + addr[1:]
            s.connect(addr)
            s.send(message.encode())
        finally:
            s.close()
    except Exception:
        pass    # notification must never break the app


def _read_watchdog_state() -> dict:
    try:
        with open(_WATCHDOG_STATE_FILE) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _write_watchdog_state(state: dict):
    try:
        tmp = _WATCHDOG_STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, _WATCHDOG_STATE_FILE)
    except Exception as e:
        log.error(f"watchdog state write failed: {e}")


def _self_restart(reason: str):
    """Deliberate self-restart: alert → audit entry → exit(1) → systemd
    restarts us (Restart=on-failure). Rate-limited to one per 6h so a restart
    that doesn't cure the underlying problem can't loop."""
    state = _read_watchdog_state()
    last = float(state.get("last_self_restart_epoch") or 0)
    if time.time() - last < _WATCHDOG_SELF_RESTART_COOLDOWN_SEC:
        log.critical(
            f"Self-restart wanted ({reason}) but last one was "
            f"{(time.time()-last)/3600:.1f}h ago (< 6h cooldown). Holding on, "
            f"alerting instead."
        )
        _telegram_send(f"🤒 CastAdhan ({_site_label()}): still unhealthy after a "
                       f"recent self-restart — {reason}. Manual look needed.\n"
                       f"Tailscale: http://{_get_tailscale_ip() or '?'}:8786")
        return
    state["last_self_restart_epoch"] = time.time()
    state["last_self_restart_reason"] = reason
    _write_watchdog_state(state)
    try:
        _log_play("watchdog", None, "WATCHDOG_RESTART", speakers_count=0,
                  error=Exception(reason))
    except Exception:
        pass
    _telegram_send(f"🔄 CastAdhan ({_site_label()}): self-restarting — {reason}. "
                   f"Back in ~30 seconds.")
    log.critical(f"🔄 SELF-RESTART: {reason}")
    # Give the Telegram POST a moment to flush, then die. systemd revives us.
    time.sleep(2)
    os._exit(1)


def _probe_self_api() -> bool:
    """True if our own /api/state answers within 15s. Probing /api/state (not
    /health) on purpose: it takes the same locks the 12-Jun deadlock wedged,
    so a lock-wedge fails the probe while a healthy-but-busy server passes."""
    try:
        import urllib.request as _ur
        req = _ur.Request(f"http://127.0.0.1:{PORT}/api/state")
        with _ur.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


def _network_is_up() -> bool:
    """Cheap reachability check so the zero-speakers rule can't fire during a
    genuine internet/LAN outage (restarting won't fix an unplugged router)."""
    try:
        s = socket.create_connection(("8.8.8.8", 53), timeout=3)
        s.close()
        return True
    except Exception:
        return False


def _watchdog_health_loop():
    """Daemon loop, every 60s:
       1. Probe own API + scheduler; ping systemd watchdog only when healthy
          (unconditional during the 5-min startup warmup).
       2. Zero-speakers-for-2h-with-network-up → self-restart.
       3. Every 6h: filesystem write probe (EROFS/EIO = dying SD card → alert).
    """
    global _zero_speakers_since
    last_fs_probe = 0.0
    log.info("Watchdog health loop started (ping interval %ss)", _WATCHDOG_PING_INTERVAL_SEC)
    while not shutdown_event.is_set():
        try:
            uptime = time.monotonic() - _process_started_monotonic
            in_warmup = uptime < _WATCHDOG_WARMUP_SEC

            # ---- 1. systemd watchdog ping (conditional on health) ----------
            healthy = True
            if not in_warmup:
                if not _probe_self_api():
                    healthy = False
                    log.critical("Watchdog: /api/state self-probe FAILED — "
                                 "withholding systemd ping (restart in <3 min "
                                 "if this persists)")
                elif not getattr(sched, "running", True):
                    healthy = False
                    log.critical("Watchdog: APScheduler not running — withholding ping")
            if in_warmup or healthy:
                _sd_notify("WATCHDOG=1")

            # ---- 2. zero-speakers rule -------------------------------------
            with _cast_lock:
                n_speakers = len(_general_casts)
            if n_speakers > 0:
                _zero_speakers_since = None
            else:
                if _zero_speakers_since is None:
                    _zero_speakers_since = time.monotonic()
                elif (time.monotonic() - _zero_speakers_since
                      > _ZERO_SPEAKERS_RESTART_AFTER_SEC) and _network_is_up():
                    _zero_speakers_since = None   # reset before the restart call
                    _self_restart("zero speakers discovered for over 2 hours "
                                  "while network is up")

            # ---- 3. six-hourly filesystem probe (SD-card early warning) ----
            if time.monotonic() - last_fs_probe > 6 * 3600:
                last_fs_probe = time.monotonic()
                for probe_dir in (ROOT, "/var/lib/castadhan"):
                    try:
                        p = os.path.join(probe_dir, ".fs_probe")
                        with open(p, "w") as f:
                            f.write("ok")
                        os.remove(p)
                    except OSError as e:
                        log.critical(f"Filesystem probe FAILED in {probe_dir}: {e}")
                        _telegram_send(
                            f"💾 CastAdhan ({_site_label()}): cannot write to "
                            f"{probe_dir} ({e}). SD card may be failing — this is "
                            f"how masood's card died on 9 June. Replace the card "
                            f"soon.\nTailscale: http://{_get_tailscale_ip() or '?'}:8786")
        except Exception as e:
            log.error(f"Watchdog loop error: {e}")
        shutdown_event.wait(_WATCHDOG_PING_INTERVAL_SEC)


def _start_watchdog_loop():
    t = threading.Thread(target=_watchdog_health_loop, daemon=True,
                         name="watchdog_health_loop")
    t.start()


def _scheduled_discover_casts():
    """Cron entry point. Runs discover_casts() with a hard wall-clock cap.

    B-Belgium-49 (v1.9.7): a single wedged discover_casts() with the cron job's
    max_instances=1 used to silently swallow every subsequent rediscovery attempt
    for HOURS. Now: if discover_casts() doesn't finish in _DISCOVER_CASTS_MAX_SECONDS
    we log CRITICAL and let APScheduler fire the next one on schedule. The
    orphaned worker thread continues in the background (daemon=True), so it
    won't block process exit, but it may eventually complete or accumulate.
    A handful of zombies per restart is acceptable; restart clears them.

    v1.9.9 (B-Belgium-60 mitigation): tracks CONSECUTIVE timeouts. On the 12 Jun
    aunt-pi incident the escape hatch fired every 30 min for 6+ hours and the
    system never recovered until a manual restart (which fixed it instantly).
    Now: 3 consecutive timeouts → _self_restart(). A success resets the streak."""
    global _discovery_timeout_streak
    t = threading.Thread(
        target=discover_casts,
        daemon=True,
        name="discover_casts-bounded",
    )
    t.start()
    t.join(timeout=_DISCOVER_CASTS_MAX_SECONDS)
    if t.is_alive():
        _discovery_timeout_streak += 1
        log.critical(
            f"❌ discover_casts() exceeded {_DISCOVER_CASTS_MAX_SECONDS}s — "
            f"abandoning this cycle (consecutive timeouts: "
            f"{_discovery_timeout_streak}/{_DISCOVERY_TIMEOUT_RESTART_STREAK}). "
            f"B-Belgium-49 escape hatch."
        )
        if _discovery_timeout_streak >= _DISCOVERY_TIMEOUT_RESTART_STREAK:
            _self_restart(
                f"{_discovery_timeout_streak} consecutive discover_casts "
                f"timeouts (B-Belgium-60 pattern — a restart fixed aunt-pi "
                f"instantly on 12 Jun)")
    else:
        if _discovery_timeout_streak:
            log.info(f"discover_casts recovered after "
                     f"{_discovery_timeout_streak} timeout(s) — streak reset")
        _discovery_timeout_streak = 0


def schedule_cast_rediscovery():
    """Schedule periodic cast rediscovery to handle network changes.

    v1.9.7 (B-Belgium-48): cron shifted from minute='*/30' (which collides at
    :00 and :30 with prayers that get capped to round half-hours — Isha tonight
    on masood's Pi is at 22:30) to minute='2,32'. The +2-minute offset is
    comfortably clear of any cap target and small enough that the prewarm
    cadence stays useful.

    v1.9.7 (B-Belgium-49): registers _scheduled_discover_casts (timeout-bounded
    wrapper) instead of discover_casts directly, so a wedged discovery cannot
    starve subsequent attempts via APScheduler's max_instances=1 gate."""
    try:
        existing = [job.id for job in sched.get_jobs()]
        if 'cast_rediscovery' not in existing:
            sched.add_job(_scheduled_discover_casts, CronTrigger(minute="2,32"), id="cast_rediscovery")
            log.info("Scheduled cast rediscovery at :02 and :32 each hour (bounded to 45s)")
    except Exception as e:
        log.error(f"Error scheduling cast rediscovery: {e}")

def schedule_dst_protection():
    """Hourly refresh to catch DST changes"""
    try:
        existing = [job.id for job in sched.get_jobs()]
        if 'dst_protection_refresh' not in existing:
            sched.add_job(refresh_daily, CronTrigger(hour='2,14', minute=5), id="dst_protection_refresh")
            log.info("Scheduled DST protection refresh hourly at minute 5")
    except Exception as e:
        log.error(f"Error scheduling DST protection: {e}")

def schedule_health_check():
    """Daily health self-test"""
    try:
        existing = [job.id for job in sched.get_jobs()]
        if 'health_check' not in existing:
            sched.add_job(run_health_check, CronTrigger(hour=3, minute=0), id="health_check")
            log.info("Scheduled daily health check at 3:00 AM")
    except Exception as e:
        log.error(f"Error scheduling health check: {e}")

def schedule_twilight_scan():
    """Scan for persistent twilight conditions respecting config frequency"""
    try:
        days = RULES.get('twilight_scan_frequency_days', 7)
        existing = [job.id for job in sched.get_jobs()]
        if 'twilight_scan' not in existing:
            # Use IntervalTrigger to respect the configured frequency
            sched.add_job(
                scan_twilight_conditions, 
                IntervalTrigger(days=days, start_date=now_local() + timedelta(days=1)),
                id="twilight_scan"
            )
            log.info(f"Scheduled binary twilight scan every {days} days")
    except Exception as e:
        log.error(f"Error scheduling twilight scan: {e}")

def schedule_daily_summary():
    """v1.8.0: schedule the daily Telegram digest at 23:15 local (after the last
    prayer, year-round for this latitude). Always scheduled; the job itself is a
    no-op when Telegram is unconfigured."""
    try:
        existing = [job.id for job in sched.get_jobs()]
        if 'daily_summary' not in existing:
            sched.add_job(run_daily_summary, CronTrigger(hour=23, minute=15), id="daily_summary")
            log.info("Scheduled daily Telegram summary at 23:15")
    except Exception as e:
        log.error(f"Error scheduling daily summary: {e}")

def run_daily_summary():
    """v1.8.0: send a once-daily Telegram digest of which adhans fired today.
    Built from the persistent play_history.jsonl (source of truth across
    restarts). No-op if Telegram isn't configured."""
    if shutdown_event.is_set():
        return
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        return  # not configured — nothing to send
    try:
        today_str = date.today().isoformat()
        prayers = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
        # Latest adhan entry per prayer for today; later entries overwrite earlier.
        results: Dict[str, Optional[dict]] = {p: None for p in prayers}
        try:
            with open(_PLAY_HISTORY_FILE) as f:
                history = [json.loads(ln) for ln in f if ln.strip()]
        except FileNotFoundError:
            history = list(_play_history)
        except Exception as e:
            log.error(f"Daily summary: history read failed, using in-memory ring: {e}")
            history = list(_play_history)
        for entry in history:
            try:
                if entry.get("audio_type") != "adhan":
                    continue
                if not (entry.get("ts_local") or "").startswith(today_str):
                    continue
                p = entry.get("prayer_name")
                if p in results:
                    results[p] = entry
            except Exception:
                continue

        try:
            isha_expected = should_play_isha(date.today())
        except Exception:
            isha_expected = True

        lines = []
        any_problem = False
        silent_whitelist = set(RULES.get("expected_silent_prayers") or [])
        for p in prayers:
            e = results[p]
            status = e.get("status") if e else None
            if status in ("PASS", "DISCOVERY_RECOVERED"):
                lines.append(f"✅ {p} {e['ts_local'][11:16]}")
            elif status == "SILENT_EXPECTED" or (status == "NO_SPEAKERS" and p in silent_whitelist):
                # v1.8.14: owner-whitelisted silent prayer (e.g. aunt's Fajr when
                # she powers her speakers down for the night). Healthy, not a fail.
                # Second condition handles historical NO_SPEAKERS entries written
                # before v1.8.14 added the SILENT_EXPECTED downgrade in _log_play.
                lines.append(f"🔕 {p} — silent by design")
            elif status in ("FAIL", "NO_SPEAKERS", "FAIL_VERIFIED"):
                any_problem = True
                lines.append(f"❌ {p} FAILED ({status})")
            elif p == "Isha" and not isha_expected:
                lines.append("➖ Isha (combined with Maghrib)")
            else:
                any_problem = True
                lines.append(f"⚠️ {p} — no record")

        # v1.9.9 (B-Belgium-45): version-drift line. The silent-update
        # graveyard of 8 Jun (masood 5 versions behind, son 8) was only found
        # because the operator happened to ask. Now every digest carries the
        # installed-vs-latest comparison, and a Pi that has MISSED an update
        # window (latest release older than 36h yet still not installed)
        # upgrades the digest to a problem report so it actually sends.
        ops_lines = []
        try:
            installed = "?"
            try:
                with open(os.path.join(ROOT, "VERSION")) as f:
                    installed = f.read().strip()
            except Exception:
                pass
            repo = "sabreenaapa-coder/castadhan-portable"
            try:
                with open("/etc/default/castadhan-update") as f:
                    for ln in f:
                        if ln.strip().startswith("GITHUB_REPO="):
                            repo = ln.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
            r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                             timeout=10)
            if r.status_code == 200:
                rel = r.json()
                latest = (rel.get("tag_name") or "").lstrip("v")
                published = rel.get("published_at") or ""
                if latest and latest != installed:
                    ops_lines.append(f"📦 Version: {installed} installed, "
                                     f"{latest} available")
                    try:
                        pub_dt = datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ")
                        age_h = (datetime.utcnow() - pub_dt).total_seconds() / 3600
                        if age_h > 36:
                            any_problem = True
                            ops_lines[-1] += (f" — released {age_h/24:.0f}d ago, "
                                              f"update window MISSED")
                    except Exception:
                        pass
        except Exception as e:
            log.debug(f"digest version check skipped: {e}")
        try:
            with open("/proc/uptime") as f:
                up_h = float(f.read().split()[0]) / 3600
            ops_lines.append(f"⏱ Uptime: {up_h/24:.1f}d" if up_h > 48
                             else f"⏱ Uptime: {up_h:.0f}h")
        except Exception:
            pass
        try:
            import shutil as _sh
            du = _sh.disk_usage("/")
            free_gb = du.free / 1e9
            ops_lines.append(f"💾 Disk: {free_gb:.1f} GB free")
            if free_gb < 2:
                any_problem = True
                ops_lines[-1] += " — LOW"
        except Exception:
            pass

        # v1.8.14: when telegram_only_on_failure is True (new global default),
        # skip the digest entirely on all-green days. Instant failure alerts
        # continue regardless; this only suppresses the once-a-day "everything
        # fine" message that the owner explicitly didn't want.
        if (RULES.get("telegram_only_on_failure", True) and not any_problem):
            log.info("Daily Telegram summary: no problems today, suppressed by telegram_only_on_failure")
            return
        if ops_lines:
            lines.extend([""] + ops_lines)

        header = (f"⚠️ CastAdhan ({_site_label()}) — a prayer may not have played today:"
                  if any_problem else
                  f"✅ CastAdhan ({_site_label()}) — all prayers fired today:")
        body = header + "\n" + "\n".join(lines)
        # v1.8.14: tail with the Tailscale URL so the owner can jump to the
        # dashboard / SSH from the alert on their phone.
        ts_ip = _get_tailscale_ip()
        if ts_ip:
            body += f"\n\nTailscale: http://{ts_ip}:8786"
        _telegram_send(body)
        log.info("Daily Telegram summary sent")
    except Exception as e:
        log.error(f"Daily summary error: {e}")

def run_health_check():
    """Run health self-test and log results"""
    if shutdown_event.is_set():
        return
    
    log.info("Running scheduled health check")
    
    # Check API connectivity
    try:
        test_times = fetch_prayer_times(date.today())
        log.info("✓ Prayer API reachable")
    except Exception as e:
        log.error(f"✗ Prayer API check failed: {e}")
    
    # Check Hijri API
    try:
        test_hijri = hijri_now_sunset_aware()
        log.info(f"✓ Hijri API reachable: {test_hijri}")
    except Exception as e:
        log.error(f"✗ Hijri API check failed: {e}")
    
    # Check twilight detection
    with _twilight_lock:
        log.info(f"Twilight status: active={_twilight_cache['persistent_twilight_active']}, "
                f"method={_twilight_cache['high_latitude_method']}")
    
    # Check speaker availability
    with _cast_lock:
        num_speakers = len(_general_casts)

    log.info(f"Speakers available: {num_speakers}")

    if num_speakers == 0:
        log.warning("⚠ No speakers available - check network connectivity")

    # v1.9.9: SD-card early warning. masood's card logged mmc/I-O errors in
    # dmesg HOURS before it fully died on 9 Jun — nobody saw them. Scan the
    # kernel log for storage-failure signatures; alert on anything new since
    # the last scan (hash-deduped via watchdog_state.json so one bad block
    # doesn't re-alert every day). Fail-quiet if dmesg is restricted.
    try:
        import hashlib
        out = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=20)
        if out.returncode == 0:
            bad = [ln for ln in out.stdout.splitlines()
                   if any(sig in ln for sig in
                          ("mmc0: error", "mmcblk0: error", "I/O error",
                           "EXT4-fs error", "Remounting filesystem read-only",
                           "Buffer I/O error"))]
            if bad:
                digest_hash = hashlib.sha256("\n".join(bad[-20:]).encode()).hexdigest()
                state = _read_watchdog_state()
                if state.get("last_dmesg_hash") != digest_hash:
                    state["last_dmesg_hash"] = digest_hash
                    _write_watchdog_state(state)
                    log.critical(f"💾 Storage errors in dmesg ({len(bad)} lines). "
                                 f"Most recent: {bad[-1][:200]}")
                    _telegram_send(
                        f"💾 CastAdhan ({_site_label()}): {len(bad)} storage "
                        f"error(s) in the kernel log. The SD card may be "
                        f"failing — this is the signal that preceded masood's "
                        f"card death by hours. Most recent:\n{bad[-1][:200]}\n"
                        f"Tailscale: http://{_get_tailscale_ip() or '?'}:8786")
        else:
            log.debug("dmesg scan skipped (restricted or unavailable)")
    except Exception as e:
        log.debug(f"dmesg scan skipped: {e}")

def refresh_daily():
    """Daily refresh of prayer times and schedule"""
    if shutdown_event.is_set():
        return

    try:
        log.info("Performing daily refresh")
        _prayer_cache["date"] = None
        _ramadan_cache["date"] = None  # Clear Ramadan cache for new day
        _hijri_cache.clear()  # Clear Hijri cache for new day
        schedule_today()
        schedule_midnight_refresh()
    except Exception as e:
        log.error(f"Error during daily refresh: {e}")

def schedule_today():
    """Schedule today's prayers and activities - preserves infrastructure jobs"""
    if shutdown_event.is_set():
        return

    try:
        # Only remove prayer-related jobs, not infrastructure
        jobs_to_remove = []
        for job in sched.get_jobs():
            # Keep infrastructure jobs
            if job.id in ['cast_rediscovery', 'dst_protection_refresh', 'health_check', 'twilight_scan', 'refresh_daily', 'daily_summary']:
                continue
            jobs_to_remove.append(job.id)
        
        for job_id in jobs_to_remove:
            sched.remove_job(job_id)
            
        discover_casts()

        times = get_times_for(date.today())
        prayers = ["Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"]
        skip_isha = False

        if RULES and RULES.get("skip_isha_between"):
            skip_isha = is_between_mmdd(date.today().strftime("%m-%d"),
                                        RULES["skip_isha_between"]["start"],
                                        RULES["skip_isha_between"]["end"])

        # Check if today is Ramadan
        ramadan = is_ramadan_today()
        if ramadan:
            log.info("🌙 RAMADAN MODE ACTIVE - Fajr cap rules disabled, Suhoor will be scheduled")

        # Check if Isha should be skipped due to combination (with defensive error handling)
        try:
            skip_isha_due_to_twilight = not should_play_isha(date.today())
        except Exception as e:
            log.error(f"Twilight logic failed, defaulting to play Isha: {e}")
            skip_isha_due_to_twilight = False

        # Detect whether Isha was capped today by the isha_max_time rule.
        # When the cap fired, the Fajr-delay rules are relaxed (don't compress the night
        # at both ends) — controlled by `fajr_at_start_when_isha_capped`.
        isha_max_cfg = RULES.get('isha_max_time')
        isha_capped_today = bool(isha_max_cfg and times.get('Isha') == isha_max_cfg)
        if isha_capped_today:
            log.info("Isha cap is active today (Isha == %s)", isha_max_cfg)

        for p in prayers:
            if p == "Isha" and (skip_isha or skip_isha_due_to_twilight):
                if skip_isha_due_to_twilight:
                    log.info(f"Skipping {p} (combined with Maghrib due to persistent twilight)")
                else:
                    log.info(f"Skipping {p} (summer period)")
                continue

            t = times.get(p)
            if not t:
                continue

            # Special Fajr timing logic with configurable cap
            if p == "Fajr":
                # v1.8.11: explicit, owner-selectable Fajr timing, always clamped to
                # the Islamic-permissibility window [true dawn, sunrise).
                #   fajr_mode = 'raw'            -> fire at true astronomical dawn (DEFAULT)
                #   fajr_mode = 'before_sunrise' -> fire `fajr_minutes_before_sunrise` before sunrise
                # Ramadan always uses raw dawn (suhoor must end at true Fajr), and the
                # Isha-cap symmetry still forces raw within before_sunrise mode.
                fajr_time = today_at(t)                                   # true dawn (raw API Fajr)
                sunrise_time = today_at(times.get("Sunrise", "07:00"))
                fajr_mode = (RULES.get("fajr_mode") or "raw").lower()
                fajr_at_start = RULES.get('fajr_at_start_when_isha_capped', True)

                if ramadan:
                    dt = fajr_time
                    log.info(f"🌙 RAMADAN: Fajr at raw dawn {dt}")
                elif fajr_mode == "before_sunrise" and not (fajr_at_start and isha_capped_today):
                    mins = abs(int(RULES.get("fajr_minutes_before_sunrise", 30)))
                    dt = sunrise_time - timedelta(minutes=mins)
                    # Permissibility clamp: never before true dawn, never at/after sunrise.
                    if dt < fajr_time:
                        dt = fajr_time
                    elif dt >= sunrise_time:
                        dt = sunrise_time - timedelta(minutes=1)
                    log.info(f"Fajr {mins}m before sunrise -> {dt} (clamped to [{fajr_time.strftime('%H:%M')}, sunrise))")
                else:
                    # 'raw' (default), Ramadan handled above, or Isha-cap symmetry override.
                    dt = fajr_time
                    log.info(f"Fajr at raw dawn {dt} (mode={fajr_mode})")
            else:
                dt = today_at(t)

            if dt > now_local():  # Only schedule future prayers
                # B-Belgium-36 (v1.9.3): replace_existing so re-running
                # schedule_today() during the first-run wizard's config save
                # doesn't throw ConflictingIdError on adhan_Maghrib (etc).
                # The remove-loop at top of schedule_today() isn't race-safe
                # under near-simultaneous config saves on startup.
                sched.add_job(
                    play_adhan_all,
                    DateTrigger(run_date=dt),
                    id=f"adhan_{p}",
                    kwargs={"prayer_name": p},
                    replace_existing=True,
                )
                log.info("Scheduled %s @ %s", p, dt)

                # v1.7.1 belt-and-braces: schedule a fresh discovery 3 minutes
                # before each adhan so the cast sockets are guaranteed live when
                # the adhan fires. ensure_connected() already self-heals stale
                # sockets at play time, but pre-warming means the heal has
                # already happened by the time the adhan job runs — no 20s
                # connection-timeout delay at the critical moment. Defends
                # against the Eid-Fajr stale-socket no-play (28 May 2026).
                prewarm_dt = dt - timedelta(minutes=3)
                if prewarm_dt > now_local():
                    try:
                        sched.add_job(
                            discover_casts,
                            DateTrigger(run_date=prewarm_dt),
                            id=f"prewarm_{p}",
                            replace_existing=True,
                        )
                        log.info("Scheduled discovery pre-warm for %s @ %s (3 min before adhan)", p, prewarm_dt)
                    except Exception as e:
                        log.warning(f"Could not schedule pre-warm for {p}: {e}")

        # Schedule suhoor alarm during Ramadan
        if ramadan and "Fajr" in times:
            fajr_time_str = times["Fajr"]
            lead_minutes = int(RULES.get("suhoor_lead_minutes", 30))
            suhoor_time = today_at(fajr_time_str) - timedelta(minutes=lead_minutes)

            if suhoor_time > now_local():
                sched.add_job(play_suhoor_alarm, DateTrigger(run_date=suhoor_time), id="suhoor_alarm", replace_existing=True)
                log.info(f"🌙 Scheduled Suhoor alarm @ {suhoor_time} (Fajr - {lead_minutes} min)")

        # Schedule other activities with Adhan conflict detection
        if RULES.get("morning_dhikr_time"):
            md = today_at(RULES["morning_dhikr_time"])
            if md > now_local():
                # Check for Fajr Adhan conflict.
                # BUG FIX (Mon 25 May 2026 ~23:00 BST, found during 360° sanity test at aunt's):
                # job.next_run_time raises AttributeError when the job is in "pending" state
                # (i.e. added before scheduler.start()). schedule_today() runs at startup,
                # so all freshly-added jobs are pending and this fails silently — which then
                # crashes schedule_today() before warnings/dhikr/wakeup/periodic jobs are added.
                # Fix: read the scheduled time from the trigger directly (works at any time).
                fajr_adhan_time = None
                for job in sched.get_jobs():
                    if job.id == "adhan_Fajr":
                        fajr_adhan_time = (
                            getattr(getattr(job, 'trigger', None), 'run_date', None)
                            or getattr(job, 'next_run_time', None)
                        )
                        break

                # Skip if within 2 minutes of Fajr Adhan
                if fajr_adhan_time and abs((md - fajr_adhan_time).total_seconds()) < 120:
                    log.info(f"⏭️ Skipping Morning Dhikr ({md.strftime('%H:%M')}) — too close to Fajr Adhan ({fajr_adhan_time.strftime('%H:%M')})")
                else:
                    sched.add_job(play_morning_dhikr, DateTrigger(run_date=md), id="morning_dhikr", replace_existing=True)
                    log.info("Scheduled morning dhikr @ %s", md)

        # Wakeup - plays on all enabled speakers
        # S4 FIX (2026-05-23): respect explicit wakeup_enabled flag.
        # v1.8.8: default flipped True → False. The wakey-wakey alarm is OFF by
        # default on every portable CastAdhan (it's a personal alarm, not a prayer
        # feature); the owner opts in via the console. The False fallback also
        # disables it on existing installs whose config predates the flag.
        if RULES.get("wakeup_enabled", False) and RULES.get("wakeup_time"):
            should_schedule_wakeup = True
            if RULES.get("wakeup_weekdays_only", True):
                should_schedule_wakeup = now_local().weekday() < 5

            if should_schedule_wakeup:
                wu = today_at(RULES["wakeup_time"])
                if wu > now_local():
                    sched.add_job(play_wakeup, DateTrigger(run_date=wu), id="wakeup", replace_existing=True)
                    log.info("Scheduled wakeup @ %s", wu)
        elif RULES.get("wakeup_time") and not RULES.get("wakeup_enabled", True):
            log.info("⏸️ Wakeup is disabled (wakeup_enabled=false in config)")

        # Evening content (with end-time cutoff — silence if it would end after the cutoff)
        maghrib = times.get("Maghrib")
        if maghrib and RULES.get("evening_after_maghrib_minutes"):
            base = today_at(maghrib)
            dt = base + timedelta(minutes=int(RULES["evening_after_maghrib_minutes"]))
            try:
                dhikr_len = _audio_duration_seconds(AUDIO.get("evening_dhikr", "audio/evening_dhikr.mp3"))
            except Exception:
                dhikr_len = 0
            cutoff_str = RULES.get("evening_dhikr_cutoff_time", "20:00")
            try:
                cutoff_dt = today_at(cutoff_str)
            except Exception as e:
                log.error(f"Invalid evening_dhikr_cutoff_time {cutoff_str!r}: {e}")
                cutoff_dt = None
            end_dt = dt + timedelta(seconds=dhikr_len)
            if cutoff_dt is not None and end_dt > cutoff_dt:
                log.info(
                    "Evening dhikr suppressed: would end %s (after cutoff %s)",
                    end_dt.strftime("%H:%M"), cutoff_str,
                )
            elif dt > now_local():
                sched.add_job(play_evening_content, DateTrigger(run_date=dt), id="evening", replace_existing=True)
                log.info("Scheduled evening content @ %s (ends ~%s, cutoff %s)",
                         dt, end_dt.strftime("%H:%M"), cutoff_str)

        # Sunrise warning — 5 minutes before sunrise (end-of-Fajr reminder)
        sunrise_str = times.get("Sunrise")
        if sunrise_str:
            sunrise_warn_dt = today_at(sunrise_str) - timedelta(minutes=5)
            if sunrise_warn_dt > now_local():
                sched.add_job(play_sunrise_warning, DateTrigger(run_date=sunrise_warn_dt), id="sunrise_warning", replace_existing=True)
                log.info("Scheduled sunrise warning @ %s (5 min before sunrise)", sunrise_warn_dt)

        # Dhuhr warning — 10 minutes before Asr (end of Dhuhr time)
        asr_str = times.get("Asr")
        if asr_str:
            dhuhr_warn_dt = today_at(asr_str) - timedelta(minutes=10)
            if dhuhr_warn_dt > now_local():
                sched.add_job(play_dhuhr_warning, DateTrigger(run_date=dhuhr_warn_dt), id="dhuhr_warning", replace_existing=True)
                log.info("Scheduled Dhuhr warning @ %s (10 min before Asr)", dhuhr_warn_dt)

        # Asr warning — 10 minutes before Maghrib (12 min on Friday to clear Friday prayer)
        if maghrib:
            asr_warn_offset = 12 if now_local().weekday() == 4 else 10
            asr_warn_dt = today_at(maghrib) - timedelta(minutes=asr_warn_offset)
            if asr_warn_dt > now_local():
                sched.add_job(play_asr_warning, DateTrigger(run_date=asr_warn_dt), id="asr_warning", replace_existing=True)
                log.info("Scheduled Asr warning @ %s (%d min before Maghrib)", asr_warn_dt, asr_warn_offset)

        # Maghrib warning — 10 minutes before Isha (suspended when Isha is fully skipped)
        isha_str = times.get("Isha")
        isha_fully_skipped = skip_isha or skip_isha_due_to_twilight
        if isha_str and not isha_fully_skipped:
            maghrib_warn_dt = today_at(isha_str) - timedelta(minutes=10)
            if maghrib_warn_dt > now_local():
                sched.add_job(play_maghrib_warning, DateTrigger(run_date=maghrib_warn_dt), id="maghrib_warning", replace_existing=True)
                log.info("Scheduled Maghrib warning @ %s (10 min before Isha)", maghrib_warn_dt)
        elif isha_fully_skipped:
            log.info("Maghrib warning suspended: Isha is combined/skipped")

        # Friday prayer — plays on Fridays, finishing just before Maghrib adhan
        if maghrib and now_local().weekday() == 4 and "friday_prayer" in AUDIO:
            try:
                fp_len = _audio_duration_seconds(AUDIO["friday_prayer"])
                gap = float(RULES.get("friday_prayer_gap_seconds", 5))
                fp_dt = today_at(maghrib) - timedelta(seconds=fp_len + gap)
                if fp_dt > now_local():
                    sched.add_job(play_friday_prayer, DateTrigger(run_date=fp_dt), id="friday_prayer", replace_existing=True)
                    log.info("Scheduled Friday prayer @ %s (%.0fs audio + %.0fs gap before Maghrib)", fp_dt, fp_len, gap)
                else:
                    log.info("Skipping Friday prayer: scheduled time %s already passed", fp_dt)
            except Exception as e:
                log.error(f"Failed to schedule Friday prayer: {e}")

        # Re-add periodic jobs (only if they don't exist)
        schedule_cast_rediscovery()
        schedule_dst_protection()
        schedule_health_check()
        schedule_twilight_scan()
        schedule_daily_summary()  # v1.8.0: Telegram daily digest

        # v1.9.8: register today's scheduled_audio (Quran Programs) jobs.
        # Reads config.yaml `scheduled_audio` map, computes absolute fire
        # times for today (handles both fixed_time AND relative_to_prayer
        # via today's prayer-time table), registers DateTriggers.
        _schedule_custom_audio_jobs(times)

    except Exception as e:
        log.error(f"Error scheduling today's activities: {e}")
        log.error(traceback.format_exc())


def _compute_custom_audio_run_time(entry: dict, prayer_times: dict, target_date: date) -> Optional[datetime]:
    """Compute today's absolute fire time for one scheduled_audio entry.
    Returns None if entry not enabled / doesn't fire on this day / has no
    valid time. Handles fixed and relative_to_prayer trigger types."""
    if not entry.get("enabled"):
        return None

    # Day filter — days list uses Mon=0..Sun=6 (Python weekday convention)
    days = entry.get("days") or []
    if target_date.weekday() not in days:
        return None

    trigger_type = (entry.get("trigger_type") or "fixed").lower()

    if trigger_type == "fixed":
        play_time = entry.get("play_time") or ""
        try:
            hh, mm = play_time.split(":")
            return datetime.combine(target_date, datetime.min.time(),
                                    tzinfo=now_local().tzinfo) \
                .replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except (ValueError, AttributeError) as e:
            log.warning(f"Bad play_time in scheduled_audio: {play_time!r} ({e})")
            return None

    if trigger_type == "relative_to_prayer":
        anchor_name = (entry.get("relative_prayer_anchor") or "").capitalize()
        offset_min = int(entry.get("offset_minutes") or 0)
        prayer_str = prayer_times.get(anchor_name)
        if not prayer_str:
            log.warning(f"relative_to_prayer entry references unknown anchor {anchor_name!r}")
            return None
        try:
            hh, mm = prayer_str.split(":")
            anchor_dt = datetime.combine(target_date, datetime.min.time(),
                                         tzinfo=now_local().tzinfo) \
                .replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            return anchor_dt + timedelta(minutes=offset_min)
        except (ValueError, AttributeError) as e:
            log.warning(f"Bad anchor time for {anchor_name}: {prayer_str!r} ({e})")
            return None

    log.warning(f"Unknown trigger_type: {trigger_type!r}")
    return None


def _schedule_custom_audio_jobs(prayer_times: dict):
    """Register today's scheduled_audio entries as one-shot DateTrigger jobs.
    Called from schedule_today() each time prayer schedule is rebuilt.

    Conflict detection: warn if two entries land within ±60s of each other.
    No blocking — Cast device's load_media will pick whichever fires second
    and the operator will see the warning in the log.
    """
    with _state_lock:
        sa_map = (CFG.get("scheduled_audio") or {}).copy()
    if not sa_map:
        return

    # Pre-download any enabled-but-not-yet-downloaded entries (idempotent)
    for audio_id, entry in sa_map.items():
        if not entry.get("enabled"):
            continue
        local_path = _custom_audio_file_path(audio_id)
        if not os.path.isfile(local_path):
            url = entry.get("audio_url") or ""
            if url:
                _enqueue_custom_audio_download(audio_id, url)

    # Compute fire times + collect for conflict detection
    today = date.today()
    today_jobs = []  # list of (audio_id, fire_time, entry)
    for audio_id, entry in sa_map.items():
        # v1.9.9: the v1.9.8.2 surah_kahf skip is GONE — the legacy Friday
        # substitution in play_morning_dhikr was removed and Kahf now fires
        # solely through this path (migrated to fixed 07:00 Friday by
        # _migrate_kahf_to_scheduled_audio at startup).
        fire_dt = _compute_custom_audio_run_time(entry, prayer_times, today)
        if fire_dt is None:
            continue
        if fire_dt <= now_local():
            log.info(f"scheduled_audio.{audio_id}: today's slot ({fire_dt:%H:%M}) already passed, skipping")
            continue
        today_jobs.append((audio_id, fire_dt, entry))

    # Conflict warning: ±60s overlap
    sorted_jobs = sorted(today_jobs, key=lambda x: x[1])
    for i in range(len(sorted_jobs) - 1):
        a_id, a_dt, _ = sorted_jobs[i]
        b_id, b_dt, _ = sorted_jobs[i + 1]
        if abs((b_dt - a_dt).total_seconds()) <= 60:
            log.warning(
                f"⚠️  scheduled_audio collision: {a_id} @ {a_dt:%H:%M:%S} "
                f"and {b_id} @ {b_dt:%H:%M:%S} fire within 60 sec — "
                f"second will interrupt first via Cast load_media."
            )

    # Register the jobs
    for audio_id, fire_dt, entry in today_jobs:
        try:
            sched.add_job(
                _play_custom_audio,
                DateTrigger(run_date=fire_dt),
                args=[audio_id],
                id=f"scheduled_audio_{audio_id}",
                replace_existing=True,
            )
            log.info(f"Scheduled {entry.get('name', audio_id)} @ {fire_dt:%H:%M:%S} "
                     f"({entry.get('trigger_type', 'fixed')})")
        except Exception as e:
            log.error(f"Failed to register scheduled_audio.{audio_id}: {e}")

def job_listener(event):
    """Listen to job execution events"""
    if event.exception:
        log.error(f"Job {event.job_id} crashed: {event.exception}")
    else:
        log.debug(f"Job {event.job_id} executed successfully")

def sanity_check_audio():
    """Check that all required audio files exist"""
    missing_files = []
    for k, rel in AUDIO.items():
        if k == 'twilight':
            continue  # Optional
        p = abs_audio_path(rel)
        if not os.path.isfile(p):
            missing_files.append(f"'{k}': {p}")

    if missing_files:
        log.error("Missing audio files:")
        for missing in missing_files:
            log.error(f"  - {missing}")
        return False
    return True

def log_feature_summary():
    """Log all enabled features at startup for clarity"""
    log.info("=" * 50)
    log.info("CASTADHAN FEATURE SUMMARY")
    log.info("=" * 50)
    log.info(f"Location: {CITY}, {COUNTRY} ({LATITUDE}, {LONGITUDE})")
    log.info(f"Timezone: {TZ}")
    log.info(f"Fajr workday cap: {RULES.get('fajr_workday_cap', '07:00')}")
    log.info(f"Fajr weekend offset: {RULES.get('fajr_weekend_offset_minutes', -30)} minutes")
    log.info(f"Eid Takbeeraat enabled: {RULES.get('enable_eid_takbeeraat', True)}")
    log.info(f"High latitude method: {RULES.get('high_latitude_method', 'combine_prayers')}")
    log.info(f"Twilight scan frequency: {RULES.get('twilight_scan_frequency_days', 7)} days")
    log.info(f"Suhoor exclusions: {SPK.get('suhoor_exclude_names', [])}")
    log.info(f"Ramadan today: {is_ramadan_today()}")
    log.info("=" * 50)

# ---------------- Play Functions ----------------
def _play_to_targets(media_relpath: str, target: Optional[str] = None, audio_type: Optional[str] = None, prayer_name: Optional[str] = None):
    """Internal helper to play a given media file to enabled targets with routing awareness."""
    global _last_play_timestamp
    
    if shutdown_event.is_set():
        return

    # Prevent rapid-fire manual triggers
    with _play_lock:
        now = time.time()
        if now - _last_play_timestamp < MIN_PLAY_INTERVAL_SECONDS:
            log.warning(f"Play requested too soon after last play, ignoring")
            return
        _last_play_timestamp = now

    # B-Belgium-42 (v1.9.5): _play_to_targets now writes play_history.jsonl
    # for every call, covering play_twilight + play_takbeeraat_all + any test/
    # manual playback that routes through here. Before this only the adhan
    # path (play_adhan_all) recorded outcomes, leaving the dhikr/wakeup/
    # warning/twilight/takbeeraat plays invisible in /api/play_history —
    # making it impossible to audit "did the morning dhikr fire?" without
    # diving into castadhan.log.
    played = 0
    casts_played = []   # v1.9.9: track actual cast objects for verification
    try:
        url = local_media_url(media_relpath)

        if target and target.lower() != "all":
            cast = _cast_by_name(target)
            if cast:
                play_on_cast(cast, url, _speaker_volume(cast.name), audio_type, prayer_name)
                played = 1
                casts_played = [cast]
            else:
                log.warning(f"Target {target} not found")
        else:
            # Check if any speakers available
            casts = _all_casts()
            if not casts:
                log.warning("No speakers available for playback")
                if audio_type:
                    _log_play(audio_type, prayer_name, "NO_SPEAKERS", speakers_count=0)
                return

            for cast in casts:
                play_on_cast(cast, url, _speaker_volume(cast.name), audio_type, prayer_name)
                played += 1
            casts_played = list(casts)
        if audio_type:
            _log_play(audio_type, prayer_name,
                      "PASS" if played else "NO_SPEAKERS",
                      speakers_count=played)
            # v1.9.9: async verification — confirms a speaker is actually
            # playing 10s from now (adhan + scheduled:* only; fail-open).
            _verify_playback_async(casts_played, audio_type, prayer_name)
    except Exception as e:
        log.error(f"Error playing media {media_relpath}: {e}")
        if audio_type:
            _log_play(audio_type, prayer_name, "FAIL", speakers_count=played, error=e)

# ─────────────────────────────────────────────────────────────────────────────
# O21 + O25 FIX (v1.2.0, Tue 26 May 2026 — post-Belgium silent-Maghrib lesson):
#
# Before this fix, any play_* function called when _all_casts() returned [] would
# silently log an error (or worse, not even that — most just did `for c in []`),
# return cleanly, and APScheduler would mark the job "executed successfully".
# Aunt's silent Maghrib failure on 25 May had no operator-visible trace except a
# single ERROR line buried in journalctl. That's the worst-case product failure.
#
# This block adds:
#   1. _log_play(...): records every play attempt to an in-memory 50-entry ring
#      AND appends to a persistent /opt/castadhan-portable/play_history.jsonl
#      so the dashboard (and future-us SSHing in) can see "did Fajr fire?".
#   2. _ensure_speakers(...): wraps _all_casts() with one automatic rediscovery
#      attempt if the list is empty. Discovery is the most fragile layer
#      (B-Belgium-2/8/16) — auto-recovery here turns most "missing speaker"
#      cases into successful plays.
#   3. /api/play_history endpoint (defined further down with the other routes).
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Telegram notifications (v1.8.0)
#
# Optional. Reads a bot token + chat id from /etc/default/castadhan-telegram
# (root-owned, 0600, never committed — see deploy/castadhan-telegram.defaults.template).
# With both set, the Pi sends an immediate alert the moment an adhan FAILs
# (hooked in _log_play below) and a daily digest at 23:15 local. With nothing
# configured, every send is a silent no-op so playback is completely unaffected.
# ─────────────────────────────────────────────────────────────────────────────
_TELEGRAM_CONFIG_FILE = "/etc/default/castadhan-telegram"

def _telegram_config() -> Tuple[Optional[str], Optional[str]]:
    """Return (bot_token, chat_id). Env vars win (for dev), then the root-only
    config file. Returns (None, None) if either is unset. Never raises."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    try:
        if (not token or not chat_id) and os.path.exists(_TELEGRAM_CONFIG_FILE):
            with open(_TELEGRAM_CONFIG_FILE) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "TELEGRAM_BOT_TOKEN" and not token:
                        token = v
                    elif k == "TELEGRAM_CHAT_ID" and not chat_id:
                        chat_id = v
    except Exception as e:
        log.error(f"Telegram config read failed: {e}")
    return (token or None, chat_id or None)

def _telegram_send(text: str) -> bool:
    """Send a Telegram message. Returns False if unconfigured or on any error.
    Never raises — notifications must never break playback."""
    token, chat_id = _telegram_config()
    if not token or not chat_id:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            log.error(f"Telegram send failed: HTTP {r.status_code} {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False

import collections
_play_history = collections.deque(maxlen=50)
_play_history_lock = threading.Lock()
_PLAY_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "play_history.jsonl")

def _log_play(audio_type: str, prayer_name: Optional[str], status: str, speakers_count: int = 0, error: Optional[Exception] = None):
    """Record a play attempt for visibility.
    status is one of: PASS, FAIL, NO_SPEAKERS, DISCOVERY_RECOVERED, SKIPPED_HOLD,
    SUPPRESSED (volume policy), SILENT_EXPECTED (v1.8.14 — NO_SPEAKERS for a
    prayer in expected_silent_prayers; intentionally silent, not a failure)."""
    try:
        # v1.8.14: a NO_SPEAKERS for an owner-whitelisted prayer is recorded as
        # SILENT_EXPECTED, NOT NO_SPEAKERS. This downstream-suppresses the
        # instant alert, the daily-digest warning, and the L11 sanity HIGH-fail.
        # A real FAIL on the same prayer (cast timeout etc.) still alerts.
        if (status == "NO_SPEAKERS" and audio_type == "adhan"
                and prayer_name
                and prayer_name in (RULES.get("expected_silent_prayers") or [])):
            status = "SILENT_EXPECTED"
        entry = {
            "ts_utc": datetime.now(utc).isoformat(timespec="seconds"),
            "ts_local": now_local().isoformat(timespec="seconds"),
            "audio_type": audio_type,
            "prayer_name": prayer_name,
            "status": status,
            "speakers_count": speakers_count,
            "error": (type(error).__name__ + ": " + str(error)) if error else None,
        }
        with _play_history_lock:
            _play_history.append(entry)
            try:
                with open(_PLAY_HISTORY_FILE, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except Exception as e:
                log.error(f"play_history write failed: {e}")
        # v1.8.0: immediate Telegram alert on a genuine playback failure.
        # Outside the lock so the network send can't block other plays.
        # SKIPPED_HOLD (Emergency Stop), DISCOVERY_RECOVERED, SUPPRESSED (quiet
        # hours policy) and SILENT_EXPECTED (v1.8.14 whitelist) are not failures.
        # v1.9.9: FAIL_VERIFIED = play command was sent but no speaker was
        # actually playing 10s later (the masood-SD-failure blind spot).
        if status in ("FAIL", "NO_SPEAKERS", "FAIL_VERIFIED"):
            try:
                label = prayer_name or audio_type or "audio"
                # v1.8.14: include the Pi's Tailscale IP so the owner can SSH /
                # open the dashboard from the alert without looking it up.
                ts_ip = _get_tailscale_ip()
                msg = (f"⚠️ CastAdhan ({_site_label()}): {label} did NOT play "
                       f"({status}) at {entry['ts_local'][11:16]}.")
                if ts_ip:
                    msg += f"\nTailscale: http://{ts_ip}:8786"
                if entry.get("error"):
                    msg += f"\n{entry['error']}"
                _telegram_send(msg)
            except Exception as e:
                log.error(f"Telegram failure-alert error: {e}")
    except Exception as e:
        # Logging must never break playback. Swallow.
        log.error(f"_log_play internal error: {e}")

def _verify_playback_async(casts: list, audio_type: str, prayer_name: Optional[str] = None):
    """v1.9.9: confirm audio is ACTUALLY playing ~10s after the play command.

    Until now PASS meant "command sent". During masood's SD-card failure
    (9 Jun) the speaker fetched a 404 and played silence while history said
    PASS — the audit trail lied exactly when it mattered. This poller closes
    that gap: 10s after the play, read each cast's media status; if NONE of
    the targeted speakers is PLAYING/BUFFERING, append a FAIL_VERIFIED entry
    (which also fires the immediate Telegram alert via _log_play).

    Scope: only adhan and scheduled:* audio — the prayer-critical types.
    Warnings/dhikr can be legitimately routed off per-speaker, which would
    make a blanket verifier cry wolf.

    Fail-open: any exception in the poller degrades to "no verification",
    never to a false FAIL. Success is logged at INFO, not appended to history
    (keeps the JSONL lean; absence of FAIL_VERIFIED == verified-or-unverifiable)."""
    if not casts:
        return
    if not (audio_type == "adhan" or (audio_type or "").startswith("scheduled:")):
        return

    def _poll():
        try:
            time.sleep(10)
            if shutdown_event.is_set():
                return
            playing = []
            for cast in casts:
                try:
                    mc = cast.media_controller
                    try:
                        mc.update_status()
                        time.sleep(1)
                    except Exception:
                        pass   # cached status is still usable
                    state = getattr(getattr(mc, "status", None), "player_state", None)
                    if state in ("PLAYING", "BUFFERING"):
                        playing.append(cast.name)
                except Exception as e:
                    log.debug(f"verify: status read failed for "
                              f"{getattr(cast, 'name', '?')}: {e}")
            if playing:
                log.info(f"✓ Playback verified for {audio_type} "
                         f"{prayer_name or ''}: {playing}")
            else:
                log.critical(f"❌ Playback NOT verified for {audio_type} "
                             f"{prayer_name or ''} — no targeted speaker is "
                             f"playing 10s after the play command")
                _log_play(audio_type, prayer_name, "FAIL_VERIFIED",
                          speakers_count=0,
                          error=Exception("no speaker playing 10s after play command"))
        except Exception as e:
            log.error(f"verify poller error (degrading to unverified): {e}")

    threading.Thread(target=_poll, daemon=True,
                     name=f"verify-{audio_type}").start()


def _ensure_speakers(audio_type: str, prayer_name: Optional[str] = None):
    """Return list of casts; if empty, auto-retry discovery ONCE; if still empty,
    log CRITICAL + record NO_SPEAKERS in play history + return []."""
    casts = _all_casts()
    if casts:
        return casts
    log.warning(f"⚠️  Zero speakers for {audio_type} {prayer_name or ''} — forcing rediscovery before giving up")
    try:
        discover_casts()
    except Exception as e:
        log.error(f"Rediscovery attempt failed: {e}")
    casts = _all_casts()
    if not casts:
        # CRITICAL log — visible in `systemctl status` and any log alerting.
        # Persisted to play history so the dashboard can show a banner.
        log.critical(f"❌ NO SPEAKERS AVAILABLE for {audio_type} {prayer_name or ''} — discovery returned 0 after one retry. Recorded to play_history.jsonl.")
        _log_play(audio_type, prayer_name, "NO_SPEAKERS")
        return []
    log.info(f"✅ Rediscovery recovered {len(casts)} speaker(s) for {audio_type}")
    _log_play(audio_type, prayer_name, "DISCOVERY_RECOVERED", speakers_count=len(casts))
    return casts

def play_takbeeraat_all(target: Optional[str] = None, prayer_name: Optional[str] = None):
    """Play Takbeeraat on enabled speakers (respects enable flags).
    prayer_name lets the volume policy apply the explicit Fajr-takbeeraat suppress."""
    if shutdown_event.is_set():
        return
    if "takbeeraat" not in AUDIO:
        log.warning("Takbeeraat requested but AUDIO['takbeeraat'] missing")
        return
    log.info("🕌 Playing Takbeeraat on enabled speakers")
    _play_to_targets(AUDIO["takbeeraat"], target=target, audio_type="takbeeraat", prayer_name=prayer_name)

def play_twilight(target: Optional[str] = None):
    """Play twilight reminder that Isha is combined with Maghrib."""
    if shutdown_event.is_set():
        return
    if "twilight" not in AUDIO:
        log.warning("Twilight audio missing - skipping")
        return
    log.info("🌅 Playing twilight reminder (Isha combined with Maghrib)")
    _play_to_targets(AUDIO["twilight"], target=target, audio_type="twilight")

def play_adhan_all(target: Optional[str] = None, prayer_name: Optional[str] = None):
    """Play Adhan on all enabled speakers, then chain appropriate follow-up audio.

    O21 + O25 (Tue 26 May 2026): uses _ensure_speakers() which auto-retries
    discovery once before declaring 0 speakers, and records every attempt
    (PASS / FAIL / NO_SPEAKERS) to play_history.jsonl + a 50-entry ring
    visible via /api/play_history. This makes the silent-Maghrib failure
    of 25 May 2026 detectable from the dashboard instead of buried in journal.

    C-4 (v1.5.0): respects scheduler hold from emergency stop.
    """
    if shutdown_event.is_set():
        return
    if _scheduler_held():
        log.warning(f"⏸️  Adhan for {prayer_name} skipped: scheduler is on hold (Emergency Stop active until {_scheduler_hold.get('until')})")
        _log_play("adhan", prayer_name, "SKIPPED_HOLD", speakers_count=0)
        return

    log.info(f"Playing Adhan for {prayer_name} on all enabled speakers")

    # O21: auto-retry discovery once; if still 0, log CRITICAL + record NO_SPEAKERS.
    casts = _ensure_speakers("adhan", prayer_name)
    if not casts:
        return  # _ensure_speakers already logged + recorded

    try:
        _play_to_targets(AUDIO["adhan"], target=target, audio_type="adhan")

        # Chain follow-up audio based on context.
        #
        # v1.6.2 ARCHITECTURAL FIX (Tue 26 May 2026 — surfaced by user:
        # "Twilight notification didn't come on after Maghrib"):
        # Before v1.6.2 the chained audio was scheduled via daemon threads with
        # time.sleep(). Daemon threads DIE on service restart. The auto-update
        # mechanism (or any other restart) between the trigger adhan and the
        # chained audio meant the chained audio was permanently lost.
        # This happened on aunt's Pi at 21:44 Maghrib on 26 May when I
        # restarted at 21:45 for the v1.6.1 hotfix — the twilight thread died.
        #
        # Now both chained jobs are added as APScheduler one-shot jobs with
        # DateTrigger(run_date=...). APScheduler stores them and they fire even
        # if the service restarts in between. Same audio, same delay — just
        # restart-resilient.
        try:
            now = now_local()
            # 1. Eid Takbeeraat chaining.
            # v1.6.2: enforce minimum 180s delay (typical adhan length). If
            # _audio_duration_seconds() can't read the mp3 (missing ffprobe /
            # corrupt metadata), the default 0 would overlap the adhan with
            # the takbeeraat — embarrassing on Eid morning. Floor protects us.
            if should_play_takbeerat_after_adhan(prayer_name=prayer_name, when=now):
                adhan_len = _audio_duration_seconds(AUDIO["adhan"])
                if not adhan_len or adhan_len < 60:
                    adhan_len = 180  # safe default ≈ 3 min
                fire_at = now + timedelta(seconds=adhan_len + 0.5)
                try:
                    sched.add_job(
                        play_takbeeraat_all,
                        DateTrigger(run_date=fire_at),
                        kwargs={"target": target, "prayer_name": prayer_name},
                        id=f"takbeeraat_after_{prayer_name}_{now.strftime('%Y%m%d%H%M%S')}",
                        replace_existing=True,
                    )
                    log.info(f"🕌 Eid takbeeraat scheduled @ {fire_at.strftime('%H:%M:%S')} ({adhan_len:.0f}s after {prayer_name} adhan) — survives restart")
                except Exception as e:
                    log.error(f"Failed to schedule takbeeraat job: {e}")

            # 2. Twilight reminder for Maghrib during combined prayer period
            if prayer_name == "Maghrib" and not should_play_isha(date.today()):
                with _twilight_lock:
                    method = _twilight_cache["high_latitude_method"]

                if method == 'combine_prayers':
                    maghrib_len = _audio_duration_seconds(AUDIO["adhan"])
                    if not maghrib_len or maghrib_len < 60:
                        maghrib_len = 180  # v1.6.2 safe default ≈ 3 min
                    fire_at = now + timedelta(seconds=maghrib_len + 1.0)
                    try:
                        sched.add_job(
                            play_twilight,
                            DateTrigger(run_date=fire_at),
                            kwargs={"target": target},
                            id=f"twilight_after_maghrib_{now.strftime('%Y%m%d%H%M%S')}",
                            replace_existing=True,
                        )
                        log.info(f"🌅 Twilight reminder scheduled @ {fire_at.strftime('%H:%M:%S')} ({maghrib_len:.0f}s after Maghrib adhan) — survives restart")
                    except Exception as e:
                        log.error(f"Failed to schedule twilight job: {e}")

        except Exception as e:
            log.error(f"Follow-up audio chaining error: {e}")

        # O25: record successful play attempt for the dashboard widget.
        _log_play("adhan", prayer_name, "PASS", speakers_count=len(casts))

    except Exception as e:
        log.error(f"Error playing Adhan: {e}")
        _log_play("adhan", prayer_name, "FAIL", speakers_count=len(casts), error=e)

def play_sunrise_warning():
    """Play fajr warning audio 5 minutes before sunrise — end-of-Fajr reminder"""
    if shutdown_event.is_set():
        return

    log.info("Playing fajr warning (5 min to sunrise) on all enabled speakers")

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO["fajr_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "fajr_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "fajr_warning")
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for fajr_warning, skipping")
        _log_play("fajr_warning", None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing fajr warning: {e}")
        _log_play("fajr_warning", None, "FAIL", speakers_count=played, error=e)

def play_asr_warning():
    """Play Asr warning audio 5 minutes before Asr time"""
    if shutdown_event.is_set():
        return

    log.info("Playing Asr warning (5 min to Asr) on all enabled speakers")

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO["asr_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "asr_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "asr_warning")
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for asr_warning, skipping")
        _log_play("asr_warning", None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing Asr warning: {e}")
        _log_play("asr_warning", None, "FAIL", speakers_count=played, error=e)

def play_dhuhr_warning():
    """Play Dhuhr warning audio 10 minutes before Dhuhr time"""
    if shutdown_event.is_set():
        return

    log.info("Playing Dhuhr warning (10 min to Dhuhr) on all enabled speakers")

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO["dhuhr_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "dhuhr_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "dhuhr_warning")
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for dhuhr_warning, skipping")
        _log_play("dhuhr_warning", None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing Dhuhr warning: {e}")
        _log_play("dhuhr_warning", None, "FAIL", speakers_count=played, error=e)

def play_maghrib_warning():
    """Play Maghrib warning audio 5 minutes before Maghrib time"""
    if shutdown_event.is_set():
        return

    log.info("Playing Maghrib warning (5 min to Maghrib) on all enabled speakers")

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO["maghrib_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "maghrib_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "maghrib_warning")
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for maghrib_warning, skipping")
        _log_play("maghrib_warning", None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing Maghrib warning: {e}")
        _log_play("maghrib_warning", None, "FAIL", speakers_count=played, error=e)

def play_morning_dhikr():
    """Play morning dhikr on all enabled speakers.

    v1.9.9 (Kahf migration): the Friday SUBSTITUTION (Kahf instead of dhikr)
    that lived here since the early releases is gone — Surah al-Kahf now fires
    exclusively through the scheduled_audio engine (default: fixed 07:00
    Friday, migrated per-Pi by _migrate_kahf_to_scheduled_audio). To preserve
    the historical "Kahf replaces dhikr on Friday" behaviour and avoid both
    firing into the same speakers at 07:00, dhikr YIELDS on Fridays whenever
    the Kahf schedule is enabled. Disable the Kahf card and Friday dhikr
    returns automatically."""
    if shutdown_event.is_set():
        return

    if now_local().weekday() == 4:
        try:
            with _state_lock:
                kahf_enabled = bool((CFG.get("scheduled_audio") or {})
                                    .get("surah_kahf", {}).get("enabled"))
        except Exception:
            kahf_enabled = False
        if kahf_enabled:
            log.info("Morning dhikr yielded: Friday + Surah al-Kahf schedule enabled")
            return

    log.info("Playing morning dhikr on all enabled speakers")
    audio_key = "morning_dhikr"

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO[audio_key])

        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, audio_key):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, audio_key)
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for {audio_key}, skipping")
        _log_play(audio_key, None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing morning dhikr: {e}")
        _log_play(audio_key, None, "FAIL", speakers_count=played, error=e)

def play_evening_content():
    """Play evening content — evening dhikr every evening"""
    if shutdown_event.is_set():
        return

    log.info("Playing evening dhikr on all enabled speakers")
    audio_key = "evening_dhikr"

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO[audio_key])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, audio_key):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, audio_key)
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for {audio_key}, skipping")
        _log_play(audio_key, None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing evening content: {e}")
        _log_play(audio_key, None, "FAIL", speakers_count=played, error=e)

def play_friday_prayer():
    """Play Friday prayer (Dua of the Soul) — scheduled to finish just before Maghrib adhan."""
    if shutdown_event.is_set():
        return
    if now_local().weekday() != 4:
        log.info("Skipping Friday prayer: not Friday (weekday=%s)", now_local().weekday())
        return

    log.info("Playing Friday prayer (Dua of the Soul) on all enabled speakers")
    audio_key = "friday_prayer"

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO[audio_key])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, audio_key):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, audio_key)
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for {audio_key}, skipping")
        _log_play(audio_key, None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing Friday prayer: {e}")
        _log_play(audio_key, None, "FAIL", speakers_count=played, error=e)

def play_wakeup():
    """Play wakeup audio on all enabled speakers"""
    if shutdown_event.is_set():
        return

    log.info("Playing wakeup audio on all enabled speakers")

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        url = local_media_url(AUDIO["wakeup"])

        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "wakeup"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "wakeup")
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for wakeup, skipping")
        _log_play("wakeup", None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing wakeup: {e}")
        _log_play("wakeup", None, "FAIL", speakers_count=played, error=e)

def play_suhoor_alarm():
    """Play suhoor alarm on enabled speakers during Ramadan (with configurable exclusions and routing)."""
    if shutdown_event.is_set():
        return

    log.info("🌙 Playing Suhoor alarm on enabled speakers (configurable exclusions applied)")

    # B-Belgium-42 (v1.9.5): record outcome to play_history.jsonl.
    played = 0
    try:
        # Stop any currently playing audio
        stop_all_audio()
        time.sleep(1)  # Brief pause to ensure clean start

        url = local_media_url(AUDIO["suhoor_alarm"])

        # Use all speakers with routing
        with _cast_lock:
            targets = list(_general_casts)

        for cast in targets:
            if _should_play_on_speaker(cast.name, "suhoor_alarm"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "suhoor_alarm")
                played += 1
            else:
                log.debug(f"Speaker {cast.name} routing disabled for suhoor, skipping")
        _log_play("suhoor_alarm", None,
                  "PASS" if played else "NO_SPEAKERS", speakers_count=played)
    except Exception as e:
        log.error(f"Error playing suhoor alarm: {e}")
        _log_play("suhoor_alarm", None, "FAIL", speakers_count=played, error=e)

def _next_prayer_with_effective(nxt):
    """O32 (v1.2.0, Tue 26 May 2026): enrich next_prayer with the EFFECTIVE
    scheduled time (i.e. what will actually play), distinct from the raw
    aladhan-calculated time. They differ when high_latitude_method shifts
    Fajr or Isha (e.g. combine_prayers in Belgium summer: raw Fajr 03:42 but
    actual adhan plays at 05:11). Before this fix the dashboard showed only
    the raw time, leading aunt to wait at 03:42 for adhan that wouldn't fire
    for another 89 minutes. UI must show both when they differ."""
    out = {
        "name": nxt.get("name"),
        "when_iso": nxt.get("when"),
        "time_pretty": nxt.get("time_pretty"),
        "effective_when_iso": nxt.get("when"),
        "effective_time_pretty": nxt.get("time_pretty"),
        "shifted": False,
        "shift_reason": None,
    }
    try:
        # Find the actual scheduled adhan job for this prayer (if any)
        target_id = f"adhan_{nxt.get('name')}"
        for job in sched.get_jobs():
            if job.id == target_id:
                run_dt = getattr(getattr(job, 'trigger', None), 'run_date', None) or getattr(job, 'next_run_time', None)
                if run_dt:
                    out["effective_when_iso"] = run_dt.isoformat()
                    out["effective_time_pretty"] = run_dt.strftime("%H:%M")
                    if out["effective_time_pretty"] != out["time_pretty"]:
                        out["shifted"] = True
                        method = _twilight_cache.get("high_latitude_method", "")
                        if _twilight_cache.get("persistent_twilight_active"):
                            out["shift_reason"] = f"High-latitude rule ({method}) shifted from raw {out['time_pretty']} to {out['effective_time_pretty']} — persistent twilight active for your location"
                        else:
                            out["shift_reason"] = f"Calculated time shifted from raw {out['time_pretty']} to {out['effective_time_pretty']} by {method} rule"
                break
    except Exception as e:
        log.error(f"_next_prayer_with_effective error: {e}")
    return out

# ---------------- API Routes ----------------
@app.route("/api/state")
def api_state():
    """Get current application state"""
    try:
        with _cast_lock:
            devices = {
                "speakers": [_cast_info(c) for c in _general_casts],
            }

        with _state_lock:
            enabled = UI["enabled"].copy()
            volumes = UI["volumes"].copy()

        today = date.today()
        times = get_times_for(today)
        cn = compute_current_next()
        current = cn["current"]
        nxt = cn["next"]

        # Get scheduled jobs info
        jobs_info = []
        for job in sched.get_jobs():
            jobs_info.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None
            })

        # Hijri snapshot (sunset-aware)
        h_now = hijri_now_sunset_aware(now_local())

        # Get audio routing for all speakers
        audio_routing = {}
        with _cast_lock:
            for c in _general_casts:
                audio_routing[c.name] = get_speaker_audio_routing(c.name)

        return jsonify({
            "ok": True,
            "location": {"city": CITY, "country": COUNTRY, "timezone": TZ},
            "devices": devices,
            "enabled": enabled,
            "volumes": volumes,
            "prayer_times": times,  # Include all times including Sunrise
            "current_prayer": current,
            "next_prayer": _next_prayer_with_effective(nxt),
            "scheduled_jobs": jobs_info,
            "ramadan": is_ramadan_today(),
            "hijri_now_sunset_aware": h_now,
            "now": now_local().isoformat(),
            "persistent_twilight": {
                "active": _twilight_cache["persistent_twilight_active"],
                "method": _twilight_cache["high_latitude_method"],
                "start": _twilight_cache["persistent_start"].isoformat() if _twilight_cache["persistent_start"] else None,
                "end": _twilight_cache["persistent_end"].isoformat() if _twilight_cache["persistent_end"] else None
            },
            "audio_types": sorted(AUDIO.keys()),
            "audio_routing": CFG.get("speakers", {}).get("audio_routing", {}),
            "scheduler_running": _scheduler_started,
            # C-4 (v1.5.0): expose hold state so dashboard banner stays in sync
            "scheduler_hold": {
                "held": _scheduler_held(),
                "until": _scheduler_hold.get("until") if _scheduler_held() else None,
                "reason": _scheduler_hold.get("reason") if _scheduler_held() else None,
            },
        })
    except Exception as e:
        log.error(f"Error getting state: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speaker/toggle", methods=["POST"])
def api_toggle_speaker():
    """Toggle speaker enabled/disabled state"""
    try:
        data = request.get_json(force=True)
        name = data.get("name")
        enabled = bool(data.get("enabled", True))

        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400

        with _state_lock:
            UI["enabled"]["speakers"][name] = enabled
            _save_state(UI)

        # Stop playback if disabling
        if not enabled:
            c = _cast_by_name(name)
            if c:
                try:
                    ensure_connected(c)
                    c.media_controller.stop()
                    with _cast_lock:
                        _speaker_playback_status[name] = False
                    log.info(f"Stopped playback on disabled speaker: {name}")
                except Exception as e:
                    log.warning(f"Could not stop playback on {name}: {e}")

        log.info(f"Speaker {name} {'enabled' if enabled else 'disabled'}")
        return jsonify({"ok": True, "name": name, "enabled": enabled})

    except Exception as e:
        log.error(f"Error toggling speaker: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speakers/toggle_all", methods=["POST"])
def api_toggle_all():
    """Toggle all speakers enabled/disabled"""
    try:
        data = request.get_json(force=True)
        enabled = bool(data.get("enabled", True))

        with _state_lock:
            UI["enabled"]["global"] = enabled
            for c in _all_casts():
                UI["enabled"]["speakers"][c.name] = enabled
            _save_state(UI)

        # Stop all playback if disabling
        if not enabled:
            for c in _all_casts():
                try:
                    ensure_connected(c)
                    c.media_controller.stop()
                    with _cast_lock:
                        _speaker_playback_status[c.name] = False
                except Exception as e:
                    log.warning(f"Could not stop playback on {c.name}: {e}")

        log.info(f"All speakers {'enabled' if enabled else 'disabled'}")
        return jsonify({"ok": True, "enabled": enabled})

    except Exception as e:
        log.error(f"Error toggling all speakers: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speaker/volume", methods=["POST"])
def api_volume():
    """Set speaker volume"""
    try:
        data = request.get_json(force=True)
        name = data.get("name")
        vol = int(data.get("volume", _default_volume))
        vol = max(0, min(vol, 100))

        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400

        with _state_lock:
            UI["volumes"][name] = vol
            _save_state(UI)

        # Apply volume to device if available
        c = _cast_by_name(name)
        if c:
            try:
                ensure_connected(c)
                c.set_volume(vol / 100.0)
                log.info(f"Set volume on {name} to {vol}%")
            except Exception as e:
                log.warning(f"Could not set volume on {name}: {e}")

        return jsonify({"ok": True, "name": name, "volume": vol})

    except Exception as e:
        log.error(f"Error setting volume: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speaker/routing", methods=["POST"])
def api_set_routing():
    """Set audio routing for a specific speaker"""
    try:
        data = request.get_json(force=True)
        name = data.get("name")
        routing = data.get("routing", {})
        
        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400
        
        set_speaker_audio_routing(name, routing)
        
        log.info(f"Updated audio routing for {name}: {routing}")
        return jsonify({"ok": True, "name": name, "routing": get_speaker_audio_routing(name)})
        
    except Exception as e:
        log.error(f"Error setting routing: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speaker/status")
def api_speaker_status():
    """Get real-time status of all speakers (playing/stopped)"""
    try:
        # Update playback status for all speakers
        with _cast_lock:
            for cast in _general_casts:
                try:
                    ensure_connected(cast)
                    mc = cast.media_controller
                    if mc.status and mc.status.player_state == "PLAYING":
                        _speaker_playback_status[cast.name] = True
                    else:
                        _speaker_playback_status[cast.name] = False
                except:
                    _speaker_playback_status[cast.name] = False
            
            return jsonify({
                "ok": True,
                "status": _speaker_playback_status.copy()
            })
    except Exception as e:
        log.error(f"Error getting speaker status: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/play_history")
def api_play_history():
    """O25: Return last 50 play attempts (in-memory ring + tail of persistent
    play_history.jsonl). Used by the dashboard to show 'did Fajr fire today?'.

    Query params:
      ?status=NO_SPEAKERS,FAIL   filter to alarming statuses only
      ?limit=N                   max entries to return (default 50, max 500)
    """
    try:
        status_filter = (request.args.get("status") or "").split(",") if request.args.get("status") else None
        try:
            limit = min(int(request.args.get("limit", 50)), 500)
        except ValueError:
            limit = 50

        # Start with in-memory ring (most recent up to 50)
        with _play_history_lock:
            entries = list(_play_history)

        # If asking for more than the ring holds, pull from disk
        if limit > len(entries) and os.path.exists(_PLAY_HISTORY_FILE):
            try:
                with open(_PLAY_HISTORY_FILE) as f:
                    disk = [json.loads(line) for line in f if line.strip()]
                # Dedupe: prefer in-memory entries (more recent), append older from disk
                seen = {(e.get("ts_utc"), e.get("audio_type"), e.get("prayer_name")) for e in entries}
                for e in reversed(disk):
                    key = (e.get("ts_utc"), e.get("audio_type"), e.get("prayer_name"))
                    if key not in seen:
                        entries.insert(0, e)
                        seen.add(key)
                    if len(entries) >= limit:
                        break
            except Exception as e:
                log.warning(f"play_history disk read failed: {e}")

        if status_filter:
            entries = [e for e in entries if e.get("status") in status_filter]

        return jsonify({
            "ok": True,
            "count": len(entries),
            "entries": entries[-limit:],
        })
    except Exception as e:
        log.error(f"play_history endpoint error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/test/play", methods=["POST"])
def api_test_play():
    """Test play audio. Body: { "device": <name|null>, "type": <audio_key|"adhan"> }"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        name      = body.get("device") or request.args.get("device")
        audio_key = body.get("type", "adhan")

        if audio_key != "adhan" and audio_key in AUDIO:
            url = AUDIO[audio_key]
            log.info(f"Test playing {audio_key} on {'all' if not name else name}")
            _play_to_targets(url, target=name, audio_type=audio_key)
            return jsonify({"ok": True, "target": name or "all", "type": audio_key})

        if name:
            log.info(f"Test playing Adhan on {name}")
            play_adhan_all(target=name, prayer_name="TEST")
        else:
            log.info("Test playing Adhan on all devices")
            play_adhan_all(prayer_name="TEST")

        return jsonify({"ok": True, "target": name or "all", "type": "adhan"})

    except Exception as e:
        log.error(f"Error in test play: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/test/pattern", methods=["POST"])
def api_test_pattern():
    """Run test pattern to identify speakers"""
    try:
        log.info("Starting speaker test pattern")
        play_test_pattern()
        return jsonify({"ok": True, "message": "Test pattern started"})
    except Exception as e:
        log.error(f"Error in test pattern: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/test/stop", methods=["POST"])
def api_test_stop():
    """Stop playback on all devices"""
    try:
        stopped_count = stop_all_audio()
        log.info(f"Stopped playback on {stopped_count} devices")
        return jsonify({"ok": True, "stopped_devices": stopped_count})

    except Exception as e:
        log.error(f"Error stopping playback: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/emergency/stop", methods=["POST"])
def api_emergency_stop():
    """Emergency stop all audio + scheduler hold 60 min.

    C-4 (v1.5.0): now meaningfully different from /api/test/stop. Stops
    playback AND puts the scheduler on a 60-min hold so a misfiring job
    that you just silenced won't immediately re-fire. The hold is
    persisted to disk, so a service restart preserves it."""
    try:
        log.warning("🚨 EMERGENCY STOP triggered via API")
        stopped_count = emergency_stop_all()
        return jsonify({
            "ok": True,
            "stopped_devices": stopped_count,
            "scheduler_hold_until": _scheduler_hold.get("until"),
            "message": "EMERGENCY STOP — audio stopped, scheduler held for 60 minutes"
        })
    except Exception as e:
        log.error(f"Error in emergency stop: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/scheduler/hold", methods=["GET"])
def api_scheduler_hold_status():
    """C-4 (v1.5.0): inspect any active scheduler hold."""
    held = _scheduler_held()
    return jsonify({
        "ok": True,
        "held": held,
        "until": _scheduler_hold.get("until") if held else None,
        "reason": _scheduler_hold.get("reason") if held else None,
    })

@app.route("/api/scheduler/resume", methods=["POST"])
def api_scheduler_resume():
    """C-4 (v1.5.0): lift any active scheduler hold immediately, so prayers
    resume firing on schedule. Use this when you've silenced a misfiring
    Emergency Stop and want to bring the system back to normal earlier than
    the 60-min auto-release."""
    try:
        _clear_scheduler_hold()
        return jsonify({"ok": True, "message": "Scheduler resumed — prayer schedule will fire normally from next prayer."})
    except Exception as e:
        log.error(f"Error resuming scheduler: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/rediscover", methods=["POST"])
def api_rediscover():
    """Rediscover Cast devices"""
    try:
        log.info("Manual device rediscovery requested")
        discover_casts()
        return api_state()
    except Exception as e:
        log.error(f"Error during rediscovery: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speakers/force_discover", methods=["POST"])
def api_force_discover():
    """Force immediate rediscovery and return results"""
    try:
        log.info("Force rediscovery requested")
        discover_casts()
        with _cast_lock:
            speakers = [_cast_info(c) for c in _general_casts]
        return jsonify({
            "ok": True,
            "speakers": speakers,
            "count": len(speakers)
        })
    except Exception as e:
        log.error(f"Error during force rediscovery: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/speakers/add_by_ip", methods=["POST"])
def api_add_speaker_by_ip():
    """O36 (v1.2.0, Tue 26 May 2026): manually register a Cast speaker by IP.

    Backend equivalent of the dashboard's "Add Speaker by IP" button. Steps:
      1. Validate the IPv4 string.
      2. Probe the IP on port 8009 (Cast control port) to confirm it's likely a
         Cast device.
      3. Call pychromecast.get_chromecast_from_host(...) to instantiate the
         Chromecast (this works even when zeroconf is silent — same fallback
         path that discover_casts() uses for known_hosts).
      4. Read the device's friendly name from its Cast status.
      5. Persist {name: ip} to known_speakers.json so it survives restarts.
      6. Trigger discover_casts() to fold the new speaker into _general_casts.

    Solves the "I added a speaker yesterday and the dashboard doesn't see it"
    failure that aunt hit with the 'Slaap' Nest Mini (B-Belgium-16).
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        ip = (body.get("ip") or "").strip()
        # B-Belgium-34 (v1.9.3): honour a user-supplied name if provided.
        # Previously the handler ignored the `name` field on the request body —
        # the friendly name only came from the Cast handshake, which routinely
        # times out or returns the IP placeholder on slow / partially-isolated
        # networks. Result: the saved name was the IP string, and a later
        # rediscovery picked up the SAME device with its real friendly_name and
        # stored it as a SECOND entry → duplicate speaker on every dashboard.
        supplied_name = (body.get("name") or "").strip()
        if not ip:
            return jsonify({"ok": False, "error": "ip required"}), 400
        import re as _re
        if not _re.match(r"^(\d{1,3}\.){3}\d{1,3}$", ip):
            return jsonify({"ok": False, "error": f"not a valid IPv4 address: {ip}"}), 400
        # Probe Cast control port via shared helper (v1.4.0 / O39).
        # Slightly longer timeout here (3s) than the discovery-loop default (1.5s)
        # because this is an interactive user action — they expect a definitive
        # "yes/no it's a Cast device" answer, even on a slow wireless network.
        if not _is_cast_port_alive(ip, timeout=3.0):
            return jsonify({"ok": False, "error": f"{ip}:8009 unreachable. Is the speaker on, on the same WiFi, and Google Cast (not Alexa)?"}), 400

        # Try to instantiate via direct-IP path. The handshake's friendly_name
        # is treated as a hint — if the user supplied one, theirs wins.
        try:
            import pychromecast as _pc
            host_tuple = (ip, 8009, ip, "Google Cast", ip)  # name placeholder; replaced after status
            cast = _pc.get_chromecast_from_host(host_tuple)
            cast.wait(timeout=10)
            handshake_name = (
                getattr(cast, "name", None)
                or getattr(getattr(cast, "cast_info", None), "friendly_name", None)
            )
        except Exception as e:
            return jsonify({"ok": False, "error": f"Cast handshake failed: {e}"}), 400

        # Resolve the canonical friendly name:
        #   1. user-supplied name (intent wins over discovery)
        #   2. handshake-reported friendly name (if not a placeholder)
        #   3. fall back to the IP itself (so something gets persisted)
        if supplied_name:
            friendly = supplied_name
        elif handshake_name and handshake_name not in (ip, "Google Cast"):
            friendly = handshake_name
        else:
            friendly = ip

        # Persist to known_speakers.json. B-Belgium-34 (v1.9.3): de-duplicate
        # by IP before inserting — any prior entry pointing at this IP (whether
        # under the same name, under the IP-as-name placeholder, or under the
        # old handshake name) is dropped. Without this dedup, a second call to
        # add_by_ip with a better name (or a subsequent rediscovery that finds
        # the same device under a proper friendly_name) leaves both rows in
        # place and the dashboard shows the same speaker twice.
        ROOT = os.path.dirname(os.path.abspath(__file__))
        KH = os.path.join(ROOT, "known_speakers.json")
        try:
            known = {}
            if os.path.exists(KH):
                with open(KH) as f:
                    known = json.load(f) or {}
            # Drop ANY existing entry pointing at this IP — one IP, one row.
            removed = [k for k, v in known.items() if v == ip and k != friendly]
            for k in removed:
                del known[k]
            known[friendly] = ip
            with open(KH, "w") as f:
                json.dump(known, f, indent=2)
            if removed:
                log.info(f"add_by_ip: replaced {len(removed)} stale entr{'y' if len(removed)==1 else 'ies'} pointing at {ip} (was {removed}) -> {friendly} -> {ip}")
            else:
                log.info(f"add_by_ip: known_speakers.json updated with {friendly} -> {ip}")
        except Exception as e:
            log.warning(f"add_by_ip: persist to known_speakers.json failed: {e} — speaker still added live")

        # Refresh discovery so the new speaker is in _general_casts
        try:
            discover_casts()
        except Exception as e:
            log.warning(f"O36: post-add discovery failed: {e} — speaker exists but not yet in casts list")

        return jsonify({"ok": True, "name": friendly, "ip": ip})
    except Exception as e:
        log.error(f"add_by_ip endpoint error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/speakers/remove", methods=["POST"])
def api_remove_speaker():
    """B-Belgium-41 (v1.9.4): explicit speaker removal endpoint.

    Pairs with v1.9.3's add_by_ip de-duplication. Removes a speaker
    completely from all three persistence layers:
      1. known_speakers.json (the manual-add registry)
      2. UI["enabled"]["speakers"][name] (the per-speaker on/off state)
      3. UI["volumes"][name] (the per-speaker volume)
    Plus disconnects any live Chromecast object for that name and
    re-runs discover_casts() so the in-memory list rebuilds clean.

    Until v1.9.4, the dashboard had no way to remove a speaker — once
    added (by manual IP or by mDNS discovery), an entry persisted in
    the UI state dicts forever, even after explicit toggle-off and
    even after removing from known_speakers.json by hand. The orphans
    surfaced on masood-pi after yesterday's accidental duplicate add
    (B-Belgium-34): even after the duplicate row was cleared from
    devices.speakers, its keys in UI["volumes"] and UI["enabled"]
    persisted across restarts. Invisible to the dashboard render,
    but a long-term cleanliness problem.

    Body: {"name": "<speaker name>"}
    Returns: {"ok": true, "name": "<...>", "removed": {known, enabled, volume}}
    """
    try:
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "name required"}), 400

        removed = {"known_speakers": False, "enabled": False, "volume": False}

        # Layer 1: known_speakers.json
        try:
            ROOT_ = os.path.dirname(os.path.abspath(__file__))
            KH = os.path.join(ROOT_, "known_speakers.json")
            if os.path.exists(KH):
                with open(KH) as f:
                    known = json.load(f) or {}
                if name in known:
                    del known[name]
                    with open(KH, "w") as f:
                        json.dump(known, f, indent=2)
                    removed["known_speakers"] = True
        except Exception as e:
            log.warning(f"remove: known_speakers.json write failed: {e}")

        # Layer 2 + 3: UI state dicts
        with _state_lock:
            if name in UI.get("enabled", {}).get("speakers", {}):
                del UI["enabled"]["speakers"][name]
                removed["enabled"] = True
            if name in UI.get("volumes", {}):
                del UI["volumes"][name]
                removed["volume"] = True
            _save_state(UI)

        # Stop + disconnect any live cast object for this name
        try:
            c = _cast_by_name(name)
            if c:
                try: ensure_connected(c)
                except Exception: pass
                try: c.media_controller.stop()
                except Exception: pass
                try: c.disconnect(blocking=False)
                except Exception: pass
                with _cast_lock:
                    _speaker_playback_status.pop(name, None)
        except Exception as e:
            log.warning(f"remove: live cast cleanup for '{name}' failed: {e}")

        # Refresh in-memory discovery so the speaker tile rebuilds clean
        try:
            discover_casts()
        except Exception as e:
            log.warning(f"remove: post-remove discovery failed: {e}")

        log.info(
            f"Speaker '{name}' removed "
            f"(known_speakers={removed['known_speakers']}, "
            f"enabled={removed['enabled']}, "
            f"volume={removed['volume']})"
        )
        return jsonify({"ok": True, "name": name, "removed": removed})

    except Exception as e:
        log.error(f"Error removing speaker: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/schedule/refresh", methods=["POST"])
def api_refresh_schedule():
    """Manually refresh today's schedule"""
    try:
        log.info("Manual schedule refresh requested")
        _prayer_cache["date"] = None
        _ramadan_cache["date"] = None
        _hijri_cache.clear()
        schedule_today()
        return jsonify({"ok": True, "message": "Schedule refreshed"})
    except Exception as e:
        log.error(f"Error refreshing schedule: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/twilight/scan", methods=["POST"])
def api_twilight_scan():
    """Manually trigger twilight scan"""
    try:
        log.info("Manual twilight scan requested")
        scan_twilight_conditions()
        return jsonify({
            "ok": True, 
            "active": _twilight_cache["persistent_twilight_active"],
            "method": _twilight_cache["high_latitude_method"],
            "start": _twilight_cache["persistent_start"].isoformat() if _twilight_cache["persistent_start"] else None,
            "end": _twilight_cache["persistent_end"].isoformat() if _twilight_cache["persistent_end"] else None
        })
    except Exception as e:
        log.error(f"Error during twilight scan: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/config/method", methods=["POST"])
def api_set_method():
    """Change high latitude method on the fly"""
    try:
        data = request.get_json(force=True)
        method = data.get("method")
        
        if method not in ['combine_prayers', '1_7_rule', 'static_offset']:
            return jsonify({"ok": False, "error": "Invalid method"}), 400
        
        # Update runtime config
        with _twilight_lock:
            _twilight_cache["high_latitude_method"] = method
        
        # Persist to config file
        CFG['rules']['high_latitude_method'] = method
        try:
            with open(CFG_PATH, "w") as f:
                yaml.dump(CFG, f, default_flow_style=False, indent=2)
            log.info(f"Updated config: high_latitude_method = {method}")
        except Exception as e:
            log.error(f"Failed to save config: {e}")
        
        # Refresh schedule to apply new method
        _prayer_cache["date"] = None
        schedule_today()
        
        return jsonify({"ok": True, "method": method})
    except Exception as e:
        log.error(f"Error setting method: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

# ================ CONFIGURATION API ENDPOINTS ================

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """Get current configuration"""
    try:
        # Return sanitized config (remove sensitive if any)
        return jsonify({
            "ok": True,
            "config": CFG
        })
    except Exception as e:
        log.error(f"Error getting config: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/audio/upload", methods=["POST"])
def api_audio_upload():
    """U-5 + E-1 (v1.6.0): accept an .mp3 file upload from the dashboard, write
    it to audio/, and optionally map an existing audio-config key to point at
    the new file.

    Security:
      - Filename sanitised (basename, no traversal, lowercase, alnum/._-/)
      - Hard size cap 50 MB (Pi storage + memory budget)
      - MIME type must be audio/mpeg
      - File contents probed for the MP3 magic bytes (ID3 header or 0xFFFB sync)
      - Writes to audio/ then sets ownership to castadhan:castadhan
      - Optional `key` form field updates config.audio[key] = "audio/<filename>"
        so the user can immediately route, e.g. assign a new adhan recording.

    Returns: {ok, path, key (if set), size_bytes}
    """
    try:
        if 'file' not in request.files:
            return jsonify({"ok": False, "error": "no file in form-data (field name must be 'file')"}), 400
        f = request.files['file']
        if not f or not f.filename:
            return jsonify({"ok": False, "error": "empty filename"}), 400

        # Sanitise the filename: basename only, conservative charset, lowercase
        import re as _re
        raw = os.path.basename(f.filename)
        # Strip path traversal attempts (..), keep only [A-Za-z0-9._-]
        safe = _re.sub(r'[^A-Za-z0-9._-]', '_', raw)
        # Force .mp3 extension
        if not safe.lower().endswith('.mp3'):
            return jsonify({"ok": False, "error": "only .mp3 uploads accepted"}), 400
        # No hidden files
        if safe.startswith('.') or safe == '':
            return jsonify({"ok": False, "error": "invalid filename"}), 400

        # Size cap (50 MB) — checked AFTER read because Flask streams to disk
        max_bytes = 50 * 1024 * 1024
        # Read into memory (50 MB is fine even on Pi 3 A+ 512 MB now that
        # CASTADHAN_LITE=1 skips pydub; the upload is freed once written)
        data = f.read()
        if len(data) > max_bytes:
            return jsonify({"ok": False, "error": f"file too large ({len(data)} bytes > {max_bytes})"}), 400
        if len(data) < 100:
            return jsonify({"ok": False, "error": "file too small to be valid mp3"}), 400

        # Magic-byte sniff: ID3 (49 44 33) or MP3 frame sync (FF Fx)
        if not (data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)):
            return jsonify({"ok": False, "error": "file does not look like an mp3 (no ID3 tag or frame sync)"}), 400

        audio_dir = os.path.join(ROOT, "audio")
        os.makedirs(audio_dir, exist_ok=True)
        target_path = os.path.join(audio_dir, safe)

        # Atomic write
        tmp_path = target_path + ".tmp." + str(os.getpid())
        with open(tmp_path, "wb") as out:
            out.write(data)
        os.replace(tmp_path, target_path)
        log.info(f"U-5: audio uploaded -> {target_path} ({len(data)} bytes)")

        # Try to chown to castadhan if running as root (install-time path)
        try:
            import pwd as _pwd
            uid = _pwd.getpwnam("castadhan").pw_uid
            gid = _pwd.getpwnam("castadhan").pw_gid
            os.chown(target_path, uid, gid)
        except Exception:
            pass  # we may not be running as root in dev

        result = {"ok": True, "path": f"audio/{safe}", "size_bytes": len(data)}

        # E-1 (v1.6.0): if the request specifies a config key to update
        # (e.g. {"key": "adhan"}), set config.audio[key] = "audio/<filename>"
        # so the new file replaces an existing audio role immediately.
        key = (request.form.get('key') or '').strip()
        if key:
            with _state_lock if '_state_lock' in globals() else _dummy_ctx():
                CFG.setdefault('audio', {})
                CFG['audio'][key] = f"audio/{safe}"
                AUDIO[key] = f"audio/{safe}"
                # Persist
                try:
                    tmp = CFG_PATH + ".tmp"
                    with open(tmp, "w") as cf:
                        yaml.dump(CFG, cf, default_flow_style=False, indent=2)
                    os.replace(tmp, CFG_PATH)
                except Exception as e:
                    log.error(f"U-5: could not persist config after audio key remap: {e}")
            result["key"] = key
            result["message"] = f"Uploaded as audio/{safe} and remapped key '{key}' to use it."

        return jsonify(result)
    except Exception as e:
        log.error(f"U-5: audio upload error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

class _dummy_ctx:
    def __enter__(self): return self
    def __exit__(self, *_): return False

@app.route("/api/audio/files", methods=["GET"])
def api_list_audio_files():
    """NEW (2026-05-23): List all .mp3 files in the audio/ folder for the
    Wakeup tab's file picker (and future per-audio file pickers).
    Returns paths relative to project root: ['audio/foo.mp3', 'audio/bar.mp3', ...]"""
    try:
        audio_dir = os.path.join(ROOT, "audio")
        files = []
        if os.path.isdir(audio_dir):
            for name in sorted(os.listdir(audio_dir)):
                if name.lower().endswith(".mp3"):
                    files.append(f"audio/{name}")
        return jsonify({"ok": True, "count": len(files), "files": files})
    except Exception as e:
        log.error(f"Error listing audio files: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

def deep_update(base: dict, update: dict) -> None:
    """Recursively update nested dictionary"""
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            deep_update(base[key], value)
        else:
            base[key] = value

@app.route("/api/config", methods=["POST"])
def api_set_config():
    """Update configuration"""
    try:
        data = request.get_json(force=True)
        
        # Validate required structure
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Invalid config format"}), 400
        
        # Create a copy for validation before applying
        test_cfg = CFG.copy()
        deep_update(test_cfg, data)
        
        # Validate the merged config
        valid, msg = validate_config(test_cfg)
        if not valid:
            return jsonify({"ok": False, "error": f"Invalid config: {msg}"}), 400
        
        # O29 (v1.2.0, Tue 26 May 2026): if the timezone is being changed, also
        # sync the underlying Linux system timezone via `timedatectl set-timezone`.
        # Before this, app config tz and system tz could drift apart silently —
        # exactly what we found on aunt's Pi on 25 May (system Europe/London,
        # app Europe/Brussels). app code uses pytz/zoneinfo so prayer times
        # are unaffected, but logs, cron, systemd timers, and any naive datetime
        # operations would be wrong. Best-effort: ignore failures (e.g. running
        # in a container without setcap, on a non-systemd OS, or without sudo).
        new_tz = ((data.get("app") or {}).get("timezone")) or ""
        old_tz = (CFG.get("app") or {}).get("timezone")
        sync_system_tz = bool(new_tz) and new_tz != old_tz

        # Apply updates to live config
        deep_update(CFG, data)

        # v1.8.12: refresh module-level location globals so the prayer-times
        # fetcher (and anything else reading the live globals) picks up the new
        # values WITHOUT a service restart. Bug surfaced on son's fresh Pi
        # 2026-05-31: wizard saved Haverfordwest+coords to CFG, but the running
        # process kept CITY=""/COUNTRY="" from startup and the Aladhan call sent
        # `?city=&country=&` -> 400 -> dashboard stuck at --:-- until restart.
        _refresh_location_globals_from_cfg()

        # Save to file with atomic write
        tmp_path = CFG_PATH + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                yaml.dump(CFG, f, default_flow_style=False, indent=2)
            os.replace(tmp_path, CFG_PATH)
            log.info("Configuration saved to disk")
        except Exception as e:
            log.error(f"Failed to save config file: {e}")
            return jsonify({"ok": False, "error": f"Failed to save: {e}"}), 500

        # O29: sync system tz AFTER config save so a failure here doesn't lose user input
        if sync_system_tz:
            try:
                import subprocess
                # Two strategies: timedatectl (systemd) first, then symlink fallback for non-systemd.
                result = subprocess.run(
                    ["sudo", "-n", "timedatectl", "set-timezone", new_tz],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    log.info(f"✅ System timezone synced to {new_tz}")
                else:
                    log.warning(
                        f"timedatectl set-timezone {new_tz} failed (rc={result.returncode}): "
                        f"{result.stderr.strip()}. Prayer times are unaffected (app uses config tz directly), "
                        f"but logs and cron will use the old system tz."
                    )
            except Exception as e:
                log.warning(f"System tz sync failed for {new_tz}: {e}. Non-fatal — app config tz still applied.")

        # Refresh caches and schedule
        _prayer_cache["date"] = None
        _ramadan_cache["date"] = None
        _hijri_cache.clear()

        # Update twilight method in cache
        with _twilight_lock:
            _twilight_cache["high_latitude_method"] = CFG['rules']['high_latitude_method']

        # Reschedule prayers
        schedule_today()
        
        log.info("Configuration updated successfully")
        return jsonify({"ok": True, "message": "Configuration updated"})
        
    except Exception as e:
        log.error(f"Error setting config: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

def _detect_location_from_ip() -> Optional[dict]:
    """Query ip-api.com (free, no key) for IP-based geolocation.
    Returns dict with keys lat, lon, city, country, timezone — or None on failure.
    """
    try:
        url = "http://ip-api.com/json/"
        params = {"fields": "status,message,country,city,lat,lon,timezone,query"}
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            log.error(f"ip-api returned non-success: {data}")
            return None
        return {
            "lat": float(data["lat"]),
            "lon": float(data["lon"]),
            "city": data.get("city", ""),
            "country": data.get("country", ""),
            "timezone": data.get("timezone", ""),
            "ip": data.get("query", ""),
        }
    except Exception as e:
        log.error(f"IP geolocation failed: {e}")
        return None

@app.route("/api/location/auto-detect", methods=["POST"])
def api_location_auto_detect():
    """Detect location from public IP and optionally save (?save=true)."""
    try:
        save = request.args.get("save", "false").lower() in ("1", "true", "yes")
        loc = _detect_location_from_ip()
        if not loc:
            return jsonify({"ok": False, "error": "Failed to detect location from IP"}), 502

        if save:
            update = {
                "app": {
                    "location": {
                        "latitude": loc["lat"],
                        "longitude": loc["lon"],
                        "city": loc["city"],
                        "country": loc["country"],
                    },
                }
            }
            if loc.get("timezone"):
                update["app"]["timezone"] = loc["timezone"]

            deep_update(CFG, update)
            global CITY, COUNTRY, LATITUDE, LONGITUDE, TZ
            CITY      = loc["city"] or CITY
            COUNTRY   = loc["country"] or COUNTRY
            LATITUDE  = loc["lat"]
            LONGITUDE = loc["lon"]
            if loc.get("timezone"):
                TZ = loc["timezone"]

            tmp_path = CFG_PATH + ".tmp"
            with open(tmp_path, "w") as f:
                yaml.dump(CFG, f, default_flow_style=False, indent=2)
            os.replace(tmp_path, CFG_PATH)
            log.info(f"Auto-detect saved: {CITY}, {COUNTRY} ({LATITUDE}, {LONGITUDE}) tz={TZ}")

            _prayer_cache["date"] = None
            try:
                schedule_today()
            except Exception as e:
                log.error(f"Schedule refresh after auto-detect failed: {e}")

        return jsonify({"ok": True, "location": loc, "saved": save})
    except Exception as e:
        log.error(f"Auto-detect error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/location/search", methods=["GET"])
def api_location_search():
    """Proxy for location search (to avoid CORS)"""
    try:
        query = request.args.get("q", "")
        if not query:
            return jsonify({"ok": False, "error": "No query"}), 400
        
        # Use Nominatim (OpenStreetMap) with proper User-Agent
        headers = {'User-Agent': 'CastAdhan/3.1 (https://github.com/sabreenaapa-coder/castadhan-portable)'}
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 5, "addressdetails": 1},
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        
        if r.status_code != 200:
            return jsonify({"ok": False, "error": "Search service unavailable"}), 502
        
        results = r.json()
        
        # Format results for console
        formatted = []
        for place in results:
            formatted.append({
                "name": place.get("display_name", "Unknown"),
                "lat": place.get("lat"),
                "lon": place.get("lon"),
                "city": place.get("address", {}).get("city", place.get("address", {}).get("town", place.get("address", {}).get("village", ""))),
                "country": place.get("address", {}).get("country", "")
            })
        
        return jsonify(formatted)
        
    except requests.exceptions.RequestException as e:
        log.error(f"Location search failed: {e}")
        return jsonify({"ok": False, "error": "Search service timeout"}), 504
    except Exception as e:
        log.error(f"Error in location search: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/location/reverse", methods=["GET"])
def api_location_reverse():
    """Reverse geocode latitude/longitude to city/country"""
    try:
        lat = request.args.get("lat")
        lon = request.args.get("lon")
        
        if not lat or not lon:
            return jsonify({"ok": False, "error": "lat and lon required"}), 400
        
        headers = {'User-Agent': 'CastAdhan/3.1 (https://github.com/sabreenaapa-coder/castadhan-portable)'}
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        
        if r.status_code != 200:
            return jsonify({"ok": False, "error": "Reverse geocoding failed"}), 502
        
        data = r.json()
        address = data.get("address", {})
        
        city = address.get("city") or address.get("town") or address.get("village") or "Unknown"
        country = address.get("country") or "Unknown"
        
        return jsonify({
            "ok": True,
            "city": city,
            "country": country,
            "display_name": data.get("display_name", "")
        })
        
    except Exception as e:
        log.error(f"Error in reverse geocoding: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/overview")
def api_overview():
    """Get API overview"""
    try:
        with _cast_lock:
            gen = [c.name for c in _general_casts]

        return jsonify({
            "ok": True,
            "location": f"{CITY}, {COUNTRY}",
            "timezone": TZ,
            "devices": {
                "speakers": gen,
            },
            "endpoints": [
                "/api/state", "/api/test/play", "/api/test/pattern", "/api/test/stop",
                "/api/emergency/stop", "/api/rediscover", "/api/speakers/force_discover",
                "/api/schedule/refresh", "/api/twilight/scan", "/api/config/method",
                "/api/config", "/api/location/search", "/api/location/reverse",
                "/api/speaker/toggle", "/api/speakers/toggle_all", "/api/speaker/volume",
                "/api/speaker/routing", "/api/speaker/status", "/health", "/metrics"
            ]
        })
    except Exception as e:
        log.error(f"Error getting overview: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/play", methods=["POST"])
def api_play():
    """Play media - supports 'all' or specific device (consistent with test endpoint)"""
    try:
        data = request.get_json(force=True)
        device = data.get("device", "all")
        url = data.get("url")
        volume = float(data.get("volume", 0.7))
        audio_type = data.get("audio_type", "custom")

        if device.lower() == "all":
            # Play on all enabled speakers with routing
            played_count = 0
            for cast in _all_casts():
                if _should_play_on_speaker(cast.name, audio_type):
                    play_on_cast(cast, url, volume, audio_type)
                    played_count += 1
            return jsonify({"ok": True, "device": "all", "played_count": played_count})
        else:
            cast = _cast_by_name(device)
            if cast:
                if _should_play_on_speaker(cast.name, audio_type):
                    play_on_cast(cast, url, volume, audio_type)
                    return jsonify({"ok": True, "device": device})
                else:
                    return jsonify({"ok": False, "error": f"Speaker {device} routing disabled for {audio_type}"}), 403
            else:
                return jsonify({"ok": False, "error": f"Device not found: {device}"}), 404
    except Exception as e:
        log.error(f"API /api/play error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# v1.9.8 — scheduled_audio (Quran Programs) API
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/scheduled_audio", methods=["GET"])
def api_scheduled_audio_list():
    """Return all scheduled_audio entries with merged config + runtime state.
    The dashboard polls this to render the cards."""
    try:
        with _state_lock:
            sa = (CFG.get("scheduled_audio") or {}).copy()
        runtime = _load_custom_audio_state()
        out = []
        for audio_id, entry in sa.items():
            merged = {
                "id": audio_id,
                "config": entry,
                "state": {**_get_custom_audio_state_entry(audio_id), **(runtime.get(audio_id) or {})},
                "file_exists": os.path.isfile(_custom_audio_file_path(audio_id)),
            }
            out.append(merged)
        # Stable order: by audio_id for now (v1.9.9 will add display_order)
        out.sort(key=lambda x: x["id"])
        return jsonify({"ok": True, "entries": out})
    except Exception as e:
        log.error(f"/api/scheduled_audio error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduled_audio/<audio_id>", methods=["POST"])
def api_scheduled_audio_update(audio_id):
    """Update one entry's config fields. Body is a partial dict of fields to
    change (e.g. {"enabled": true, "play_time": "09:30"}).

    Triggers download if 'enabled' transitions to true and file missing.
    Re-runs schedule_today() so the change takes effect today.

    Kahf bridge: when audio_id == "surah_kahf", dual-writes to the legacy
    rules block too so the old code path picks up the change. Remove in v1.9.9.
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "error": "body must be a JSON object"}), 400

        ALLOWED_FIELDS = {
            "enabled", "play_time", "days", "audio_url", "target_speakers",
            "max_duration_minutes", "trigger_type", "relative_prayer_anchor",
            "offset_minutes", "name", "category",
        }
        invalid = set(body.keys()) - ALLOWED_FIELDS
        if invalid:
            return jsonify({"ok": False, "error": f"unknown fields: {sorted(invalid)}"}), 400

        with _state_lock:
            sa = CFG.setdefault("scheduled_audio", {})
            entry = sa.get(audio_id)
            if entry is None:
                return jsonify({"ok": False, "error": f"unknown audio_id: {audio_id}"}), 404
            was_enabled = bool(entry.get("enabled"))
            entry.update(body)
            now_enabled = bool(entry.get("enabled"))

            # If user re-enabled after auto-disable, reset failure counter
            if now_enabled and not was_enabled:
                _update_custom_audio_state(audio_id, consecutive_failures=0, last_error=None,
                                           download_status="NOT_STARTED")

            # v1.9.9: Kahf bridge dual-write removed — the legacy Friday
            # substitution in play_morning_dhikr is gone; scheduled_audio is
            # now the sole Kahf path.

            _save_config_yaml(CFG)

        # If just enabled and file missing, kick off the download
        if now_enabled and not os.path.isfile(_custom_audio_file_path(audio_id)):
            url = entry.get("audio_url") or ""
            if url:
                _enqueue_custom_audio_download(audio_id, url)

        # v1.9.9: re-register only the edited entry's job (was a full
        # schedule_today() rebuild per click — 8 rebuilds in 17 min observed)
        try:
            _reschedule_one_custom_audio(audio_id)
        except Exception as e:
            log.error(f"reschedule after scheduled_audio update failed: {e}")

        return jsonify({"ok": True, "id": audio_id, "config": entry})
    except Exception as e:
        log.error(f"/api/scheduled_audio/{audio_id} error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduled_audio/<audio_id>/skip_today", methods=["POST"])
def api_scheduled_audio_skip_today(audio_id):
    """Set skip_until_date to today — the schedule fires but the audio
    doesn't play this once. Cleared automatically tomorrow."""
    try:
        with _state_lock:
            if audio_id not in (CFG.get("scheduled_audio") or {}):
                return jsonify({"ok": False, "error": f"unknown audio_id: {audio_id}"}), 404
        today_iso = date.today().isoformat()
        _update_custom_audio_state(audio_id, skip_until_date=today_iso)
        return jsonify({"ok": True, "id": audio_id, "skip_until_date": today_iso})
    except Exception as e:
        log.error(f"/api/scheduled_audio/{audio_id}/skip_today error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduled_audio/<audio_id>/play_now", methods=["POST"])
def api_scheduled_audio_play_now(audio_id):
    """Fire the audio immediately, regardless of schedule. Ignores skip_until_date.
    Useful when the user wants to hear it now (testing, missed the slot, etc.)."""
    try:
        with _state_lock:
            if audio_id not in (CFG.get("scheduled_audio") or {}):
                return jsonify({"ok": False, "error": f"unknown audio_id: {audio_id}"}), 404
        threading.Thread(target=_play_custom_audio, args=(audio_id,),
                         kwargs={"force": True}, daemon=True).start()
        return jsonify({"ok": True, "id": audio_id, "status": "playback started"})
    except Exception as e:
        log.error(f"/api/scheduled_audio/{audio_id}/play_now error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scheduled_audio/<audio_id>/quiet_test", methods=["POST"])
def api_scheduled_audio_quiet_test(audio_id):
    """10-second test play. Plays the audio file then stops cleanly after 10s.
    Used during setup to confirm playback works without blasting the full surah."""
    try:
        with _state_lock:
            if audio_id not in (CFG.get("scheduled_audio") or {}):
                return jsonify({"ok": False, "error": f"unknown audio_id: {audio_id}"}), 404
        threading.Thread(target=_quiet_test_custom_audio, args=(audio_id,),
                         daemon=True).start()
        return jsonify({"ok": True, "id": audio_id, "status": "quiet test started"})
    except Exception as e:
        log.error(f"/api/scheduled_audio/{audio_id}/quiet_test error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------- Bootstrap Initialization (COMPLETE with Kernel Lock) ----------------
_initialized = False

def ensure_initialized():
    """Complete initialization for both gunicorn and direct execution"""
    global _initialized, _scheduler_started
    
    # Guard against double initialization
    if _initialized:
        return
    
    # CRITICAL: Acquire kernel-level process lock FIRST
    acquire_global_lock()
    
    _initialized = True
    
    log.info("=" * 60)
    log.info("CastAdhan v3.1 Complete Speaker Management - Kernel-Enforced Singleton")
    log.info("=" * 60)
    
    # Run all startup tasks
    if not check_port_availability(PORT):
        log.warning(f"Port {PORT} is in use, attempting to free it...")
        kill_process_on_port(PORT)

    ensure_audio_directory()
    verify_audio_integrity()
    sanity_check_audio()

    # Optional: auto-detect location from IP before scheduling
    # Portable default: True. Honours the configured value if user has disabled it.
    if RULES.get("auto_detect_location_on_startup", True):
        log.info("auto_detect_location_on_startup=True — querying IP geolocation...")
        loc = _detect_location_from_ip()
        if loc:
            CFG["app"]["location"]["latitude"]  = loc["lat"]
            CFG["app"]["location"]["longitude"] = loc["lon"]
            CFG["app"]["location"]["city"]      = loc["city"] or CFG["app"]["location"].get("city", "")
            CFG["app"]["location"]["country"]   = loc["country"] or CFG["app"]["location"].get("country", "")
            if loc.get("timezone"):
                CFG["app"]["timezone"] = loc["timezone"]
            LATITUDE  = loc["lat"]
            LONGITUDE = loc["lon"]
            CITY      = CFG["app"]["location"]["city"]
            COUNTRY   = CFG["app"]["location"]["country"]
            TZ        = CFG["app"]["timezone"]
            try:
                with open(CFG_PATH + ".tmp", "w") as f:
                    yaml.dump(CFG, f, default_flow_style=False, indent=2)
                os.replace(CFG_PATH + ".tmp", CFG_PATH)
            except Exception as e:
                log.error(f"Could not persist auto-detected location: {e}")
            log.info(f"Auto-detected location: {CITY}, {COUNTRY} ({LATITUDE}, {LONGITUDE}) tz={TZ}")
        else:
            log.warning("Auto-detect failed; keeping configured location")

    # CRITICAL: Run initial twilight scan to set cache before scheduling
    log.info("Performing initial twilight detection...")
    try:
        # Quick check for today
        active = check_twilight_today()
        with _twilight_lock:
            _twilight_cache["persistent_twilight_active"] = active
            _twilight_cache["last_scan"] = date.today()
            _twilight_cache["high_latitude_method"] = RULES.get('high_latitude_method', 'combine_prayers')
        log.info(f"Initial twilight status: {'ACTIVE' if active else 'inactive'}")
        
        # Run full scan in background (doesn't block startup)
        threading.Thread(target=scan_twilight_conditions, daemon=True).start()
    except Exception as e:
        log.error(f"Initial twilight detection failed: {e}")
    
    # Log feature summary
    log_feature_summary()
    
    # C-4 (v1.5.0): restore any active scheduler hold from disk so a restart
    # doesn't undo an emergency-stop that the user issued before restart.
    try:
        _load_scheduler_hold()
    except Exception as e:
        log.error(f"Could not load scheduler hold: {e}")

    # v1.9.9: start the watchdog health loop FIRST — systemd's WatchdogSec=180
    # expects the first WATCHDOG=1 ping within 3 minutes of start, and pings
    # are unconditional during the 5-minute warmup so a slow startup (twilight
    # scan, discovery) can never be mistaken for a hang.
    try:
        _start_watchdog_loop()
    except Exception as e:
        log.error(f"Could not start watchdog loop: {e}")

    # v1.9.8: kick off the scheduled_audio (Quran Programs) download worker
    # before the scheduler starts, so any enabled-but-not-yet-downloaded
    # entries can begin fetching while the rest of the system boots.
    try:
        _start_custom_audio_download_worker()
    except Exception as e:
        log.error(f"Could not start custom audio download worker: {e}")

    # v1.9.9: Kahf bridge → full migration (must run BEFORE schedule_today
    # so today's Kahf job is registered with the migrated 07:00 trigger).
    try:
        _migrate_kahf_to_scheduled_audio()
    except Exception as e:
        log.error(f"Kahf migration error: {e}")

    # Set up scheduler (only once)
    if not _scheduler_started:
        sched.add_listener(job_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
        schedule_today()
        schedule_midnight_refresh()

        sched.start()
        _scheduler_started = True
        log.info("Scheduler started successfully")

        for job in sched.get_jobs():
            log.info(f"Job: {job.id} - Next run: {job.next_run_time}")

        # O35 (v1.2.0): post-startup self-test. The periodic-maintenance jobs
        # (cast_rediscovery / dst_protection_refresh / health_check / twilight_scan)
        # are added at the very END of schedule_today(). If any are missing,
        # schedule_today() crashed mid-function and silently truncated the
        # schedule. This is exactly the silent-failure mode that the
        # Job.next_run_time AttributeError caused between 23 May and 26 May 2026.
        try:
            job_ids = {j.id for j in sched.get_jobs()}
            adhan_count = sum(1 for jid in job_ids if jid.startswith("adhan_"))
            warning_count = sum(1 for jid in job_ids if jid.endswith("_warning"))
            REQUIRED_PERIODIC = {"cast_rediscovery", "dst_protection_refresh", "health_check", "twilight_scan", "refresh_daily"}
            missing_periodic = REQUIRED_PERIODIC - job_ids
            if missing_periodic:
                log.critical(
                    f"❌ STARTUP SELF-TEST FAILED: periodic-maintenance jobs missing: "
                    f"{sorted(missing_periodic)}. This means schedule_today() crashed "
                    f"mid-function — search journal for the last [ERROR] line before "
                    f"'Scheduler started successfully'. Adhans: {adhan_count}, "
                    f"warnings: {warning_count}, total jobs: {len(job_ids)}."
                )
                _log_play("startup_selftest", None,
                          "SCHEDULER_INCOMPLETE",
                          speakers_count=0,
                          error=Exception(f"Missing periodic jobs: {sorted(missing_periodic)}"))
            else:
                log.info(
                    f"✅ STARTUP SELF-TEST PASSED: {len(job_ids)} jobs scheduled "
                    f"({adhan_count} adhans, {warning_count} warnings, "
                    f"{len(REQUIRED_PERIODIC)} periodic, "
                    f"{len(job_ids) - adhan_count - warning_count - len(REQUIRED_PERIODIC)} other)"
                )
        except Exception as e:
            log.error(f"Startup self-test internal error (non-fatal): {e}")

# ---------------- Gunicorn Bootstrap (SAFE LOCATION - after all definitions) ----------------
# This runs at import time for gunicorn, after all functions and variables are defined
if os.environ.get("USE_GUNICORN", "").lower() == "true" and not os.environ.get("SKIP_INIT"):
    ensure_initialized()

# ---------------- Signal Handlers ----------------
def _shutdown(signum, frame):
    """Handle shutdown signals gracefully"""
    log.info(f"Received signal {signum}, shutting down CastAdhan...")
    shutdown_event.set()

    # Allow threads to complete (max 2 seconds)
    log.info("Waiting 2 seconds for threads to complete...")
    time.sleep(2)

    if sched and sched.running:
        try:
            sched.shutdown(wait=False)
            log.info("Scheduler shutdown completed")
        except Exception as e:
            log.error(f"Error shutting down scheduler: {e}")

    if _cast_browser:
        try:
            _cast_browser.stop_discovery()
        except Exception as e:
            log.error(f"Error stopping cast browser: {e}")

    # Close global lock file (released automatically on exit, but good practice)
    if _global_lock_f:
        _global_lock_f.close()

    shutdown_complete.set()
    os._exit(0)

signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)

# ---------------- Main Application Entry Point ----------------
if __name__ == "__main__":
    try:
        # Bootstrap initialization for direct execution (not gunicorn)
        if not os.environ.get('USE_GUNICORN'):
            ensure_initialized()
        
        log.info("CastAdhan web interface running on http://%s:%s", HOST, PORT)
        log.info("Prayer times API: coordinate-based for %s, %s", LATITUDE, LONGITUDE)
        log.info("Metrics available at http://%s:%s/metrics", HOST, PORT)
        log.info("Configuration API available at http://%s:%s/api/config", HOST, PORT)
        log.info("Speaker management available at http://%s:%s/api/speaker/status", HOST, PORT)
        log.info("Emergency stop endpoint: POST /api/emergency/stop")
        log.info("High latitude method: %s (can be changed via API)", _twilight_cache["high_latitude_method"])

        # For direct execution (not gunicorn)
        if not os.environ.get('USE_GUNICORN'):
            app.run(host=HOST, port=PORT, debug=False, threaded=True)
        else:
            log.info("Running under gunicorn - app.run() skipped")

    except KeyboardInterrupt:
        log.info("Received keyboard interrupt")
        _shutdown(signal.SIGINT, None)
    except Exception as e:
        log.error(f"Fatal error starting CastAdhan: {e}")
        log.error(traceback.format_exc())
        sys.exit(1)
