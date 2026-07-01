/* ============================================================================
 * CastAdhan — device-speaker player (website edition)
 * ----------------------------------------------------------------------------
 * Mirrors the appliance's audio, but plays through the SPEAKERS OF THE DEVICE
 * showing this page (phone / laptop / TV) instead of casting to Nest speakers.
 *
 * Rides on prayer-shim.js (supplies /api/state with the chosen city's
 * prayer_times). Adds: a client-side scheduler, one-tap arm (browsers block
 * autoplay sound until a user gesture), quiet hours + per-group night policy,
 * editable alarm times, a "today" preview, upload-your-own-audio (IndexedDB),
 * Screen Wake Lock, no-replay tracking, and a settings panel.
 *
 * HARD LIMITS (browser, not bugs): only runs while the tab is open & the device
 * awake; cannot reach external Nest/Cast speakers.
 * ========================================================================== */
(function () {
  'use strict';

  /* ---- audio sources: all already-public (repo audio + surah release assets) */
  var RAW = 'https://raw.githubusercontent.com/sabreenaapa-coder/castadhan-portable/main/audio/';
  var REL = 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/';
  var URLS = {
    adhan: RAW + 'adhan.mp3', fajr_adhan: 'audio/fajr_adhan.mp3', fajr_warning: RAW + 'fajr_warning.mp3', dhuhr_warning: RAW + 'dhuhr_warning.mp3',
    asr_warning: RAW + 'asr_warning.mp3', maghrib_warning: RAW + 'maghrib_warning.mp3',
    morning_dhikr: RAW + 'morning_dhikr.mp3', evening_dhikr: RAW + 'evening_dhikr.mp3', surah_kahf: RAW + 'surah_kahf.mp3',
    friday_prayer: RAW + 'friday_prayer.mp3', wakeup: RAW + 'wakey_wakey.mp3', suhoor: RAW + 'suhoor_alarm.mp3',
    takbeeraat: RAW + 'takbeeraat.mp3', surah_baqarah: REL + 'surah_baqarah.mp3', surah_yasin: REL + 'surah_yasin.mp3',
    surah_mulk: REL + 'surah_mulk.mp3', surah_waqiah: REL + 'surah_waqiah.mp3', surah_sajdah: REL + 'surah_sajdah.mp3'
  };

  /* ---- event catalogue. anchor: prayer name or 'time'. offset minutes (neg=before). day 0=Sun..6=Sat. */
  var EVENTS = [
    { id: 'adhan_fajr', group: 'adhan', label: 'Fajr adhan', anchor: 'Fajr', offset: 0, sound: 'fajr_adhan', core: true },
    { id: 'adhan_dhuhr', group: 'adhan', label: 'Dhuhr adhan', anchor: 'Dhuhr', offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_asr', group: 'adhan', label: 'Asr adhan', anchor: 'Asr', offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_maghrib', group: 'adhan', label: 'Maghrib adhan', anchor: 'Maghrib', offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_isha', group: 'adhan', label: 'Isha adhan', anchor: 'Isha', offset: 0, sound: 'adhan', core: true },
    { id: 'warn_fajr', group: 'warnings', label: 'Fajr ending (pre-sunrise)', anchor: 'Sunrise', offset: -5, sound: 'fajr_warning' },
    { id: 'warn_dhuhr', group: 'warnings', label: 'Dhuhr ending', anchor: 'Asr', offset: -10, sound: 'dhuhr_warning' },
    { id: 'warn_maghrib', group: 'warnings', label: 'Maghrib soon', anchor: 'Maghrib', offset: -10, sound: 'maghrib_warning' },
    { id: 'morning_dhikr', group: 'morning_dhikr', label: 'Morning dhikr', anchor: 'time', time: '07:00', sound: 'morning_dhikr', notDay: 5 },
    { id: 'friday_kahf', group: 'friday_kahf', label: 'Surah al-Kahf (Fri)', anchor: 'time', time: '07:00', sound: 'surah_kahf', day: 5 },
    { id: 'friday_dua', group: 'friday_dua', label: 'Friday dua (before Maghrib)', anchor: 'Maghrib', offset: -31, sound: 'friday_prayer', day: 5 },
    { id: 'evening_dhikr', group: 'evening_dhikr', label: 'Evening dhikr', anchor: 'Maghrib', offset: 30, sound: 'evening_dhikr', cutoff: '21:30' },
    { id: 'wakeup', group: 'wakeup', label: 'Wake-up alarm', anchor: 'time', time: '06:30', sound: 'wakeup' },
    { id: 'suhoor', group: 'suhoor', label: 'Suhoor alarm', anchor: 'Fajr', offset: -40, sound: 'suhoor' },
    { id: 'surah_mulk', group: 'quran', label: 'Surah al-Mulk', anchor: 'time', time: '22:00', sound: 'surah_mulk' },
    { id: 'surah_baqarah', group: 'quran', label: 'Surah al-Baqarah', anchor: 'time', time: '10:00', sound: 'surah_baqarah' },
    { id: 'surah_waqiah', group: 'quran', label: "Surah al-Waqi'ah", anchor: 'time', time: '17:00', sound: 'surah_waqiah' },
    { id: 'surah_yasin', group: 'quran', label: 'Surah Yasin (Thu)', anchor: 'Maghrib', offset: 15, sound: 'surah_yasin', day: 4 },
    { id: 'surah_sajdah', group: 'quran', label: 'Surah as-Sajdah (Fri)', anchor: 'Fajr', offset: 15, sound: 'surah_sajdah', day: 5 }
  ];
  var GROUPS = [
    { id: 'adhan', label: 'Adhan (5 prayers)' }, { id: 'warnings', label: 'Pre-prayer reminders' },
    { id: 'morning_dhikr', label: 'Morning dhikr', timed: 'morning_dhikr' }, { id: 'friday_kahf', label: 'Surah al-Kahf (Fridays)' },
    { id: 'friday_dua', label: 'Friday dua (before Maghrib)' },
    { id: 'evening_dhikr', label: 'Evening dhikr' }, { id: 'quran', label: "Qur'an programs" },
    { id: 'wakeup', label: 'Wake-up alarm', timed: 'wakeup' }, { id: 'suhoor', label: 'Suhoor alarm (Ramadan)' }
  ];

  /* ---- settings (per-browser) -------------------------------------------- */
  var DEFAULTS = {
    armed: false, volume: 0.85, quietStart: '22:00', quietEnd: '07:00',
    groups: { adhan: true, warnings: false, morning_dhikr: false, friday_kahf: false, friday_dua: true, evening_dhikr: false, quran: false, wakeup: false, suhoor: false },
    // peripheral policy during quiet hours: play=full · quieter=~45% · silent=suppressed
    night: { adhan: 'play', warnings: 'silent', morning_dhikr: 'quieter', friday_kahf: 'quieter', friday_dua: 'quieter', evening_dhikr: 'quieter', quran: 'quieter', wakeup: 'play', suhoor: 'play' },
    times: {}   // per-event HH:MM overrides for fixed-time events (e.g. morning_dhikr, wakeup)
  };
  var cfg;
  function loadCfg() {
    try {
      var s = JSON.parse(localStorage.getItem('pc_player_cfg'));
      cfg = Object.assign({}, DEFAULTS, s || {});
      cfg.groups = Object.assign({}, DEFAULTS.groups, (s && s.groups) || {});
      cfg.night = Object.assign({}, DEFAULTS.night, (s && s.night) || {});
      cfg.times = Object.assign({}, (s && s.times) || {});
    } catch (e) { cfg = JSON.parse(JSON.stringify(DEFAULTS)); }
  }
  function saveCfg() { try { localStorage.setItem('pc_player_cfg', JSON.stringify(cfg)); } catch (e) {} }
  loadCfg();

  /* ---- custom audio (your own files), persisted in IndexedDB ------------- */
  var customURL = {};   // group -> objectURL
  function idb(cb) {
    try { var rq = indexedDB.open('pc_player_audio', 1);
      rq.onupgradeneeded = function () { rq.result.createObjectStore('sounds'); };
      rq.onsuccess = function () { cb(rq.result); }; rq.onerror = function () { cb(null); };
    } catch (e) { cb(null); }
  }
  function idbPut(group, blob, done) { idb(function (db) { if (!db) return done && done(); var t = db.transaction('sounds', 'readwrite'); t.objectStore('sounds').put(blob, group); t.oncomplete = function () { done && done(); }; }); }
  function idbClear(done) { idb(function (db) { if (!db) return done && done(); var t = db.transaction('sounds', 'readwrite'); t.objectStore('sounds').clear(); t.oncomplete = function () { done && done(); }; }); }
  function idbLoadAll(done) {
    idb(function (db) {
      if (!db) return done && done({});
      var st = db.transaction('sounds', 'readonly').objectStore('sounds'), out = {}, ck = st.openCursor();
      ck.onsuccess = function (e) { var c = e.target.result; if (c) { try { out[c.key] = URL.createObjectURL(c.value); } catch (x) {} c.continue(); } else { customURL = out; done && done(out); } };
      ck.onerror = function () { done && done({}); };
    });
  }
  function setCustom(group, file) { idbPut(group, file, function () { try { customURL[group] = URL.createObjectURL(file); } catch (e) {} }); }

  /* ---- no-replay tracking (per local day) -------------------------------- */
  function todayStr() { var d = new Date(); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); }
  function getFired() { try { var o = JSON.parse(localStorage.getItem('pc_player_fired')); return (o && o.day === todayStr()) ? o.ids : []; } catch (e) { return []; } }
  function markFired(id) { var ids = getFired(); ids.push(id); try { localStorage.setItem('pc_player_fired', JSON.stringify({ day: todayStr(), ids: ids })); } catch (e) {} }

  /* ---- audio element + autoplay unlock + wake lock ----------------------- */
  var media = new Audio(); media.preload = 'none';
  var wakeLock = null;
  function unlock() { try { media.muted = true; media.src = URLS.adhan; var p = media.play(); if (p && p.then) p.then(function () { media.pause(); media.currentTime = 0; media.muted = false; }).catch(function () { media.muted = false; }); } catch (e) {} }
  function requestWake() { try { if (navigator.wakeLock && cfg.armed) navigator.wakeLock.request('screen').then(function (w) { wakeLock = w; }).catch(function () {}); } catch (e) {} }
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible' && cfg.armed) requestWake(); });

  /* ---- playback (silent-test hook keeps dev verification soundless) ------- */
  function play(sound, label, vol, group) {
    if (vol == null) vol = cfg.volume;
    var src = (group && customURL[group]) || URLS[sound] || URLS.adhan;
    if (window.__playerSilent) { (window.__playerLog = window.__playerLog || []).push({ t: new Date().toISOString(), sound: sound, label: label, vol: Math.round(vol * 100) / 100, custom: !!(group && customURL[group]) }); banner(label + ' (silent-test)'); return; }
    try { media.pause(); } catch (e) {}
    media.src = src; media.volume = Math.max(0, Math.min(1, vol)); media.muted = false;
    var p = media.play(); if (p && p.catch) p.catch(function () { banner('⚠️ Tap “Enable” first to allow sound'); });
    banner('▶ ' + label);
  }
  function stopAll() { try { media.pause(); media.currentTime = 0; } catch (e) {} banner('■ Stopped'); }

  /* ---- time helpers ------------------------------------------------------ */
  function hhmmToToday(hhmm, base) { var m = String(hhmm || '').match(/(\d{1,2}):(\d{2})/); if (!m) return null; var d = new Date(base); d.setHours(+m[1], +m[2], 0, 0); return d; }
  function inQuiet(now) { var s = hhmmToToday(cfg.quietStart, now), e = hhmmToToday(cfg.quietEnd, now); if (!s || !e) return false; return (s <= e) ? (now >= s && now < e) : (now >= s || now < e); }

  /* ---- build today's schedule -------------------------------------------- */
  var lastTimes = {};
  function fetchTimes() { return fetch('/api/state').then(function (r) { return r.json(); }).then(function (s) { lastTimes = s.prayer_times || lastTimes; return lastTimes; }).catch(function () { return lastTimes; }); }
  function buildSchedule(times, now) {
    var out = [];
    EVENTS.forEach(function (ev) {
      if (!cfg.groups[ev.group]) return;
      if (ev.day != null && now.getDay() !== ev.day) return;
      if (ev.notDay != null && now.getDay() === ev.notDay) return;
      var when;
      if (ev.anchor === 'time') { when = hhmmToToday(cfg.times[ev.id] || ev.time, now); }
      else { var base = hhmmToToday(times[ev.anchor], now); if (!base) return; when = new Date(base.getTime() + (ev.offset || 0) * 60000); }
      if (!when) return;
      if (ev.cutoff) { var c = hhmmToToday(ev.cutoff, now); if (c && when > c) return; }
      out.push({ id: ev.id, group: ev.group, label: ev.label, sound: ev.sound, core: !!ev.core, when: when });
    });
    out.sort(function (a, b) { return a.when - b.when; });
    return out;
  }

  /* ---- scheduler tick ---------------------------------------------------- */
  function tick() {
    if (!cfg.armed) return;
    var now = new Date();
    fetchTimes().then(function (times) {
      var sched = buildSchedule(times, now), fired = getFired();
      sched.forEach(function (e) {
        if (fired.indexOf(e.id) >= 0) return;
        var dt = now - e.when;
        if (dt >= 0 && dt < 5 * 60000) {
          markFired(e.id);
          var beh = (cfg.night && cfg.night[e.group]) || (e.core ? 'play' : 'quieter');
          if (inQuiet(now)) {
            if (beh === 'silent') { banner('🔇 ' + e.label + ' — silent (quiet hours)'); return; }
            play(e.sound, e.label, beh === 'quieter' ? cfg.volume * 0.45 : cfg.volume, e.group);
          } else { play(e.sound, e.label, cfg.volume, e.group); }
        }
      });
      renderNext(sched, now); renderSched();
    });
  }

  /* ---- UI ---------------------------------------------------------------- */
  var chip, modal, bannerEl, nextEl;
  function banner(msg) { if (bannerEl) { bannerEl.textContent = msg; bannerEl.style.opacity = '1'; clearTimeout(banner._t); banner._t = setTimeout(function () { bannerEl.style.opacity = '0'; }, 6000); } }
  function fmt(d) { return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  function renderNext(sched, now) {
    if (!nextEl) return;
    var up = sched.filter(function (e) { return e.when > now && getFired().indexOf(e.id) < 0; })[0];
    nextEl.textContent = cfg.armed ? (up ? ('Next on this device: ' + up.label + ' · ' + fmt(up.when)) : 'Armed — no more events today') : '';
  }
  function renderSched() {
    var box = modal && modal.querySelector('#pc-sched'); if (!box) return;
    var now = new Date(), sched = buildSchedule(lastTimes, now);
    if (!sched.length) { box.innerHTML = '<div style="color:#7a756a">Nothing enabled for today.</div>'; return; }
    box.innerHTML = '<div style="color:#9a958a;margin-bottom:4px">Today on this device</div>' + sched.map(function (e) {
      var done = getFired().indexOf(e.id) >= 0, col = done ? '#5fb55f' : (e.when < now ? '#7a756a' : '#e7e0cf');
      return '<div class="r"><span style="color:' + col + '">' + e.label + '</span><span style="color:' + col + '">' + fmt(e.when) + (done ? ' ✓' : '') + '</span></div>';
    }).join('');
  }
  function updateCustBtns() { if (!modal) return; GROUPS.forEach(function (g) { var b = modal.querySelector('button[data-cust="' + g.id + '"]'); if (b) { var on = !!customURL[g.id]; b.className = 'cust' + (on ? ' on' : ''); b.textContent = on ? '📁✓' : '📁'; } }); }
  function setArmed(on) {
    cfg.armed = on; saveCfg();
    if (on) { unlock(); requestWake(); } else { try { if (wakeLock) { wakeLock.release(); wakeLock = null; } } catch (e) {} }
    if (chip) { chip.textContent = on ? '🔊 Adhan: ON' : '🔊 Adhan: off'; chip.style.borderColor = on ? '#3a7a3a' : '#3a3422'; chip.style.color = on ? '#7fe07f' : '#D4AF37'; }
    if (on) tick();
  }

  function buildUI() {
    var css = document.createElement('style');
    css.textContent =
      '#pc-adhan-chip{position:fixed;bottom:16px;right:16px;z-index:6000;background:#161616;border:.5px solid #3a3422;color:#D4AF37;cursor:pointer;font:600 13px system-ui,sans-serif;padding:8px 14px;border-radius:18px;box-shadow:0 2px 10px rgba(0,0,0,.5)}' +
      '#pc-adhan-next{position:fixed;bottom:54px;right:16px;z-index:5999;color:#cdbf8a;font:500 12px system-ui,sans-serif;background:rgba(12,12,12,.7);padding:3px 9px;border-radius:10px;max-width:70vw;text-align:right}' +
      '#pc-adhan-banner{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:6002;background:#1a1a1a;border:1px solid #3a3422;color:#f0e6c8;font:600 14px system-ui,sans-serif;padding:9px 18px;border-radius:22px;opacity:0;transition:opacity .4s;pointer-events:none;box-shadow:0 4px 18px rgba(0,0,0,.6)}' +
      '#pc-adhan-modal{position:fixed;inset:0;z-index:6001;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.6)}' +
      '#pc-adhan-modal .box{background:#121212;border:1px solid #3a3422;border-radius:16px;padding:20px 22px;width:min(440px,93vw);max-height:90vh;overflow:auto;color:#e7e0cf;font:14px system-ui,sans-serif;box-shadow:0 8px 40px rgba(0,0,0,.6)}' +
      '#pc-adhan-modal h3{color:#D4AF37;font-size:17px;font-weight:600;margin:0 0 4px}' +
      '#pc-adhan-modal .sub{color:#9a958a;font-size:12px;margin:0 0 14px}' +
      '#pc-adhan-modal label.row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid #1f1f1f}' +
      '#pc-adhan-modal .row .t{display:flex;gap:8px;align-items:center}' +
      '#pc-adhan-modal button.test,#pc-adhan-modal button.cust{background:#1c1c1c;border:.5px solid #333;color:#cfcabb;padding:3px 8px;border-radius:7px;cursor:pointer;font-size:11px}' +
      '#pc-adhan-modal button.cust.on{border-color:#3a7a3a;color:#7fe07f}' +
      '#pc-adhan-modal select.night{background:#0c0c0c;color:#cfcabb;border:.5px solid #333;border-radius:6px;font-size:11px;padding:2px}' +
      '#pc-adhan-modal input[type=time]{background:#0c0c0c;color:#e7e0cf;border:1px solid #2d2d2d;border-radius:6px;padding:2px 4px;font-size:11px}' +
      '#pc-adhan-modal input[type=range]{accent-color:#D4AF37}' +
      '#pc-adhan-modal #pc-sched{margin:12px 0 2px;font-size:12px;background:#0e0e0e;border:1px solid #1f1f1f;border-radius:8px;padding:8px 10px;max-height:150px;overflow:auto}' +
      '#pc-adhan-modal #pc-sched .r{display:flex;justify-content:space-between;padding:1px 0}' +
      '#pc-adhan-modal .act{display:flex;gap:10px;justify-content:space-between;margin-top:16px}' +
      '#pc-adhan-modal .arm{flex:1;border:none;border-radius:9px;padding:11px;font-weight:700;cursor:pointer;background:#D4AF37;color:#1a1a1a}' +
      '#pc-adhan-modal .stop{border:.5px solid #5a2a2a;background:#1c1414;color:#e0857a;border-radius:9px;padding:11px 16px;cursor:pointer;font-weight:600}' +
      '#pc-adhan-modal .reset{background:none;border:none;color:#7a756a;text-decoration:underline;cursor:pointer;font-size:11px;padding:0}' +
      '#pc-adhan-modal .note{color:#8a857a;font-size:11px;line-height:1.5;margin-top:10px}';
    document.head.appendChild(css);

    chip = document.createElement('button'); chip.id = 'pc-adhan-chip'; document.body.appendChild(chip);
    nextEl = document.createElement('div'); nextEl.id = 'pc-adhan-next'; document.body.appendChild(nextEl);
    bannerEl = document.createElement('div'); bannerEl.id = 'pc-adhan-banner'; document.body.appendChild(bannerEl);

    modal = document.createElement('div'); modal.id = 'pc-adhan-modal';
    var nightOpts = '<option value="play">play</option><option value="quieter">quieter</option><option value="silent">silent</option>';
    var groupRows = GROUPS.map(function (g) {
      var timeInput = g.timed ? '<input type="time" class="gtime" data-gt="' + g.id + '" title="Play time">' : '';
      return '<label class="row"><span class="t"><input type="checkbox" data-g="' + g.id + '"> ' + g.label + '</span>' +
             '<span style="display:flex;gap:6px;align-items:center">' + timeInput +
             '<select class="night" data-n="' + g.id + '" title="During quiet hours">' + nightOpts + '</select>' +
             '<button class="cust" data-cust="' + g.id + '" title="Use your own audio file">📁</button>' +
             '<button class="test" data-test="' + g.id + '">test</button>' +
             '<input type="file" accept="audio/*" data-file="' + g.id + '" style="display:none"></span></label>';
    }).join('');
    var ti = 'background:#0c0c0c;color:#e7e0cf;border:1px solid #2d2d2d;border-radius:6px;padding:3px';
    modal.innerHTML =
      '<div class="box">' +
        '<h3>Adhan on this device</h3>' +
        '<p class="sub">Plays through <b>this device’s</b> speakers while this page stays open. It can’t reach Nest/Cast speakers.</p>' +
        '<label class="row"><span class="t">Volume</span><input type="range" id="pc-vol" min="0" max="1" step="0.05" style="width:150px"></label>' +
        '<label class="row"><span class="t">Quiet hours</span><span><input type="time" id="pc-qs" style="' + ti + '"> – <input type="time" id="pc-qe" style="' + ti + '"></span></label>' +
        '<div style="margin:12px 0 4px;color:#9a958a;font-size:12px;display:flex;justify-content:space-between;align-items:baseline">What to play <span style="font-size:11px;color:#7a756a">time · night · your file · test</span></div>' +
        groupRows +
        '<div id="pc-sched"></div>' +
        '<div style="margin-top:6px;text-align:right"><button class="reset" id="pc-resetaudio">Reset custom audio to defaults</button></div>' +
        '<div class="act"><button class="arm" id="pc-arm"></button><button class="stop" id="pc-stop">Stop</button></div>' +
        '<p class="note">Tip: leave this tab open on an always-on screen (desktop/TV) for reliable playback. Phones may pause it when locked or backgrounded. Settings &amp; uploaded audio are stored in this browser only. <a href="#cp-homeunit" id="pc-homeunit-link" style="color:#D4AF37;text-decoration:underline">The CastAdhan home unit</a> does all of this on real speakers — even when every phone in the house is asleep.</p>' +
      '</div>';
    document.body.appendChild(modal);

    chip.addEventListener('click', openModal);
    modal.addEventListener('click', function (e) { if (e.target === modal) modal.style.display = 'none'; });
    modal.querySelector('#pc-vol').addEventListener('input', function (e) { cfg.volume = +e.target.value; saveCfg(); });
    modal.querySelector('#pc-qs').addEventListener('change', function (e) { cfg.quietStart = e.target.value; saveCfg(); });
    modal.querySelector('#pc-qe').addEventListener('change', function (e) { cfg.quietEnd = e.target.value; saveCfg(); });
    GROUPS.forEach(function (g) {
      modal.querySelector('input[data-g="' + g.id + '"]').addEventListener('change', function (e) { cfg.groups[g.id] = e.target.checked; saveCfg(); renderSched(); tick(); });
      modal.querySelector('select[data-n="' + g.id + '"]').addEventListener('change', function (e) { cfg.night[g.id] = e.target.value; saveCfg(); });
      modal.querySelector('button[data-test="' + g.id + '"]').addEventListener('click', function () { unlock(); var ev = EVENTS.filter(function (x) { return x.group === g.id; })[0]; if (ev) play(ev.sound, 'Test: ' + ev.label, cfg.volume, g.id); });
      var fileInput = modal.querySelector('input[data-file="' + g.id + '"]');
      modal.querySelector('button[data-cust="' + g.id + '"]').addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', function (e) { var f = e.target.files && e.target.files[0]; if (f) { setCustom(g.id, f); updateCustBtns(); banner('Saved your audio for ' + g.label); } });
      if (g.timed) modal.querySelector('input[data-gt="' + g.id + '"]').addEventListener('change', function (e) { cfg.times[g.id] = e.target.value; saveCfg(); renderSched(); tick(); });
    });
    modal.querySelector('#pc-resetaudio').addEventListener('click', function () { idbClear(function () { Object.keys(customURL).forEach(function (k) { try { URL.revokeObjectURL(customURL[k]); } catch (e) {} }); customURL = {}; updateCustBtns(); banner('Custom audio cleared'); }); });
    modal.querySelector('#pc-arm').addEventListener('click', function () { setArmed(!cfg.armed); syncModal(); });
    modal.querySelector('#pc-stop').addEventListener('click', stopAll);
    var huLink = modal.querySelector('#pc-homeunit-link');
    if (huLink) huLink.addEventListener('click', function (e) {
      e.preventDefault(); modal.style.display = 'none';
      if (window.__cpGoHomeUnit) window.__cpGoHomeUnit();
      else { var s = document.getElementById('cp-homeunit'); if (s) s.scrollIntoView(); }
    });

    setArmed(cfg.armed);
  }
  function syncModal() {
    modal.querySelector('#pc-vol').value = cfg.volume;
    modal.querySelector('#pc-qs').value = cfg.quietStart;
    modal.querySelector('#pc-qe').value = cfg.quietEnd;
    GROUPS.forEach(function (g) {
      modal.querySelector('input[data-g="' + g.id + '"]').checked = !!cfg.groups[g.id];
      var ns = modal.querySelector('select[data-n="' + g.id + '"]'); if (ns) ns.value = (cfg.night && cfg.night[g.id]) || 'play';
      if (g.timed) { var ev = EVENTS.filter(function (x) { return x.id === g.timed; })[0]; modal.querySelector('input[data-gt="' + g.id + '"]').value = cfg.times[g.id] || (ev && ev.time) || ''; }
    });
    updateCustBtns(); renderSched();
    var arm = modal.querySelector('#pc-arm'); arm.textContent = cfg.armed ? '🔊 Adhan is ON — tap to turn off' : '🔊 Enable adhan on this device';
    arm.style.background = cfg.armed ? '#234d23' : '#D4AF37'; arm.style.color = cfg.armed ? '#bff0bf' : '#1a1a1a';
  }
  function openModal() { syncModal(); modal.style.display = 'flex'; }

  /* ---- boot -------------------------------------------------------------- */
  function boot() {
    buildUI(); setInterval(tick, 10000);
    idbLoadAll(function () { updateCustBtns(); });
    fetchTimes().then(function (t) { renderNext(buildSchedule(t, new Date()), new Date()); renderSched(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();

  /* ---- test surface (headless verification only) ------------------------- */
  window.__player = { cfg: function () { return cfg; }, schedule: function () { return buildSchedule(lastTimes, new Date()); }, tickNow: tick, setArmed: setArmed };
})();
