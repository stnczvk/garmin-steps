// Wspólny skrypt zakładek centrum (ładowany w <head>, przed skryptami strony)
(function () {
  // 1) Oszczędzanie: zakładka ukryta w centrum (albo apka w tle) nie odświeża danych i nie renderuje 3D.
  window.CENTRUM_ACTIVE = true;
  const pending = new Set();
  addEventListener('message', e => {
    if (e.origin !== location.origin || !e.data || typeof e.data.active !== 'boolean') return;
    window.CENTRUM_ACTIVE = e.data.active;
    if (e.data.active) { pending.forEach(f => { try { f(); } catch (x) {} }); pending.clear(); }
  });
  const live = () => window.CENTRUM_ACTIVE !== false && !document.hidden;
  document.addEventListener('visibilitychange', () => { if (live()) { pending.forEach(f => { try { f(); } catch (x) {} }); pending.clear(); } });
  const si = window.setInterval.bind(window);
  window.setInterval = (fn, ms, ...a) => typeof fn !== 'function' ? si(fn, ms, ...a)
    : si(() => { if (live()) fn(...a); else pending.add(() => fn(...a)); }, ms);

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
