#!/usr/bin/env python3
"""CastAdhan Portable — captive-portal responder (port 80).

Only runs while the "CastAdhan Setup" onboarding hotspot is up (started and
stopped by castadhan-hotspot.sh). Its whole job is to make phones auto-open
the WiFi setup page:

  • iOS / macOS fetch http://captive.apple.com/hotspot-detect.html and expect
    the body "Success". Anything else pops the captive sign-in sheet.
  • Android fetches /generate_204 and expects HTTP 204. Anything else flags a
    captive portal and offers "Sign in to network".
  • Windows fetches /ncsi.txt / /connecttest.txt.

NetworkManager's shared-mode dnsmasq is pointed at us (address=/#/10.42.0.1 via
deploy/captive-dnsmasq-shared.conf), so every DNS lookup on the hotspot resolves
to this Pi. We answer all of the above with a 302 to the real setup page, which
is served by the main app on :8786. We deliberately do NOT return the
"online" bodies, so the captive sheet always opens.

Fail-safe: binds 0.0.0.0:80; if it can't (already in use, no privilege) it
exits quietly and onboarding still works by browsing to the IP manually.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SETUP_URL = "http://10.42.0.1:8786/wifi-setup"

# A minimal page for clients that render the body instead of following the 302.
_FALLBACK_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<meta http-equiv='refresh' content='0; url=" + SETUP_URL + "'>"
    "<title>CastAdhan Setup</title></head>"
    "<body style='font-family:sans-serif;text-align:center;padding:2rem'>"
    "<h2>\U0001F54C CastAdhan setup</h2>"
    "<p><a href='" + SETUP_URL + "'>Tap here to connect your prayer clock to WiFi</a></p>"
    "</body></html>"
).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _portal(self):
        # 302 first (most OS captive checks follow it); include an HTML body
        # with a meta-refresh as a belt-and-braces fallback.
        self.send_response(302)
        self.send_header("Location", SETUP_URL)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_FALLBACK_HTML)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(_FALLBACK_HTML)
        except Exception:
            pass

    do_GET = _portal
    do_POST = _portal

    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", SETUP_URL)
        self.end_headers()

    def log_message(self, *_args):
        pass  # stay quiet


if __name__ == "__main__":
    try:
        ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
    except Exception:
        pass  # port busy / no privilege — onboarding still works via the IP
