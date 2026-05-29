"""volume_policy.py — CastAdhan peripheral-audio volume + quiet-hours policy.

Built to the LOCKED v1 spec. Two independent axes, never collapsed:
  volume_category:       CORE | SECONDARY | PERIPHERAL   -> how loud (ratio of master)
  quiet_hours_behaviour: ALLOW | ATTENUATE | SUPPRESS    -> whether night can touch it
Every audio type sets BOTH. No hard-coded per-type branches in the resolver.

  ALLOW does NOT mean "loud." ALLOW means quiet hours cannot silence it; it still
  rides its volume_category at the speaker's master volume. The adhan at Fajr is
  CORE + ALLOW = master volume, uninterrupted.

THE RESOLVER is the single choke point — everything that casts audio calls
resolve_play_volume() first. It returns an int 0–100 (play at that %) or None
(do not play; log as 'suppressed', which is HEALTHY, not a failure).

LOCKED DECISIONS
  1. ATTENUATE's night floor reuses the PERIPHERAL ratio (no extra knob):
     "attenuated audio drops to the peripheral level at night."
  2. Clamp order during quiet hours: night_volume = min(category_volume,
     peripheral_volume). The night floor always wins -> "quiet but present."
  Eid: takbeeraat is PERIPHERAL+SUPPRESS (already silent in quiet hours) PLUS an
  explicit belt-and-braces rule — takbeeraat after Fajr is always suppressed.
  Quiet hours: clock-only 22:00–07:00 (explainable). quiet_strategy flag reserved
  for a v2 astro/union mode; only CLOCK_ONLY is implemented.

FAIL-SAFE: an unknown type or ANY internal error resolves to the master volume
(never None). A policy bug must never mute the adhan — worst case is "too loud."
Duration rule (owner ask): an UNMAPPED, non-adhan clip longer than 60s is treated
as PERIPHERAL (neighbour-safe). Explicitly-classified types always win, so this
never overrides the locked twilight (SECONDARY+ATTENUATE) protection.
"""
from datetime import datetime, time as _time

# Ratio of the speaker's master volume, per loudness category (Apartment profile).
_DEFAULT_RATIOS = {"CORE": 1.0, "SECONDARY": 0.4, "PERIPHERAL": 0.3}

# Per-audio-type policy, keyed by the REAL audio_type strings app.py casts with.
_DEFAULT_TYPES = {
    # CORE + ALLOW — the call to prayer + the reminders the user must hear.
    "adhan":           {"category": "CORE",      "quiet": "ALLOW"},
    "adhan_compatible":{"category": "CORE",      "quiet": "ALLOW"},
    "fajr_warning":    {"category": "CORE",      "quiet": "ALLOW"},
    "dhuhr_warning":   {"category": "CORE",      "quiet": "ALLOW"},
    "asr_warning":     {"category": "CORE",      "quiet": "ALLOW"},
    "maghrib_warning": {"category": "CORE",      "quiet": "ALLOW"},
    # CORE + ALLOW — deliberate alarms that fire INSIDE quiet hours by design.
    # (Not in the abstract spec list, but the spec's own rule says Core is never
    # silenced; silencing these would defeat the alarm's entire purpose.)
    # suhoor: enabled by default, ALLOW so quiet hours can't silence the wake
    # alarm, but at a fixed 50% (per-type `ratio` override) — loud enough to wake
    # the sleeper, not the block. wakeup: CORE+ALLOW if played, but DISABLED by
    # default at the scheduler level (owner opts in via the console).
    "suhoor_alarm":    {"category": "CORE",      "quiet": "ALLOW", "ratio": 0.5},  # Ramadan ~Fajr-30m
    "wakeup":          {"category": "CORE",      "quiet": "ALLOW"},   # alarm clock (e.g. 06:30)
    # SECONDARY + ATTENUATE — the key case: the only signal Isha entered when
    # prayers are combined, so it must be quiet-but-present, never silenced.
    "twilight":        {"category": "SECONDARY", "quiet": "ATTENUATE"},
    # PERIPHERAL — the neighbour-risk audio. Eid takbeeraat is SUPPRESS (the core
    # complaint, esp. at Fajr). Owner preference (overrides the spec): dhikr + the
    # duas stay quiet-but-present at night = ATTENUATE, not silenced.
    "takbeeraat":      {"category": "PERIPHERAL", "quiet": "SUPPRESS"},
    "morning_dhikr":   {"category": "PERIPHERAL", "quiet": "ATTENUATE"},
    "evening_dhikr":   {"category": "PERIPHERAL", "quiet": "ATTENUATE"},
    "surah_kahf":      {"category": "PERIPHERAL", "quiet": "SUPPRESS"},   # long recitation
    "friday_prayer":   {"category": "PERIPHERAL", "quiet": "ATTENUATE"},  # Friday dua, quiet-but-present
    "dua_of_soul":     {"category": "PERIPHERAL", "quiet": "ATTENUATE"},
}

# Fail-safe for any type not in the map AND not caught by the duration rule:
# treat as CORE+ALLOW so an unmapped clip still PLAYS rather than being muted.
_DEFAULT_TYPE = {"category": "CORE", "quiet": "ALLOW"}

