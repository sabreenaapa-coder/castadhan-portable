/* =====================================================================
   site.js — shared behaviour: reveal-on-scroll, year stamp, mail unveil.
   Kept tiny and defensive so every page can include it safely.
   ===================================================================== */
(function () {
  // reveal on scroll ---------------------------------------------------
  const items = document.querySelectorAll('.reveal');
  if (items.length && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
    items.forEach((el, i) => {
      // stagger without inline scripts
      const d = el.getAttribute('data-delay');
      if (d) el.style.transitionDelay = d + 'ms';
      io.observe(el);
    });
  } else {
    items.forEach((el) => el.classList.add('in'));
  }

  // year stamp ---------------------------------------------------------
  document.querySelectorAll('[data-year]').forEach((el) => {
    el.textContent = new Date().getFullYear();
  });

  // unveil a mailbox only on real interaction (defeats naive scrapers) --
  // markup: <button class="mail" data-user="hello" data-domain="mustafayunis.co.uk">…</button>
  document.querySelectorAll('.mail').forEach((btn) => {
    const build = () => btn.dataset.user + '@' + btn.dataset.domain;
    const reveal = () => {
      if (btn.dataset.open) return;
      btn.dataset.open = '1';
      const addr = build();
      const a = document.createElement('a');
      a.href = 'mailto:' + addr;
      a.textContent = addr;
      a.className = 'mail-addr';
      btn.replaceWith(a);
    };
    btn.addEventListener('click', reveal);
    btn.addEventListener('mouseenter', reveal, { once: true });
  });
})();
