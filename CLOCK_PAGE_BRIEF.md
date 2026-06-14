# CastAdhan — Clock Page Brief (for multi-LLM style debate)

**Why this exists:** masood (non-technical recipient) said the dashboard is
"too complicated for the average person." Decision taken: split into two pages.
This brief defines **exactly what must be on the family-facing Clock page**,
compares it line-by-line against the original console, and records that the
**Admin page keeps the entire original console unchanged**. The *style* of the
Clock page is what we will debate with other LLMs — the *contents* below are
settled.

---

## 1. The two-page model (settled)

- **Clock page** — the default. What everyone lands on. Family-facing,
  calm, glanceable. Contents defined in §3.
- **Admin page** — one tap away via a **Clock / Admin flip**. Operator-facing.

### NOTE ON THE ADMIN PAGE (settled, not up for debate)
> **The Admin page contains EVERYTHING the original console has today —
> nothing removed, nothing redesigned — PLUS a single button to flip back to
> Clock mode.**

This is deliberate and low-risk: the current console is battle-tested across
the fleet. We do **not** rebuild it. We simply stop showing it by default and
put it behind the flip. The family never sees it; the operator taps "Admin"
and gets the exact console they have now, with a "← Clock" button added.

- **No PIN gate** (operator decision). The flip is open.
- The flip control appears on **both** pages (top, centred, subtle —
  placement is a style question, see §6).

---

## 2. The non-negotiable constraints for the Clock page

These are fixed before any styling debate:

1. **Bilateral symmetry is preserved.** The clock sits on the central axis;
   prayer information mirrors left/right. This symmetry is the product's
   identity and must NOT be broken (an earlier asymmetric clock-left/info-right
   mockup was rejected outright).
2. **The original analog clock is the hero** — the 24-hour dial with prayer
   dots and day/night shading. Not a "next prayer" card, not the 12-hour
   simplified face (that was only for the TV `?mode=clock`).
3. **Dark + gold identity** unchanged (#0d0d0d background, #D4AF37 gold,
   silver-grey secondary).
4. **Stop button appears ONLY while audio is playing** — adhan or any other
   sound file. When idle, no Stop button exists. When playing, a "Now playing"
   indicator + Stop appear, then vanish when audio ends.
5. **Single self-contained page**, framework-free, served offline by the Pi
   (no build step, no CDN) — works on phone, tablet, monitor, TV.
6. **A non-technical person never needs to be told a URL** — the Clock page is
   the default; the flip is visible.

---

## 3. CLOCK PAGE — definitive MUST-HAVE list

Everything a family member needs, and nothing else:

| # | Element | Notes |
|---|---|---|
| 1 | **24-hour analog clock** (the original) | Central axis. Gold rim, hour/minute/second hands, hour numerals, prayer-time dots, sunrise marker. |
| 2 | **Day/night shading** on the clock face | The lighter sunrise→maghrib arc, dark at night — as today. |
| 3 | **All six prayer times** | Fajr, Sunrise, Dhuhr, Asr, Maghrib, Isha — mirrored 3 left / 3 right of the clock. Next prayer highlighted gold. |
| 4 | **Digital time** (HH:MM:SS) | Centred below the clock. Tabular numerals. |
| 5 | **Gregorian date** | e.g. "Sat 13 June 2026". |
| 6 | **Hijri date** | e.g. "27 Dhū al-Ḥijjah 1447". |
| 7 | **Next-prayer countdown** | "Next: Asr in 2h 14m". Centred. |
| 8 | **Today's status** ("did it play?") | Per-prayer ✓ fired / ✗ failed / · pending — the one bit of reassurance a family wants without tapping. |
| 9 | **Now-playing indicator** | Appears only while audio plays: "Now playing — Asr adhan · Hall speaker". |
| 10 | **Stop button** | Appears only while audio plays. Stops current audio. (Does NOT need the scheduler-pause of Emergency Stop — that stays in Admin.) |
| 11 | **Location label** | Small, e.g. "Swansea, United Kingdom". |
| 12 | **Clock / Admin flip** | The only navigation. Flips to the Admin page. |

### Clock page — OPTIONAL / ambient (debate whether to keep)
These were on the original console, fit the aesthetic, and could help fill the
symmetric top corners left empty by removing the admin panels. Keep or drop is
a **style question** for the debate:

| Element | Argument to keep | Argument to drop |
|---|---|---|
| **Weather widget** (icon, temp, condition) | Ambient, pleasant, balances a corner | Not prayer-related; adds an API dependency |
| **Moon-phase glyph** | Thematic, balances the other corner | Decorative only |
| **Language picker** (EN/NL/FR/AR) | Family may need their language | Set once at gift-time; could live in Admin |
| **"PRAYER TIMES" title** | Identity, centred, symmetric | Could be redundant with the clock |

---

## 4. COMPREHENSIVE COMPARISON — original console → where each element goes

Every element of the current console, and its destination. **C** = Clock page,
**A** = Admin page.

### Chrome / header
| Original element | C | A | Note |
|---|:--:|:--:|---|
| "PRAYER TIMES" title | ◐ | — | Optional on Clock (§3 debate) |
| Location subtitle | ✅ | ✅ | Small on Clock; full on Admin |
| Language picker | ◐ | ✅ | Debate placement |
| Footer (version, device id) | — | ✅ | Operator info |

