/* Most między dziennikiem a własnym serwerem: baza, AI, pobieranie plików, Garmin. */
(() => {
  const clone = v => v == null ? v : JSON.parse(JSON.stringify(v));
  const err = (msg, code) => Object.assign(new Error(msg), { code });
  let loginShown = false;

  function showLogin(msg) {
    if (loginShown) return; loginShown = true;
    const go = () => {
      const d = document.createElement("div");
      d.id = "dz-login";
      d.innerHTML = `<form style="position:fixed;inset:0;z-index:9999;background:#0D1015;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;padding:24px;font-family:system-ui,-apple-system,sans-serif;color:#EEF1F5">
        <div style="font-size:28px;font-weight:800;letter-spacing:.02em">DZIENNIK TRENINGOWY</div>
        <input id="dz-pw" type="password" autocomplete="current-password" placeholder="Hasło" style="width:min(320px,90vw);font-size:17px;padding:14px;border-radius:12px;border:1px solid #2A313C;background:#202630;color:#EEF1F5">
        <button style="width:min(320px,90vw);font-size:17px;font-weight:700;padding:14px;border:0;border-radius:12px;background:#C8F560;color:#0D1015">Zaloguj</button>
        <div id="dz-msg" style="color:#FF6B5A;min-height:20px;font-size:14px">${msg || ""}</div></form>`;
      document.body.appendChild(d);
      const f = d.querySelector("form");
      setTimeout(() => d.querySelector("#dz-pw").focus(), 100);
      f.addEventListener("submit", async e => {
        e.preventDefault();
        const r = await fetch("/api/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ password: d.querySelector("#dz-pw").value }) }).catch(() => null);
        if (r && r.ok) location.reload();
        else d.querySelector("#dz-msg").textContent = r ? ((await r.json().catch(() => ({}))).error || "Nie udało się.") : "Brak połączenia z serwerem.";
      });
    };
    document.body ? go() : document.addEventListener("DOMContentLoaded", go);
  }

  async function req(method, url, body, isForm) {
    let r;
    try {
      r = await fetch("/api" + url, { method, credentials: "same-origin", headers: body && !isForm ? { "content-type": "application/json" } : {}, body: body ? (isForm ? body : JSON.stringify(body)) : undefined });
    } catch (e) { throw err("Brak połączenia z serwerem.", "network"); }
    if (r.status === 401) { showLogin(); throw err("Zaloguj się.", "auth"); }
    if (!r.ok) { const j = await r.json().catch(() => ({})); throw err(j.error || ("Błąd " + r.status), j.code || (r.status === 429 ? "rate_limited" : "http")); }
    return r.status === 204 ? null : r.json();
  }

  /* ---------- baza ---------- */
  const cache = new Map(), docL = new Map(), colL = new Map();
  let since = 0, readyRes, polling = null;
  const ready = new Promise(r => readyRes = r);
  const snapDoc = p => { const v = cache.get(p); return { id: p.split("/").pop(), exists: v !== undefined, data: () => clone(v) }; };
  const snapCol = c => ({ docs: [...cache.keys()].filter(k => k.startsWith(c + "/")).sort().map(snapDoc) });
  function emit(paths) {
    const cols = new Set(paths.map(p => p.split("/")[0]));
    for (const p of paths) for (const cb of docL.get(p) || []) try { cb(snapDoc(p)) } catch (e) { console.error(e) }
    for (const c of cols) for (const cb of colL.get(c) || []) try { cb(snapCol(c)) } catch (e) { console.error(e) }
  }
  async function pull() {
    const r = await req("GET", "/sync?since=" + since);
    const ch = [];
    for (const d of r.docs) {
      const old = cache.get(d.path);
      if (d.data == null) { if (old !== undefined) { cache.delete(d.path); ch.push(d.path) } }
      else if (JSON.stringify(old) !== JSON.stringify(d.data)) { cache.set(d.path, d.data); ch.push(d.path) }
    }
    since = r.now;
    if (ch.length) emit(ch);
  }
  async function resync() { since = 0; const old = [...cache.keys()]; cache.clear(); await pull(); emit(old.filter(k => !cache.has(k))); }
  function startPolling() {
    if (polling) return;
    polling = setInterval(() => { if (document.visibilityState === "visible") pull().catch(() => {}) }, 5000);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") pull().catch(() => {}) });
    window.addEventListener("online", () => pull().catch(() => {}));
  }
  const add = (m, k, cb) => { if (!m.has(k)) m.set(k, new Set()); m.get(k).add(cb); return () => m.get(k).delete(cb); };
  const db = Object.freeze({
    doc(path) {
      return {
        async set(v) {
          const prev = cache.get(path); cache.set(path, clone(v)); emit([path]);
          try { await req("PUT", "/doc/" + path, v) } catch (e) { if (prev === undefined) cache.delete(path); else cache.set(path, prev); emit([path]); throw e }
        },
        async update(v) { await this.set({ ...(cache.get(path) || {}), ...v }) },
        async delete() {
          const prev = cache.get(path); cache.delete(path); emit([path]);
          try { await req("DELETE", "/doc/" + path) } catch (e) { if (prev !== undefined) { cache.set(path, prev); emit([path]) } throw e }
        },
        async get() { await ready; return snapDoc(path) },
        onSnapshot(cb) { const off = add(docL, path, cb); ready.then(() => cb(snapDoc(path))); return off; }
      };
    },
    collection(c) {
      return { onSnapshot(cb) { const off = add(colL, c, cb); ready.then(() => cb(snapCol(c))); return off; } };
    }
  });

  /* ---------- AI ---------- */
  const sample = Object.freeze({
    async json(prompt, opts = {}) {
      const fd = new FormData();
      fd.append("prompt", prompt); fd.append("tier", opts.modelTier || "default");
      for (const f of opts.images || []) fd.append("files", f, f.name || "obraz.jpg");
      const r = await req("POST", "/ai", fd, true);
      return r.json;
    },
    async limits() { return { images: { mediaTypes: ["image/jpeg", "image/png", "image/webp", "image/gif"], maxCount: 5 } }; }
  });

  /* ---------- pobieranie plików ---------- */
  const downloads = Object.freeze({
    async save({ filename, data }) {
      const u = URL.createObjectURL(new Blob([data], { type: "application/json" }));
      const a = document.createElement("a"); a.href = u; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(u), 5000);
    }
  });

  /* ---------- Garmin (zamiast zadań Claude) ---------- */
  const mcp = Object.freeze({
    async callTool(server, tool) {
      if (tool === "fire_trigger") return req("POST", "/garmin/refresh", {});
      throw err("Nieznane narzędzie", "tool_error");
    }
  });


  /* ---------- powiadomienia na telefon ---------- */
  const b64 = s => { const p = "=".repeat((4 - s.length % 4) % 4); const r = atob((s + p).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); };
  const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  let pushMsg = "";
  async function pushState() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) return standalone() ? "unsupported" : "install";
    if (Notification.permission === "denied") return "denied";
    const reg = await navigator.serviceWorker.ready; const sub = await reg.pushManager.getSubscription();
    return sub ? "on" : "off";
  }
  async function paintPush() {
    const el = document.getElementById("dz-push"); if (!el) return;
    const st = await pushState().catch(() => "unsupported");
    const txt = { on: "✅ Powiadomienia są włączone na tym telefonie.", off: "Powiadomienia są wyłączone na tym urządzeniu.",
      denied: "Powiadomienia są zablokowane. Włącz je w Ustawieniach telefonu → Powiadomienia → Dziennik.",
      install: "Na iPhonie powiadomienia działają tylko z ikony na ekranie głównym: w Safari kliknij Udostępnij → „Do ekranu początkowego”, otwórz Dziennik z ikony i wróć tutaj.",
      unsupported: "Ta przeglądarka nie obsługuje powiadomień." }[st];
    el.innerHTML = `<div class="nb-step" style="margin:0"><b>Powiadomienia na telefon</b><br>${txt}</div>
      <div class="row" style="gap:8px">${st === "off" ? `<button class="btn small" data-dzpush="on">🔔 Włącz powiadomienia</button>` : ""}${st === "on" ? `<button class="btn small" data-dzpush="test">Wyślij test</button><button class="btn ghost small" data-dzpush="off">Wyłącz</button>` : ""}</div>
      ${pushMsg ? `<div class="note" style="margin:0;font-weight:600">${pushMsg}</div>` : ""}`;
  }
  document.addEventListener("click", async ev => {
    const b = ev.target.closest("[data-dzpush]"); if (!b) return;
    const a = b.dataset.dzpush; b.disabled = true;
    try {
      const reg = await navigator.serviceWorker.ready;
      if (a === "on") {
        const perm = await Notification.requestPermission();
        if (perm !== "granted") { pushMsg = "Nie zezwolono na powiadomienia."; }
        else {
          const { key } = await req("GET", "/push/key");
          const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(key) });
          await req("POST", "/push/subscribe", sub.toJSON());
          pushMsg = "Włączone. Kliknij „Wyślij test”, żeby sprawdzić.";
        }
      } else if (a === "off") {
        const sub = await reg.pushManager.getSubscription();
        if (sub) { await req("POST", "/push/unsubscribe", { endpoint: sub.endpoint }).catch(() => {}); await sub.unsubscribe(); }
        pushMsg = "Wyłączone na tym urządzeniu.";
      } else if (a === "test") {
        const r = await req("POST", "/push/test", {});
        pushMsg = r.sent ? "Wysłano – powiadomienie powinno przyjść za kilka sekund." : "Nie udało się wysłać. Wyłącz i włącz powiadomienia jeszcze raz.";
      }
    } catch (e) { pushMsg = "Błąd: " + (e.message || e); }
    paintPush();
  });
  window.dzPush = { html: () => { setTimeout(paintPush, 0); return `<div id="dz-push" class="stack" style="gap:8px"></div>`; } };


  /* ---------- zużycie AI ---------- */
  async function paintAi() {
    const el = document.getElementById("dz-ai"); if (!el) return;
    try {
      const u = await req("GET", "/ai/usage");
      const usd = v => "$" + (v < 0.01 && v > 0 ? v.toFixed(3) : v.toFixed(2));
      el.innerHTML = `<div class="wk-grid">
        <div class="wk-t"><span class="label">Dziś</span><b class="num">${u.today.n} / ${u.limit}</b><span class="note num">zapytań · ${usd(u.today.cost)}</span></div>
        <div class="wk-t"><span class="label">Ten miesiąc</span><b class="num">${usd(u.month.cost)}</b><span class="note num">${u.month.n} zapytań</span></div></div>
        <p class="note" style="margin:0">AI czyta etykiety, zrzuty z Fitatu i szacuje posiłki. Koszt jest szacunkowy (według cennika Anthropic). Dokładne rozliczenie i saldo: platform.claude.com → Usage. Dzienny limit ${u.limit} zapytań chroni przed przypadkowym wydaniem kredytów.</p>`;
    } catch (e) { el.innerHTML = `<p class="note" style="margin:0">Nie udało się pobrać zużycia.</p>`; }
  }
  window.dzAi = { html: () => { setTimeout(paintAi, 0); return `<div class="card stack"><h2>Zużycie AI</h2><div id="dz-ai" class="stack" style="gap:10px"><p class="note" style="margin:0">Ładuję…</p></div></div>`; } };


  /* ---------- waga Xiaomi przez Zdrowie + Skróty ---------- */
  let scaleOpen = false;
  async function paintScale() {
    const el = document.getElementById("dz-scale"); if (!el) return;
    let info; try { info = await req("GET", "/hook/info") } catch (e) { el.innerHTML = `<p class="note" style="margin:0">Nie udało się pobrać ustawień.</p>`; return; }
    const last = info.last ? `Ostatni pomiar z wagi: <b>${String(info.last.kg).replace(".", ",")} kg</b> (${new Date(info.last.at).toLocaleString("pl-PL", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })})` : "Jeszcze nie przyszedł żaden pomiar z wagi.";
    el.innerHTML = `<div class="nb-step" style="margin:0">${last}</div>
      <div class="stack" style="gap:6px"><span class="label">Twój link dla Skrótów</span>
        <div class="row" style="gap:8px;flex-wrap:nowrap"><input id="dz-hookurl" readonly value="${info.url}" style="flex:1;min-width:0;font-size:13px"><button class="btn small" data-dzscale="copy">Kopiuj</button></div>
        <span class="note">Link działa jak hasło do wpisywania wagi – nie udostępniaj go.</span></div>
      <details ${scaleOpen ? "open" : ""} id="dz-scale-how"><summary class="note" style="cursor:pointer;font-weight:600">Jak ustawić (raz, ok. 3 minuty)</summary>
      <ol class="steps" style="margin-top:8px">
        <li><b>Xiaomi Home → Zdrowie:</b> Ustawienia iPhone'a → Zdrowie → Dostęp do danych i urządzenia → Xiaomi Home → włącz <b>Waga</b>.</li>
        <li>Otwórz aplikację <b>Skróty</b> → zakładka <b>Automatyzacja</b> → <b>+</b> → <b>Aplikacja</b> → wybierz <b>Xiaomi Home</b>, zaznacz <b>Jest zamknięta</b> (odznacz „Jest otwarta”) → <b>Uruchom natychmiast</b> → Dalej → <b>Nowy pusty skrót</b>.</li>
        <li>Dodaj akcję <b>Znajdź próbki zdrowia</b>: typ <b>Waga</b>, dodaj filtr <b>Data rozpoczęcia – jest dzisiaj</b>, sortuj <b>Data rozpoczęcia – od najnowszych</b>, <b>Ogranicz</b> do <b>1</b>.</li>
        <li>Dodaj akcję <b>Pobierz zawartość URL</b>: w miejsce adresu wklej link skopiowany wyżej. Rozwiń: <b>Metoda: POST</b>, <b>Treść żądania: JSON</b>, dodaj pole typu <b>Tekst</b>: klucz <b>kg</b>, wartość – wybierz zmienną <b>Próbki zdrowia</b>.</li>
        <li>Gotowe. Zważ się, otwórz Xiaomi Home, poczekaj aż pomiar się pojawi i zamknij aplikację – waga wpisze się sama do dziennika (Podsumowanie → Waga).</li>
      </ol>
      <p class="note" style="margin:0">Jeśli danego dnia nie było ważenia, skrót niczego nie wyśle. Kilka pomiarów jednego dnia – zostaje ostatni.</p></details>
      <div class="row" style="gap:8px"><button class="btn ghost small" data-dzscale="new">Zmień link (stary przestanie działać)</button></div>`;
    el.querySelector("#dz-scale-how").addEventListener("toggle", e => { scaleOpen = e.target.open });
  }
  document.addEventListener("click", async ev => {
    const b = ev.target.closest("[data-dzscale]"); if (!b) return;
    if (b.dataset.dzscale === "copy") { const i = document.getElementById("dz-hookurl"); try { await navigator.clipboard.writeText(i.value); b.textContent = "✓ Skopiowano" } catch (e) { i.select(); document.execCommand && document.execCommand("copy"); b.textContent = "✓ Skopiowano" } setTimeout(() => b.textContent = "Kopiuj", 2000); return; }
    if (b.dataset.dzscale === "new") { await req("POST", "/hook/newkey", {}); paintScale(); }
  });
  window.dzScale = { html: () => { setTimeout(paintScale, 0); return `<div class="card stack"><h2>Waga Xiaomi</h2><div id="dz-scale" class="stack" style="gap:10px"><p class="note" style="margin:0">Ładuję…</p></div></div>`; } };

  let authP = null;
  const auth = () => authP || (authP = fetch("/api/me", { credentials: "same-origin" }).then(r => { if (!r.ok) { showLogin(); return new Promise(() => {}) } }).catch(() => { showLogin("Brak połączenia z serwerem."); return new Promise(() => {}) }));
  let dbP = null;
  const initDb = () => dbP || (dbP = auth().then(async () => { await pull(); readyRes(); startPolling(); return db; }));

  window.claude = Object.freeze({
    async use(name) {
      if (name === "db") return initDb();
      await auth();
      return { sample, downloads, mcp }[name] || null;
    }
  });
  window.dzResync = resync;

  if ("serviceWorker" in navigator) window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
})();
