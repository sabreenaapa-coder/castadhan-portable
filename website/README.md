# mustafayunis.co.uk

A personal calling card — a small, deliberately mysterious gallery whose theme
is a single idea: **ideas made tangible.** It reveals capability, not personal
information. No home location, personal email, phone number, biography or
unnecessary photographs appear anywhere.

## What's here

```
website/
├── index.html                 # the entrance — animated surreal landing
├── work/
│   ├── index.html             # the gallery (the seven projects, in order)
│   ├── castadhan/             # I    · Time / Devotion
│   ├── gobble/                # III  · Pattern / Perception
│   ├── stopclock/             # IV   · Play / Synchrony
│   ├── mosque-timetable/      # V    · Order / Community
│   ├── seerah/                # VI   · Story / Faith
│   └── trading-systems/       # VII  · Discipline / Autonomy
├── contact/index.html         # the single contact channel (form + address)
├── assets/                    # site.css, scene.js, site.js, contact.js, mark.svg
├── 404.html, robots.txt, sitemap.xml, _headers
```

> **II · Conflict Simulator** lives at its own subdomain,
> `https://conflict.mustafayunis.co.uk`, and is linked from the gallery
> rather than hosted here.

## Design principles

- **Dependency-free.** No web fonts, no CDNs, no trackers, no third-party
  requests. Everything is first-party, so the site is fast and leaks nothing.
- **Deliberate motion.** The homepage runs a lightweight canvas field
  (`scene.js`) that pauses when the tab is hidden and honours
  `prefers-reduced-motion`. Reveal-on-scroll degrades to fully visible.
- **Clean URLs** via directory `index.html` files — works on Cloudflare
  Pages, Netlify, GitHub Pages or any static host.

## Before it goes live — two things to wire

1. **Contact form endpoint.** In `contact/index.html`, replace
   `REPLACE_WITH_FORM_ENDPOINT` on the `<form action="…">` with your form
   provider (Formspree, Web3Forms, or a Cloudflare Pages Function). Until then
   the form politely redirects visitors to the email option. Then add the
   provider's host to `connect-src`/`form-action` in `_headers`.
2. **Dedicated email.** The contact page reveals `hello@mustafayunis.co.uk`
   only on interaction (scraper-resistant). Point that at wherever you want it
   forwarded, or change the address in the `data-user` / `data-domain`
   attributes on the `.mail` button.

## Deploy (any static host)

Point the host at the `website/` directory as the site root.

- **Cloudflare Pages / Netlify** — build command: none; output dir: `website`.
  `_headers` is picked up automatically for security + caching headers.
- **GitHub Pages** — serve the `website/` folder (or move its contents to the
  Pages root). `_headers` is ignored by Pages; the meta tags still apply.

## Local preview

```bash
cd website
python3 -m http.server 8080
# open http://localhost:8080
```

## The gallery order

1. CastAdhan
2. Conflict Simulator  → conflict.mustafayunis.co.uk
3. GOBBLE
4. STOPCLOCK
5. Mosque Timetable Creator
6. The Seerah for Children
7. Autonomous Trading Robots
   · *(and more, as they mature)*
