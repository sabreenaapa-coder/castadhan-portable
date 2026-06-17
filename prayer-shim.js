/* ============================================================================
 * Prayer-clock website — client-side data shim
 * ----------------------------------------------------------------------------
 * The CastAdhan clock UI normally gets its data from a Raspberry-Pi backend
 * (GET /api/state etc.). This file makes the SAME UI work as a standalone
 * static website with NO backend: it intercepts those /api/* calls and answers
 * them in the browser, computing prayer times from the free Aladhan API for a
 * city the viewer picks (remembered in localStorage). External calls (Aladhan,
 * open-meteo weather) pass straight through untouched.
 * ========================================================================== */
(function () {
  'use strict';

  var DEFAULT = { city: 'London', country: 'United Kingdom', method: 2 };
  var METHODS = [
    { v: 2, label: 'ISNA (N. America)' },
    { v: 3, label: 'Muslim World League' },
    { v: 4, label: 'Umm al-Qura (Makkah)' },
    { v: 1, label: 'Karachi' },
    { v: 5, label: 'Egyptian' },
    { v: 12, label: 'France (UOIF)' },
    { v: 13, label: 'Turkey (Diyanet)' },
    { v: 15, label: 'Moonsighting Committee' }
  ];

  function getCity() {
    try { return JSON.parse(localStorage.getItem('pc_city')) || DEFAULT; }
    catch (e) { return DEFAULT; }
  }
  function setCity(c) { localStorage.setItem('pc_city', JSON.stringify(c)); }

  var STATE = { times: null, meta: null, city: getCity(), error: null };

  /* ---- load today's times from Aladhan (cached per city+date) ------------- */
  function cacheKey(c) {
    var today = new Date().toISOString().slice(0, 10);
    return 'pc_times_' + c.city + '|' + c.country + '|' + (c.method || 2) + '|' + today;
  }
  function loadTimes() {
    var c = STATE.city;
    var ck = cacheKey(c);
    var cached = localStorage.getItem(ck);
    if (cached) {
      try { var d = JSON.parse(cached); STATE.times = d.times; STATE.meta = d.meta; STATE.error = null; return Promise.resolve(); }
      catch (e) { /* fall through to network */ }
    }
    var url = 'https://api.aladhan.com/v1/timingsByCity?city=' + encodeURIComponent(c.city) +
      '&country=' + encodeURIComponent(c.country) + '&method=' + (c.method || 2);
    return _realFetch(url).then(function (r) { return r.json(); }).then(function (j) {
      if (!j || j.code !== 200 || !j.data) throw new Error('city not found');
      STATE.times = j.data.timings;
      STATE.meta = j.data.meta || {};
      STATE.error = null;
      try { localStorage.setItem(ck, JSON.stringify({ times: STATE.times, meta: STATE.meta })); } catch (e) {}
    }).catch(function (e) {
      STATE.error = e.message || 'lookup failed';
      // keep any previously-loaded times so the clock doesn't go blank
    });
  }

  /* ---- "now" in the selected city's timezone (so countdown is correct even
   *      if the viewer is physically elsewhere) ---------------------------- */
  function cityNow() {
    var tz = STATE.meta && STATE.meta.timezone;
    if (!tz) return new Date();
    try { return new Date(new Date().toLocaleString('en-US', { timeZone: tz })); }
    catch (e) { return new Date(); }
  }

  function hhmm(s) { var m = String(s || '').match(/(\d{1,2}):(\d{2})/); return m ? (m[1].padStart(2, '0') + ':' + m[2]) : ''; }

  /* ---- build the /api/state payload the clock expects --------------------- */
  function buildState() {
    var t = STATE.times || {};
    var pt = {};
    ['Fajr', 'Sunrise', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'].forEach(function (k) {
      if (t[k]) pt[k] = hhmm(t[k]);
    });
    var now = cityNow();
    var adhans = ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha'];
    var nxt = null;
    for (var i = 0; i < adhans.length; i++) {
      var v = pt[adhans[i]]; if (!v) continue;
      var hm = v.split(':'); var w = new Date(now); w.setHours(+hm[0], +hm[1], 0, 0);
      if (w > now) { nxt = { name: adhans[i], when: w, t: v }; break; }
    }
    if (!nxt && pt.Fajr) {
      var hm2 = pt.Fajr.split(':'); var w2 = new Date(now); w2.setDate(w2.getDate() + 1);
      w2.setHours(+hm2[0], +hm2[1], 0, 0); nxt = { name: 'Fajr', when: w2, t: pt.Fajr };
    }
    var np = nxt ? {
      name: nxt.name, when_iso: nxt.when.toISOString(), effective_when_iso: nxt.when.toISOString(),
      time_pretty: nxt.t, effective_time_pretty: nxt.t, shifted: false
    } : null;
    return {
      ok: true,
      location: { city: STATE.city.city, country: STATE.city.country },
      prayer_times: pt,
      next_prayer: np,
      now: now.toISOString(),
      scheduler_running: true,
      devices: { speakers: [] }
    };
  }
  function configPayload() {
    return {
      ok: true, app: { location: {
        city: STATE.city.city, country: STATE.city.country,
        latitude: (STATE.meta && STATE.meta.latitude) || 51.5074,
        longitude: (STATE.meta && STATE.meta.longitude) || -0.1278
      } }
    };
  }

  /* ---- fetch shim: answer same-origin /api/* in the browser --------------- */
  var _realFetch = window.fetch.bind(window);
  function json(o) { return new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } }); }
  window.fetch = function (input, init) {
    var url = (typeof input === 'string') ? input : (input && input.url) || '';
    var path = url.split('?')[0];
    if (/^https?:\/\//i.test(url) && url.indexOf(location.origin) !== 0) return _realFetch(input, init); // external (Aladhan, open-meteo) → pass through
    if (/\/api\/state$/.test(path)) return STATE._ready.then(function () { return json(buildState()); });
    if (/\/api\/config$/.test(path)) return Promise.resolve(json(configPayload()));
    if (/\/api\/play_history$/.test(path)) return Promise.resolve(json({ ok: true, entries: [] }));
    if (/\/api\/speaker\/status$/.test(path)) return Promise.resolve(json({ ok: true, status: {} }));
    if (/\/api\/scheduled_audio$/.test(path)) return Promise.resolve(json({ ok: true, entries: [] }));
    if (/\/api\/version$/.test(path)) return Promise.resolve(json({ ok: true, version: 'web' }));
    if (/\/api\//.test(path)) return Promise.resolve(json({ ok: true })); // any other API → harmless ok
    return _realFetch(input, init);
  };

  /* ---- kick off + refresh ------------------------------------------------- */
  STATE._ready = loadTimes();
  setInterval(function () { loadTimes(); }, 30 * 60 * 1000);  // refresh every 30 min (covers midnight rollover)

  /* ---- city picker UI ----------------------------------------------------- */
  function injectPicker() {
    var btn = document.createElement('button');
    btn.id = 'pc-citybtn';
    btn.textContent = '📍 ' + STATE.city.city;
    btn.title = 'Change city';
    btn.style.cssText = 'position:fixed;bottom:16px;left:16px;z-index:6000;background:#161616;border:.5px solid #3a3422;' +
      'color:#D4AF37;cursor:pointer;font:500 13px system-ui,sans-serif;padding:8px 14px;border-radius:18px;box-shadow:0 2px 10px rgba(0,0,0,.5);';
    document.body.appendChild(btn);

    var modal = document.createElement('div');
    modal.id = 'pc-modal';
    modal.style.cssText = 'position:fixed;inset:0;z-index:6001;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.6);';
    modal.innerHTML =
      '<div style="background:#121212;border:1px solid #3a3422;border-radius:16px;padding:22px 24px;width:min(380px,90vw);color:#e7e0cf;font:14px system-ui,sans-serif;box-shadow:0 8px 40px rgba(0,0,0,.6);">' +
        '<div style="font-size:17px;font-weight:500;color:#D4AF37;margin-bottom:14px;">Choose your city</div>' +
        '<label style="font-size:12px;color:#9a958a;">City</label>' +
        '<input id="pc-city" style="width:100%;box-sizing:border-box;margin:4px 0 12px;padding:9px;border-radius:8px;border:1px solid #2d2d2d;background:#0c0c0c;color:#e7e0cf;">' +
        '<label style="font-size:12px;color:#9a958a;">Country</label>' +
        '<input id="pc-country" style="width:100%;box-sizing:border-box;margin:4px 0 12px;padding:9px;border-radius:8px;border:1px solid #2d2d2d;background:#0c0c0c;color:#e7e0cf;">' +
        '<label style="font-size:12px;color:#9a958a;">Calculation method</label>' +
        '<select id="pc-method" style="width:100%;box-sizing:border-box;margin:4px 0 16px;padding:9px;border-radius:8px;border:1px solid #2d2d2d;background:#0c0c0c;color:#e7e0cf;"></select>' +
        '<div id="pc-err" style="color:#e0857a;font-size:12px;min-height:16px;margin-bottom:8px;"></div>' +
        '<div style="display:flex;gap:10px;justify-content:flex-end;">' +
          '<button id="pc-cancel" style="background:#1c1c1c;border:.5px solid #333;color:#cfcabb;padding:8px 16px;border-radius:8px;cursor:pointer;">Cancel</button>' +
          '<button id="pc-save" style="background:#D4AF37;border:none;color:#1a1a1a;font-weight:600;padding:8px 18px;border-radius:8px;cursor:pointer;">Use this city</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    var sel = modal.querySelector('#pc-method');
    METHODS.forEach(function (m) { var o = document.createElement('option'); o.value = m.v; o.textContent = m.label; sel.appendChild(o); });

    function open() {
      modal.querySelector('#pc-city').value = STATE.city.city;
      modal.querySelector('#pc-country').value = STATE.city.country;
      sel.value = STATE.city.method || 2;
      modal.querySelector('#pc-err').textContent = '';
      modal.style.display = 'flex';
    }
    function close() { modal.style.display = 'none'; }
    btn.addEventListener('click', open);
    modal.querySelector('#pc-cancel').addEventListener('click', close);
    modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
    modal.querySelector('#pc-save').addEventListener('click', function () {
      var city = modal.querySelector('#pc-city').value.trim();
      var country = modal.querySelector('#pc-country').value.trim();
      if (!city) { modal.querySelector('#pc-err').textContent = 'Please enter a city.'; return; }
      var saveBtn = modal.querySelector('#pc-save'); saveBtn.textContent = 'Checking…'; saveBtn.disabled = true;
      STATE.city = { city: city, country: country, method: +sel.value };
      loadTimes().then(function () {
        if (STATE.error) { saveBtn.textContent = 'Use this city'; saveBtn.disabled = false; modal.querySelector('#pc-err').textContent = 'Couldn’t find that city — check the spelling/country.'; return; }
        setCity(STATE.city);
        location.reload();
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', injectPicker);
  else injectPicker();
})();
