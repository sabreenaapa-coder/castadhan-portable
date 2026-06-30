# Audio Library — Accepted Design

**Status:** Accepted (blueprint for future additive work — not yet built)
**Date:** 2026-06-30
**Scope:** Let users choose from multiple adhans, Quran reciters, wake-up alarms, and
morning/evening dhikrs — without bloating the image, breaking the live fleet, or
rewriting the playback path.

---

## 1. Codebase reality (verified, not assumed)

The system already has both halves of this; the work is *wiring*, not a rewrite.

- **Flat role→file map is the single source of truth for playback.** `AUDIO = CFG["audio"]`
  (app.py:627). Every player reads `AUDIO["adhan"]`, `AUDIO["wakeup"]`, etc.
- **Swapping a role's file is already a solved, live operation.** The upload + key-remap
  endpoint (`/api/audio/upload` with `key=`) writes a file and does `AUDIO[key] = …` in
  place, persisting atomically. The Wake-up tab already has a file-picker dropdown.
- **On-demand download engine exists.** `scheduled_audio` + `custom_audio`: sequential
  download worker, atomic `.tmp` → size-check (`>1024`) → `os.replace`, state tracking,
  3-strikes auto-disable, `bundled`/`http`/`file` URL resolution.
- **Downloads are NOT converted.** `_do_custom_audio_download` streams raw and serves it
  at `/media/custom/`. `create_compatible_audio_file` runs only for *bundled* startup
  files (app.py:764) and is skipped entirely under `LITE_MODE` (app.py:750). → There is
  **no ffmpeg-on-the-Pi for downloaded audio**; large surahs are served as-is.
