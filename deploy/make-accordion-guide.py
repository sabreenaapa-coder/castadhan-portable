#!/usr/bin/env python3
"""Print-ready ACCORDION (concertina) 'Getting Started Guide'.

A 4-panel linear accordion that folds down to a 96 mm square. 8 printed
sides (4 front, 4 back). Prints 1-up on A3 landscape, then guillotine the
strip out on the crop marks and concertina-fold on the fold ticks.

Design system matches the box booklet: near-black (#101418) gold covers,
ivory interior, the same line-style icons and real scannable QR (segno).

  Front sheet, left -> right : side 1  2  3  4
  Back  sheet, left -> right : side 5  6  7  8

Because the panels are equal width and the reader flips the object on the
SHORT edge (left <-> right, like turning a page), each back panel lands
directly behind its front panel and every side reads upright and in order
(1 2 3 4  flip  5 6 7 8). Corner registration targets let you hold the two
sides to the light and confirm the back aligns with the front.

PRINT: A3 landscape, 100% scale (never "fit to page"), DUPLEX, flip on the
SHORT edge (horizontal). 3 mm bleed included; trim/fold marks sit in the
waste and are guillotined away.

Output (Desktop + box-documentation/01-infogram/):
  castadhan-accordion-guide.html   two A3 pages -> one duplex PDF
  _accordion-front.html / _accordion-back.html   single-page proofs
Requires: pip install segno
"""
import base64
import io
import os
import sys

try:
    import segno
except ImportError:
    sys.exit("Missing dependency. Run:  pip3 install segno")

# ---------------------------------------------------------------- paths / assets
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DESK = os.path.expanduser("~/Desktop")
BOXDIR = os.path.join(ROOT, "box-documentation", "01-infogram")
URL = "http://castadhan.local:8786"


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


LOGO_B64 = _b64(os.path.join(ROOT, "brochure", "castadhan_logo.png"))
with open(os.path.join(ROOT, "brochure", "site-qr.svg"), encoding="utf-8") as _f:
    SITE_QR_SVG = _f.read()

_buf = io.BytesIO()
segno.make(URL, error="m").save(_buf, kind="png", scale=10, border=2)
QR_B64 = base64.b64encode(_buf.getvalue()).decode()

# product photos cropped (+ feathered) from the infogram; small = fine at panel size
ASSETS = os.path.join(os.path.dirname(HERE), "box-documentation", "01-infogram", "assets")
PROD = {n: _b64(os.path.join(ASSETS, n + ".png")) for n in (
    "prod-box", "prod-power", "prod-ethernet", "prod-socket",
    "prod-router", "prod-speaker", "welcome")}


def photo(name, cls="pimg"):
    return f'<img class="{cls}" src="data:image/png;base64,{PROD[name]}" alt="">'

# ---------------------------------------------------------------- line icons
_P = {
    "sd":       '<path d="M7 3h7l4 4v13a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20V4.5A1.5 1.5 0 0 1 7 3z"/><path d="M9 3v3M12 3v3M15 4v2"/>',
    "ethernet": '<rect x="6" y="7" width="12" height="9" rx="1"/><path d="M9 7V5h6v2"/><path d="M9 10v3M12 10v3M15 10v3"/><path d="M12 16v4"/>',
    "box":      '<rect x="3" y="7" width="18" height="11" rx="2.5"/><circle cx="7" cy="12.5" r="1.1"/><circle cx="10.2" cy="12.5" r="1.1"/><circle cx="13.4" cy="12.5" r="1.1"/><circle cx="17.6" cy="10" r=".8"/>',
    "plug":     '<rect x="7" y="6" width="10" height="8" rx="1.5"/><path d="M12 6V3"/><path d="M9.6 14v2.4M14.4 14v2.4"/><path d="M12 14v3.5"/>',
    "socket":   '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M12 8v2.2"/><path d="M9 13.4l1.6.9M15 13.4l-1.6.9"/>',
    "router":   '<rect x="4" y="13" width="16" height="6" rx="1.5"/><path d="M8 13l-1.5-4M16 13l1.5-4"/><circle cx="8" cy="16" r=".9"/><path d="M11 16h6"/>',
    "speaker":  '<ellipse cx="12" cy="8.5" rx="7" ry="2.5"/><path d="M5 8.5v4.5c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V8.5"/><path d="M9 12h.01M12 12.3h.01M15 12h.01"/>',
    "stopwatch":'<circle cx="12" cy="13.5" r="7"/><path d="M12 13.5V9.5"/><path d="M10 3h4"/><path d="M12 3.5V6"/>',
    "wifi":     '<path d="M4.5 9.5a11 11 0 0 1 15 0"/><path d="M8 13a6 6 0 0 1 8 0"/><circle cx="12" cy="16.5" r="1.1"/>',
    "gear":     '<circle cx="12" cy="12" r="3.2"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6.2 6.2l1.6 1.6M16.2 16.2l1.6 1.6M17.8 6.2l-1.6 1.6M7.8 16.2l-1.6 1.6"/>',
    "check":    '<path d="M5 13l4.5 4.5L19 8"/>',
    "pin":      '<path d="M12 21c4-5 6-8 6-11a6 6 0 1 0-12 0c0 3 2 6 6 11z"/><circle cx="12" cy="10" r="2.2"/>',
    "hand":     '<path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 11V4.5a1.5 1.5 0 0 1 3 0V11M15 11V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1.5a5 5 0 0 1-3.6-1.6L4 16s1-1.4 2.5-.5L9 17"/>',
    "calc":     '<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M9 7h6"/><path d="M9 11h.01M12 11h.01M15 11h.01M9 14h.01M12 14h.01M15 14h.01M9 17h6"/>',
    "chat":     '<path d="M4 5.5h16v11H9l-4 3v-3H4z"/>',
}