### Left panel — "Speakers (N)"
| Original element | C | A | Note |
|---|:--:|:--:|---|
| Per-speaker status dot | — | ✅ | |
| Per-speaker on/off toggle | — | ✅ | |
| Per-speaker volume slider | — | ✅ | |
| Per-speaker remove (🗑) | — | ✅ | |
| Enable All / Disable All | — | ✅ | |
| Rediscover / Force Rediscover | — | ✅ | |
| Add Speaker by IP | — | ✅ | |
| Audio Routing button | — | ✅ | |

### Centre — clock cluster
| Original element | C | A | Note |
|---|:--:|:--:|---|
| 24-hour analog clock | ✅ | — | The Clock-page hero |
| Day/night shaded arc | ✅ | — | |
| Prayer-time dots on rim | ✅ | — | |
| Sunrise marker | ✅ | — | |
| Moon-phase glyph | ◐ | — | Optional (§3) |
| Digital time HH:MM:SS | ✅ | — | |
| Gregorian date | ✅ | — | |
| Hijri date | ✅ | — | |
| Weather widget | ◐ | — | Optional (§3) |

### Right panel — "Quick Actions"
| Original element | C | A | Note |
|---|:--:|:--:|---|
| Test Adhan | — | ✅ | Operator action |
| Dua of the Soul | — | ✅ | |
| Stop All | ◐ | ✅ | Clock has a Stop **only while playing**; this always-on button stays in Admin |
| Emergency Stop (red, +60min pause) | — | ✅ | Heavier action → Admin |
| Resume Schedule | — | ✅ | |
| Refresh Data | — | ✅ | |
| Settings | — | ✅ | (Admin IS settings) |
| Today's status strip | ✅ | ✅ | Family wants "did it play?"; operator keeps it too |

### Prayer-time cards
| Original element | C | A | Note |
|---|:--:|:--:|---|
| Fajr / Sunrise / Dhuhr / Asr / Maghrib / Isha | ✅ | ✅ | Mirrored on Clock; also visible in Admin |
| Next-prayer highlight | ✅ | ✅ | |
| Countdown bar | ✅ | ✅ | |

### Settings modal — Tab "Basic"
| Original element | C | A |
|---|:--:|:--:|
| Location search / autodetect / lat / lon / timezone | — | ✅ |
| Calculation method | — | ✅ |
| Madhab (Asr) | — | ✅ |
| Fajr adhan-time mode / mins-before-sunrise / workday cap / weekend offset | — | ✅ |
| Evening content mins / suppress-after / suhoor lead | — | ✅ |
| Morning dhikr time | — | ✅ |

### Settings modal — Tab "Wakeup"
| Original element | C | A |
|---|:--:|:--:|
| Wakeup enable / time / weekdays-only / audio file | — | ✅ |
| Which speakers play wakeup | — | ✅ |

### Settings modal — Tab "Advanced"
| Original element | C | A |
|---|:--:|:--:|
| High-latitude method / always-apply / mins-after-maghrib / max-isha / fajr-at-raw / scan-frequency | — | ✅ |
| Persistent twilight skip | — | ✅ |
| WiFi scan / network / password | — | ✅ |
| Speaker defaults (include/exclude name, master volume) | — | ✅ |
| Considerate mode (quiet overnight) | — | ✅ |
| Updates (version, channel, auto-update, log) | — | ✅ |

### Settings modal — Tab "Speaker Routing"
| Original element | C | A |
|---|:--:|:--:|
| Audio routing matrix (speakers × audio types) | — | ✅ |
| Suhoor exclusions | — | ✅ |

### Settings modal — Tab "Audio Files"
| Original element | C | A |
|---|:--:|:--:|
| Upload audio | — | ✅ |
| Audio files table + per-file Test | — | ✅ |
| Quran & Programs (6 surahs: enable, schedule, days, speakers, quiet-test, play-now, skip-today, advanced) | — | ✅ |

**Legend:** ✅ = present · ◐ = optional / debate · — = not present

---

## 5. Summary of the split

- **Clock page = 12 must-haves** (§3) + up to 4 optional ambient items.
- **Admin page = 100% of the original console** + a "← Clock" flip button.
- **Net removal from the family's eyeline:** the entire Speakers panel (8
  controls), the Quick Actions panel (7 buttons), and all 5 settings tabs
  (17 sections). That is the whole of masood's "too complicated."