- **Choices already persist across updates.** The updater preserves `config.yaml`,
  `audio/`, and `/var/lib/castadhan/custom_audio`. New config keys auto-appear via
  `_deep_merge_defaults` (fills MISSING keys only — never clobbers a user's saved value).
- **In-memory swap is race-free.** `AUDIO[role]` is a single dict→string assignment under
  the GIL; a player reads either the old or new path, never garbage. No file-lock needed.

## 2. The design

**Catalogue + selection → resolver → the existing `AUDIO` map.**

- **Catalogue** = the *universe* of options the system knows about (metadata only; ships
  in releases; grows over time).
- **Library** = the *subset* actually downloaded to this unit
  (`/var/lib/castadhan/custom_audio`).
- **Selection** = the user's active pick per role (per unit; preserved by the updater).
- **Resolver** (runs at startup + on any change): selection → look up option in catalogue
  → ensure the file is local (download via the existing `custom_audio` worker if not
  bundled) → **write the resolved path into `AUDIO[role]`.** The resolver is
  **non-blocking at boot**: if a selected file is missing it leaves `AUDIO[role]` on its
  last-good value, spawns the async worker, and hot-swaps when the download finishes — the
  Pi never waits on the network during startup (a slow link must never delay Fajr).

**The entire playback path stays untouched** — players still read `AUDIO[role]`; the
catalogue is purely a resolution layer feeding it. (Proven: that's exactly what the E-1
key-remap already does in production.)

### Data model

```yaml
audio_catalog:                      # the menu (release-shipped, metadata only)
  adhan:
    label: "Adhan"
    options:
      - {id: makkah_haram,  title: "Makkah Haram",   source: bundled, file: audio/adhan/makkah.mp3}
      - {id: alafasy_adhan, title: "Mishary Alafasy", source: "https://…/pack-adhans/alafasy.mp3",
         compatible: "https://…/pack-adhans/alafasy_compatible.mp3", size_mb: 3.4}
  fajr_adhan: {label: "Fajr adhan", options: [ … ]}
  wakeup:     {label: "Wake-up",    options: [ … ]}
  morning_dhikr: {…}; evening_dhikr: {…}
quran_reciters:
  options:
    - {id: alafasy, title: "Mishary Alafasy", base_url: "https://…/pack-alafasy/"}
    - {id: sudais,  title: "Al-Sudais",       base_url: "https://…/pack-sudais/"}

audio_selection:                    # the user's picks (per unit; preserved)
  adhan:
    default: makkah_haram
    overrides:                      # Fajr is an OVERRIDE of the default, not a separate role
      fajr: alafasy_adhan
  wakeup: birdsong
quran: {reciter: alafasy}
```

The resolver still emits the flat `AUDIO["adhan"]` and `AUDIO["fajr_adhan"]`, so the
playback code never changes.

## 3. Adopt / Push-back (the distilled decisions)

### Adopt
| Decision | Rationale |
|---|---|
| **Catalogue vs Library** distinction | Clarifies model + UI: menu of options vs downloaded subset |
| **Pre-cache popular options** in the golden image | Removes the download wait for the 80% case (Makkah + Alafasy + current wakeup/dhikr) |
| **Pre-bake `_compatible` twins in the audio-packs** (build machine) | The Pi never runs ffmpeg on downloaded files — kills the latency/CPU concern at source |
| **Fajr as an *override*** of the default adhan, not a separate role | Fajr *is* an adhan; gives per-prayer for free without choice-paralysis |
| **Stable IDs + `deprecated` flag** | Catalogue will churn; fall back to default + notify on a deprecated pick |
| **Graceful reciter switch** | Keep the old reciter active until the new pack's first surahs are downloaded + ready |
| **Unified backend, role-specific UIs** | One resolver; Adhan picker ≠ alarm picker in feel |

### Push back (already handled by the code — do NOT add)
| Worry | Why it's moot |
|---|---|
| ffmpeg pins the Pi on a 30 MB surah | Downloads aren't converted; served raw (verified) |
| Race writing `AUDIO[role]` mid-read → file-lock | Single GIL-atomic dict assignment; config writes already atomic |
| OOM / SD wear on half-finished download | Sequential queue, atomic `.tmp`→`replace`, size verify, 3-strikes disable |
| Deploy the full structure *instead of* flat `fajr_adhan` today | Flat key IS the resolver's output → shipping it is the free, forward-compatible seed |

### Product line
**Fajr is liturgically distinct** (carries *"aṣ-ṣalātu khayrun min an-nawm"*), so a separate
Fajr choice is warranted. **Per-prayer-for-all-five is choice-paralysis** → support
overrides in the data model (free), but **surface only Fajr in the UI by default.**

## 4. Phased plan

- **Phase 0 (shipping now):** flat `audio.fajr_adhan` on the golden image + live fleet.
  Forward-compatible seed — *not* tech debt.
- **Phase 1:** `audio_catalog` + `audio_selection` + resolver feeding `AUDIO`; startup
  migration infers selection from the existing flat map. Backward-compatible, no UI.
- **Phase 2:** wire on-demand download to the `custom_audio` worker; audio-packs ship
  pre-baked `_compatible` twins.
- **Phase 3:** role-specific pickers (dropdown + ▶ preview, async download states) + a
  single Quran reciter picker.
- **Phase 4:** publish the audio-pack GitHub releases the catalogue points at.

## 4a. Implementation conditions (folded from 3 review rounds)

The load-bearing conditions reviewers raised; the rest was process or already-handled.

- **Async/non-blocking resolver at boot (hard requirement).** Missing selection → keep
  `AUDIO[role]` on last-good, spawn worker, hot-swap on completion. (Also in §2.)
- **Migration runs exactly once and never loses the current sound.** First boot under the
  new model infers `audio_selection` from the flat map (`audio.fajr_adhan=alafasy` →
  `selection.adhan.overrides.fajr=alafasy`); idempotent thereafter.
- **Catalogue is a remote `catalog.json`, not bundled.** Served alongside the GitHub
  audio-pack releases, fetched at boot, cached locally (last-good used offline). New
  adhans/reciters appear in the picker **without an OTA**.
- **Pre-cache stays bounded.** Ship only popular defaults in the image; rule is "bounded +
  checked against free space" — the updater's staging-space check already guards this
  (32 GB cards leave ample room, so the exact MB figure is not load-bearing).
- **UI download states are first-class** (Phase 3): `Available` / `Downloading` (disabled,
  progress) / `Ready`. **Preview plays the local file only** — never stream a partial
  download. Fajr override shows `Default: Makkah · Fajr: Alafasy [✕ clear]`.
- **Usage signal without telemetry:** resolver logs a line whenever an override is active,
  so fleet logs reveal whether the Fajr choice is actually used (validate or cut later).
- **Phase 3 ships the adhan/Fajr picker FIRST** (highest value), then the rest — closes the
  "no way to change it between Phase 0 and the full UI" gap without a throwaway toggle.

## 5. Deferred (note, don't block)

- A single write-lock for `config.yaml` to cover the narrow UI-write vs 04:00-OTA-write
  race. Cheap; add when building the settings-save path.
- CDN mirror / fallback source for packs. GitHub releases are highly available; revisit
  only if it becomes a real failure mode.
