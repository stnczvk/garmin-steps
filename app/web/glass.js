// Wspólny skrypt zakładek centrum (ładowany w <head>, przed skryptami strony)
(function () {
  // 1) Oszczędzanie: zakładka ukryta w centrum (albo apka w tle) nie odświeża danych i nie renderuje 3D.
  window.CENTRUM_ACTIVE = true;
  const pending = new Map();  // jedna zaległa akcja na funkcję (inaczej po powrocie do karty odpalało się ich setki naraz)
  addEventListener('message', e => {
    if (e.origin !== location.origin || !e.data || typeof e.data.active !== 'boolean') return;
    window.CENTRUM_ACTIVE = e.data.active;
    if (e.data.active) flush();
  });
  const live = () => window.CENTRUM_ACTIVE !== false && !document.hidden;
  function flush() { const L = [...pending.values()]; pending.clear(); L.forEach((f, i) => setTimeout(() => { try { f(); } catch (x) {} }, i * 150)); }
  document.addEventListener('visibilitychange', () => { if (live()) flush(); });
  const si = window.setInterval.bind(window);
  window.setInterval = (fn, ms, ...a) => typeof fn !== 'function' ? si(fn, ms, ...a)
    : si(() => { if (live()) fn(...a); else pending.set(fn, () => fn(...a)); }, ms);

  // 1b) Animacje 3D: na telefonie max ~30 kl./s, a w ukrytej zakładce ~2 kl./s (zamiast 60 kl./s w tle)
  const rafN = window.requestAnimationFrame.bind(window), lastT = new Map();
  const slow = matchMedia('(hover: none)').matches || innerWidth < 760;
  window.requestAnimationFrame = cb => rafN(function w(t) {
    const gap = !live() ? 500 : slow ? 31 : 0, l = lastT.get(cb) || 0;
    if (gap && t - l < gap) return rafN(w);
    lastT.set(cb, t); cb(t);
  });

  // 2) Liquid Glass v2 – światło na szkle podąża za kursorem
  if (matchMedia('(hover: none)').matches) return;
  let raf = 0, ev = null;
  addEventListener('pointermove', e => {
    ev = e;
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      const el = ev.target.closest && ev.target.closest('.glass,.card,.kpi,.side');
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty('--mx', (ev.clientX - r.left) + 'px');
      el.style.setProperty('--my', (ev.clientY - r.top) + 'px');
    });
  }, { passive: true });
})();
