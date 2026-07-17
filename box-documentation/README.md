# CastAdhan — In-Box Documentation

Everything that goes inside the CastAdhan product box, gathered in one place and
ready for the printer. Two printed pieces plus one companion guide.

Assembled 2026-07-17. Copy verified against software **v1.15.1**.

---

## 📦 What's in the box

| # | Piece | Print-ready file | Format |
|---|-------|------------------|--------|
| 1 | **Infogram** — Getting Started Guide | `01-infogram/CastAdhan-Infogram-FINAL.png` | Single sheet, A4 landscape |
| 2 | **Booklet** — Box Brochure ("A Home That Remembers Allah") | `02-booklet/castadhan-booklet.pdf` | 148 × 148 mm square, 16 pp, saddle-stitch |
| 3 | **Setup Guide** — companion quick-start | `03-setup-guide/castadhan-setup-guide.pdf` | 148 × 148 mm square, 8 pp, saddle-stitch |

---

## 01 — Infogram / Getting Started Guide

Two formats of the same content — what's in the parcel, what you need at home,
and the 6 setup steps with a scannable QR to `http://castadhan.local:8786`:

### 1a. Accordion (concertina) — **the box format** ⭐

A 4-panel linear accordion (8 printed sides) in the same near-black/gold + ivory
design system as the booklet, **ganged up N-per-sheet** so one print run yields
several copies to guillotine apart. **Current setting: A3, 5-up @ 70 mm fold**
(5 copies per sheet, strips run vertically).

- `castadhan-accordion-guide.pdf` — **print-ready, 2 pages: page 1 = front, page 2 = back.**
- `castadhan-accordion-guide.html` — layout source (both sheets).
- `_accordion-front.html` / `_accordion-back.html` — single-page proofs (for screenshots).
- `assets/` — the photoreal product shots (box, power, cable, socket, router,
  speaker) + welcome-screen UI, cropped and edge-feathered from the infogram so
  they blend into the cream panels. The accordion uses these, not line icons.
- `make-accordion-guide.py` — regenerates all of the above. Run:
  `pip3 install segno && python3 deploy/make-accordion-guide.py`.

> **Image resolution caveat:** these photos are cropped from the 1536×1024
> infogram (~130 dpi at A4). At the accordion's small 96 mm panels they look
> crisp, but they can't be enlarged much. For a large or premium run, replace
> the files in `assets/` with higher-res product images (same names) and re-run.

**Copies per sheet — set `SHEET`, `PANEL`, `STRIP`, `GRID_ROWS`, `GRID_COLS`
at the top of the script.** `STRIP="V"` runs strips vertically (more copies
across a wide A3); `STRIP="H"` runs them horizontally (stacked).

| Sheet | Recipe (`STRIP`/`PANEL`/rows×cols) | Copies | Fold size | Notes |
|---|---|---|---|---|
| A3 | V / 70 / 1×5 | **5** | 70 mm | ← current |
| A3 | V / 67 / 1×6 | 6 | 67 mm | Most copies |
| A3 | H / 90 / 3×1 | 3 | 90 mm | Bigger squares |
| A3 | H / 96 / 2×1 | 2 | 96 mm | Largest / premium |
| A4 | H / 70 / 2×1 | 2 | 70 mm | If A3 unavailable |

The whole design is drawn at 96 mm then uniformly scaled, so type stays
proportional at any fold size. Duplex imposition auto-adjusts: for `H` strips
the back is authored 5-6-7-8, for `V` strips 8-7-6-5, so a short-edge flip
always lands each back panel behind its front panel.

**Print & finish**
- **Landscape, 100 % scale** (never "fit to page"), **DUPLEX, flip on the SHORT
  edge** (left↔right, like turning a page — *not* top-to-bottom).
- **Guillotine** on the **solid grey cut lines** — they run to the sheet edge so
  the blade can line up. Each cut releases one folded-size square strip.
- **Concertina-fold** on the **faint dashed fold lines** printed on each copy;
  the `V` / `M` letters under the block show valley / mountain direction.
  Cover ends up on top, back cover underneath.

**Why the back aligns with the front:** the panels are equal width, so a
short-edge flip lands each back panel exactly behind its front panel. Imposition
is `FRONT = sides 1 2 3 4`, `BACK = sides 5 6 7 8`, both upright and in order.
The two **red corner registration targets** print in the same spot on both
sheets — hold the sheet to the light and they should coincide. If the back
comes out upside-down, the printer flipped on the long edge — switch it to
short edge.

**Sides:** 1 Cover · 2 What's in the parcel · 3 You'll need at home ·
4 Steps 1–2 · 5 Steps 3–4 · 6 Steps 5–6 · 7 Need help · 8 Back cover.

### 1b. Flat sheet (original single-page version)

- `CastAdhan-Infogram-FINAL.png` — polished single A4 landscape artwork.
- `castadhan-guide-birmingham.html` — editable source, full-kit recipient (box + power + cable).
- `castadhan-guide-balkis.html` — editable source, SD-card-only recipient.
- `castadhan-guide-birmingham-FINAL.png` — rendered full-kit version.
- `make-visual-guide.py` — regenerates the flat HTML sources.

## 02 — Booklet (Box Brochure)

The story + features booklet. Dark covers (`#101418`) with gold; ivory interior.

- `castadhan-booklet.pdf` — **print-ready export.**
- `castadhan-booklet.html` — layout source (edit copy here, then re-export).
- `CASTADHAN_BROCHURE-copy-source.md` — the accepted copy (source of truth).
- `castadhan_logo.png` — back-cover logo. `site-qr.svg` — back-cover QR to the
  public clock site. `_backcover.png` — back-cover artwork.

## 03 — Setup Guide (companion)

Same design system as the booklet; Register-1 (gift-recipient) voice.

- `castadhan-setup-guide.pdf` — **print-ready export.**
- `castadhan-setup-guide.html` — layout source.

---

## 🖨️ Printing

Full print spec (trim size, stock, bleed, re-export commands) is in
**`README_PRINT.md`** in this folder.

Quick recommendations:
- **Booklet & setup guide:** 148 × 148 mm trim; ask for uncoated/silk 150–170 gsm
  inner, 250–300 gsm cover. For a pro run, set `--bleed: 3mm` in the HTML `:root`,
  re-export, and tell the printer the PDF includes 3 mm bleed all sides.
- **Infogram:** print A4 landscape at 100 % scale.
- **Always decode-test any QR** on the final print render before a full run.

## ✏️ If the copy changes

Edit the copy source first, then mirror it into the layout HTML and re-export the PDF:
- Booklet copy → `02-booklet/CASTADHAN_BROCHURE-copy-source.md`
- Then edit `02-booklet/castadhan-booklet.html` and re-export (see `README_PRINT.md`).

> Note: the master copy source also lives at `../CASTADHAN_BROCHURE.md` in the
> project root. Keep the two in sync, or treat the root file as canonical.
