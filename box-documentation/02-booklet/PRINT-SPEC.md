# CastAdhan Booklet — Printer Spec Sheet

Hand this file (and `castadhan-booklet-print.pdf`) to the print shop.

## The file
- **`castadhan-booklet-print.pdf`** — the press file. **Print this one**, not
  `castadhan-booklet.pdf` (that is a larger, no-bleed on-screen proof).

## Specification
| | |
|---|---|
| **Finished (trim) size** | **120 × 120 mm square** (12 cm) |
| **Document / media size** | 136 × 136 mm (includes bleed + crop marks) |
| **Bleed** | **3 mm** all four sides |
| **Crop marks** | Yes — printed at the trim corners |
| **Pages** | **16**, single pages **in reading order** (please impose) |
| **Imposition** | Saddle-stitch, folds from 4 sheets (16 = multiple of 4) |
| **Binding** | **Saddle-stitch** (2 wire staples on the spine) |
| **Fonts** | Embedded (Cormorant Garamond, EB Garamond) |
| **Colour space** | RGB (see note) |

## Stock (recommended)
- **Inner pages:** uncoated or silk, **150–170 gsm**.
- **Cover:** **250–300 gsm** for a premium feel.
- Covers are near-black (`#101418`) with gold; interior is ivory (`#faf6ec`).

## Notes for the printer / preflight
- **Colour:** the PDF is RGB (exported from HTML). For a **digital press** this
  is usually fine. For **offset / CMYK**, please convert; the cover uses a rich
  near-black `#101418` — build it as a rich black, not 100%K alone. `[VERIFY]`
  with the shop whether they want CMYK-converted artwork.
- **Fonts are embedded but as Type 3** (a by-product of the HTML→PDF export).
  They print correctly, but if your preflight requires Type 1 / TrueType /
  CFF outlines, tell us — we can re-issue with the fonts outlined or fully
  embedded. `[VERIFY]`
- No TrimBox/BleedBox metadata is set in the PDF; use the **printed crop marks**
  for the 148 mm trim (they sit in a 5 mm slug that is cut away).

## Regenerating the press file
`TARGET_TRIM` at the top of `make-booklet-print.py` sets the finished size
(currently 120 mm); the whole design scales uniformly, so change it and re-run.
```bash
python3 deploy/make-booklet-print.py           # scale + bleed + crop marks from master
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --disable-gpu --no-pdf-header-footer --virtual-time-budget=20000 \
  --print-to-pdf=box-documentation/02-booklet/castadhan-booklet-print.pdf \
  "file://$PWD/box-documentation/02-booklet/castadhan-booklet-print.html"
```
`--virtual-time-budget` is **required** — without it Chrome prints before the
web-fonts finish loading and the booklet falls back to Times. Copy source of
truth remains `castadhan-booklet.html` / `../../CASTADHAN_BROCHURE.md`; the
print HTML is generated, never hand-edited.
