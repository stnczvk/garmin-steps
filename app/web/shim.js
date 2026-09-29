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
