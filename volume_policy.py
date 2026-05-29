"""volume_policy.py — CastAdhan peripheral-audio volume + quiet-hours policy.

WHY THIS EXISTS
---------------
Aunt lives in a high-rise. The adhan and the prayer warnings should play at her
intended volume, but "peripheral" audio (Eid takbeeraat, dhikr, duas) shouldn't
risk disturbing neighbours — especially at night. This module decides, per audio
type and time of day, how loud a clip should be and whether quiet hours may touch
it. It is a PURE policy layer: it computes numbers, it never casts anything.

TWO ORTHOGONAL AXES (the whole design in two lines)
---------------------------------------------------
  volume_category:       CORE | SECONDARY | PERIPHERAL   -> how loud (ratio of master)
  quiet_hours_behaviour: ALLOW | ATTENUATE | SUPPRESS    -> what night may do to it

Every audio type sets BOTH. There are no hard-coded per-type exceptions in the
resolver — the twilight reminder isn't "the special case," it's just the type
that happens to be SECONDARY + ATTENUATE. New audio types pick one value on each
axis and the resolver never grows a new branch.

  NOTE ON "ALLOW": ALLOW means "quiet hours don't touch this," NOT "play loud."
  ALLOW audio still rides its volume_category. The adhan at Fajr is CORE + ALLOW
  = full master volume, uninterrupted — correct. A 06:30 wakeup alarm is also
  ALLOW so quiet hours can't silence the alarm you set on purpose.

KEY DECISIONS (locked; change via config, not code)
---------------------------------------------------
  1b. ATTENUATE at night reuses the PERIPHERAL ratio as its floor (no 4th number).
  2.  ATTENUATE clamps with min(category_volume, night_floor) so the night floor
      always wins when quiet hours are active — "quiet but present."

FAIL-SAFE
---------
Unknown audio types and ANY internal error resolve to (master_volume, "play").
A policy bug must never silence the adhan — the worst outcome here is "too loud,"
never "didn't play."
"""
from datetime import datetime, time as _time

# Ratio of the speaker's master volume, per loudness category.
# (Converged numbers: SECONDARY 40%, PERIPHERAL 30%. Tunable via config.)
_DEFAULT_RATIOS = {"CORE": 1.0, "SECONDARY": 0.4, "PERIPHERAL": 0.3}

# Per-audio-type policy. Grounded in the REAL audio inventory in app.py.
# Two axes per type. Anything not listed falls back to _DEFAULT_TYPE (fail-safe).
_DEFAULT_TYPES = {
    # --- CORE: the call to prayer + reminders the user must hear ---------------
    "adhan":           {"category": "CORE",      "quiet": "ALLOW"},
    "adhan_compatible":{"category": "CORE",      "quiet": "ALLOW"},   # alternate adhan encoding
    "fajr_warning":    {"category": "CORE",      "quiet": "ALLOW"},
    "dhuhr_warning":   {"category": "CORE",      "quiet": "ALLOW"},
    "asr_warning":     {"category": "CORE",      "quiet": "ALLOW"},
    "maghrib_warning": {"category": "CORE",      "quiet": "ALLOW"},
    # --- Intentional alarms that fire DURING quiet hours BY DESIGN ------------
    # These must stay ALLOW or quiet hours would silence the alarm's whole point.
    "suhoor_alarm":    {"category": "CORE",      "quiet": "ALLOW"},   # Ramadan ~Fajr-30m
    "wakeup":          {"category": "CORE",      "quiet": "ALLOW"},   # alarm clock (e.g. 06:30)
    # --- SECONDARY: meaningful but quieter, night may soften ------------------
    "twilight":        {"category": "SECONDARY", "quiet": "ATTENUATE"},  # Isha-combined reminder
    "friday_prayer":   {"category": "SECONDARY", "quiet": "ALLOW"},      # DECISION: evening dua
    # --- PERIPHERAL: the neighbour-risk audio (dhikr, takbeeraat, duas) -------
    "takbeeraat":      {"category": "PERIPHERAL", "quiet": "SUPPRESS"},  # Eid, after each adhan
    "morning_dhikr":   {"category": "PERIPHERAL", "quiet": "ATTENUATE"},
    "evening_dhikr":   {"category": "PERIPHERAL", "quiet": "ATTENUATE"}, # cutoff 20:00, rarely in quiet hours
    "surah_kahf":      {"category": "PERIPHERAL", "quiet": "ATTENUATE"}, # Friday recitation
    "dua_of_soul":     {"category": "PERIPHERAL", "quiet": "ATTENUATE"}, # manual trigger
}

