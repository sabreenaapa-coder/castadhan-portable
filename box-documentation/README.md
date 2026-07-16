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

## 01 — Infogram (Getting Started Guide)

A single illustrated sheet: what's in the parcel, what you need at home, and the
6 setup steps with a scannable QR to `http://castadhan.local:8786`.

- `CastAdhan-Infogram-FINAL.png` — **the final artwork to print.**
- `castadhan-guide-birmingham.html` — editable source, full-kit recipient (box + power + cable).
- `castadhan-guide-balkis.html` — editable source, SD-card-only recipient.
- `castadhan-guide-birmingham-FINAL.png` — rendered full-kit version.
- `make-visual-guide.py` — regenerates the HTML sources (real QR via `segno`,
  editable line icons). Run: `pip3 install segno && python3 make-visual-guide.py`.

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
