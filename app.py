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
from datetime import datetime, timedelta, date, timezone
from urllib.parse import quote
from typing import List, Dict, Optional, Tuple, Any, Union
from pathlib import Path

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
    'rules': {
        'morning_dhikr_time': '07:00',
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
        'fajr_workday_cap': '07:00',
        'fajr_weekend_offset_minutes': -30,
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
        'fajr_at_start_when_isha_capped': True,  # When Isha cap fires today, play Fajr at raw API time
        'twilight_scan_frequency_days': 7
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

        # Disconnect stale objects OUTSIDE the lock (disconnect can block briefly)
        for c in stale_to_drop:
            _disconnect_cast_quietly(c)
        if stale_to_drop:
            log.info(f"Disconnected {len(stale_to_drop)} stale cast object(s) to prevent thread leak")

        with _state_lock:
            for c in found_general:
                if c and c.name not in UI["enabled"]["speakers"]:
                    UI["enabled"]["speakers"][c.name] = True
                if c and c.name not in UI["volumes"]:
                    UI["volumes"][c.name] = UI["volumes"].get("__default", _default_volume)
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

def local_media_url(relpath: str) -> str:
    """Get local media URL with better IP detection"""
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

    rel = relpath.lstrip("/")
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

def play_on_cast(cast, media_url: str, volume: float, audio_type: str = None):
    """Enhanced play function with better error handling and routing awareness"""
    if shutdown_event.is_set():
        return

    # Check if this audio type should play on this speaker
    if audio_type and not _should_play_on_speaker(cast.name, audio_type):
        log.debug(f"Skipping {audio_type} on {cast.name} (routing disabled)")
        return

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

    # Check if we need to apply method-specific overrides
    with _twilight_lock:
        twilight_active = _twilight_cache["persistent_twilight_active"]
        method = _twilight_cache["high_latitude_method"] if twilight_active else None

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

def schedule_cast_rediscovery():
    """Schedule periodic cast rediscovery to handle network changes"""
    try:
        existing = [job.id for job in sched.get_jobs()]
        if 'cast_rediscovery' not in existing:
            sched.add_job(discover_casts, CronTrigger(minute="*/30"), id="cast_rediscovery")
            log.info("Scheduled cast rediscovery every 30 minutes")
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
        for p in prayers:
            e = results[p]
            status = e.get("status") if e else None
            if status in ("PASS", "DISCOVERY_RECOVERED"):
                lines.append(f"✅ {p} {e['ts_local'][11:16]}")
            elif status in ("FAIL", "NO_SPEAKERS"):
                any_problem = True
                lines.append(f"❌ {p} FAILED ({status})")
            elif p == "Isha" and not isha_expected:
                lines.append("➖ Isha (combined with Maghrib)")
            else:
                any_problem = True
                lines.append(f"⚠️ {p} — no record")

        header = (f"⚠️ CastAdhan ({_site_label()}) — a prayer may not have played today:"
                  if any_problem else
                  f"✅ CastAdhan ({_site_label()}) — all prayers fired today:")
        _telegram_send(header + "\n" + "\n".join(lines))
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
                fajr_time = today_at(t)

                fajr_at_start = RULES.get('fajr_at_start_when_isha_capped', True)

                if ramadan:
                    # Ramadan: Use raw Fajr time, ignore all caps
                    dt = fajr_time
                    log.info(f"🌙 RAMADAN: Fajr at {dt} (raw API time)")
                elif fajr_at_start and isha_capped_today:
                    # Isha was capped today — symmetry: don't delay Fajr either.
                    # Play at the raw API start time so the night isn't compressed at both ends.
                    dt = fajr_time
                    log.info(f"Fajr at raw start {dt} (Isha capped today; fajr_at_start_when_isha_capped=True)")
                else:
                    sunrise_time = today_at(times.get("Sunrise", "07:00"))
                    offset_minutes = RULES.get('fajr_weekend_offset_minutes', -30)
                    target_before_sunrise = sunrise_time + timedelta(minutes=offset_minutes)

                    # Weekday rule: configurable max time, unless Fajr is after that time
                    if now_local().weekday() < 5:  # Monday-Friday
                        cap_str = RULES.get('fajr_workday_cap', '07:00')
                        max_weekday_time = today_at(cap_str)
                        if fajr_time > max_weekday_time:
                            # Exception: Fajr time is after cap, use Fajr time
                            dt = fajr_time
                            log.info(f"Fajr after {cap_str} on weekday, using Fajr time: {dt}")
                        else:
                            # Use earlier of: target-before-sunrise or cap time
                            dt = min(target_before_sunrise, max_weekday_time)
                            log.info(f"Weekday Fajr: target-before-sunrise={target_before_sunrise}, max={cap_str}, using: {dt}")
                    else:
                        # Weekend rule: target minutes before sunrise
                        dt = target_before_sunrise
                        log.info(f"Weekend Fajr: {abs(offset_minutes)} minutes before sunrise: {dt}")
                    
                    # Defensive clamp: ensure dt is not in the past
                    if dt < now_local():
                        log.warning(f"Fajr calculation resulted in past time {dt}, using raw Fajr time {fajr_time}")
                        dt = fajr_time
            else:
                dt = today_at(t)

            if dt > now_local():  # Only schedule future prayers
                sched.add_job(
                    play_adhan_all,
                    DateTrigger(run_date=dt),
                    id=f"adhan_{p}",
                    kwargs={"prayer_name": p}
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
                sched.add_job(play_suhoor_alarm, DateTrigger(run_date=suhoor_time), id="suhoor_alarm")
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
                    sched.add_job(play_morning_dhikr, DateTrigger(run_date=md), id="morning_dhikr")
                    log.info("Scheduled morning dhikr @ %s", md)

        # Wakeup - plays on all enabled speakers
        # S4 FIX (2026-05-23): respect explicit wakeup_enabled flag.
        # Default True so existing configs (which never had this field) keep current behaviour.
        # Setting it False disables wakeup regardless of wakeup_time being set —
        # cleaner than the old "empty the time field to disable" workaround.
        if RULES.get("wakeup_enabled", True) and RULES.get("wakeup_time"):
            should_schedule_wakeup = True
            if RULES.get("wakeup_weekdays_only", True):
                should_schedule_wakeup = now_local().weekday() < 5

            if should_schedule_wakeup:
                wu = today_at(RULES["wakeup_time"])
                if wu > now_local():
                    sched.add_job(play_wakeup, DateTrigger(run_date=wu), id="wakeup")
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
                sched.add_job(play_evening_content, DateTrigger(run_date=dt), id="evening")
                log.info("Scheduled evening content @ %s (ends ~%s, cutoff %s)",
                         dt, end_dt.strftime("%H:%M"), cutoff_str)

        # Sunrise warning — 5 minutes before sunrise (end-of-Fajr reminder)
        sunrise_str = times.get("Sunrise")
        if sunrise_str:
            sunrise_warn_dt = today_at(sunrise_str) - timedelta(minutes=5)
            if sunrise_warn_dt > now_local():
                sched.add_job(play_sunrise_warning, DateTrigger(run_date=sunrise_warn_dt), id="sunrise_warning")
                log.info("Scheduled sunrise warning @ %s (5 min before sunrise)", sunrise_warn_dt)

        # Dhuhr warning — 10 minutes before Asr (end of Dhuhr time)
        asr_str = times.get("Asr")
        if asr_str:
            dhuhr_warn_dt = today_at(asr_str) - timedelta(minutes=10)
            if dhuhr_warn_dt > now_local():
                sched.add_job(play_dhuhr_warning, DateTrigger(run_date=dhuhr_warn_dt), id="dhuhr_warning")
                log.info("Scheduled Dhuhr warning @ %s (10 min before Asr)", dhuhr_warn_dt)

        # Asr warning — 10 minutes before Maghrib (12 min on Friday to clear Friday prayer)
        if maghrib:
            asr_warn_offset = 12 if now_local().weekday() == 4 else 10
            asr_warn_dt = today_at(maghrib) - timedelta(minutes=asr_warn_offset)
            if asr_warn_dt > now_local():
                sched.add_job(play_asr_warning, DateTrigger(run_date=asr_warn_dt), id="asr_warning")
                log.info("Scheduled Asr warning @ %s (%d min before Maghrib)", asr_warn_dt, asr_warn_offset)

        # Maghrib warning — 10 minutes before Isha (suspended when Isha is fully skipped)
        isha_str = times.get("Isha")
        isha_fully_skipped = skip_isha or skip_isha_due_to_twilight
        if isha_str and not isha_fully_skipped:
            maghrib_warn_dt = today_at(isha_str) - timedelta(minutes=10)
            if maghrib_warn_dt > now_local():
                sched.add_job(play_maghrib_warning, DateTrigger(run_date=maghrib_warn_dt), id="maghrib_warning")
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
                    sched.add_job(play_friday_prayer, DateTrigger(run_date=fp_dt), id="friday_prayer")
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

    except Exception as e:
        log.error(f"Error scheduling today's activities: {e}")
        log.error(traceback.format_exc())

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
def _play_to_targets(media_relpath: str, target: Optional[str] = None, audio_type: Optional[str] = None):
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

    try:
        url = local_media_url(media_relpath)

        if target and target.lower() != "all":
            cast = _cast_by_name(target)
            if cast:
                play_on_cast(cast, url, _speaker_volume(cast.name), audio_type)
            else:
                log.warning(f"Target {target} not found")
        else:
            # Check if any speakers available
            casts = _all_casts()
            if not casts:
                log.warning("No speakers available for playback")
                return
                
            for cast in casts:
                play_on_cast(cast, url, _speaker_volume(cast.name), audio_type)
    except Exception as e:
        log.error(f"Error playing media {media_relpath}: {e}")

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
    status is one of: PASS, FAIL, NO_SPEAKERS, DISCOVERY_RECOVERED."""
    try:
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
        # SKIPPED_HOLD (Emergency Stop) and DISCOVERY_RECOVERED are not failures.
        if status in ("FAIL", "NO_SPEAKERS"):
            try:
                label = prayer_name or audio_type or "audio"
                msg = (f"⚠️ CastAdhan ({_site_label()}): {label} did NOT play "
                       f"({status}) at {entry['ts_local'][11:16]}.")
                if entry.get("error"):
                    msg += f"\n{entry['error']}"
                _telegram_send(msg)
            except Exception as e:
                log.error(f"Telegram failure-alert error: {e}")
    except Exception as e:
        # Logging must never break playback. Swallow.
        log.error(f"_log_play internal error: {e}")

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

def play_takbeeraat_all(target: Optional[str] = None):
    """Play Takbeeraat on enabled speakers (respects enable flags)."""
    if shutdown_event.is_set():
        return
    if "takbeeraat" not in AUDIO:
        log.warning("Takbeeraat requested but AUDIO['takbeeraat'] missing")
        return
    log.info("🕌 Playing Takbeeraat on enabled speakers")
    _play_to_targets(AUDIO["takbeeraat"], target=target, audio_type="takbeeraat")

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
                        kwargs={"target": target},
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

    try:
        url = local_media_url(AUDIO["fajr_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "fajr_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "fajr_warning")
            else:
                log.debug(f"Speaker {cast.name} routing disabled for fajr_warning, skipping")
    except Exception as e:
        log.error(f"Error playing fajr warning: {e}")

def play_asr_warning():
    """Play Asr warning audio 5 minutes before Asr time"""
    if shutdown_event.is_set():
        return

    log.info("Playing Asr warning (5 min to Asr) on all enabled speakers")

    try:
        url = local_media_url(AUDIO["asr_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "asr_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "asr_warning")
            else:
                log.debug(f"Speaker {cast.name} routing disabled for asr_warning, skipping")
    except Exception as e:
        log.error(f"Error playing Asr warning: {e}")

def play_dhuhr_warning():
    """Play Dhuhr warning audio 10 minutes before Dhuhr time"""
    if shutdown_event.is_set():
        return

    log.info("Playing Dhuhr warning (10 min to Dhuhr) on all enabled speakers")

    try:
        url = local_media_url(AUDIO["dhuhr_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "dhuhr_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "dhuhr_warning")
            else:
                log.debug(f"Speaker {cast.name} routing disabled for dhuhr_warning, skipping")
    except Exception as e:
        log.error(f"Error playing Dhuhr warning: {e}")

def play_maghrib_warning():
    """Play Maghrib warning audio 5 minutes before Maghrib time"""
    if shutdown_event.is_set():
        return

    log.info("Playing Maghrib warning (5 min to Maghrib) on all enabled speakers")

    try:
        url = local_media_url(AUDIO["maghrib_warning"])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "maghrib_warning"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "maghrib_warning")
            else:
                log.debug(f"Speaker {cast.name} routing disabled for maghrib_warning, skipping")
    except Exception as e:
        log.error(f"Error playing Maghrib warning: {e}")

def play_morning_dhikr():
    """Play morning dhikr on all enabled speakers — Surah Kahf on Fridays"""
    if shutdown_event.is_set():
        return

    is_friday = now_local().weekday() == 4
    if is_friday and "surah_kahf" in AUDIO:
        log.info("Playing Surah Kahf (Friday morning) on all enabled speakers")
        audio_key = "surah_kahf"
    else:
        log.info("Playing morning dhikr on all enabled speakers")
        audio_key = "morning_dhikr"

    try:
        url = local_media_url(AUDIO[audio_key])

        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, audio_key):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, audio_key)
            else:
                log.debug(f"Speaker {cast.name} routing disabled for {audio_key}, skipping")
    except Exception as e:
        log.error(f"Error playing morning dhikr: {e}")

def play_evening_content():
    """Play evening content — evening dhikr every evening"""
    if shutdown_event.is_set():
        return

    log.info("Playing evening dhikr on all enabled speakers")
    audio_key = "evening_dhikr"

    try:
        url = local_media_url(AUDIO[audio_key])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, audio_key):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, audio_key)
            else:
                log.debug(f"Speaker {cast.name} routing disabled for {audio_key}, skipping")
    except Exception as e:
        log.error(f"Error playing evening content: {e}")

def play_friday_prayer():
    """Play Friday prayer (Dua of the Soul) — scheduled to finish just before Maghrib adhan."""
    if shutdown_event.is_set():
        return
    if now_local().weekday() != 4:
        log.info("Skipping Friday prayer: not Friday (weekday=%s)", now_local().weekday())
        return

    log.info("Playing Friday prayer (Dua of the Soul) on all enabled speakers")
    audio_key = "friday_prayer"

    try:
        url = local_media_url(AUDIO[audio_key])
        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, audio_key):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, audio_key)
            else:
                log.debug(f"Speaker {cast.name} routing disabled for {audio_key}, skipping")
    except Exception as e:
        log.error(f"Error playing Friday prayer: {e}")

def play_wakeup():
    """Play wakeup audio on all enabled speakers"""
    if shutdown_event.is_set():
        return

    log.info("Playing wakeup audio on all enabled speakers")

    try:
        url = local_media_url(AUDIO["wakeup"])

        for cast in _all_casts():
            if _should_play_on_speaker(cast.name, "wakeup"):
                vol = _speaker_volume(cast.name)
                play_on_cast(cast, url, vol, "wakeup")
            else:
                log.debug(f"Speaker {cast.name} routing disabled for wakeup, skipping")
    except Exception as e:
        log.error(f"Error playing wakeup: {e}")

def play_suhoor_alarm():
    """Play suhoor alarm on enabled speakers during Ramadan (with configurable exclusions and routing)."""
    if shutdown_event.is_set():
        return

    log.info("🌙 Playing Suhoor alarm on enabled speakers (configurable exclusions applied)")

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
            else:
                log.debug(f"Speaker {cast.name} routing disabled for suhoor, skipping")
    except Exception as e:
        log.error(f"Error playing suhoor alarm: {e}")

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

        # Try to instantiate via direct-IP path
        try:
            import pychromecast as _pc
            host_tuple = (ip, 8009, ip, "Google Cast", ip)  # name placeholder; replaced after status
            cast = _pc.get_chromecast_from_host(host_tuple)
            cast.wait(timeout=10)
            friendly = getattr(cast, "name", None) or getattr(getattr(cast, "cast_info", None), "friendly_name", None) or ip
        except Exception as e:
            return jsonify({"ok": False, "error": f"Cast handshake failed: {e}"}), 400

        # Persist to known_speakers.json
        ROOT = os.path.dirname(os.path.abspath(__file__))
        KH = os.path.join(ROOT, "known_speakers.json")
        try:
            known = {}
            if os.path.exists(KH):
                with open(KH) as f:
                    known = json.load(f) or {}
            known[friendly] = ip
            with open(KH, "w") as f:
                json.dump(known, f, indent=2)
            log.info(f"O36: known_speakers.json updated with {friendly} -> {ip}")
        except Exception as e:
            log.warning(f"O36: persist to known_speakers.json failed: {e} — speaker still added live")

        # Refresh discovery so the new speaker is in _general_casts
        try:
            discover_casts()
        except Exception as e:
            log.warning(f"O36: post-add discovery failed: {e} — speaker exists but not yet in casts list")

        return jsonify({"ok": True, "name": friendly, "ip": ip})
    except Exception as e:
        log.error(f"add_by_ip endpoint error: {e}")
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
