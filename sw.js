/* CastAdhan Portable — Service Worker (v1.6.0, E-5 / U-1)
 * =========================================================
 * Strategy:
 *   - Cache the shell HTML + CSS + manifest for offline launch (so the
 *     home-screen icon works even when the Pi is briefly unreachable —
 *     e.g. mid-restart during auto-update at 04:14).
 *   - Network-first for /api/* (always want fresh data when online).
 *   - Cache-first for static assets (HTML, manifest, icons).
 *   - Cache versioned by the constant below so a future release can purge
 *     the old cache cleanly. Bump CACHE_NAME on any v1.x.y release that
 *     changes the shell.
 */
const CACHE_NAME = 'castadhan-shell-v1.11.1';
const SHELL_URLS = ['/', '/manifest.json'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL_URLS))
      .catch(() => { /* if Pi is offline at install, that's fine — shell will populate on first online fetch */ })
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  /* /api/* and /media/* always go to the network. We never cache prayer-
     time data because it changes daily, nor audio because it's huge. */
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/media/')) {
    event.respondWith(fetch(event.request).catch(() =>
      new Response(JSON.stringify({ok:false, error:'offline'}), {headers:{'Content-Type':'application/json'}})
    ));
    return;
  }

  /* Everything else: cache-first with background refresh. */
  event.respondWith(
    caches.match(event.request).then(cached => {
      const fetched = fetch(event.request).then(res => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => {});
        }
        return res;
      }).catch(() => cached);
      return cached || fetched;
    })
  );
});
