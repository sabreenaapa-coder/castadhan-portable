#!/usr/bin/env python3
"""Print-perfect illustrated 'Getting Started Guide' (A4 landscape poster).

A clean, correct, editable alternative to an AI-generated guide: line-style SVG
icons (UK plugs/sockets), a REAL scannable QR (segno), sequential numbering 1-6,
and two recipient-correct versions (Balkis = SD + cable; Birmingham = full kit).

Output: one HTML per recipient on the Desktop. Print A4 landscape, 100% scale.
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

URL = "http://castadhan.local:8786"
DESK = os.path.expanduser("~/Desktop")
_buf = io.BytesIO()
segno.make(URL, error="m").save(_buf, kind="png", scale=10, border=2)
QR_B64 = base64.b64encode(_buf.getvalue()).decode()

# --- line-style icons (24x24, stroke = currentColor) -------------------------
_P = {
    "sd":       '<path d="M7 3h7l4 4v13a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 20V4.5A1.5 1.5 0 0 1 7 3z"/><path d="M9 3v3M12 3v3M15 4v2"/>',
    "ethernet": '<rect x="6" y="7" width="12" height="9" rx="1"/><path d="M9 7V5h6v2"/><path d="M9 10v3M12 10v3M15 10v3"/><path d="M12 16v4"/>',
    "box":      '<rect x="3" y="7" width="18" height="11" rx="2.5"/><circle cx="7" cy="12.5" r="1.1"/><circle cx="10.2" cy="12.5" r="1.1"/><circle cx="13.4" cy="12.5" r="1.1"/><circle cx="17.6" cy="10" r=".8"/>',
    "plug":     '<rect x="7" y="6" width="10" height="8" rx="1.5"/><path d="M12 6V3"/><path d="M9.6 14v2.4M14.4 14v2.4"/><path d="M12 14v3.5"/>',
    "socket":   '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M12 8v2.2"/><path d="M9 13.4l1.6.9M15 13.4l-1.6.9"/>',
    "router":   '<rect x="4" y="13" width="16" height="6" rx="1.5"/><path d="M8 13l-1.5-4M16 13l1.5-4"/><circle cx="8" cy="16" r=".9"/><path d="M11 16h6"/>',
    "speaker":  '<ellipse cx="12" cy="8.5" rx="7" ry="2.5"/><path d="M5 8.5v4.5c0 1.5 3.1 2.7 7 2.7s7-1.2 7-2.7V8.5"/><path d="M9 12h.01M12 12.3h.01M15 12h.01"/>',
    "stopwatch":'<circle cx="12" cy="13.5" r="7"/><path d="M12 13.5V9.5"/><path d="M10 3h4"/><path d="M12 3.5V6"/>',
    "phone":    '<rect x="8" y="3" width="8" height="18" rx="2"/><path d="M11 18.2h2"/>',
    "wifi":     '<path d="M4.5 9.5a11 11 0 0 1 15 0"/><path d="M8 13a6 6 0 0 1 8 0"/><circle cx="12" cy="16.5" r="1.1"/>',
    "gear":     '<circle cx="12" cy="12" r="3.2"/><path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2M6.2 6.2l1.6 1.6M16.2 16.2l1.6 1.6M17.8 6.2l-1.6 1.6M7.8 16.2l-1.6 1.6"/>',
    "check":    '<path d="M5 13l4.5 4.5L19 8"/>',
    "pin":      '<path d="M12 21c4-5 6-8 6-11a6 6 0 1 0-12 0c0 3 2 6 6 11z"/><circle cx="12" cy="10" r="2.2"/>',
    "hand":     '<path d="M9 11V5.5a1.5 1.5 0 0 1 3 0V11M12 11V4.5a1.5 1.5 0 0 1 3 0V11M15 11V6a1.5 1.5 0 0 1 3 0v8a6 6 0 0 1-6 6h-1.5a5 5 0 0 1-3.6-1.6L4 16s1-1.4 2.5-.5L9 17"/>',
    "calc":     '<rect x="6" y="3" width="12" height="18" rx="2"/><path d="M9 7h6"/><path d="M9 11h.01M12 11h.01M15 11h.01M9 14h.01M12 14h.01M15 14h.01M9 17h6"/>',
    "qr":       '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3M21 14v.01M21 21v-4M14 21h3"/>',
    "wave":     '<path d="M4 16c2-4 4-4 6 0M14 16c2-4 4-4 6 0"/><path d="M9 9h6"/>',
}


def icon(name, size=26):
    return (f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
            f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{_P[name]}</svg>')


STYLE = """
@page { size: A4 landscape; margin: 6mm; }
@media print { body { background:#fff; padding:0; } .guide { box-shadow:none; } }
* { box-sizing:border-box; }
body { font-family:-apple-system,system-ui,"Segoe UI",sans-serif; background:#ded9cd; margin:0; padding:10px; display:flex; justify-content:center; color:#34302a; }
.guide { width:285mm; height:198mm; background:#fdfaf2; box-shadow:0 6px 22px rgba(0,0,0,.15); padding:8mm; display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:auto repeat(3,1fr); gap:5mm; }
.icn { color:#8a6d2e; }
header { grid-column:1/-1; display:flex; align-items:center; gap:12px; border-bottom:2px solid #e3dcc8; padding-bottom:6px; }
header .clock { color:#8a6d2e; }
header h1 { font-size:23px; margin:0; }
header .greet { color:#8a7a3a; font-weight:600; font-size:13px; }
header .gsg { margin-left:auto; color:#8a7a3a; font-size:11px; text-transform:uppercase; letter-spacing:2px; font-weight:600; }
.panel { background:#fff; border:1px solid #ece4cf; border-radius:10px; padding:9px 11px; position:relative; }
.panel h2 { font-size:10.5px; text-transform:uppercase; letter-spacing:.8px; color:#8a7a3a; margin:0 0 6px; }
.badge { position:absolute; top:-9px; left:-9px; width:24px; height:24px; border-radius:50%; background:#bd9a3e; color:#fff; font-size:13px; font-weight:600; display:flex; align-items:center; justify-content:center; box-shadow:0 1px 3px rgba(0,0,0,.2); }
.items { display:flex; gap:10px; }
.item { display:flex; flex-direction:column; align-items:center; gap:3px; flex:1; text-align:center; }
.item span { font-size:9.5px; line-height:1.2; color:#5a5448; }
.step { display:flex; gap:10px; align-items:flex-start; }
.step .big { color:#8a6d2e; flex:0 0 auto; }
.step .body { font-size:11px; line-height:1.35; }
.step .t { font-weight:600; font-size:11.5px; display:block; margin-bottom:1px; }
.muted { color:#6a6253; }
.wait { display:flex; align-items:center; gap:6px; margin-top:5px; font-size:10.5px; color:#8a6d2e; font-weight:600; }
.qrwrap { display:flex; gap:10px; align-items:center; }
.qrwrap img { width:96px; height:96px; image-rendering:pixelated; }
.qrwrap .u { font-size:10.5px; line-height:1.4; }
.qrwrap .u b { color:#1a1a1a; }
ol.sub { margin:2px 0 0; padding-left:0; list-style:none; }
ol.sub li { display:flex; gap:7px; align-items:center; font-size:10px; margin-bottom:3px; line-height:1.25; }
ol.sub .n { flex:0 0 auto; width:15px; height:15px; border-radius:50%; background:#efe6cc; color:#8a6d2e; font-size:9px; font-weight:600; display:flex; align-items:center; justify-content:center; }
ol.sub .icn { color:#8a6d2e; display:flex; }
.wire { font-size:10px; line-height:1.4; }
.wire .row { display:flex; align-items:center; gap:6px; margin:2px 0; }
.wire .safe { color:#3b7d54; font-weight:600; margin-top:4px; }
.help b { color:#1a1a1a; }
.hayya { color:#8a7a3a; font-size:14px; font-weight:500; letter-spacing:.3px; margin-top:6px; }
.faq { font-size:9.5px; color:#5a5448; line-height:1.35; margin-top:5px; }
.faq b { color:#34302a; }
"""


def _items(arr):
    return '<div class="items">' + ''.join(
        f'<div class="item"><span class="icn">{icon(ic,30)}</span><span>{label}</span></div>'
        for ic, label in arr) + '</div>'


def build(recipient, parcel_items, step1_title, step1_body, out_name):
    welcome = (
        '<ol class="sub">'
        f'<li><span class="n">1</span><span class="icn">{icon("hand",15)}</span>A short hello.</li>'
        f'<li><span class="n">2</span><span class="icn">{icon("pin",15)}</span><b>Location</b> &mdash; tap &ldquo;Auto-detect.&rdquo;</li>'
        f'<li><span class="n">3</span><span class="icn">{icon("calc",15)}</span><b>Method</b> &mdash; pick or keep default.</li>'
        f'<li><span class="n">4</span><span class="icn">{icon("speaker",15)}</span><b>Speakers</b> &mdash; tick your Nest / Cast.</li>'
        f'<li><span class="n">5</span><span class="icn">{icon("check",15)}</span><b>Test adhan</b> &mdash; hear it, finish. Done.</li>'
        '</ol>'
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>CastAdhan getting-started guide &mdash; {recipient}</title>
<style>{STYLE}</style></head><body>
<div class="guide">
  <header>
    <span class="clock">{icon("stopwatch",30)}</span>
    <div><h1>Your CastAdhan</h1><span class="greet">Assalamu alaikum &#127769;</span></div>
    <span class="gsg">Getting&nbsp;started&nbsp;guide</span>
  </header>

  <div class="panel"><h2>What&rsquo;s in this parcel</h2>{_items(parcel_items)}</div>

  <div class="panel"><h2>You&rsquo;ll need at home</h2>{_items([("socket","Free wall socket"),("router","Router with a spare socket"),("speaker","Google Nest / Cast speaker")])}</div>

  <div class="panel"><span class="badge">1</span>
    <div class="step"><span class="big">{icon("sd",30)}</span><div class="body"><span class="t">{step1_title}</span><span class="muted">{step1_body}</span></div></div>
  </div>

  <div class="panel"><span class="badge">2</span>
    <div class="step"><span class="big">{icon("ethernet",30)}</span><div class="body"><span class="t">Plug in Ethernet</span><span class="muted">From your CastAdhan into your router (any spare socket).</span></div></div>
  </div>

  <div class="panel"><span class="badge">3</span>
    <div class="step"><span class="big">{icon("plug",30)}</span><div class="body"><span class="t">Plug in power</span><span class="muted">Into a wall socket.</span><div class="wait">{icon("stopwatch",17)} Wait 1 minute</div></div></div>
  </div>

  <div class="panel"><span class="badge">4</span>
    <h2>Scan to begin</h2>
    <div class="qrwrap"><img src="data:image/png;base64,{QR_B64}" alt="QR to castadhan.local"><div class="u">Scan with your phone camera<br>&hellip; or open<br><b>castadhan.local:8786</b></div></div>
  </div>

  <div class="panel"><span class="badge">5</span><h2>The welcome screen</h2>{welcome}</div>

  <div class="panel"><span class="badge">6</span><h2>Go wireless (optional)</h2>
    <div class="wire">
      <div class="muted" style="margin-bottom:3px;">Leave the cable in &mdash; you&rsquo;re done. Or:</div>
      <div class="row">{icon("gear",16)} Dashboard &rarr; <b>Settings</b></div>
      <div class="row">{icon("wifi",16)} <b>WiFi Setup</b> &rarr; Scan</div>
      <div class="row">{icon("check",16)} Pick WiFi &rarr; <b>Connect</b></div>
      <div class="safe">&ldquo;Safe to unplug Ethernet&rdquo; &rarr; remove cable.</div>
    </div>
  </div>

  <div class="panel"><h2>Need help?</h2>
    <div class="help" style="font-size:12px;">WhatsApp: <b>07595 998350</b></div>
    <div class="faq"><b>Can&rsquo;t find the address?</b> Type <b>http://castadhan.local:8786</b> in full, or message me for its number.<br><b>Updates?</b> Automatic, overnight.</div>
    <div class="hayya">Hayya &rsquo;ala Salaah</div>
  </div>
</div>
</body></html>"""
    path = os.path.join(DESK, out_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


balkis = build(
    recipient="Balkis",
    parcel_items=[("sd", "SD card"), ("ethernet", "Short Ethernet cable")],
    step1_title="Slide in the SD card",
    step1_body="Into your CastAdhan &mdash; fits one way; remove any old card first.",
    out_name="castadhan-guide-balkis.html",
)
birmingham = build(
    recipient="Birmingham family",
    parcel_items=[("box", "CastAdhan"), ("plug", "Power supply"), ("ethernet", "Ethernet cable")],
    step1_title="SD card is already inside",
    step1_body="Nothing to do &mdash; your CastAdhan is ready to plug in.",
    out_name="castadhan-guide-birmingham.html",
)

print("OK — wrote illustrated guides:")
print("  ", balkis)
print("  ", birmingham)
print("\nPrint A4 LANDSCAPE, 100% scale. Real scannable QR embedded.")
