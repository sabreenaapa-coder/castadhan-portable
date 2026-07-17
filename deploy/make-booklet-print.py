#!/usr/bin/env python3
"""Build a PRINT-READY booklet HTML from the editable master, at a target size.

The master (box-documentation/02-booklet/castadhan-booklet.html) is drawn at
148 mm square. This scales the whole design uniformly to TARGET_TRIM (keeping
the accepted layout/proportions exactly, just smaller) and adds press marks:

  * finished (trim) size = TARGET_TRIM square      (default 120 mm = 12 cm)
  * 3 mm real bleed on all four sides  (backgrounds run off the trim)
  * crop / trim marks at the trim corners, in a 5 mm slug
  * single pages in reading order (the printer imposes the saddle-stitch)

Uniform scaling is done with `zoom` on <html>; everything the design specifies
in mm/pt is written in the 148 mm "design space" and scaled by K = TARGET/148.
The only real-mm value is the @page media size (the physical sheet), and the
crop marks, which are computed so they land at 3 mm bleed / 5 mm slug after the
zoom. The master is never modified; re-run after any copy change.

Export (fonts need the network + a virtual-time budget, or they fall back to Times):
  "Google Chrome" --headless --disable-gpu --no-pdf-header-footer \\
    --virtual-time-budget=20000 \\
    --print-to-pdf=castadhan-booklet-print.pdf castadhan-booklet-print.html
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BK = os.path.join(os.path.dirname(HERE), "box-documentation", "02-booklet")
SRC = os.path.join(BK, "castadhan-booklet.html")
OUT = os.path.join(BK, "castadhan-booklet-print.html")

TARGET_TRIM = 120.0    # finished square size in mm (10-12 cm range)
DESIGN_TRIM = 148.0    # the size the master is drawn at
K = TARGET_TRIM / DESIGN_TRIM
BLEED = 3.0            # real mm, per side
SLUG = 5.0            # real mm, room for crop marks outside the bleed
GAP = 3.0            # real gap between trim corner and mark
LEN = 3.5            # real crop-mark length
MEDIA = TARGET_TRIM + 2 * (BLEED + SLUG)     # real media size for @page


def d(v):
    """real mm -> design mm (pre-zoom); zoom:K scales it back to the real value."""
    return v / K


def _mm(v):
    return f"{v:.3f}mm"


def crop_marks():
    """Eight L-ticks (2 per corner), authored in design space so they land in
    the slug just outside the 3 mm bleed after zoom:K."""
    media_d, inset_d = d(MEDIA), d(BLEED + SLUG)
    gap_d, len_d, th_d = d(GAP), d(LEN), d(0.2)
    lo, hi = inset_d, media_d - inset_d
    ticks = []
    for cx in (lo, hi):
        for cy in (lo, hi):
            hx = cx - gap_d - len_d if cx == lo else cx + gap_d
            ticks.append(f'<i style="left:{_mm(hx)};top:{_mm(cy)};'
                         f'width:{_mm(len_d)};height:{_mm(th_d)}"></i>')
            vy = cy - gap_d - len_d if cy == lo else cy + gap_d
            ticks.append(f'<i style="left:{_mm(cx)};top:{_mm(vy)};'
                         f'width:{_mm(th_d)};height:{_mm(len_d)}"></i>')
    return '<div class="cropmarks">' + "".join(ticks) + "</div>"


def print_css():
    return f"""
  /* ---- injected by make-booklet-print.py : scale to {TARGET_TRIM:.0f} mm + bleed + marks ---- */
  html {{ zoom: {K:.6f}; }}                 /* uniform scale 148 -> {TARGET_TRIM:.0f} mm */
  @page {{ size: {_mm(MEDIA)} {_mm(MEDIA)}; margin: 0; }}  /* real physical sheet */
  * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  .page {{ margin: {_mm(d(SLUG))}; }}       /* centre bleed page in the media (design mm) */
  .cropmarks {{ position: fixed; inset: 0; z-index: 9999; pointer-events: none; }}
  .cropmarks i {{ position: absolute; background: #000; }}
  @media screen {{ .cropmarks {{ display: none; }} }}
"""


def build():
    with open(SRC, encoding="utf-8") as f:
        html = f.read()

    assert "--bleed: 0mm;" in html, "master's --bleed line changed; update builder"
    html = html.replace("--bleed: 0mm;", f"--bleed: {d(BLEED):.4f}mm;", 1)  # -> 3mm real
    html = html.replace("</style>", print_css() + "</style>", 1)
    html = html.replace("<body>", "<body>\n" + crop_marks(), 1)

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", os.path.relpath(OUT))
    print(f"trim {TARGET_TRIM:.0f}x{TARGET_TRIM:.0f} mm  |  media {MEDIA:.0f}x{MEDIA:.0f} mm"
          f"  |  bleed {BLEED:.0f} mm  |  crop marks in {SLUG:.0f} mm slug  |  scale {K:.3f}")


if __name__ == "__main__":
    build()
