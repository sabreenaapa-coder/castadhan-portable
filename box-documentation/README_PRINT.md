# CastAdhan box booklet — print guide

**Files**
- `castadhan-booklet.html` — the layout source (edit copy here).
- `castadhan-booklet.pdf` — export, ready to send to a printer.
- `castadhan_logo.png` — back-cover logo (referenced by the HTML).
- `site-qr.svg` — back-cover QR → the public clock site (ink modules on the ivory
  tile; regenerate with `segno` if the URL ever changes, and decode-test the print
  render before reprinting).

**Spec**
- Trim size: **148 × 148 mm square**, **16 pages** (a saddle-stitch booklet must be a
  multiple of 4 pages; 16pp folds from 4 flat sheets).
- Covers print near-black (`#101418`) with gold; interior is ivory. Ask for
  **uncoated or silk 150–170 gsm** inner stock and **250–300 gsm cover** for a premium feel.
- Copy source of truth: `../CASTADHAN_BROCHURE.md` (accepted 2026-07-01, verified
  against `CASTADHAN_FEATURES.md` at v1.15.1). If the copy changes, change it there
  first, then mirror it here.

**Re-export the PDF**

```bash
cd brochure
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --print-to-pdf=castadhan-booklet.pdf --no-pdf-header-footer \
  "file://$(pwd)/castadhan-booklet.html"
```

**Professional print with bleed**
Set `--bleed: 3mm` in the `:root` block of the HTML, re-export, and tell the printer
the PDF includes 3 mm bleed on all sides with a 148 mm trim. (Dark pages already run
their background to the page edge.)

**Home / proof printing**
Print the PDF 2-up on A4 to proof spreads, or single pages borderless if your
printer supports it.

## Setup guide (companion booklet)

- `castadhan-setup-guide.html` — the quick-start booklet source (same design
  system as the brochure; edit copy here).
- `castadhan-setup-guide.pdf` — export, ready to print.
- Trim size: **148 × 148 mm square**, **8 pages** (saddle-stitch: folds from
  2 flat sheets). Same stock recommendations as the brochure.
- Copy verified against `../setup.html` and the README at **v1.15.1**; voice is
  Register 1 (gift recipient).

Re-export the same way:

```bash
cd brochure
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --print-to-pdf=castadhan-setup-guide.pdf --no-pdf-header-footer \
  "file://$(pwd)/castadhan-setup-guide.html"
```