def icon(name, size=24, cls="icn"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" width="{size}" height="{size}" '
            f'fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round">{_P[name]}</svg>')

# ---------------------------------------------------------------- geometry (mm)
# --- CONFIG: pick the sheet + how many copies to gang up (change these) --------
SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}    # landscape (w, h)
SHEET = "A3"          # "A4" or "A3"
PANEL = 70.0          # folded square size
STRIP = "V"           # strip orientation: "H" panels in a row, "V" in a column
GRID_ROWS, GRID_COLS = 1, 5   # how the copies are tiled. V strips => go in COLS
GUTTER = 6.0          # spacer between copies (printer wants 6 mm, trim marks in it)
MARGIN = 7.0          # minimum outer sheet margin
# Good A3 recipes:  V/70/1x5 = 5 copies @70mm ;  V/67/1x6 = 6 @67mm ;
#                   H/90/3x1 = 3 @90mm      ;  H/96/2x1 = 2 @96mm (largest)

SHEET_W, SHEET_H = SHEETS[SHEET]
N = 4
DESIGN = 96.0                     # one panel is drawn at 96 mm, then scaled...
S = PANEL / DESIGN                # ...by this factor, so type stays proportional
# one copy's footprint: a strip is 4 panels long in its orientation
FW, FH = (PANEL * N, PANEL) if STRIP == "H" else (PANEL, PANEL * N)
PITCH_X = FW + GUTTER             # copy-to-copy step, incl. the gutter
PITCH_Y = FH + GUTTER
BLOCK_W = FW * GRID_COLS + GUTTER * (GRID_COLS - 1)
BLOCK_H = FH * GRID_ROWS + GUTTER * (GRID_ROWS - 1)
MX = (SHEET_W - BLOCK_W) / 2      # block (all copies) origin, centred on sheet
MY = (SHEET_H - BLOCK_H) / 2
COPIES = GRID_ROWS * GRID_COLS
assert MX >= MARGIN - 0.05 and MY >= MARGIN - 0.05, (
    f"{GRID_ROWS}x{GRID_COLS} {STRIP}-strips @ {PANEL}mm + {GUTTER}mm gutters "
    f"won't fit {SHEET}: block {BLOCK_W:.0f}x{BLOCK_H:.0f}mm, "
    f"margins {MX:.1f}/{MY:.1f} < {MARGIN}")

DARK = "#101418"
GOLD = "#c9a54a"
IVORY = "#fdf7ea"   # matches the infogram card cream so photo crops blend seamlessly

# Site QR: the raw segno svg has ink paths but no background/quiet-zone, so give
# it a viewBox + white tile (2-module quiet zone) so it fills the back-cover box.
SITE_QR_FILL = (SITE_QR_SVG
                .replace('width="33" height="33"',
                         'viewBox="-2 -2 37 37" width="100%" height="100%" '
                         'preserveAspectRatio="xMidYMid meet"')
                .replace('class="segno">',
                         'class="segno"><rect x="-2" y="-2" width="37" '
                         'height="37" fill="#ffffff"/>', 1))