- **Net change to the clock composition:** none. Same symmetric centred clock,
  same mirrored prayer cards, same countdown — the two admin corner-panels are
  simply gone (their corners' treatment is the one open style question).

---

## 6. OPEN STYLE QUESTIONS for the LLM debate

The contents are settled; these are the looks-and-feel forks to resolve:

1. **The empty top corners.** Removing the Speakers + Quick-Actions panels
   leaves the upper-left and upper-right corners bare. Options:
   (a) leave them clean/minimal; (b) fill symmetrically with ambient
   non-admin content (weather one side, moon-phase/date the other);
   (c) something else. Which best preserves the balanced-corner symmetry?
2. **Flip control placement & style.** Top-centre segmented toggle
   (Clock | Admin)? A small gear in one corner (breaks symmetry)? A footer
   link? It must be discoverable but not compete with the clock.
3. **Today's-status presentation.** A row of 5 ticks under the clock? Tiny
   chips? Integrated into the prayer cards (a ✓ on each)? 
4. **Now-playing + Stop treatment.** Where does the Stop bar appear so it
   doesn't break symmetry when it pops in — centred under the clock? A full-
   width bar? An overlay?
5. **Optional ambient items** (weather, moon, language, title) — keep which,
   drop which, for the cleanest symmetric result?
6. **Prayer-card layout.** Keep today's two stacked columns of 3 flanking the
   clock? Or a single horizontal strip of 6 below? (Stacked-flanking preserves
   the current symmetry; horizontal is more glanceable on TV.)
7. **Clock-face numerals.** The original 24-hour dial — keep all hour
   numerals, every-2-hours, or minimal? (masood found dense 24h hard to read,
   but the operator likes the original. This is the live tension.)

---

## 7. What is NOT being debated

- The two-page split itself (settled).
- That Admin = the full original console + flip-back (settled).
- The dark + gold identity (settled).
- That the original analog clock is the Clock-page hero (settled).
- Bilateral symmetry as a hard constraint (settled).
- Stop-only-while-playing (settled).
- No PIN (settled).

---

## 8. §8 — CONVERGED STYLE SPEC (frozen)

Outcome of the multi-LLM debate (Claude, ChatGPT, DeepSeek, Gemini) on the §6
style questions. All seven are resolved. This section is the **single source
of truth for implementation**. Documentation only — no code has been written;
no console change until Mustafa gives an explicit go.

### 8.1 The seven questions — final answers

| Q | Final decision | Vote |
|---|---|---|
| **Q1 — corners** | **Weather top-left, moon/Hijri top-right.** Passive, small, non-clickable, secondary colour. **Fail-silent**: if offline or the API errors, the container degrades to empty cleanly — never an error or a broken box. | unanimous |
| **Q2 — flip control** | **Top-centre segmented pill `[ Clock | Admin ]`** on the central axis. Active side gold-filled; inactive side muted. Appears on both pages. No gear-in-corner (breaks symmetry), no footer link (undiscoverable). | unanimous |
| **Q3 — today's status** | **Per-prayer status indicator on each of the 6 flanking cards** (primary) + **one** compact legend strip at the bottom centre. **Drop** the separate "Today's Status" box (it triplicated the information). | unanimous |
| **Q4 — Now-playing + Stop** | **Centred, directly below the digital-time / countdown cluster.** Appears ONLY while audio plays; pushes the date row down, then clears — never a left/right imbalance. Contains "Now playing — <type> · <speaker>" + a red **STOP** button. Stop calls `/api/test/stop` (current audio only; the 60-min Emergency-Stop pause stays in Admin). Reject: full-width ribbon (asymmetric mass), tap-the-dial (hidden affordance + accidental stops), bottom-right card (lopsided when it appears). | unanimous |
| **Q5 — ambient items** | **Keep weather + moon. Drop "PRAYER TIMES" title** (the clock is the title) **and the language picker** (set once; lives in Admin). | unanimous |
| **Q6 — prayer cards** | **3-left / 3-right flanking the clock** (Fajr·Sunrise·Dhuhr | clock | Asr·Maghrib·Isha). Next prayer highlighted gold. On phone (<900px) they reflow below the clock — acceptable, different context. Reject the single horizontal strip (kills the mosque-clock silhouette). | unanimous |
| **Q7 — clock numerals** | **Every 2 hours** (24, 2, 4 … 22) in muted gold + **tick marks for the odd hours.** Reject all-24 (cluttered) and 4-cardinals-only (too sparse). | unanimous |

### 8.2 The clock face — RESOLVED: Option B (muted vector day/night scene)

The one genuine fork. All four LLMs ruled **Option B**; **C (photographic) was
rejected by everyone**; A (flat) was the runner-up.

- **Build:** a pure **SVG** day/night scene drawn **behind** the hands and
  pins, inside `.clock`, `pointer-events: none`. Stylised "luxury-watch /
  Ottoman restraint" — a soft dawn gradient with a small sun, a night gradient
  with a crescent and a few stars, simple mountain silhouettes. **Vector only —
  no raster images, no CDN, works fully offline.**
- **Muted & dominated:** the scene is deeply desaturated / low-contrast (a
  ~40% knock-back). The gold hands, prayer dots and numerals MUST stay
  dominant. **Acceptance = the squint test:** from ~8 feet you identify (1) the
  hands, (2) the current-prayer marker, (3) the next-prayer marker BEFORE you
  notice the scenery. If you notice mountains/sun/moon first, it is too strong.
- **Day/night divide tracks real sunrise→sunset**, not a fixed horizontal line.
  Reuse the existing `drawDaylightArc()` sunrise/maghrib angles; the scene's
  "horizon" rotates with the seasons. (Mockups showed a fixed split for
  simplicity — the build must track the real angle.)
- **Live moon (ChatGPT refinement):** the crescent inside the night half
  reflects the **actual current moon phase** — a full-moon night looks
  different from a crescent night. This makes the dial feel alive and lets the
  top-right corner carry Hijri date + phase name.
- **Flat-shading fallback toggle (Gemini/DeepSeek):** a config switch falls the
  dial back to the current flat conic-gradient shading — resilience across the
  fleet and a clean degrade path if the scene ever misbehaves on a given Pi.

### 8.3 Status colour key (locked)

| State | Colour |
|---|---|
| Fired (played on time) | green `#5fb55f` (✓) |
| Current / next prayer | gold `#D4AF37`, optional soft pulse |
| Upcoming (later today) | hollow grey ring |
| Missed / failed | red `#c0392b` (✗) |

Used identically on the per-card indicators, the dial prayer-dots, and the
bottom legend.

### 8.4 Final composition (frozen)

```
                 [ Clock | Admin ]
  Weather                            Moon / Hijri
                Swansea, United Kingdom

  Fajr   03:07 ✓                       Asr   17:38  ◄ next
  Sunrise 04:58 ✓     ( CLOCK )         Maghrib 21:34 ○
  Dhuhr  13:16 ✓                        Isha  22:34 ○

                     15:53
            Sat 13 June · 27 Dhū al-Ḥijjah
                Next: Asr in 2h 14m

           ── Now playing · STOP (only while playing) ──

        ✓ Fired   ◄ Next   ○ Upcoming   ✗ Missed   (legend)
```

### 8.5 IMPLEMENTATION GUARDRAILS (read before any code)

When Mustafa greenlights, the Clock page is built by **surgical edits to the
real `console.html`** (currently 4,097 lines, v1.10.1), NOT a rewrite:

- **NEVER replace or regenerate the whole file.** During this debate one LLM
  ran a script that overwrote a `console.html` with a ~100-line stub and
  reported "SUCCESS" — that would have destroyed the Quran Programs feature,
  the watchdog, and all reliability work. Edits must be additive/in-place.
- Reuse the existing engine: `drawDaylightArc()`, `drawPins()`, `drawNumerals()`,
  `updateHands()`, the prayer-time API, `_log_play` status. The Clock page is a
  new **view layer + CSS**, not a new backend.
- **Admin page = today's console, unchanged**, gated behind the flip + a
  "← Clock" button. Zero changes to its contents.
- A view-state toggle (`?view=clock` default / `?view=admin`) on `<body>`;
  preserve the existing `?mode=simple` and `?mode=clock` for backward compat.
- Single self-contained page, framework-free, offline-served. No CDN, no raster.
- Corners + weather + moon fail-silent; SVG scene degrades to flat shading.

### 8.6 Status

**Spec FROZEN. Design phase complete.** Awaiting Mustafa's explicit go before
any implementation. Suggested build order when greenlit:
1. View-state toggle + `[ Clock | Admin ]` flip (default → Clock).
2. Family Clock layout (symmetric grid, flanking cards, dates, countdown).
3. Muted vector day/night dial (Option B) + flat fallback toggle.
4. Per-card status + legend; centred Now-playing/Stop bar.
5. Weather + moon corners (fail-silent); live moon phase in the dial.
6. Verify in the preview harness at phone / tablet / monitor / TV widths.

---

## 9. §9 — VISUAL DESIGN SPECIFICATION (self-contained build reference)

This section describes the Clock page completely in words. A reader who has
never seen a mockup should be able to picture it, judge it, or build it from
this section alone. All values are exact.

### 9.1 Purpose (one paragraph)

The Clock page is the **default, family-facing face** of CastAdhan — what
anyone sees when they open the device's address. Its single job is to answer,
at a glance from across a room: *what time is it, when is the next prayer, did
today's prayers play, and (only when something is sounding) how do I stop it.*
It is calm, symmetric, and contains **none** of the operator controls. All
configuration and speaker management lives one tap away on the **Admin page**
(reached by the flip control), which is the existing console unchanged.

### 9.2 Canvas & overall feel

- **Background:** near-black, `#0c0c0c` (the page) sitting in a slightly
  lighter device bezel `#1c1c1c` when framed. Flat — no gradients, no texture
  on the page itself.
- **Mood:** a premium "mosque clock" / luxury-watch object. Dark, gold,
  restrained. The analog clock is always the visual hero on the central
  vertical axis; everything else is mirrored or centred around it.
- **Composition:** strict **bilateral symmetry**. Left and right of the clock
  are mirror-weighted. Nothing sits off-axis.

### 9.3 Colour palette (exact)

| Token | Hex | Used for |
|---|---|---|
| Page background | `#0c0c0c` | the whole page |
| Bezel / frame | `#1c1c1c` | device frame, segmented-control track |
| Gold (primary accent) | `#D4AF37` | clock rim, hands (hour), next-prayer text/highlight, active flip tab, countdown border, gold dots |
| Gold-bright (highlight edge) | `#fff3c4` | the 1px ring on the "next prayer" dot only |
| Cream (primary light text) | `#f4efe2` | the big digital time |
| Warm off-white (card values) | `#e7e0cf` | prayer times in cards, weather temp |
| Silver (secondary text) | `#cfcabb` | prayer names, body text |
| Dim (tertiary text) | `#7d786c` | location, dates, legend, "Mainly clear", captions |
| Muted gold (numerals) | `#8f897a` | the 24-hour dial numerals |
| Card background | `#121212` | the five non-next prayer cards |
| Card border | `#1f1f1f` (0.5px) | prayer-card borders |
| Next-prayer card bg | `#1a150a` | the single highlighted (next) prayer card |
| Next-prayer card border | `#D4AF37` (1px) | the next card's gold border |
| Green — fired | `#5fb55f` | "played on time" status dots |
| Red — missed / stop | `#c0392b` | failed-prayer dot, the STOP button, second hand |

Dial day/night scene (all muted, sit *behind* a ~40% knock-back so they never
dominate):

| Element | Hex / detail |
|---|---|
| Dawn gradient (upper, day) | top `#15273a` → mid `#574025` → fades to transparent at the horizon |
| Sun (upper) | soft radial glow `#d8b160` over a `#c9a456` disc, ~0.6 opacity |
| Night gradient (lower) | `#0c1626` → `#090f17` |
| Stars | tiny `#cfd6e2` dots, 0.4–0.55 opacity |
| Live crescent moon (lower, in night half) | `#d2ab55`, ~0.7 opacity, shape reflects the **real current moon phase** |
| Mountain silhouette (horizon) | `#0a0f18` |
| Horizon line | `#D4AF37` at 0.28 opacity, 0.4px |
| Scene knock-back overlay | `#0c0c0c` at 40% opacity over the whole scene |

### 9.4 Typography

- **Font stack (whole page):** `"Segoe UI", system-ui, -apple-system, Roboto,
  sans-serif`. No web fonts, no CDN — system fonts only (offline-served).
- **Weights:** two only — 400 (regular) and 500 (medium). No bold-heavy 700.
- **All times use `font-variant-numeric: tabular-nums`** so digits don't jitter
  as they change (the seconds especially).
- **Sentence case / as-written** — never ALL CAPS except nothing here uses it;
  the old "PRAYER TIMES" word-mark is dropped.

Per-element type sizes (desktop/monitor; they scale fluidly with `clamp()` on
smaller/larger screens — see 9.8):

| Element | Size | Weight | Colour |
|---|---|---|---|
| Big digital time (e.g. `15:53`) | ~27px | 500 | cream `#f4efe2` |
| Prayer time in card (`17:38`) | 15px | 500 | `#e7e0cf` (gold on next card) |
| Prayer name (`Asr`) | 13px | 500 | silver `#cfcabb` (gold on next card) |
| Weather temp (`15°C`) | 16px | 500 | `#e7e0cf` |
| Moon phase / Hijri (corner) | 13px / 10px | 400 | `#e7e0cf` / `#7d786c` |
| Countdown ("Next: Asr in 2h 14m") | 12px | 400 (Asr 500 gold) | `#cfcabb` |
| Date line (Gregorian · Hijri) | 11px | 400 | `#7d786c` |
| Location | 11px | 400 | `#7d786c` |
| Flip tabs ("Clock" / "Admin") | 12px | 500 | `#1a1a1a` on gold (active) / `#8a8a8a` (inactive) |
| Dial numerals (24,2,4…22) | ~7px | 400 | `#8f897a` |
| Legend labels | 11px | 400 | `#7d786c` |

### 9.5 Layout map (top → bottom)

A single centred column of full-width bands; the middle band is a 3-column
grid:

```
BAND 1  (3-col grid)   [ weather ]   [ Clock | Admin pill ]   [ moon + Hijri ]
BAND 2  (centred)                    Swansea, United Kingdom
BAND 3  (3-col grid)   [ 3 cards ]      [  CLOCK DIAL  ]       [ 3 cards ]
                       Fajr            digital time             Asr (next)
                       Sunrise         date line                Maghrib
                       Dhuhr           countdown pill           Isha
BAND 4  (centred, conditional)        Now playing · STOP   (only while playing)
BAND 5  (centred)      legend:  ● Fired   ● Next   ○ Upcoming   ● Missed
```

- Bands 1 and 3 are CSS grids `1fr auto 1fr` — left column, centre (auto-width
  clock), right column — which guarantees the clock stays dead-centre and the
  side content mirrors.
- Vertical rhythm: ~6–16px between bands; cards gap 9px.

### 9.6 Element-by-element specification

**Flip control (Band 1, centre).** A segmented pill: track `#161616` with a
0.5px `#2d2d2d` border, ~18px corner radius, 3px inner padding. Two tabs,
"Clock" and "Admin". The active tab is a gold `#D4AF37` rounded fill with dark
`#1a1a1a` text; the inactive tab is transparent with `#8a8a8a` text. Tapping
"Admin" swaps the whole page to the Admin console; "Clock" returns. Present on
both pages.

**Weather corner (Band 1, left).** A cloud/sun line-icon (~26px, muted gold
`#caaf74`) beside a two-line block: temperature (`15°C`, 16px, `#e7e0cf`) over
condition (`Mainly clear`, 10px, `#7d786c`). Passive, non-clickable.
**Fail-silent:** if offline or the API errors, the whole block simply renders
empty — never an error, never a broken box.

**Moon + Hijri corner (Band 1, right).** Mirror of the weather block,
right-aligned: a two-line block (phase name `Waxing crescent` 13px `#e7e0cf`
over Hijri date `27 Dhū al-Ḥijjah` 10px `#7d786c`) beside a moon line-icon
(~24px, `#caaf74`). The moon icon reflects the real phase.

**Location (Band 2).** A small centred line — pin glyph + `Swansea, United
Kingdom`, 11px `#7d786c`.

**The clock dial (Band 3, centre) — the hero.** A circle ~210–220px on desktop
(fluid: `min(62vmin, 620px)` in clock contexts). Construction, back to front:
1. **Day/night scene** (clipped to the circle, `pointer-events:none`): upper
   half a muted dawn gradient with a soft sun glow; lower half a deep night
   gradient with a few faint stars and the **live crescent moon**; a dark
   mountain-silhouette horizon across the middle; a thin gold horizon line; the
   whole scene knocked back ~40% with a black overlay so it reads as quiet
   texture. The light/dark **divide tracks the real sunrise→maghrib angle** (it
   rotates with the seasons — it is NOT a fixed horizontal split).
2. **Rim:** a 3px gold `#D4AF37` circle.
3. **Numerals:** the 24-hour hours, shown **every 2 hours** (24 at top, then
   2,4,6,8,10,12 at the bottom, 14,16,18,20,22 round to the left), ~7px muted
   gold `#8f897a`. Odd hours are small tick marks, no numeral.
4. **Prayer dots** on the rim at each prayer's true 24-hour angle: green
   `#5fb55f` for already-fired, a larger gold `#D4AF37` dot with a `#fff3c4`
   ring for the **next** prayer, dim `#7d786c` for still-upcoming.
5. **Hands:** hour = 5px gold `#D4AF37`; minute = 2px cream `#f4efe2`; second =
   1px red `#c0392b`. All from a central hub. The hour hand completes one full
   turn per 24 hours (this is a 24-hour dial, not 12).
6. **Hub:** a 4.5px gold centre cap.

**Digital time (below the dial).** The current time `15:53` (or with seconds),
~27px, weight 500, cream `#f4efe2`, tabular figures.

**Date line.** One centred line: `Sat 13 June 2026 · 27 Dhū al-Ḥijjah 1447`,
11px, `#7d786c`.

**Countdown pill.** A small rounded chip: background `rgba(20,20,20,.85)`, 1px
gold border, ~16px radius. Text `Next: Asr in 2h 14m` — "Asr" in gold 500, rest
silver `#cfcabb`, 12px.

**Prayer cards (Band 3, flanking — 3 left, 3 right).** Each card is a rounded
rectangle (8px radius) with the name on the left and the time on the right, and
a small status dot to the left of the name:
- **Normal card:** background `#121212`, 0.5px `#1f1f1f` border. Name silver
  `#cfcabb`, time `#e7e0cf`.
- **Next-prayer card** (exactly one): background `#1a150a`, 1px gold border;
  name and time both gold `#D4AF37`; its status dot is gold with a soft glow.
- **Order:** left column = Fajr, Sunrise, Dhuhr; right column = Asr, Maghrib,
  Isha (the natural day order, mirrored around the clock).
- **Status dot states:** green `#5fb55f` = fired; gold (glow) = next; hollow
  grey ring = upcoming; red `#c0392b` = missed.

**Now-playing + Stop bar (Band 4) — conditional.** Hidden whenever nothing is
playing. The instant any audio sounds (adhan, dhikr, a surah), a **centred**
block appears directly under the countdown (pushing the date/legend down,
never shifting left/right): a line "Now playing — Asr adhan · Hall speaker"
over a red `#c0392b` **STOP** button. STOP halts the current audio only
(`/api/test/stop`); the 60-minute Emergency-Stop pause is an operator action
and stays on the Admin page. When the audio ends, the block disappears and the
layout closes back up.

**Legend (Band 5).** A single centred row decoding the status colours:
`● Fired` (green) · `● Next` (gold) · `○ Upcoming` (grey ring) · `● Missed`
(red), 11px `#7d786c`, separated by a 0.5px top divider `#181818`.

### 9.7 The two states

- **Idle (the everyday view):** no Stop button anywhere. Clock, prayer times,
  dates, countdown, status, legend. Calm.
- **Playing:** the centred Now-playing + STOP block appears (Band 4); the
  corresponding prayer's card and dial-dot switch to the "current/playing"
  treatment. Everything else is unchanged. Reverts automatically when audio
  stops.

### 9.8 Responsive behaviour

- **Monitor / TV (landscape, ≥900px):** the 3-column layout above — cards
  flank the clock. Type scales up via `clamp()` so it reads from across a room.
  On a TV with no input, it is purely a display; the family controls everything
  from their phone on the same device.
- **Phone / small tablet (portrait, <900px):** the page becomes a single
  scrolling column — flip pill, then the clock (sized to fit, never clipped),
  then digital time + dates + countdown, then the six prayer cards wrapping
  two-up, then the legend. The strict left/right mirror relaxes here (different
  context) but the clock stays first and central.
- All sizes use `clamp(min, vmin, max)` so one layout serves a 375px phone up
  to a 4K TV; the clock container is `min(62vmin, 620px)`.

### 9.9 Motion & live elements

- Second hand sweeps; minute/hour update continuously.
- The "next" prayer dot may carry a soft pulse (optional, gentle).
- The dial's day/night divide and the in-dial crescent update with real
  astronomical state (sunrise/maghrib angle; current moon phase).
- The Now-playing/STOP block animates in/out (appear on play, clear on stop).
- No flashing, no aggressive motion — the page is meant to sit on a wall or
  counter all day.

### 9.10 Resilience notes (built into the look)

- Weather + moon corners **fail silent** (empty, never broken) when offline.
- The vector dial scene has a **flat-shading fallback** (the current
  conic-gradient day/night arc) selectable by config, so a Pi that struggles
  with the SVG still shows a correct, legible clock.
- Everything is system-font, vector-only, no external asset — fully offline.

---

## 10. §10 — CRAFT & POLISH DECISIONS (adjudicated)

Three external LLMs proposed ~100 "premium feel" techniques (DeepSeek and
Gemini returned the **identical** 45-item list; ChatGPT a distinct 50). This
section is the **decision** on each — what we adopt, reject, or defer — and the
rules that govern them. It sits *on top of* the frozen §8/§9 composition: **no
technique here changes the layout, the colours, or the contents.** Polish is
execution quality, not new features.

### 10.1 Framing correction (important)

All three proposals call these "Pi-friendly." The Pi does **not** render the
Clock page — it serves it; the **client** renders it: a family phone, a tablet,
or a TV browser (often old/weak), or the Pi's own Chromium when it drives an
HDMI TV in kiosk mode. So the binding constraint is **a weak client painting
this all day**, which makes the GPU-heavy techniques *more* dangerous, not less.
Every "depth" effect below is judged on one question: does it cost a
repaint/compositing layer every frame, or is it a one-time static paint?

### 10.2 The governing rule — progressive enhancement

Adopted from ChatGPT #45 and made the spine of the build:

> **Base layer must be complete and correct on its own.** Flat conic dial,
> cards, times, status, countdown — fully legible with zero effects.
> **Enhancement layer** (vector scene, glows, pulse, moon phase, depth) layers
> on top and may fail or be switched off without breaking anything.

The existing **flat-shading config toggle** (§9.10) is the master switch for the
enhancement layer. If a client struggles, the operator flips it and still gets a
correct, beautiful clock.

### 10.3 ADOPT — already in the frozen §9 (no new work, listed for completeness)

Grid `1fr auto 1fr` symmetry · two-column card reflow · `clamp()` fluid type ·
`tabular-nums` on all times · SVG day/night scene with clipPath · live moon
phase in the dial · sunrise/maghrib-rotated horizon · prayer dots as SVG circles
· 40% knock-back overlay · single soft pulse on the *next* dot only ·
prayer-dot hierarchy (fired/next/upcoming/missed) · CSS custom-property tokens ·
`prefers-reduced-motion` · flat-shading fallback · strict central axis ·
mirror-weighted cards · gold-as-accent-not-wallpaper · muted/restrained numerals
every 2 hours · quiet weather rendering · tiny low-contrast legend · the
squint-test acceptance protocol.

### 10.4 ADOPT — new craft to add (all cheap, static, or one-shot)

| # | Technique | Why it's safe | Source |
|---|---|---|---|
| A1 | **Inner shadow on the dial** (`inset` box-shadow) + soft outer drop-shadow → dial looks recessed/physical | static paint, zero per-frame cost | DS5 / GPT4 |
| A2 | **Layered gold rim rings** via stacked `box-shadow` (gold / faint cream / dark inner) — the "brushed/raised" look | static; preferred over a conic-gradient border (cheaper, no mask tricks) | DS6 / GPT3 |
| A3 | **Glass-lens highlight** — one barely-visible radial-gradient over the dial, below the hands | a single static gradient, no filter | GPT2 |
| A4 | **Page vignette** — fixed `::before` radial gradient, `pointer-events:none`, very faint | static, one element | DS21 |
| A5 | **Next-card lift** — subtle gold→dark linear gradient + soft gold box-shadow on the one next card | static, one card | GPT14 |
| A6 | **Stop bar / now-playing animate-in** — `opacity` + `translateY(-4px→0)`, 180–220ms, no bounce | runs once, on play only | DS11/26, GPT32 |
| A7 | **Flip-tab micro-press** — `:active { scale(.97) }`, `:focus-visible` gold ring | trivial, on interaction only | DS23/24, GPT |
| A8 | **Second hand**: thin, dim red `rgba(192,57,43,.75)`, optional tiny counterweight; updates **once per second** via a cheap `transform` (see 10.6 — *not* a rAF loop) | one transform/sec | DS9/27, GPT25/26 |
| A9 | **Tick hierarchy** — 2-hour ticks slightly longer than odd-hour ticks | static SVG | GPT29 |
| A10 | **Spacing & radius systems** — spacing from `4/8/12/16/24/32`; radii only `10px` (chips) / `16px` (cards) / circular. No stray `13px`/`27px`. | discipline, free | GPT19/20 |
| A11 | **Icon consistency** — one line-icon family (the Tabler set already in use), uniform stroke/colour, no emoji | already our icon set | GPT21 |
| A12 | **Fail-soft data state** — if `/api/state` fails, show a small dim "Clock offline · last update HH:MM" **and keep the analog clock ticking from local time.** Never a broken dashboard. | resilience, cheap | GPT40 |
| A13 | **Version/device footer is Admin-only** (or ultra-dim bottom-centre) — operator info never looks like family content | removal, free | GPT49 |
| A14 | **Rules as acceptance gates**: gold only on active-tab/rim/hour-hand/next/dots/countdown; important text never in dim grey; no decoration within reach of prayer times or the Stop button; "soft shadows, not neon." | review checklist | GPT15/24/46/47 |

### 10.5 REJECT — with reasons (these do **not** go in)

| Technique | Verdict & reason | Source |
|---|---|---|
| **`backdrop-filter: blur()` on cards / stop bar** | **Reject as default.** Per-frame GPU blur is the single most expensive thing here and the worst offender on old-phone/TV browsers and Pi-Chromium; §9 cards are deliberately flat. A solid translucent fill gives ~95% of the look at ~0% of the cost. (Revisit *only* behind `@supports` with a solid fallback — not for v1.) | DS4 / GPT13 |
| **Noise/grain texture overlay** | **Reject.** A full-screen layer for a "feel" almost no one consciously sees; fights the squint-test calm and costs paint. Against masood's "too complicated." | DS22 |
| **Islamic-geometry watermark** | **Reject for v1** (revisit only as an opt-in *theme*, §10.7). Same clutter/cost objection; risks looking busy behind the hero. | GPT17 |
| **Countdown progress bar** | **Reject.** A second moving element competing with the clock for "time remaining," which the dial + countdown text already convey. ChatGPT itself hedged "unless very subtle" — so: off. | DS29 |
| **`requestAnimationFrame` smooth-sweep second hand** | **Reject the rAF loop.** A continuous frame-loop running all day on a wall phone/TV is a real battery/CPU/heat drain. Keep the second hand (A8) but tick it once per second. | GPT26 (rAF variant) |
| **Blanket `will-change: transform`** | **Reject as blanket; allow surgically.** `will-change` permanently promotes elements to compositor layers and costs memory if over-applied — bad on weak clients. Apply to the hands only, and only if measurement shows jank. | DS20 |
| **Heavy inline SVG filters (blur/glow) on large elements** | **Reject on large elements.** Prefer CSS box/drop-shadow. A tiny filter on the next-dot only, if needed. | GPT44 |

### 10.6 Second-hand decision (settle the one real disagreement)

The proposals conflict (DeepSeek "200ms transition sweep," ChatGPT "rAF or fall
back"). **Decision: tick once per second**, driven by the existing clock update,
applying `transform: rotate()` with **no** CSS transition — a 6°/sec jump reads
as a clean mechanical tick and avoids the "rubber-band" wrap glitch at 59→0s
that a transition causes. No animation loop. Lowest power, looks correct. Under
`prefers-reduced-motion: reduce`, the second hand may be dropped entirely.

### 10.7 DEFER — good ideas, but post-MVP enhancements (logged, not built now)

These are genuine features, not polish; each needs config + its own testing.
Captured so they aren't lost; **not** part of the first Clock-page build.

- **Auto night-dimming** (after Isha / a bedtime): reduce brightness + gold
  intensity, drop the pulse, dim the second hand → bedroom-safe. *High value.*
  (GPT37)
- **Burn-in protection**: imperceptible 1–2px slow drift of the whole cluster
  for TVs left on all day / OLED. (GPT36)
- **Fajr wake clarity**: at alarm time, oversize the Stop target and keep the
  current prayer unmistakable for a half-asleep tap. *Safety-aligned — promote
  early if Fajr-stop ergonomics come up.* (GPT38)
- **Theme architecture**: ship one theme now, but structure with `body.theme-*`
  classes + tokens so "luxury / ottoman / minimal" faces (and an opt-in geometry
  watermark) can be added later without a rewrite. Adopt the *structure* now
  (just tokens + a class hook, already implied by A10); defer the extra themes
  and any local SVG dial-plate assets. (GPT41/42/43)

### 10.8 Net effect on the build order (§8.6 unchanged, annotated)

The §8.6 surgical build order stands. Craft attaches like this:
1. View-state toggle → 2. Family layout (base, flat, fully legible — **this is
the progressive-enhancement base, ship-ready on its own**) → 3. Vector dial
(enhancement layer) → 4. Status/Stop (+ A6 animate-in) → 5. Corners (+ A12
fail-soft) → 6. Cross-device verify (run the §9 squint test + the §10.5
performance rejections as a checklist). Static depth (A1–A5, A9–A11) is applied
during steps 2–3; the rules (A14) are the review gate at step 6.

---

**End of brief. §8 decisions + §9 visual spec + §10 craft adjudication frozen —
a complete, self-contained description ready for implementation on Mustafa's go.**