# Fail-safe for any audio type not in the map: treat as CORE+ALLOW so an
# unmapped/new clip still PLAYS at full volume rather than being silently muted.
_DEFAULT_TYPE = {"category": "CORE", "quiet": "ALLOW"}

# Apartment-friendly defaults (the gift default; owner can opt "up").
DEFAULT_POLICY = {
    "enabled": True,
    "quiet_hours": {"strategy": "clock", "start": "22:00", "end": "07:00"},
    "category_ratios": dict(_DEFAULT_RATIOS),
    "types": {k: dict(v) for k, v in _DEFAULT_TYPES.items()},
    "default_type": dict(_DEFAULT_TYPE),
}

ACTION_PLAY = "play"
ACTION_SUPPRESS = "suppress"


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
    p.update({k: v for k, v in policy.items() if k != "types" and k != "category_ratios"})
    p["category_ratios"] = {**_DEFAULT_RATIOS, **(policy.get("category_ratios") or {})}
    p["types"] = {**DEFAULT_POLICY["types"], **(policy.get("types") or {})}
    p["default_type"] = policy.get("default_type") or DEFAULT_POLICY["default_type"]
    p["quiet_hours"] = {**DEFAULT_POLICY["quiet_hours"], **(policy.get("quiet_hours") or {})}
    return p


def in_quiet_hours(now, quiet_cfg):
    """Clock-only quiet-hours test, wrap-around aware (e.g. 22:00–07:00 spans midnight).
    `strategy` is reserved for a future astro/union mode; only 'clock' is implemented."""
    start = _parse_hhmm(quiet_cfg.get("start", "22:00"), _time(22, 0))
    end = _parse_hhmm(quiet_cfg.get("end", "07:00"), _time(7, 0))
    t = now.time() if isinstance(now, datetime) else now
    if start <= end:
        return start <= t < end
    return t >= start or t < end   # wraps past midnight


def resolve(audio_type, master_volume, now=None, policy=None):
    """Resolve how an audio clip should play right now.

    Args:
      audio_type:    e.g. "adhan", "takbeeraat", "twilight", "suhoor_alarm".
      master_volume: the speaker's configured volume as a float 0.0–1.0.
      now:           datetime (defaults to datetime.now()).
      policy:        optional override dict (merged over DEFAULT_POLICY).

    Returns (effective_volume: float 0.0–1.0, action: "play"|"suppress").
    """
    try:
        if now is None:
            now = datetime.now()
        master = max(0.0, min(float(master_volume), 1.0))
        p = _merge(policy)
        if not p.get("enabled", True):
            return (master, ACTION_PLAY)

        spec = p["types"].get(audio_type) or p["default_type"]
        category = spec.get("category", "CORE")
        behaviour = spec.get("quiet", "ALLOW")
        ratios = p["category_ratios"]

        category_volume = master * ratios.get(category, 1.0)

        if in_quiet_hours(now, p["quiet_hours"]):
            if behaviour == "SUPPRESS":
                return (0.0, ACTION_SUPPRESS)
            if behaviour == "ATTENUATE":
                # Decision 1b: night floor = PERIPHERAL ratio (no extra knob).
                # Decision 2: the floor wins, so a high master can't leak through.
                night_floor = master * ratios.get("PERIPHERAL", 0.3)
                return (min(category_volume, night_floor), ACTION_PLAY)
            # behaviour == "ALLOW": quiet hours don't touch it.

        return (category_volume, ACTION_PLAY)
    except Exception:
        # FAIL-SAFE: never silence audio because of a policy bug.
        try:
            return (max(0.0, min(float(master_volume), 1.0)), ACTION_PLAY)
        except Exception:
            return (1.0, ACTION_PLAY)
