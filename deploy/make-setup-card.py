import segno, base64, io, os
URL = "http://castadhan.local:8786"
DESK = os.path.expanduser("~/Desktop")
qr = segno.make(URL, error='m')
qr.save(os.path.join(DESK, "castadhan-qr.png"), scale=10, border=3)
buf = io.BytesIO(); qr.save(buf, kind='png', scale=10, border=3)
b64 = base64.b64encode(buf.getvalue()).decode()
card = '''<!doctype html><html><head><meta charset="utf-8"><title>CastAdhan setup card</title>
<style>
@media print { @page { margin: 12mm; } body { background:#fff; } }
body{font-family:-apple-system,system-ui,sans-serif;display:flex;justify-content:center;padding:24px;background:#f3f1ec;}
.card{width:340px;background:#fff;border:2px solid #d4af37;border-radius:18px;padding:26px 24px;text-align:center;box-shadow:0 4px 18px rgba(0,0,0,.12);}
.card h1{color:#1a1a1a;font-size:22px;margin:0 0 2px;}
.card .sub{color:#8a7a3a;font-size:13px;margin:0 0 16px;font-weight:700;letter-spacing:1px;}
.card img{width:230px;height:230px;image-rendering:pixelated;}
.card ol{text-align:left;color:#333;font-size:14px;line-height:1.7;margin:14px 4px 6px;padding-left:20px;}
.card .addr{margin-top:12px;font-size:13px;color:#555;}
.card .addr b{color:#1a1a1a;}
</style></head><body><div class="card">
<h1>&#128347; Your CastAdhan</h1>
<div class="sub">SCAN TO OPEN</div>
<img src="data:image/png;base64,''' + b64 + '''" alt="QR code">
<ol>
<li>Connect your phone to your <b>home Wi-Fi</b>.</li>
<li>Point your camera at this code.</li>
<li>Tap the link &mdash; your dashboard opens.</li>
</ol>
<div class="addr">Or type into any browser:<br><b>castadhan.local:8786</b></div>
</div></body></html>'''
open(os.path.join(DESK, "castadhan-setup-card.html"), "w").write(card)
print("OK: wrote castadhan-qr.png + castadhan-setup-card.html to", DESK)