# ---------------------------------------------------------------- side content


def _items(arr):
    cells = "".join(
        f'<div class="it">{photo(img)}<span class="lbl">{lab}</span></div>'
        for img, lab in arr)
    return f'<div class="grid">{cells}</div>'


def _ministep(n, ic, title, body, extra=""):
    return (f'<div class="ms"><span class="badge">{n}</span>'
            f'<span class="msico">{icon(ic,28)}</span>'
            f'<div class="mst"><b>{title}</b><span>{body}</span>{extra}</div></div>')


def _ministep_img(n, imgkey, title, body, extra=""):
    return (f'<div class="ms"><span class="badge">{n}</span>'
            f'{photo(imgkey, "msthumb")}'
            f'<div class="mst"><b>{title}</b><span>{body}</span>{extra}</div></div>')


def side_cover():
    return ('<div class="face cover">'
            f'<img class="logo" src="data:image/png;base64,{LOGO_B64}" alt="">'
            '<div class="wm">CastAdhan</div>'
            '<div class="greet">Assalamu alaikum &#127769;</div>'
            '<div class="rule"></div>'
            '<div class="gsg">Getting&nbsp;Started&nbsp;Guide</div></div>')


def side_parcel():
    return ('<div class="face light"><h2>What&rsquo;s in this parcel</h2>'
            + _items([("prod-box", "CastAdhan"), ("prod-power", "Power&nbsp;supply"),
                      ("prod-ethernet", "Ethernet&nbsp;cable")]) + '</div>')


def side_home():
    return ('<div class="face light"><h2>You&rsquo;ll need at home</h2>'
            + _items([("prod-socket", "A free wall&nbsp;socket"),
                      ("prod-router", "Router + a spare&nbsp;port"),
                      ("prod-speaker", "Google Nest / Cast")]) + '</div>')


def side_steps12():
    s1 = _ministep_img(1, "prod-box", "SD card is already inside",
                       "Nothing to do &mdash; it&rsquo;s ready to plug in.")
    s2 = _ministep_img(2, "prod-ethernet", "Plug in Ethernet",
                       "From your CastAdhan into your router &mdash; any spare port.")
    return f'<div class="face light steps">{s1}{s2}</div>'


def side_steps34():
    s3 = _ministep_img(3, "prod-power", "Plug in power",
                       "Into a wall socket.",
                       f'<span class="wait">{icon("stopwatch",15)} Wait 1 minute</span>')
    s4 = (f'<div class="ms"><span class="badge">4</span>'
          f'<img class="qr" src="data:image/png;base64,{QR_B64}" alt="QR">'
          '<div class="mst"><b>Scan to begin</b>'
          '<span>Point your phone camera, or open '
          '<b class="mono">castadhan.local:8786</b></span></div></div>')
    return f'<div class="face light steps">{s3}{s4}</div>'


def side_steps56():
    s5 = (f'<div class="ms"><span class="badge">5</span>'
          f'{photo("welcome", "msthumb wide")}'
          '<div class="mst"><b>The welcome screen</b>'
          '<span>Set location, pick your speakers, play a test adhan '
          '&mdash; then you&rsquo;re done.</span></div></div>')
    s6 = (f'<div class="ms"><span class="badge">6</span>'
          f'<span class="msico">{icon("wifi",28)}</span>'
          '<div class="mst"><b>Go wireless <i>(optional)</i></b>'
          '<span>Settings &rarr; WiFi Setup &rarr; pick your network, then '
          'unplug the cable. Happy on Ethernet? Leave it.</span></div></div>')
    return f'<div class="face light steps">{s5}{s6}</div>'


def side_help():
    return ('<div class="face light help"><h2>Need help?</h2>'
            f'<div class="hrow">{icon("chat",22)} WhatsApp '
            '<b>07595&nbsp;998350</b></div>'
            '<div class="faq"><b>Can&rsquo;t find the address?</b> Type '
            '<b class="mono">http://castadhan.local:8786</b> in full, or '
            'message me for its number.</div>'
            '<div class="faq"><b>Updates?</b> Automatic, overnight &mdash; '
            'nothing for you to do.</div></div>')


