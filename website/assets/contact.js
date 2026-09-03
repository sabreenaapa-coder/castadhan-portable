/* =====================================================================
   contact.js — progressive contact-form handling.
   If the endpoint is still the placeholder, we don't pretend to send —
   we point the visitor at the dedicated email instead. Honeypot guarded.
   ===================================================================== */
(function () {
  var f = document.getElementById('contactForm');
  if (!f) return;
  var msg = document.getElementById('formMsg');
  var btn = f.querySelector('button.send');
  var PLACEHOLDER = 'REPLACE_WITH_FORM_ENDPOINT';

  f.addEventListener('submit', function (e) {
    if (f.company && f.company.value) { e.preventDefault(); return; } // honeypot

    if (f.action.indexOf(PLACEHOLDER) !== -1) {
      e.preventDefault();
      msg.className = 'form-msg err';
      msg.textContent = 'Contact channel not yet connected — use the email option for now.';
      return;
    }
    if (!f.checkValidity()) { return; } // native validation shows

    e.preventDefault();
    btn.disabled = true;
    msg.className = 'form-msg';
    msg.textContent = 'Sending…';

    fetch(f.action, {
      method: 'POST',
      headers: { 'Accept': 'application/json' },
      body: new FormData(f)
    }).then(function (r) {
      if (r.ok) {
        f.reset();
        msg.className = 'form-msg ok';
        msg.textContent = 'Received. I’ll reply to you directly.';
      } else { throw new Error('bad status'); }
    }).catch(function () {
      msg.className = 'form-msg err';
      msg.textContent = 'Something went wrong — please try the email option.';
    }).finally(function () { btn.disabled = false; });
  });
})();
