/* =====================================================================
   scene.js — homepage atmosphere
   A slow field of luminous "ideas" that drift upward and, when near,
   remember each other (constellation lines): interconnected systems,
   ideas crossing the boundary between thought and thing.
   Vanilla, DPR-aware, pauses when hidden, honours reduced-motion.
   ===================================================================== */
(function () {
  const canvas = document.getElementById('scene');
  if (!canvas) return;
  const ctx = canvas.getContext('2d', { alpha: true });

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const coarse = window.matchMedia('(pointer: coarse)').matches;

  let W = 0, H = 0, DPR = 1;
  let nodes = [];
  const pointer = { x: 0.5, y: 0.45, tx: 0.5, ty: 0.45 };

  function seed() {
    // count scales with area, capped for performance
    const target = Math.min(84, Math.round((W * H) / 26000));
    nodes = [];
    for (let i = 0; i < target; i++) {
      nodes.push({
        x: Math.random(),
        y: Math.random(),
        r: 0.4 + Math.random() * 1.7,
        // gentle upward drift + faint lateral sway
        vx: (Math.random() - 0.5) * 0.00007,
        vy: -(0.00004 + Math.random() * 0.00012),
        tw: Math.random() * Math.PI * 2,      // twinkle phase
        tws: 0.006 + Math.random() * 0.012,   // twinkle speed
        depth: 0.3 + Math.random() * 0.7      // parallax depth
      });
    }
  }

  function resize() {
    DPR = Math.min(2, window.devicePixelRatio || 1);
    W = canvas.clientWidth;
    H = canvas.clientHeight;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    seed();
  }

  const LINK = 132;      // px distance to draw a link
  const LINK2 = LINK * LINK;

  function frame() {
    if (document.hidden) { raf = requestAnimationFrame(frame); return; }

    // ease pointer
    pointer.x += (pointer.tx - pointer.x) * 0.05;
    pointer.y += (pointer.ty - pointer.y) * 0.05;
    const px = (pointer.x - 0.5), py = (pointer.y - 0.5);

    ctx.clearRect(0, 0, W, H);

    // advance + project to px
    const pts = nodes;
    for (let i = 0; i < pts.length; i++) {
      const n = pts[i];
      n.x += n.vx; n.y += n.vy; n.tw += n.tws;
      // wrap
      if (n.y < -0.04) { n.y = 1.04; n.x = Math.random(); }
      if (n.x < -0.04) n.x = 1.04; else if (n.x > 1.04) n.x = -0.04;
      // parallax offset from pointer (nearer nodes move more)
      const ox = px * 26 * n.depth;
      const oy = py * 18 * n.depth;
      n._px = n.x * W + ox;
      n._py = n.y * H + oy;
    }

    // links
    ctx.lineWidth = 1;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i];
      for (let j = i + 1; j < pts.length; j++) {
        const b = pts[j];
        const dx = a._px - b._px, dy = a._py - b._py;
        const d2 = dx * dx + dy * dy;
        if (d2 < LINK2) {
          const t = 1 - d2 / LINK2;
          ctx.strokeStyle = 'rgba(217,180,90,' + (t * 0.14).toFixed(3) + ')';
          ctx.beginPath();
          ctx.moveTo(a._px, a._py);
          ctx.lineTo(b._px, b._py);
          ctx.stroke();
        }
      }
    }

    // nodes
    for (let i = 0; i < pts.length; i++) {
      const n = pts[i];
      const tw = 0.55 + 0.45 * Math.sin(n.tw);
      const r = n.r * (0.9 + 0.25 * tw);
      const g = ctx.createRadialGradient(n._px, n._py, 0, n._px, n._py, r * 4);
      g.addColorStop(0, 'rgba(240,228,196,' + (0.9 * tw).toFixed(3) + ')');
      g.addColorStop(0.4, 'rgba(232,161,60,' + (0.30 * tw).toFixed(3) + ')');
      g.addColorStop(1, 'rgba(232,161,60,0)');
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(n._px, n._py, r * 4, 0, Math.PI * 2);
      ctx.fill();
    }

    raf = requestAnimationFrame(frame);
  }

  function paintStatic() {
    resize();
    // one still frame
    ctx.clearRect(0, 0, W, H);
    for (const n of nodes) {
      const x = n.x * W, y = n.y * H;
      const g = ctx.createRadialGradient(x, y, 0, x, y, n.r * 4);
      g.addColorStop(0, 'rgba(240,228,196,.8)');
      g.addColorStop(1, 'rgba(232,161,60,0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(x, y, n.r * 4, 0, Math.PI * 2); ctx.fill();
    }
  }

  let raf;
  function start() {
    resize();
    window.addEventListener('resize', debounce(resize, 180), { passive: true });
    if (reduce) { paintStatic(); return; }
    if (!coarse) {
      window.addEventListener('pointermove', (e) => {
        pointer.tx = e.clientX / window.innerWidth;
        pointer.ty = e.clientY / window.innerHeight;
      }, { passive: true });
    }
    raf = requestAnimationFrame(frame);
  }

  function debounce(fn, ms) {
    let t; return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  if (document.readyState !== 'loading') start();
  else document.addEventListener('DOMContentLoaded', start);
})();