def side_back():
    return ('<div class="face cover back">'
            '<div class="tagline">Hayya &rsquo;ala Salaah</div>'
            '<div class="sub">A home that remembers Allah.</div>'
            f'<div class="siteqr">{SITE_QR_FILL}</div>'
            '<div class="scan">See the live prayer-time clock</div>'
            '<div class="wm sm">CastAdhan</div></div>')


# Front strip is always 1-2-3-4 (in strip order). The back strip must place each
# panel's reverse behind it after a SHORT-edge (left<->right) duplex flip:
#   H strips: the flip reverses left->right, so author back as 5-6-7-8.
#   V strips: the flip leaves top->bottom untouched, so author back as 8-7-6-5.
FRONT_SIDES = [side_cover, side_parcel, side_home, side_steps12]     # 1 2 3 4
if STRIP == "H":
    BACK_SIDES = [side_steps34, side_steps56, side_help, side_back]  # 5 6 7 8
else:
    BACK_SIDES = [side_back, side_help, side_steps56, side_steps34]  # 8 7 6 5

# ---------------------------------------------------------------- marks


def _mm(v):
    return f"{v:.3f}mm"


def _crop_marks(ox, oy, w, h):
    """Four corner trim marks around one copy, sitting in the gutter / margin
    (never touching the artwork) so each strip is cut out cleanly on all sides."""
    g, ln, th = 0.8, 2.0, 0.25          # gap from trim, tick length, thickness
    out = []
    for cx, outward_x in ((ox, -1), (ox + w, +1)):
        for cy, outward_y in ((oy, -1), (oy + h, +1)):
            hx = cx - g - ln if outward_x < 0 else cx + g
            out.append(f'<div class="tick" style="left:{_mm(hx)};'
                       f'top:{_mm(cy - th/2)};width:{_mm(ln)};height:{_mm(th)}"></div>')
            vy = cy - g - ln if outward_y < 0 else cy + g
            out.append(f'<div class="tick" style="left:{_mm(cx - th/2)};'
                       f'top:{_mm(vy)};width:{_mm(th)};height:{_mm(ln)}"></div>')
    return "".join(out)


def guides():
    """Corner TRIM marks around every copy (in the 6 mm gutters) + faint dashed
    fold creases inside each copy + a V/M fold legend + registration targets."""
    out = []
    for i in range(GRID_ROWS):
        for j in range(GRID_COLS):
            ox, oy = MX + j * PITCH_X, MY + i * PITCH_Y
            out.append(_crop_marks(ox, oy, FW, FH))
            for k in (1, 2, 3):                     # fold creases inside the copy
                if STRIP == "H":                    # vertical creases
                    out.append(f'<div class="foldline v" style="'
                               f'left:{_mm(ox + k * PANEL)};top:{_mm(oy)};'
                               f'height:{_mm(FH)}"></div>')
                else:                               # horizontal creases
                    out.append(f'<div class="foldline h" style="'
                               f'left:{_mm(ox)};top:{_mm(oy + k * PANEL)};'
                               f'width:{_mm(FW)}"></div>')
    # --- fold legend (valley / mountain) beside the first copy ----------------
    for k in (1, 2, 3):
        lab = "V" if k % 2 else "M"
        if STRIP == "H":
            out.append(f'<div class="foldlbl" style="left:{_mm(MX + k*PANEL - 3)};'
                       f'top:{_mm(min(MY + FH + 1.6, SHEET_H - 3.5))};">{lab}</div>')
        else:
            out.append(f'<div class="foldlbl" style="left:{_mm(max(MX - 5.5, 1))};'
                       f'top:{_mm(MY + k*PANEL - 2)};">{lab}</div>')
    # --- registration targets (identical spot on front & back) ---------------
    for (rx, ry) in [(MARGIN - 2, MARGIN - 2),
                     (SHEET_W - MARGIN + 2, SHEET_H - MARGIN + 2)]:
        out.append(f'<div class="reg" style="left:{_mm(rx-3)};top:{_mm(ry)};'
                   f'width:{_mm(6)};height:0"></div>')
        out.append(f'<div class="reg" style="left:{_mm(rx)};top:{_mm(ry-3)};'
                   f'width:0;height:{_mm(6)}"></div>')
        out.append(f'<div class="regc" style="left:{_mm(rx-1.5)};'
                   f'top:{_mm(ry-1.5)};"></div>')
    return "".join(out)

