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
        <li>Otwórz aplikację <b>Skróty</b> → zakładka <b>Automatyzacja</b> → <b>+</b> → <b>Aplikacja</b> → wybierz <b>Xiaomi Home</b> i ustaw <b>Zamknięto</b> (nie „Otwarto”). Przełącznik <b>Automatyzacja</b> ma być włączony – wtedy skrót uruchamia się sam, bez pytania.</li>
        <li>Dodaj akcję <b>Znajdź próbki zdrowia</b>: typ <b>Waga</b>, dodaj filtr <b>Data rozpoczęcia – jest dzisiaj</b>, sortuj <b>Data rozpoczęcia – od najnowszych</b>, <b>Ogranicz</b> do <b>1</b>.</li>
        <li>Dodaj akcję <b>Pobierz zawartość URL</b>: w miejsce adresu wklej link skopiowany wyżej. Rozwiń: <b>Metoda: POST</b>, <b>Treść żądania: JSON</b>, dodaj pole typu <b>Tekst</b>: po lewej (klucz) wpisz ręcznie <b>kg</b>, po prawej (wartość) wybierz zmienną <b>Próbki zdrowia</b>.</li>
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


  /* ---------- aktualizacja aplikacji z telefonu ---------- */
  let updPoll = null, updStarted = 0, updSeen = false;
  const fdt = d => d ? new Date(d).toLocaleString("pl-PL", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
  async function paintUpd() {
    const el = document.getElementById("dz-upd"); if (!el) return;
    let st; try { st = await req("GET", "/update/status") } catch (e) {
      if (updStarted) { el.innerHTML = `<p class="note" style="margin:0">Serwer się restartuje…</p>`; return; }
      el.innerHTML = `<p class="note" style="margin:0">Nie udało się sprawdzić wersji.</p>`; return; }
    const cur = st.current || {}, lat = st.latest;
    const fresh = lat && cur.sha && lat.sha === cur.sha;
    if (st.running) updSeen = true;
    if (updStarted && !st.running && fresh && (updSeen || Date.now() - updStarted > 15000)) { el.innerHTML = `<div class="nb-step" style="margin:0">✓ Zaktualizowano. Przeładowuję…</div>`; clearInterval(updPoll); updPoll = null; setTimeout(() => location.reload(), 1200); return; }
    if (updStarted && !st.running && Date.now() - updStarted > 20000 && !fresh) { updStarted = 0; clearInterval(updPoll); updPoll = null; }
    const busy = st.running || updStarted;
    el.innerHTML = `<div class="nb-step" style="margin:0">${busy ? "⏳ Aktualizuję… to trwa ok. 1–2 minuty, aplikacja sama się przeładuje." : !lat ? "Nie udało się sprawdzić, czy jest nowa wersja." : fresh ? "✓ Masz najnowszą wersję." : "<b>Jest nowa wersja.</b>"}</div>
      <div class="note">Na serwerze: ${cur.msg ? `„${cur.msg}” (${fdt(cur.date)})` : "—"}${lat && !fresh ? `<br>Najnowsza: „${lat.msg}” (${fdt(lat.date)})` : ""}</div>
      ${busy ? "" : `<div class="row" style="gap:8px"><button class="btn small${lat && fresh ? " ghost" : ""}" data-dzupd="run">${lat && fresh ? "Zaktualizuj mimo to" : "Zaktualizuj teraz"}</button><button class="btn ghost small" data-dzupd="check">Sprawdź ponownie</button></div>`}
      ${st.log && !busy && !fresh && st.last_run ? `<details><summary class="note" style="cursor:pointer">Log ostatniej aktualizacji</summary><pre style="white-space:pre-wrap;font-size:12px;margin:6px 0 0">${st.log.replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))}</pre></details>` : ""}`;
  }
  document.addEventListener("click", async ev => {
    const b = ev.target.closest("[data-dzupd]"); if (!b) return;
    if (b.dataset.dzupd === "check") { b.textContent = "Sprawdzam…"; paintUpd(); return; }
    b.disabled = true; b.textContent = "Uruchamiam…";
    try { await req("POST", "/update", {}); updStarted = Date.now(); updSeen = false; if (!updPoll) updPoll = setInterval(paintUpd, 4000); paintUpd(); }
    catch (e) { b.disabled = false; b.textContent = "Zaktualizuj teraz"; const el = document.getElementById("dz-upd"); el && el.insertAdjacentHTML("beforeend", `<p class="note" style="margin:0;color:var(--bad)">Nie udało się uruchomić aktualizacji. Spróbuj za chwilę.</p>`); }
  });
  window.dzUpdate = { html: () => { setTimeout(paintUpd, 0); return `<div class="card stack"><h2>Aktualizacja aplikacji</h2><div id="dz-upd" class="stack" style="gap:10px"><p class="note" style="margin:0">Sprawdzam…</p></div></div>`; } };

  /* ---------- montaż filmu dnia (TikTok) ---------- */
  const mz = { info: null, job: null, files: [], titles: {}, up: null, err: "", poll: null, blob: null, open: false, list: [], pick: null, delAsk: null };
  const mzEsc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const mzMB = n => (n / 1048576).toFixed(n > 104857600 ? 0 : 1).replace(".", ",") + " MB";
  const mzUrls = new Map();
  function mzListOpen() { try { return localStorage.getItem("dz-mz-list") === "1" } catch (e) { return false } }
  function mzUrl(f) { if (!mzUrls.has(f)) mzUrls.set(f, URL.createObjectURL(f)); return mzUrls.get(f); }
  function mzDate() { try { return fst.date } catch (e) { return new Date().toISOString().slice(0, 10) } }
  function mzTitle(d) {
    if (mz.titles[d]) return mz.titles[d];
    const start = (mz.info && mz.info.start) || "2026-09-08";
    const n = Math.round((new Date(d + "T12:00:00") - new Date(start + "T12:00:00")) / 864e5) + 1;
    let tr = true, kc = null, nazwa = "Redukcja";
    try { tr = isTrainDay(d); kc = kcalGoal(d); nazwa = MODE === "masa" ? "Masa" : "Redukcja"; } catch (e) {}
    kc = kc ? Math.round(kc / 100) * 100 : (tr ? 2100 : 2000);
    return [`${nazwa} dzień ${n}`, `${kc} kcal`, tr ? "dzień treningowy" : "dzień nietreningowy"];
  }
  function mzDayN(t) { const m = /dzień\s+(\d+)/i.exec(t[0] || ""); return m ? +m[1] : 0; }
  function mzMeals(d) { try { const it = food[d] || []; return MEALS.filter(m => it.some(x => x.meal === m)); } catch (e) { return []; } }
  function mzCardName(m, d) { return (m === "II śniadanie" ? "drugie-sniadanie" : m.toLowerCase().replace(/\s+/g, "-")) + "-" + d + ".png"; }
  function mzJobShown() {
    const j = mz.job; if (!j) return null;
    if (j.state === "working" || mz.up || mz.pick === j.id) return j;
    return j.date === mzDate() ? j : null;
  }
  function mzPaint() {
    const el = document.getElementById("dz-mz"); if (!el) return;
    if (el.contains(document.activeElement) && document.activeElement.tagName === "INPUT" && document.activeElement.type !== "file") return;
    const d = mzDate(), info = mz.info, j = mzJobShown();
    if (!info) { el.innerHTML = `<p class="note" style="margin:0">Ładuję…</p>`; return; }
    let h = "";
    if (!info.groq) h += `<div class="nb-step" style="margin:0"><b>Brak klucza Groq na serwerze</b> (potrzebny do wycinania pomyłek). Zaloguj się na serwer i wpisz:<br><code style="font-size:12px;word-break:break-all">read -rsp "Klucz Groq: " K &amp;&amp; echo "GROQ_API_KEY=$K" &gt;&gt; /root/dziennik/.env &amp;&amp; unset K</code><br>wklej klucz (nie będzie widoczny) i Enter.</div>`;
    if (mz.up) {
      const u = mz.up;
      h += `<div class="stack" style="gap:6px"><b>Wysyłam ${mzEsc(u.label)} (${u.i} z ${u.n}) · ${Math.round(u.pct * 100)}%${u.tot ? ` <span class="note num">${mzMB(u.got)} z ${mzMB(u.tot)}</span>` : ""}</b><div class="bar"><i style="width:${Math.round(u.pct * 100)}%"></i></div>${u.pct >= 0.999 ? `<span class="note">Plik wysłany, serwer go zapisuje…</span>` : ""}<span class="note">Nie zamykaj aplikacji, dopóki wysyłanie się nie skończy. Potem możesz.</span></div>`;
    } else if (j && j.state === "working") {
      const sec = j.started ? Math.round((Date.now() / 1000 - j.started)) : 0;
      h += `<div class="nb-step" style="margin:0">⏳ <b>${mzEsc(j.step || "Montuję…")}</b>${sec > 5 ? ` <span class="note num">${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}</span>` : ""}<br><span class="note">Montaż trwa zwykle 3–8 minut. Możesz zamknąć aplikację – film poczeka.</span></div>`;
    } else if (j && j.state === "done") {
      h += `<video src="/api/montaz/${j.id}/film" controls playsinline preload="metadata" style="width:100%;max-height:60vh;border-radius:14px;background:#000"></video>
        <div class="row" style="gap:8px;flex-wrap:wrap">${mz.blob ? `<button class="btn small" data-mz="share">Zapisz / udostępnij film</button>` : `<button class="btn small" data-mz="prep">${mz.prep ? "Pobieram… " + mz.prep : "Zapisz w telefonie"}</button>`}<a class="btn ghost small" href="/api/montaz/${j.id}/film?dl=1">Pobierz plik</a></div>
        <span class="note">${mzEsc(j.film || "")} · ${mzMB(j.size || 0)}</span>
        ${j.raport ? `<details><summary class="note" style="cursor:pointer;font-weight:600">Co zostało wycięte (raport)</summary><pre style="white-space:pre-wrap;font-size:12px;margin:6px 0 0">${mzEsc(j.raport)}</pre></details>` : ""}
        <div class="row" style="gap:8px"><button class="btn ghost small" data-mz="new">Zrób nowy film</button><button class="btn ghost small" data-mz="redo">Zmontuj jeszcze raz</button></div>`;
    } else if (j && j.state === "error") {
      h += `<div class="nb-step" style="margin:0;border-color:var(--bad)"><b>Montaż się nie udał.</b> ${mzEsc(j.error || "")}</div>
        ${j.log ? `<details><summary class="note" style="cursor:pointer">Szczegóły</summary><pre style="white-space:pre-wrap;font-size:12px;margin:6px 0 0">${mzEsc(j.log)}</pre></details>` : ""}
        <div class="row" style="gap:8px"><button class="btn small" data-mz="redo">Spróbuj ponownie</button><button class="btn ghost small" data-mz="new">Nowy film</button></div>`;
    } else {
      const t = mzTitle(d), meals = mzMeals(d);
      h += `<div class="stack" style="gap:6px"><span class="label">Tytuł na początku filmu</span>${t.map((l, i) => `<input data-mzt="${i}" value="${mzEsc(l)}" style="font-weight:600">`).join("")}
          <span class="note">Dzień 1 = <input type="date" data-mz="start" value="${mzEsc(info.start)}" style="width:auto;display:inline-block;padding:2px 6px"></span></div>
        <div class="note">Karty posiłków z dziennika: ${meals.length ? mzEsc(meals.join(", ")) : "<b>brak posiłków w tym dniu</b> – film będzie bez kart"}.</div>
        <div class="note">Muzyka w tle: ${info.music ? "jest ✓" : "<b>brak</b> – film będzie bez muzyki"} · <label class="techlink" style="cursor:pointer">${info.music ? "zmień" : "wgraj mp3"}<input type="file" accept="audio/*" data-mz="music" hidden></label></div>
        ${mz.files.length ? `<div class="stack" style="gap:0"><span class="label">Klipy do filmu: ${mz.files.length} · ${mzMB(mz.files.reduce((a, f) => a + f.size, 0))}</span>${mz.files.map((f, i) => `<div class="mzrow"><video class="mzthumb" src="${mzUrl(f)}#t=0.1" muted playsinline preload="metadata"></video><span class="mzname"><b>Klip ${i + 1}</b><span class="note">${mzEsc(f.name)} · ${mzMB(f.size)}</span></span><button class="x" data-mz="rmclip" data-i="${i}" aria-label="Usuń klip">×</button></div>`).join("")}</div>` : ""}
        <label class="btn ghost" style="cursor:pointer;text-align:center">${mz.files.length ? "+ Dodaj kolejne klipy" : "Wybierz klipy z galerii"}<input type="file" accept="video/*" multiple data-mz="clips" hidden></label>
        ${mz.files.length ? `<button class="btn" data-mz="go" ${info.groq && info.ffmpeg ? "" : "disabled"}>Montaż film</button>` : `<span class="note">Każdy posiłek = osobny klip, mów co jesz („na obiad…”). Pomyłkę powtórz od początku zdania – pierwsze podejście się wytnie. Kolejność ułoży się wg godziny nagrania.</span>`}`;
    }
    if (mz.err) h += `<p class="note" style="margin:0;color:var(--bad)">${mzEsc(mz.err)}</p>`;
    if (mz.list.length) {
      const lab = x => x.state === "done" ? `gotowy · ${mzMB(x.size || 0)}` : x.state === "working" ? "montuje się…" : x.state === "error" ? "błąd montażu" : "niewysłany";
      const lo = mzListOpen();
      h += `<div class="stack" style="gap:0;margin-top:6px"><button class="mzhead" data-mz="listtog" aria-expanded="${lo}"><span class="label">Twoje filmy (${mz.list.length})</span><span class="chev" aria-hidden="true">${lo ? "▴" : "▾"}</span></button>${!lo ? "" : mz.list.map(x => {
        const cur = j && j.id === x.id, d = x.date ? new Date(x.date + "T12:00:00").toLocaleDateString("pl-PL", { day: "numeric", month: "short" }) : "";
        return `<div class="mzrow${cur ? " cur" : ""}"><button class="mzname" data-mz="pick" data-id="${x.id}"><b>${mzEsc((x.title && x.title[0]) || x.film || "Film")}</b><span class="note">${mzEsc(d)} · ${lab(x)}</span></button>${x.state === "working" ? "" : `<button class="x${mz.delAsk === x.id ? " ask" : ""}" data-mz="del" data-id="${x.id}" aria-label="Usuń film">${mz.delAsk === x.id ? "Usuń?" : "×"}</button>`}</div>`; }).join("")}
        ${lo ? `<span class="note" style="margin-top:6px">Filmy są trzymane na serwerze 14 dni.</span>` : ""}</div>`;
    }
    el.innerHTML = h;
  }
  async function mzList() { try { mz.list = await req("GET", "/montaz/list"); } catch (e) {} mzPaint(); }
  async function mzLoad() { mzList(); try { mz.info = await req("GET", "/montaz/info"); if (!mz.job || !(mz.up || mz.job.state === "working")) mz.job = mz.info.last; mzPaint(); mzWatch(); } catch (e) { const el = document.getElementById("dz-mz"); if (el) el.innerHTML = `<p class="note" style="margin:0">Montaż jest teraz niedostępny.</p>`; } }
  function mzWatch() {
    if (mz.poll || !mz.job || mz.job.state !== "working") return;
    mz.poll = setInterval(async () => {
      try { mz.job = await req("GET", "/montaz/" + mz.job.id); } catch (e) { return; }
      if (mz.job.state !== "working") { clearInterval(mz.poll); mz.poll = null; mz.blob = null; mzList(); }
      mzPaint();
    }, 3000);
  }
  function mzPut(url, body, onp) {
    return new Promise((res, rej) => {
      const x = new XMLHttpRequest(); x.open("PUT", url); x.withCredentials = true;
      let last = Date.now();
      const dog = setInterval(() => { if (Date.now() - last > 90000) { clearInterval(dog); x.abort(); rej(new Error("Wysyłanie stanęło (brak postępu przez 90 s). Sprawdź internet i spróbuj ponownie.")); } }, 5000);
      x.upload.onprogress = e => { last = Date.now(); if (e.lengthComputable) onp(e.loaded / e.total, e.loaded, e.total) };
      x.onloadend = () => clearInterval(dog);
      x.onload = () => x.status < 300 ? res() : rej(new Error((() => { try { return JSON.parse(x.responseText).error } catch (e) { return "Błąd " + x.status } })()));
      x.onerror = () => rej(new Error("Przerwane połączenie podczas wysyłania."));
      x.send(body);
    });
  }
  async function mzGo() {
    const d = mzDate(), title = mzTitle(d), files = mz.files.slice();
    mz.err = ""; mz.blob = null;
    try {
      const { id } = await req("POST", "/montaz/new", { date: d, title, n: mzDayN(title) });
      mz.job = { id, state: "uploading", date: d };
      for (let i = 0; i < files.length; i++) {
        mz.up = { label: "klip", i: i + 1, n: files.length, pct: 0 }; mzPaint();
        await mzPut(`/api/montaz/${id}/file?kind=clip&i=${i + 1}&name=${encodeURIComponent(files[i].name)}`, files[i], (p, g, t) => { mz.up.pct = p; mz.up.got = g; mz.up.tot = t; mzPaint(); });
      }
      const meals = mzMeals(d);
      for (let i = 0; i < meals.length; i++) {
        mz.up = { label: "kartę posiłku", i: i + 1, n: meals.length, pct: 0 }; mzPaint();
        const blob = await mealImage(meals[i]);
        await mzPut(`/api/montaz/${id}/file?kind=card&i=${i + 1}&name=${encodeURIComponent(mzCardName(meals[i], d))}`, blob, (p, g, t) => { mz.up.pct = p; mz.up.got = g; mz.up.tot = t; mzPaint(); });
      }
      mz.up = null;
      mz.job = await req("POST", `/montaz/${id}/start`, {});
      for (const u of mzUrls.values()) URL.revokeObjectURL(u); mzUrls.clear();
      mz.files = []; delete mz.titles[d]; mz.pick = id; mzList();
    } catch (e) { mz.up = null; mz.err = e.message || "Nie udało się wysłać."; }
    mzPaint(); mzWatch();
  }
  document.addEventListener("change", async ev => {
    const t = ev.target;
    if (t.dataset && t.dataset.mz === "clips") {
      const key = f => f.name + "|" + f.size + "|" + f.lastModified;
      const have = new Set(mz.files.map(key));
      mz.files = mz.files.concat([...t.files].filter(f => !have.has(key(f))));
      mz.err = ""; t.value = ""; mzPaint();
    }
    if (t.dataset && t.dataset.mz === "start" && t.value) { try { mz.info = await req("POST", "/montaz/settings", { start: t.value }); } catch (e) {} delete mz.titles[mzDate()]; mzPaint(); }
    if (t.dataset && t.dataset.mz === "music" && t.files[0]) {
      mz.err = ""; mz.up = { label: "muzykę", i: 1, n: 1, pct: 0 }; mzPaint();
      try { await mzPut("/api/montaz/music", t.files[0], (p, g, t) => { mz.up.pct = p; mz.up.got = g; mz.up.tot = t; mzPaint(); }); mz.info.music = true; } catch (e) { mz.err = e.message; }
      mz.up = null; mzPaint();
    }
  });
  document.addEventListener("input", ev => {
    const i = ev.target.dataset && ev.target.dataset.mzt; if (i == null) return;
    const d = mzDate(); const t = mzTitle(d).slice(); t[+i] = ev.target.value; mz.titles[d] = t;
  });
  document.addEventListener("click", async ev => {
    const b = ev.target.closest("[data-mz]"); if (!b || b.tagName === "INPUT") return;
    const a = b.dataset.mz;
    if (a === "go") { b.disabled = true; mzGo(); }
    if (a === "listtog") { try { localStorage.setItem("dz-mz-list", mzListOpen() ? "0" : "1") } catch (e) {} mzPaint(); return; }
    if (a === "rmclip") { const f = mz.files[+b.dataset.i]; if (f && mzUrls.has(f)) { URL.revokeObjectURL(mzUrls.get(f)); mzUrls.delete(f); } mz.files.splice(+b.dataset.i, 1); mzPaint(); return; }
    if (a === "new") { mz.job = null; mz.pick = null; mz.blob = null; mz.err = ""; mzPaint(); }
    if (a === "pick") { mz.delAsk = null; mz.err = ""; try { mz.job = await req("GET", "/montaz/" + b.dataset.id); mz.pick = b.dataset.id; mz.blob = null; } catch (e) { mz.err = e.message; } mzPaint(); mzWatch(); document.getElementById("dz-mz")?.scrollIntoView({ behavior: "smooth", block: "start" }); }
    if (a === "del") {
      const id = b.dataset.id;
      if (mz.delAsk !== id) { mz.delAsk = id; mzPaint(); setTimeout(() => { if (mz.delAsk === id) { mz.delAsk = null; mzPaint(); } }, 4000); return; }
      mz.delAsk = null;
      try { await req("DELETE", "/montaz/" + id); mz.list = mz.list.filter(x => x.id !== id); if (mz.job && mz.job.id === id) { mz.job = null; mz.pick = null; mz.blob = null; } } catch (e) { mz.err = e.message; }
      mzPaint();
    }
    if (a === "redo" && mz.job) { mz.err = ""; try { mz.job = await req("POST", `/montaz/${mz.job.id}/start`, {}); } catch (e) { mz.err = e.message; } mz.blob = null; mzPaint(); mzWatch(); }
    if (a === "prep" && mz.job && !mz.prep) {
      mz.prep = "0%"; mzPaint();
      try {
        const r = await fetch(`/api/montaz/${mz.job.id}/film`, { credentials: "same-origin" });
        const total = +r.headers.get("content-length") || mz.job.size || 0, rd = r.body.getReader(), parts = []; let got = 0;
        for (;;) { const { done, value } = await rd.read(); if (done) break; parts.push(value); got += value.length; mz.prep = total ? Math.round(got / total * 100) + "%" : mzMB(got); mzPaint(); }
        mz.blob = new Blob(parts, { type: "video/mp4" });
      } catch (e) { mz.err = "Nie udało się pobrać filmu."; }
      mz.prep = null; mzPaint();
    }
    if (a === "share" && mz.blob) {
      const f = new File([mz.blob], mz.job.film || "film.mp4", { type: "video/mp4" });
      try {
        if (navigator.canShare && navigator.canShare({ files: [f] })) await navigator.share({ files: [f] });
        else { const u = URL.createObjectURL(f); const l = document.createElement("a"); l.href = u; l.download = f.name; l.click(); setTimeout(() => URL.revokeObjectURL(u), 60000); }
      } catch (e) { if (e.name !== "AbortError") { mz.err = "Udostępnianie nie zadziałało – użyj „Pobierz plik”."; mzPaint(); } }
    }
  });
  window.dzMontaz = { html: () => { setTimeout(() => { mz.info ? (mzPaint(), mzWatch()) : mzLoad(); }, 0); return `<div class="card stack"><div class="row" style="justify-content:space-between;align-items:baseline"><h2>Film dnia</h2><span class="note">TikTok</span></div><div id="dz-mz" class="stack" style="gap:10px"><p class="note" style="margin:0">Ładuję…</p></div></div>`; } };

  window.dzSleep = { refresh: () => req("POST", "/sleep/refresh", {}), status: () => req("GET", "/sleep/status") };

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
