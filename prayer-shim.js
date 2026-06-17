/* ============================================================================
 * Prayer-clock website — client-side data shim  (v2: search + Asr/Isha rules)
 * ----------------------------------------------------------------------------
 * Makes the CastAdhan clock UI work as a backend-free static site: intercepts
 * the page's same-origin /api/* calls and answers them in the browser. Prayer
 * times come from the Aladhan API by LAT/LON (so a type-to-search city picker,
 * powered by Open-Meteo geocoding, can disambiguate places like "High Point").
 * Exposes Asr method (madhab) + high-latitude rule, mirroring the appliance.
 * External calls (Open-Meteo, Aladhan, weather) pass straight through.
 * ========================================================================== */
(function () {
  'use strict';

  var DEFAULT = {
    label: 'London, England, United Kingdom',
    lat: 51.5074, lon: -0.1278, country: 'United Kingdom', tz: 'Europe/London',
    method: 2, school: 0, latMethod: ''   // method=ISNA, school=0 Shafii, latMethod=auto
  };
  var METHODS = [
    { v: 2, label: 'ISNA (N. America)' }, { v: 3, label: 'Muslim World League' },
    { v: 4, label: 'Umm al-Qura (Makkah)' }, { v: 1, label: 'Karachi' },
    { v: 5, label: 'Egyptian' }, { v: 12, label: 'France (UOIF)' },
    { v: 13, label: 'Turkey (Diyanet)' }, { v: 15, label: 'Moonsighting Committee' }
  ];
  var SCHOOLS = [
    { v: 0, label: "Standard — Shafi'i / Maliki / Hanbali (earlier Asr)" },
    { v: 1, label: 'Hanafi (later Asr)' }
  ];
  var LATMETHODS = [
    { v: '', label: 'Automatic (recommended)' },
    { v: 3, label: 'Angle-based (best for high latitudes)' },
    { v: 1, label: 'Middle of the night' },
    { v: 2, label: 'One-seventh of the night' }
  ];

  function getCity() {
    try {
      var c = JSON.parse(localStorage.getItem('pc_city'));
      if (!c) return Object.assign({}, DEFAULT);
      if (c.lat == null && c.city) {   // legacy save (city/country only) — keep for by-city fallback, don't inherit London's coords
        return { city: c.city, country: c.country || '', label: c.city, lat: null, lon: null, tz: null, method: c.method || 2, school: c.school || 0, latMethod: c.latMethod || '' };
      }
      return Object.assign({}, DEFAULT, c);          // new-shape save — fill any missing new fields
    } catch (e) { return Object.assign({}, DEFAULT); }
  }
  function setCity(c) { localStorage.setItem('pc_city', JSON.stringify(c)); }

  var STATE = { times: null, meta: null, city: getCity(), error: null };

  /* ---- load today's times from Aladhan (by coords; cached per config+date) */
  function ddmmyyyy(d) { function p(n) { return (n < 10 ? '0' : '') + n; } return p(d.getDate()) + '-' + p(d.getMonth() + 1) + '-' + d.getFullYear(); }
  function cacheKey(c) { return 'pc_t2_' + [c.lat, c.lon, c.method, c.school, c.latMethod, new Date().toISOString().slice(0, 10)].join('|'); }
  function loadTimes() {
    var c = STATE.city, ck = cacheKey(c), cached = localStorage.getItem(ck);
    if (cached) { try { var d = JSON.parse(cached); STATE.times = d.times; STATE.meta = d.meta; STATE.error = null; return Promise.resolve(); } catch (e) {} }
    var url;
    if (c.lat != null && c.lon != null) {
      url = 'https://api.aladhan.com/v1/timings/' + ddmmyyyy(new Date()) +
        '?latitude=' + c.lat + '&longitude=' + c.lon + '&method=' + (c.method || 2) + '&school=' + (c.school || 0);
      if (c.latMethod !== '' && c.latMethod != null) url += '&latitudeAdjustmentMethod=' + c.latMethod;
    } else {   // legacy fallback (older saved city with no coords)
      url = 'https://api.aladhan.com/v1/timingsByCity?city=' + encodeURIComponent(c.city || c.label || 'London') +
        '&country=' + encodeURIComponent(c.country || '') + '&method=' + (c.method || 2) + '&school=' + (c.school || 0);
    }
    return _realFetch(url).then(function (r) { return r.json(); }).then(function (j) {
      if (!j || j.code !== 200 || !j.data) throw new Error('lookup failed');
      STATE.times = j.data.timings; STATE.meta = j.data.meta || {}; STATE.error = null;
      try { localStorage.setItem(ck, JSON.stringify({ times: STATE.times, meta: STATE.meta })); } catch (e) {}
    }).catch(function (e) { STATE.error = e.message || 'lookup failed'; });
  }

  function cityNow() {
    var tz = STATE.city.tz || (STATE.meta && STATE.meta.timezone);
    if (!tz) return new Date();
    try { return new Date(new Date().toLocaleString('en-US', { timeZone: tz })); } catch (e) { return new Date(); }
  }
  function hhmm(s) { var m = String(s || '').match(/(\d{1,2}):(\d{2})/); return m ? (m[1].padStart(2, '0') + ':' + m[2]) : ''; }

  function buildState() {
    var t = STATE.times || {}, pt = {};
    ['Fajr', 'Sunrise', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'].forEach(function (k) { if (t[k]) pt[k] = hhmm(t[k]); });
    var now = cityNow(), adhans = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'], nxt = null;
    for (var i = 0; i < adhans.length; i++) {
      var v = pt[adhans[i]]; if (!v) continue;
      var hm = v.split(':'), w = new Date(now); w.setHours(+hm[0], +hm[1], 0, 0);
      if (w > now) { nxt = { name: adhans[i], when: w, t: v }; break; }
    }
    if (!nxt && pt.Fajr) { var h2 = pt.Fajr.split(':'), w2 = new Date(now); w2.setDate(w2.getDate() + 1); w2.setHours(+h2[0], +h2[1], 0, 0); nxt = { name: 'Fajr', when: w2, t: pt.Fajr }; }
    var np = nxt ? { name: nxt.name, when_iso: nxt.when.toISOString(), effective_when_iso: nxt.when.toISOString(), time_pretty: nxt.t, effective_time_pretty: nxt.t, shifted: false } : null;
    var cityName = (STATE.city.label || STATE.city.city || '').split(',')[0];
    return { ok: true, location: { city: cityName, country: STATE.city.country || '' }, prayer_times: pt, next_prayer: np, now: now.toISOString(), scheduler_running: true, devices: { speakers: [] } };
  }
  function configPayload() { return { ok: true, app: { location: { city: (STATE.city.label || '').split(',')[0], country: STATE.city.country || '', latitude: STATE.city.lat || 51.5, longitude: STATE.city.lon || -0.13 } } }; }

  /* ---- fetch shim -------------------------------------------------------- */
  var _realFetch = window.fetch.bind(window);
  function json(o) { return new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } }); }
  window.fetch = function (input, init) {
    var url = (typeof input === 'string') ? input : (input && input.url) || '', path = url.split('?')[0];
    if (/^https?:\/\//i.test(url) && url.indexOf(location.origin) !== 0) return _realFetch(input, init); // external → pass through
    if (/\/api\/state$/.test(path)) return STATE._ready.then(function () { return json(buildState()); });
    if (/\/api\/config$/.test(path)) return Promise.resolve(json(configPayload()));
    if (/\/api\/play_history$/.test(path)) return Promise.resolve(json({ ok: true, entries: [] }));
    if (/\/api\/speaker\/status$/.test(path)) return Promise.resolve(json({ ok: true, status: {} }));
    if (/\/api\/scheduled_audio$/.test(path)) return Promise.resolve(json({ ok: true, entries: [] }));
    if (/\/api\/version$/.test(path)) return Promise.resolve(json({ ok: true, version: 'web' }));
    if (/\/api\//.test(path)) return Promise.resolve(json({ ok: true }));
    return _realFetch(input, init);
  };

  STATE._ready = loadTimes();
  setInterval(function () { loadTimes(); }, 30 * 60 * 1000);

  /* ---- city picker UI (search + Asr/Isha rules) -------------------------- */
  function el(tag, css, html) { var e = document.createElement(tag); if (css) e.style.cssText = css; if (html != null) e.innerHTML = html; return e; }
  function injectPicker() {
    var btn = el('button', 'position:fixed;bottom:16px;left:16px;z-index:6000;background:#161616;border:.5px solid #3a3422;color:#D4AF37;cursor:pointer;font:500 13px system-ui,sans-serif;padding:8px 14px;border-radius:18px;box-shadow:0 2px 10px rgba(0,0,0,.5);');
    btn.id = 'pc-citybtn'; btn.textContent = '📍 ' + (STATE.city.label || 'Set city').split(',')[0];
    document.body.appendChild(btn);

    var modal = el('div', 'position:fixed;inset:0;z-index:6001;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.6);');
    modal.id = 'pc-modal';
    var inB = 'width:100%;box-sizing:border-box;margin:4px 0 12px;padding:9px;border-radius:8px;border:1px solid #2d2d2d;background:#0c0c0c;color:#e7e0cf;font-size:14px;';
    modal.innerHTML =
      '<div style="background:#121212;border:1px solid #3a3422;border-radius:16px;padding:22px 24px;width:min(420px,92vw);max-height:90vh;overflow:auto;color:#e7e0cf;font:14px system-ui,sans-serif;box-shadow:0 8px 40px rgba(0,0,0,.6);">' +
        '<div style="font-size:17px;font-weight:600;color:#D4AF37;margin-bottom:4px;">Location &amp; prayer rules</div>' +
        '<div id="pc-cur" style="font-size:12px;color:#9a958a;margin-bottom:14px;"></div>' +
        '<label style="font-size:12px;color:#9a958a;">Search for your city</label>' +
        '<input id="pc-search" autocomplete="off" placeholder="Start typing, e.g. High Point" style="' + inB + 'margin-bottom:4px;">' +
        '<div id="pc-results" style="max-height:170px;overflow:auto;border:1px solid #222;border-radius:8px;margin:0 0 12px;display:none;"></div>' +
        '<label style="font-size:12px;color:#9a958a;">Calculation method</label><select id="pc-method" style="' + inB + '"></select>' +
        '<label style="font-size:12px;color:#9a958a;">Asr method (madhab)</label><select id="pc-school" style="' + inB + '"></select>' +
        '<label style="font-size:12px;color:#9a958a;">High-latitude rule (Fajr / Isha in summer)</label><select id="pc-lat" style="' + inB + '"></select>' +
        '<div id="pc-err" style="color:#e0857a;font-size:12px;min-height:15px;margin:2px 0 6px;"></div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end;">' +
          '<button id="pc-cancel" style="background:#1c1c1c;border:.5px solid #333;color:#cfcabb;padding:8px 16px;border-radius:8px;cursor:pointer;">Cancel</button>' +
          '<button id="pc-save" style="background:#D4AF37;border:none;color:#1a1a1a;font-weight:700;padding:8px 18px;border-radius:8px;cursor:pointer;">Save</button>' +
        '</div></div>';
    document.body.appendChild(modal);

    var $ = function (id) { return modal.querySelector(id); };
    METHODS.forEach(function (m) { var o = document.createElement('option'); o.value = m.v; o.textContent = m.label; $('#pc-method').appendChild(o); });
    SCHOOLS.forEach(function (m) { var o = document.createElement('option'); o.value = m.v; o.textContent = m.label; $('#pc-school').appendChild(o); });
    LATMETHODS.forEach(function (m) { var o = document.createElement('option'); o.value = m.v; o.textContent = m.label; $('#pc-lat').appendChild(o); });

    var picked = null;   // a freshly chosen geocoding result (else keep current)
    function setCurLine() { $('#pc-cur').textContent = 'Current: ' + (STATE.city.label || '—'); }

    var debounce;
    $('#pc-search').addEventListener('input', function (e) {
      var q = e.target.value.trim(); clearTimeout(debounce);
      if (q.length < 2) { $('#pc-results').style.display = 'none'; return; }
      debounce = setTimeout(function () {
        _realFetch('https://geocoding-api.open-meteo.com/v1/search?count=8&language=en&format=json&name=' + encodeURIComponent(q))
          .then(function (r) { return r.json(); }).then(function (j) {
            var res = (j && j.results) || [], box = $('#pc-results'); box.innerHTML = '';
            if (!res.length) { box.style.display = 'none'; return; }
            res.forEach(function (r) {
              var label = [r.name, r.admin1, r.country].filter(Boolean).join(', ');
              var row = el('div', 'padding:8px 10px;cursor:pointer;border-bottom:1px solid #1c1c1c;font-size:13px;', label + '<span style="color:#7a756a"> &middot; ' + (r.timezone || '') + '</span>');
              row.addEventListener('mouseenter', function () { row.style.background = '#1e1e1e'; });
              row.addEventListener('mouseleave', function () { row.style.background = ''; });
              row.addEventListener('click', function () {
                picked = { label: label, lat: r.latitude, lon: r.longitude, country: r.country, tz: r.timezone };
                $('#pc-search').value = label; box.style.display = 'none'; $('#pc-err').textContent = '';
              });
              box.appendChild(row);
            });
            box.style.display = 'block';
          }).catch(function () { $('#pc-results').style.display = 'none'; });
      }, 280);
    });

    function open() {
      setCurLine(); picked = null; $('#pc-search').value = ''; $('#pc-results').style.display = 'none'; $('#pc-err').textContent = '';
      $('#pc-method').value = STATE.city.method != null ? STATE.city.method : 2;
      $('#pc-school').value = STATE.city.school != null ? STATE.city.school : 0;
      $('#pc-lat').value = STATE.city.latMethod != null ? STATE.city.latMethod : '';
      modal.style.display = 'flex'; $('#pc-search').focus();
    }
    function close() { modal.style.display = 'none'; }
    btn.addEventListener('click', open);
    $('#pc-cancel').addEventListener('click', close);
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    $('#pc-save').addEventListener('click', function () {
      var base = picked || STATE.city;
      if (!base.lat) { $('#pc-err').textContent = 'Search and pick a city first.'; return; }
      var save = $('#pc-save'); save.textContent = 'Checking...'; save.disabled = true;
      STATE.city = { label: base.label, lat: base.lat, lon: base.lon, country: base.country, tz: base.tz, method: +$('#pc-method').value, school: +$('#pc-school').value, latMethod: $('#pc-lat').value };
      loadTimes().then(function () {
        if (STATE.error) { save.textContent = 'Save'; save.disabled = false; $('#pc-err').textContent = 'Could not fetch times - try again.'; return; }
        setCity(STATE.city); location.reload();
      });
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', injectPicker); else injectPicker();
})();