# ---------------------------------------------------------------- assembly

STYLE = f"""
@page {{ size: {SHEET} landscape; margin: 0; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
html, body {{ margin: 0; padding: 0; }}
body {{ font-family: -apple-system, system-ui, "Segoe UI", sans-serif; color:#34302a; background:#c9c3b4; }}
.sheet {{ position: relative; width:{_mm(SHEET_W)}; height:{_mm(SHEET_H)};
         background:#ffffff; overflow:hidden; page-break-after: always; }}
.sheet:last-child {{ page-break-after: auto; }}
.strip {{ position:absolute; display:flex; }}
.strip.h {{ flex-direction:row; }}
.strip.v {{ flex-direction:column; }}
.panel {{ width:{_mm(PANEL)}; height:{_mm(PANEL)}; overflow:hidden; }}
/* each face is DRAWN at {DESIGN:.0f} mm then uniformly scaled to the panel size,
   so all type/artwork stays proportional at any folded size */
.face {{ width:{_mm(DESIGN)}; height:{_mm(DESIGN)}; padding:6.5mm;
        display:flex; flex-direction:column;
        transform: scale({S:.5f}); transform-origin: top left; }}
.light {{ background:{IVORY}; }}
.cover {{ background:{DARK}; color:#efe7d2; align-items:center; justify-content:center; text-align:center; }}

h2 {{ font-size:11pt; text-transform:uppercase; letter-spacing:.6px; color:#8a7a3a; margin:0 0 5mm; font-weight:700; }}
.icn {{ color:#8a6d2e; }}

/* cover */
.cover .logo {{ width:34mm; height:34mm; border-radius:50%; object-fit:cover; box-shadow:0 0 0 1.5pt {GOLD}; }}
.cover .wm {{ font-size:26pt; font-weight:700; letter-spacing:.5px; margin-top:5mm; color:#f3ecd6; }}
.cover .wm .a {{ color:{GOLD}; }}
.cover .greet {{ color:{GOLD}; font-size:12pt; margin-top:1.5mm; }}
.cover .rule {{ width:26mm; height:1pt; background:{GOLD}; opacity:.6; margin:5mm 0; }}
.cover .gsg {{ font-size:10.5pt; text-transform:uppercase; letter-spacing:3px; color:#cdbf95; }}

/* what's in / need at home */
.grid {{ display:flex; flex-direction:column; gap:3mm; margin-top:1mm; }}
.it {{ display:flex; align-items:center; gap:3mm; }}
.pimg {{ width:30mm; height:19mm; object-fit:contain; flex:0 0 auto; }}
.it .lbl {{ font-size:12.5pt; font-weight:600; color:#4a4438; line-height:1.15; }}

/* steps (two mini-steps per side) */
.steps {{ justify-content:space-evenly; }}
.ms {{ position:relative; display:flex; gap:4mm; align-items:flex-start; padding-left:2mm; }}
.badge {{ position:absolute; left:-2mm; top:-2mm; width:6mm; height:6mm; border-radius:50%;
         background:#bd9a3e; color:#fff; font-size:10.5pt; font-weight:700;
         display:flex; align-items:center; justify-content:center; box-shadow:0 1px 2px rgba(0,0,0,.25); }}
.msico {{ color:#8a6d2e; flex:0 0 auto; margin-top:1mm; }}
.msthumb {{ width:24mm; height:17mm; object-fit:contain; flex:0 0 auto; margin-top:.5mm; }}
.msthumb.wide {{ width:30mm; height:20mm; }}
.ms .qr {{ width:20mm; height:20mm; image-rendering:pixelated; flex:0 0 auto; }}
.mst b {{ font-size:12pt; display:block; margin-bottom:1mm; color:#33302a; }}
.mst span {{ font-size:10.5pt; line-height:1.4; color:#5f584b; }}
.wait {{ display:flex; align-items:center; gap:1.5mm; margin-top:2mm; color:#8a6d2e; font-weight:600; font-size:10pt; }}
.mono {{ font-family:"SF Mono",ui-monospace,Menlo,monospace; color:#1a1a1a; }}

/* help */
.help {{ justify-content:flex-start; }}
.hrow {{ display:flex; align-items:center; gap:3mm; font-size:13pt; color:#33302a; margin-bottom:5mm; }}
.hrow .icn {{ color:#8a6d2e; }}
.faq {{ font-size:10.5pt; line-height:1.45; color:#5a5448; margin-bottom:3.5mm; }}
.faq b {{ color:#34302a; }}

/* back cover */
.back .tagline {{ font-size:19pt; color:{GOLD}; letter-spacing:.3px; }}
.back .sub {{ font-size:11pt; color:#cdbf95; margin-top:2mm; }}
.back .siteqr {{ width:34mm; height:34mm; margin:5mm 0 3mm; background:#fff;
               border-radius:1.5mm; overflow:hidden; }}
.back .siteqr svg {{ width:100%; height:100%; display:block; }}
.back .scan {{ font-size:9.5pt; color:#cdbf95; }}
.back .wm.sm {{ font-size:13pt; margin-top:5mm; color:#efe7d2; }}

/* trim + fold guides (sheet coordinates, NOT scaled) */
.tick {{ position:absolute; background:#111; }}
.foldline {{ position:absolute; }}
.foldline.v {{ width:0; border-left:.2mm dashed rgba(60,50,40,.28); }}
.foldline.h {{ height:0; border-top:.2mm dashed rgba(60,50,40,.28); }}
.foldlbl {{ position:absolute; font-size:6pt; color:#b3b0a6; width:6mm; text-align:center; }}
.reg {{ position:absolute; background:#e0002b; }}
.regc {{ position:absolute; width:3mm; height:3mm; border:.4pt solid #e0002b; border-radius:50%; }}
.slug {{ position:absolute; left:{_mm(MX)}; top:{_mm(max(2.0, MARGIN - 5))};
        width:{_mm(BLOCK_W)}; font-size:7.5pt; color:#7a7364; letter-spacing:.3px; }}
.slug b {{ color:#c0392b; }}
"""


