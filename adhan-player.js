/* ============================================================================
 * CastAdhan — device-speaker player (website edition)
 * ----------------------------------------------------------------------------
 * Mirrors the appliance's audio, but plays through the SPEAKERS OF THE DEVICE
 * showing this page (phone / laptop / TV) instead of casting to Nest speakers.
 *
 * It rides on top of prayer-shim.js (which already supplies /api/state with the
 * chosen city's prayer_times). Here we add: a client-side scheduler that fires
 * the right audio at each prayer/event time, a one-tap "arm" (browsers block
 * auto-play sound until a user gesture), quiet hours, volume, a Screen Wake Lock
 * to keep the page alive, no-replay tracking, and a settings panel.
 *
 * HARD LIMITS (browser, not bugs): only runs while the tab is open & the device
 * awake; cannot reach external Nest/Cast speakers. See the feature table.
 * ========================================================================== */
(function () {
  'use strict';

  /* ---- audio sources: all already-public (repo audio + surah release assets) */
  var RAW = 'https://raw.githubusercontent.com/sabreenaapa-coder/castadhan-portable/main/audio/';
  var REL = 'https://github.com/sabreenaapa-coder/castadhan-portable/releases/download/audio-pack-v1/';
  var URLS = {
    adhan:         RAW + 'adhan.mp3',
    fajr_warning:  RAW + 'fajr_warning.mp3',
    dhuhr_warning: RAW + 'dhuhr_warning.mp3',
    asr_warning:   RAW + 'asr_warning.mp3',
    maghrib_warning: RAW + 'maghrib_warning.mp3',
    morning_dhikr: RAW + 'morning_dhikr.mp3',
    evening_dhikr: RAW + 'evening_dhikr.mp3',
    surah_kahf:    RAW + 'surah_kahf.mp3',
    friday_dua:    RAW + 'friday_prayer_BARRY_DUA.mp3',
    wakeup:        RAW + 'wakey_wakey.mp3',
    suhoor:        RAW + 'suhoor_alarm.mp3',
    takbeeraat:    RAW + 'takbeeraat.mp3',
    surah_baqarah: REL + 'surah_baqarah.mp3',
    surah_yasin:   REL + 'surah_yasin.mp3',
    surah_mulk:    REL + 'surah_mulk.mp3',
    surah_waqiah:  REL + 'surah_waqiah.mp3',
    surah_sajdah:  REL + 'surah_sajdah.mp3'
  };

  /* ---- event catalogue (data-driven). anchor: a prayer name or 'time'.
   *      offset: minutes from the anchor (negative = before). day: 0=Sun..6=Sat. */
  var EVENTS = [
    { id: 'adhan_fajr',    group: 'adhan',    label: 'Fajr adhan',    anchor: 'Fajr',    offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_dhuhr',   group: 'adhan',    label: 'Dhuhr adhan',   anchor: 'Dhuhr',   offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_asr',     group: 'adhan',    label: 'Asr adhan',     anchor: 'Asr',     offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_maghrib', group: 'adhan',    label: 'Maghrib adhan', anchor: 'Maghrib', offset: 0, sound: 'adhan', core: true },
    { id: 'adhan_isha',    group: 'adhan',    label: 'Isha adhan',    anchor: 'Isha',    offset: 0, sound: 'adhan', core: true },
    { id: 'warn_fajr',     group: 'warnings', label: 'Fajr ending (pre-sunrise)', anchor: 'Sunrise', offset: -5,  sound: 'fajr_warning' },
    { id: 'warn_dhuhr',    group: 'warnings', label: 'Dhuhr ending', anchor: 'Asr',     offset: -10, sound: 'dhuhr_warning' },
    { id: 'warn_maghrib',  group: 'warnings', label: 'Maghrib soon', anchor: 'Maghrib', offset: -10, sound: 'maghrib_warning' },
    { id: 'morning_dhikr', group: 'morning_dhikr', label: 'Morning dhikr', anchor: 'time', time: '07:00', sound: 'morning_dhikr', notDay: 5 },
    { id: 'friday_kahf',   group: 'friday_kahf',   label: 'Surah al-Kahf (Fri)', anchor: 'time', time: '07:00', sound: 'surah_kahf', day: 5 },
    { id: 'evening_dhikr', group: 'evening_dhikr', label: 'Evening dhikr', anchor: 'Maghrib', offset: 30, sound: 'evening_dhikr', cutoff: '21:30' },
    { id: 'wakeup',        group: 'wakeup',   label: 'Wake-up alarm', anchor: 'time', time: '06:30', sound: 'wakeup' },
    { id: 'suhoor',        group: 'suhoor',   label: 'Suhoor alarm',  anchor: 'Fajr', offset: -40, sound: 'suhoor' },
    { id: 'surah_mulk',    group: 'quran',    label: 'Surah al-Mulk',    anchor: 'time', time: '22:00', sound: 'surah_mulk' },
    { id: 'surah_baqarah', group: 'quran',    label: 'Surah al-Baqarah', anchor: 'time', time: '10:00', sound: 'surah_baqarah' },
    { id: 'surah_waqiah',  group: 'quran',    label: "Surah al-Waqi'ah", anchor: 'time', time: '17:00', sound: 'surah_waqiah' },
    { id: 'surah_yasin',   group: 'quran',    label: 'Surah Yasin (Thu)', anchor: 'Maghrib', offset: 15, sound: 'surah_yasin', day: 4 },
    { id: 'surah_sajdah',  group: 'quran',    label: 'Surah as-Sajdah (Fri)', anchor: 'Fajr', offset: 15, sound: 'surah_sajdah', day: 5 }
  ];
  var GROUPS = [
    { id: 'adhan',         label: 'Adhan (5 prayers)' },
    { id: 'warnings',      label: 'Pre-prayer reminders' },
    { id: 'morning_dhikr', label: 'Morning dhikr' },
    { id: 'friday_kahf',   label: 'Surah al-Kahf (Fridays)' },
    { id: 'evening_dhikr', label: 'Evening dhikr' },
    { id: 'quran',         label: "Qur'an programs" },
    { id: 'wakeup',        label: 'Wake-up alarm' },
    { id: 'suhoor',        label: 'Suhoor alarm (Ramadan)' }
  ];

  /* ---- settings (per-browser) -------------------------------------------- */
  var DEFAULTS = {
    armed: false,
    volume: 0.85,
    quietStart: '22:00', quietEnd: '07:00',
    groups: { adhan: true, warnings: false, morning_dhikr: false, friday_kahf: false,
              evening_dhikr: false, quran: false, wakeup: false, suhoor: false }
  };
  var cfg;
  function loadCfg() {
    try {
      var s = JSON.parse(localStorage.getItem('pc_player_cfg'));
      cfg = Object.assign({}, DEFAULTS, s || {});
      cfg.groups = Object.assign({}, DEFAULTS.groups, (s && s.groups) || {});
    } catch (e) { cfg = JSON.parse(JSON.stringify(DEFAULTS)); }
  }
  function saveCfg() { try { localStorage.setItem('pc_player_cfg', JSON.stringify(cfg)); } catch (e) {} }
  loadCfg();

  /* ---- no-replay tracking (per local day) -------------------------------- */
  function todayStr() { var d = new Date(); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); }
  var firedKey = 'pc_player_fired';
  function getFired() { try { var o = JSON.parse(localStorage.getItem(firedKey)); return (o && o.day === todayStr()) ? o.ids : []; } catch (e) { return []; } }
  function markFired(id) { var ids = getFired(); ids.push(id); try { localStorage.setItem(firedKey, JSON.stringify({ day: todayStr(), ids: ids })); } catch (e) {} }

  /* ---- audio element + autoplay unlock + wake lock ----------------------- */
  var media = new Audio(); media.preload = 'none';
  var unlocked = false, wakeLock = null;
  function unlock() {
    // play+pause a muted blip on the user gesture so later programmatic plays work
    try { media.muted = true; media.src = URLS.adhan; var p = media.play();
      if (p && p.then) p.then(function () { media.pause(); media.currentTime = 0; media.muted = false; unlocked = true; }).catch(function () { media.muted = false; });
    } catch (e) {}
  }
  function requestWake() {
    try { if (navigator.wakeLock && cfg.armed) navigator.wakeLock.request('screen').then(function (w) { wakeLock = w; }).catch(function () {}); } catch (e) {}
  }
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible' && cfg.armed) requestWake(); });

  /* ---- playback (honours the silent-test hook so logic can be verified
   *      WITHOUT ever sounding the adhan during development) --------------- */
  function play(sound, label) {
    if (window.__playerSilent) { (window.__playerLog = window.__playerLog || []).push({ t: new Date().toISOString(), sound: sound, label: label }); banner(label + ' (silent-test)'); return; }
    try { media.pause(); } catch (e) {}
    media.src = URLS[sound] || URLS.adhan;
    media.volume = Math.max(0, Math.min(1, cfg.volume));
    media.muted = false;
    var p = media.play();
    if (p && p.catch) p.catch(function () { banner('⚠️ Tap “Enable” first to allow sound'); });
    banner('▶ ' + label);
  }
  function stopAll() { try { media.pause(); media.currentTime = 0; } catch (e) {} banner('■ Stopped'); }

  /* ---- time helpers ------------------------------------------------------ */
  function hhmmToToday(hhmm, base) { var m = String(hhmm || '').match(/(\d{1,2}):(\d{2})/); if (!m) return null; var d = new Date(base); d.setHours(+m[1], +m[2], 0, 0); return d; }
  function inQuiet(now) {
    var s = hhmmToToday(cfg.quietStart, now), e = hhmmToToday(cfg.quietEnd, now); if (!s || !e) return false;
    return (s <= e) ? (now >= s && now < e) : (now >= s || now < e);   // wraps midnight
  }

  /* ---- build today's schedule from the chosen city's prayer times -------- */
  var lastTimes = {};
  function fetchTimes() { return fetch('/api/state').then(function (r) { return r.json(); }).then(function (s) { lastTimes = s.prayer_times || lastTimes; return lastTimes; }).catch(function () { return lastTimes; }); }
  function buildSchedule(times, now) {
    var out = [];
    EVENTS.forEach(function (ev) {
      if (!cfg.groups[ev.group]) return;
      if (ev.day != null && now.getDay() !== ev.day) return;
      if (ev.notDay != null && now.getDay() === ev.notDay) return;
      var when;
      if (ev.anchor === 'time') { when = hhmmToToday(ev.time, now); }
      else { var base = hhmmToToday(times[ev.anchor], now); if (!base) return; when = new Date(base.getTime() + (ev.offset || 0) * 60000); }
      if (!when) return;
      if (ev.cutoff) { var c = hhmmToToday(ev.cutoff, now); if (c && when > c) return; }
      out.push({ id: ev.id, label: ev.label, sound: ev.sound, core: !!ev.core, when: when });
    });
    out.sort(function (a, b) { return a.when - b.when; });
    return out;
  }

  /* ---- the scheduler tick ------------------------------------------------ */
  function tick() {
    if (!cfg.armed) return;
    var now = new Date();
    fetchTimes().then(function (times) {
      var sched = buildSchedule(times, now), fired = getFired();
      sched.forEach(function (e) {
        if (fired.indexOf(e.id) >= 0) return;
        var dt = now - e.when;
        if (dt >= 0 && dt < 5 * 60000) {            // due within the last 5 min (don't replay stale events on a late arm)
          markFired(e.id);
          if (inQuiet(now) && !e.core) { banner('🔇 ' + e.label + ' — quiet hours'); return; }
          play(e.sound, e.label);
        }
      });
      renderNext(sched, now);
    });
  }

  /* ---- tiny UI: arm chip, settings modal, now/next banner ---------------- */
  var chip, modal, bannerEl, nextEl;
  function banner(msg) { if (bannerEl) { bannerEl.textContent = msg; bannerEl.style.opacity = '1'; clearTimeout(banner._t); banner._t = setTimeout(function () { bannerEl.style.opacity = '0'; }, 6000); } }
  function fmt(d) { return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
  function renderNext(sched, now) {
    if (!nextEl) return;
    var up = sched.filter(function (e) { return e.when > now && getFired().indexOf(e.id) < 0; })[0];
    nextEl.textContent = cfg.armed ? (up ? ('Next on this device: ' + up.label + ' · ' + fmt(up.when)) : 'Armed — no more events today') : '';
  }
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
      '#pc-adhan-modal .box{background:#121212;border:1px solid #3a3422;border-radius:16px;padding:20px 22px;width:min(420px,92vw);max-height:88vh;overflow:auto;color:#e7e0cf;font:14px system-ui,sans-serif;box-shadow:0 8px 40px rgba(0,0,0,.6)}' +
      '#pc-adhan-modal h3{color:#D4AF37;font-size:17px;font-weight:600;margin:0 0 4px}' +
      '#pc-adhan-modal .sub{color:#9a958a;font-size:12px;margin:0 0 14px}' +
      '#pc-adhan-modal label.row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid #1f1f1f}' +
      '#pc-adhan-modal .row .t{display:flex;gap:8px;align-items:center}' +
      '#pc-adhan-modal button.test{background:#1c1c1c;border:.5px solid #333;color:#cfcabb;padding:3px 9px;border-radius:7px;cursor:pointer;font-size:11px}' +
      '#pc-adhan-modal input[type=time],#pc-adhan-modal input[type=range]{accent-color:#D4AF37}' +
      '#pc-adhan-modal .act{display:flex;gap:10px;justify-content:space-between;margin-top:16px}' +
      '#pc-adhan-modal .arm{flex:1;border:none;border-radius:9px;padding:11px;font-weight:700;cursor:pointer;background:#D4AF37;color:#1a1a1a}' +
      '#pc-adhan-modal .stop{border:.5px solid #5a2a2a;background:#1c1414;color:#e0857a;border-radius:9px;padding:11px 16px;cursor:pointer;font-weight:600}' +
      '#pc-adhan-modal .note{color:#8a857a;font-size:11px;line-height:1.5;margin-top:12px}';
    document.head.appendChild(css);

    chip = document.createElement('button'); chip.id = 'pc-adhan-chip'; document.body.appendChild(chip);
    nextEl = document.createElement('div'); nextEl.id = 'pc-adhan-next'; document.body.appendChild(nextEl);
    bannerEl = document.createElement('div'); bannerEl.id = 'pc-adhan-banner'; document.body.appendChild(bannerEl);

    modal = document.createElement('div'); modal.id = 'pc-adhan-modal';
    var groupRows = GROUPS.map(function (g) {
      return '<label class="row"><span class="t"><input type="checkbox" data-g="' + g.id + '"> ' + g.label + '</span>' +
             '<button class="test" data-test="' + g.id + '">test</button></label>';
    }).join('');
    modal.innerHTML =
      '<div class="box">' +
        '<h3>Adhan on this device</h3>' +
        '<p class="sub">Plays through <b>this device’s</b> speakers while this page stays open. It can’t reach Nest/Cast speakers.</p>' +
        '<label class="row"><span class="t">Volume</span><input type="range" id="pc-vol" min="0" max="1" step="0.05" style="width:150px"></label>' +
        '<label class="row"><span class="t">Quiet hours (only adhan plays)</span><span><input type="time" id="pc-qs" style="background:#0c0c0c;color:#e7e0cf;border:1px solid #2d2d2d;border-radius:6px;padding:3px"> – <input type="time" id="pc-qe" style="background:#0c0c0c;color:#e7e0cf;border:1px solid #2d2d2d;border-radius:6px;padding:3px"></span></label>' +
        '<div style="margin:12px 0 4px;color:#9a958a;font-size:12px">What to play</div>' +
        groupRows +
        '<div class="act"><button class="arm" id="pc-arm"></button><button class="stop" id="pc-stop">Stop</button></div>' +
        '<p class="note">Tip: leave this tab open on an always-on screen (desktop/TV) for reliable playback. Phones may pause it when locked or in the background. Settings are saved in this browser only.</p>' +
      '</div>';
    document.body.appendChild(modal);

    chip.addEventListener('click', openModal);
    modal.addEventListener('click', function (e) { if (e.target === modal) modal.style.display = 'none'; });
    modal.querySelector('#pc-vol').addEventListener('input', function (e) { cfg.volume = +e.target.value; saveCfg(); });
    modal.querySelector('#pc-qs').addEventListener('change', function (e) { cfg.quietStart = e.target.value; saveCfg(); });
    modal.querySelector('#pc-qe').addEventListener('change', function (e) { cfg.quietEnd = e.target.value; saveCfg(); });
    GROUPS.forEach(function (g) {
      modal.querySelector('input[data-g="' + g.id + '"]').addEventListener('change', function (e) { cfg.groups[g.id] = e.target.checked; saveCfg(); tick(); });
      modal.querySelector('button[data-test="' + g.id + '"]').addEventListener('click', function () {
        unlock(); var ev = EVENTS.filter(function (x) { return x.group === g.id; })[0]; if (ev) play(ev.sound, 'Test: ' + ev.label);
      });
    });
    modal.querySelector('#pc-arm').addEventListener('click', function () { setArmed(!cfg.armed); syncModal(); });
    modal.querySelector('#pc-stop').addEventListener('click', stopAll);

    setArmed(cfg.armed);   // reflect saved state in the chip
  }
  function syncModal() {
    modal.querySelector('#pc-vol').value = cfg.volume;
    modal.querySelector('#pc-qs').value = cfg.quietStart;
    modal.querySelector('#pc-qe').value = cfg.quietEnd;
    GROUPS.forEach(function (g) { modal.querySelector('input[data-g="' + g.id + '"]').checked = !!cfg.groups[g.id]; });
    var arm = modal.querySelector('#pc-arm'); arm.textContent = cfg.armed ? '🔊 Adhan is ON — tap to turn off' : '🔊 Enable adhan on this device';
    arm.style.background = cfg.armed ? '#234d23' : '#D4AF37'; arm.style.color = cfg.armed ? '#bff0bf' : '#1a1a1a';
  }
  function openModal() { syncModal(); modal.style.display = 'flex'; }

  /* ---- boot -------------------------------------------------------------- */
  function boot() { buildUI(); setInterval(tick, 10000); fetchTimes().then(function (t) { renderNext(buildSchedule(t, new Date()), new Date()); }); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();

  /* ---- test surface (used only for headless logic verification) ---------- */
  window.__player = { cfg: function () { return cfg; }, schedule: function () { return buildSchedule(lastTimes, new Date()); }, tickNow: tick, setArmed: setArmed };
})();