# Apartment profile = the gift default. Owner opts "up", never down.
DEFAULT_POLICY = {
    "enabled": True,
    "quiet_hours": {"strategy": "CLOCK_ONLY", "start": "22:00", "end": "07:00"},
    "category_ratios": dict(_DEFAULT_RATIOS),
    "types": {k: dict(v) for k, v in _DEFAULT_TYPES.items()},
    "default_type": dict(_DEFAULT_TYPE),
}

_DURATION_PERIPHERAL_THRESHOLD_S = 60  # non-adhan clips longer than this, if unmapped, are peripheral


def _parse_hhmm(s, fallback):
    try:
        h, m = str(s).split(":")[:2]
        return _time(int(h), int(m))
    except Exception:
        return fallback


def _merge(policy):
    """Shallow-merge a caller policy over DEFAULT_POLICY so partial configs work."""
    if not policy:
        return DEFAULT_POLICY
    p = dict(DEFAULT_POLICY)
    p.update({k: v for k, v in policy.items()
              if k not in ("types", "category_ratios", "quiet_hours", "default_type")})
    p["category_ratios"] = {**_DEFAULT_RATIOS, **(policy.get("category_ratios") or {})}
    p["types"] = {**DEFAULT_POLICY["types"], **(policy.get("types") or {})}
    p["default_type"] = policy.get("default_type") or DEFAULT_POLICY["default_type"]
    p["quiet_hours"] = {**DEFAULT_POLICY["quiet_hours"], **(policy.get("quiet_hours") or {})}
    return p


def in_quiet_hours(now, quiet_cfg):
    """Clock-only quiet-hours test, wrap-around aware (22:00–07:00 spans midnight).
    `strategy` is reserved for a v2 astro/union mode; only CLOCK_ONLY is built."""
    start = _parse_hhmm(quiet_cfg.get("start", "22:00"), _time(22, 0))
    end = _parse_hhmm(quiet_cfg.get("end", "07:00"), _time(7, 0))
    t = now.time() if isinstance(now, datetime) else now
    if start <= end:
        return start <= t < end
    return t >= start or t < end   # wraps past midnight


def _classify(audio_type, policy, duration_s):
    """Return the {category, quiet} spec for an audio type. Explicit map wins;
    otherwise the duration rule (unmapped, non-adhan, >60s -> PERIPHERAL); else
    the CORE+ALLOW fail-safe default."""
    spec = policy["types"].get(audio_type)
    if spec is not None:
        return spec
    if (audio_type != "adhan" and duration_s is not None
            and duration_s > _DURATION_PERIPHERAL_THRESHOLD_S):
        return {"category": "PERIPHERAL", "quiet": "SUPPRESS"}
    return policy["default_type"]


def resolve_play_volume(audio_type, speaker_base_volume, profile_config=None,
                        now_local=None, prayer_name=None, duration_s=None):
    """THE choke point. Decide how a clip should play right now.

    Args:
      audio_type:          e.g. "adhan", "takbeeraat", "twilight", "suhoor_alarm".
      speaker_base_volume: the speaker's master volume as an int 0–100.
      profile_config:      optional override dict (merged over the Apartment default).
      now_local:           datetime (defaults to datetime.now()).
      prayer_name:         the prayer that triggered this clip, if any (for the
                           explicit Fajr-takbeeraat suppression).
      duration_s:          clip length in seconds, if known (for the duration rule
                           on unmapped types).

    Returns int 0–100 to play at, or None to suppress (caller logs 'suppressed').
    """
    try:
        if now_local is None:
            now_local = datetime.now()
        base = max(0, min(int(round(float(speaker_base_volume))), 100))
        p = _merge(profile_config)
        if not p.get("enabled", True):
            return base  # policy off -> behave exactly as before (master volume)

        # Eid belt-and-braces: takbeeraat after Fajr is always suppressed, even if
        # quiet hours were narrowed away from ~05:00. This is the actual complaint.
        if audio_type == "takbeeraat" and (prayer_name or "").strip().lower() == "fajr":
            return None

        spec = _classify(audio_type, p, duration_s)
        category = spec.get("category", "CORE")
        behaviour = spec.get("quiet", "ALLOW")
        ratios = p["category_ratios"]
        # A type may pin its own volume ratio (e.g. suhoor at 0.5) that overrides
        # its category ratio; otherwise it rides the category ratio.
        ratio = spec.get("ratio")
        if ratio is None:
            ratio = ratios.get(category, 1.0)
        category_volume = base * ratio

        if in_quiet_hours(now_local, p["quiet_hours"]):
            if behaviour == "SUPPRESS":
                return None
            if behaviour == "ATTENUATE":
                night_floor = base * ratios.get("PERIPHERAL", 0.3)   # decision 1
                return int(round(min(category_volume, night_floor)))  # decision 2
            # ALLOW: quiet hours don't touch it.

        return int(round(category_volume))
    except Exception:
        # FAIL-SAFE: never silence audio because of a policy bug.
        try:
            return max(0, min(int(round(float(speaker_base_volume))), 100))
        except Exception:
            return 100