def wordmark_fix(html):
    return html.replace("CastAdhan</div>",
                        'Cast<span class="a">Adhan</span></div>')


def tiled_sheet(label, sides, note):
    """Gang up COPIES identical accordions on one sheet, with cut/fold guides."""
    cls = "strip " + STRIP.lower()
    strips = []
    for i in range(GRID_ROWS):
        for j in range(GRID_COLS):
            x, y = MX + j * PITCH_X, MY + i * PITCH_Y
            panels = "".join(f'<div class="panel">{fn()}</div>' for fn in sides)
            strips.append(
                f'<div class="{cls}" style="left:{_mm(x)};top:{_mm(y)};'
                f'width:{_mm(FW)};height:{_mm(FH)}">{panels}</div>')
    slug = (f'<div class="slug">CastAdhan Accordion &middot; <b>{label}</b> '
            f'&middot; {SHEET} &middot; {COPIES}-up &middot; {PANEL:.0f} mm fold '
            f'&middot; {GUTTER:.0f} mm gutters, trim marks &middot; print 100% '
            f'&middot; duplex, flip on SHORT edge &middot; {note}</div>')
    return f'<div class="sheet">{"".join(strips)}{guides()}{slug}</div>'


def page(title, body):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{title}</title><style>{STYLE}</style></head>'
            f'<body>{body}</body></html>')


front = wordmark_fix(tiled_sheet("FRONT (sides 1-4)", FRONT_SIDES, "sides 1 2 3 4"))
back = wordmark_fix(tiled_sheet("BACK (sides 5-8)", BACK_SIDES, "sides 5 6 7 8"))

combined = page("CastAdhan Accordion Guide", front + back)
proof_front = page("Accordion FRONT proof", front)
proof_back = page("Accordion BACK proof", back)

os.makedirs(BOXDIR, exist_ok=True)
outs = {
    "castadhan-accordion-guide.html": combined,
    "_accordion-front.html": proof_front,
    "_accordion-back.html": proof_back,
}
for name, html in outs.items():
    for d in (DESK, BOXDIR):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(html)

print(f"OK - wrote accordion guide ({SHEET}, {COPIES}-up @ {PANEL:.0f} mm fold):")
for name in outs:
    print("   ", name)
print(f"\n{COPIES} copies per {SHEET} sheet. 4-panel accordion, folds to "
      f"{PANEL:.0f} mm square.")
print(f"PRINT: {SHEET} landscape, 100% scale, DUPLEX flip on SHORT edge.")
print(f"{GUTTER:.0f} mm gutters between strips; guillotine to the corner trim marks;")
print("concertina-fold on the dashed lines.")
