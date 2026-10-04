"""Dziennik treningowy – prywatny serwer (FastAPI + SQLite).

Baza dokumentów w stylu kolekcja/dokument, logowanie hasłem, pośrednik do API Claude
(odczyt etykiet i zrzutów), import kroków i cardio z Garmina.
"""
import asyncio, sys, base64, hashlib, hmac, io, json, os, re, secrets, shutil, sqlite3, subprocess, threading, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request, Response, UploadFile, Form, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("DZ_HOME", "/root/dziennik"))


def load_env(p):
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env(ROOT / ".env")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PASS = os.environ.get("DZ_PASS", "")  # scrypt$salt$hash
FOOD_DIR = Path(os.environ.get("FOOD_DATA", str(ROOT / "data")))
STEPS_FILE = Path(os.environ.get("GARMIN_STEPS", "/root/garmin-sync/repo/steps.json"))
GARMIN_SERVICE = os.environ.get("GARMIN_SERVICE", "garmin-steps.service")
AI_DAILY = int(os.environ.get("AI_DAILY_LIMIT", "150"))
MODELS = {"default": os.environ.get("MODEL_DEFAULT", "claude-sonnet-5"),
          "quick": os.environ.get("MODEL_QUICK", "claude-haiku-4-5-20251001")}
TZ = ZoneInfo("Europe/Warsaw")
DB_PATH = ROOT / "dziennik.db"
PATH_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}/[A-Za-z0-9_.:-]{1,120}$")

# ---------- baza ----------
_lock = threading.Lock()
_con = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_con.execute("PRAGMA journal_mode=WAL")
_con.execute("CREATE TABLE IF NOT EXISTS docs(path TEXT PRIMARY KEY, coll TEXT, data TEXT, ts REAL)")
_con.execute("CREATE INDEX IF NOT EXISTS docs_ts ON docs(ts)")
_con.execute("CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, created REAL, agent TEXT)")
_con.execute("CREATE TABLE IF NOT EXISTS ai_usage(day TEXT PRIMARY KEY, n INTEGER, tin INTEGER, tout INTEGER)")
if "cost" not in [r[1] for r in _con.execute("PRAGMA table_info(ai_usage)").fetchall()]:
    _con.execute("ALTER TABLE ai_usage ADD COLUMN cost REAL DEFAULT 0")
PRICES = {"claude-sonnet-5": (2.0, 10.0), "claude-haiku-4-5-20251001": (1.0, 5.0), "claude-opus-5-5": (4.0, 20.0)}  # $ za 1 mln tokenów (wejście, wyjście)
_last_ts = 0.0


def _now_ts():
    global _last_ts
    t = max(time.time(), _last_ts + 0.000001)
    _last_ts = t
    return t


def put_doc(path, data):
    with _lock:
        _con.execute("INSERT INTO docs(path,coll,data,ts) VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET data=excluded.data, ts=excluded.ts",
                     (path, path.split("/")[0], None if data is None else json.dumps(data, ensure_ascii=False), _now_ts()))


def get_doc(path):
    r = _con.execute("SELECT data FROM docs WHERE path=?", (path,)).fetchone()
    return json.loads(r[0]) if r and r[0] is not None else None


# ---------- logowanie ----------
def check_pass(pw):
    try:
        _, salt, h = PASS.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()
        return hmac.compare_digest(got, h)
    except Exception:
        return False


def authed(req: Request):
    tok = req.cookies.get("dz")
    if not tok:
        return False
    return _con.execute("SELECT 1 FROM sessions WHERE token=?", (tok,)).fetchone() is not None


_fails = {}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def guard(req: Request, call_next):
    p = req.url.path
    if p.startswith("/api/") and p not in ("/api/login", "/api/me", "/api/hook/weight") and not authed(req):
        return JSONResponse({"error": "auth"}, status_code=401)
    resp = await call_next(req)
    if p in ("/", "/index.html", "/shim.js", "/sw.js"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/api/me")
def me(req: Request):
    return {"ok": authed(req)} if authed(req) else JSONResponse({"ok": False}, status_code=401)


@app.post("/api/login")
async def login(req: Request):
    ip = req.headers.get("x-forwarded-for", req.client.host if req.client else "?").split(",")[0]
    f = _fails.get(ip, [0, 0])
    if f[0] >= 5 and time.time() - f[1] < 600:
        return JSONResponse({"error": "Za dużo prób. Spróbuj za 10 minut."}, status_code=429)
    body = await req.json()
    if not PASS or not check_pass(str(body.get("password", ""))):
        _fails[ip] = [f[0] + 1, time.time()]
        await asyncio.sleep(1)
        return JSONResponse({"error": "Złe hasło."}, status_code=401)
    _fails.pop(ip, None)
    tok = secrets.token_urlsafe(32)
    _con.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, time.time(), req.headers.get("user-agent", "")[:200]))
    r = JSONResponse({"ok": True})
    r.set_cookie("dz", tok, max_age=400 * 86400, httponly=True, secure=True, samesite="lax")
    return r


@app.post("/api/logout")
def logout(req: Request):
    _con.execute("DELETE FROM sessions WHERE token=?", (req.cookies.get("dz", ""),))
    r = JSONResponse({"ok": True})
    r.delete_cookie("dz")
    return r


# ---------- dokumenty ----------
@app.get("/api/sync")
def sync(since: float = 0):
    rows = _con.execute("SELECT path,data,ts FROM docs WHERE ts>? ORDER BY ts", (since,)).fetchall()
    now = rows[-1][2] if rows else since
    if since == 0:
        rows = [r for r in rows if r[1] is not None]
    return {"now": now, "docs": [{"path": p, "data": (json.loads(d) if d is not None else None)} for p, d, _ in rows]}


@app.put("/api/doc/{path:path}")
async def set_doc(path: str, req: Request):
    if not PATH_RE.match(path):
        raise HTTPException(400, "bad path")
    raw = await req.body()
    if len(raw) > 3_000_000:
        raise HTTPException(413, "too big")
    put_doc(path, json.loads(raw))
    return Response(status_code=204)


@app.delete("/api/doc/{path:path}")
def del_doc(path: str):
    if not PATH_RE.match(path):
        raise HTTPException(400, "bad path")
    put_doc(path, None)
    return Response(status_code=204)


@app.get("/api/export")
def export():
    rows = _con.execute("SELECT path,data FROM docs WHERE data IS NOT NULL").fetchall()
    return {p: json.loads(d) for p, d in rows}


# ---------- AI (Claude) ----------
def _shrink(data: bytes, mt: str):
    if len(data) < 3_500_000 and mt in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            if max(im.size) <= 2000:
                return data, mt
        except Exception:
            return data, mt
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((2000, 2000))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return data, mt


def _json_from(text):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                pass
    return json.loads(t)


@app.post("/api/ai")
async def ai(prompt: str = Form(...), tier: str = Form("default"), files: list[UploadFile] = File(default=[])):
    if not API_KEY:
        return JSONResponse({"error": "Brak klucza API na serwerze.", "code": "not_granted"}, status_code=503)
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    row = _con.execute("SELECT n FROM ai_usage WHERE day=?", (day,)).fetchone()
    if row and row[0] >= AI_DAILY:
        return JSONResponse({"error": "Dzienny limit zapytań do AI wyczerpany.", "code": "rate_limited"}, status_code=429)
    content = []
    for f in files[:5]:
        data, mt = _shrink(await f.read(), f.content_type or "image/jpeg")
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": prompt[:20000]})
    body = {"model": MODELS.get(tier, MODELS["default"]), "max_tokens": 4000,
            "system": "Odpowiadasz wyłącznie poprawnym JSON, bez żadnego tekstu przed ani po.",
            "messages": [{"role": "user", "content": content}]}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post("https://api.anthropic.com/v1/messages", json=body,
                         headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01"})
    if r.status_code != 200:
        code = "rate_limited" if r.status_code == 429 else "ai_error"
        return JSONResponse({"error": r.text[:300], "code": code}, status_code=502 if code == "ai_error" else 429)
    res = r.json()
    u = res.get("usage", {})
    pi, po = PRICES.get(body["model"], (2.0, 10.0))
    cost = (u.get("input_tokens", 0) * pi + u.get("output_tokens", 0) * po) / 1e6
    _con.execute("INSERT INTO ai_usage(day,n,tin,tout,cost) VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET n=n+1, tin=tin+excluded.tin, tout=tout+excluded.tout, cost=cost+excluded.cost",
                 (day, 1, u.get("input_tokens", 0), u.get("output_tokens", 0), cost))
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    try:
        return {"json": _json_from(text)}
    except Exception:
        return JSONResponse({"error": "Nie udało się odczytać odpowiedzi.", "code": "ai_error", "raw": text[:500]}, status_code=502)


@app.get("/api/ai/usage")
def ai_usage():
    rows = _con.execute("SELECT day,n,tin,tout,cost FROM ai_usage ORDER BY day DESC LIMIT 400").fetchall()
    month = datetime.now(TZ).strftime("%Y-%m")
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    m = [r for r in rows if r[0].startswith(month)]
    t = next((r for r in rows if r[0] == today), None)
    return {"limit": AI_DAILY, "today": {"n": t[1] if t else 0, "cost": round(t[4] or 0, 4) if t else 0},
            "month": {"n": sum(r[1] for r in m), "cost": round(sum(r[4] or 0 for r in m), 4)},
            "total": {"n": sum(r[1] for r in rows), "cost": round(sum(r[4] or 0 for r in rows), 4)},
            "days": [{"day": d, "n": n, "cost": round(c or 0, 4)} for d, n, i, o, c in rows[:31]]}


# ---------- Garmin ----------
def garmin_import():
    """Kroki i cardio ze steps.json → activity/<data> (dziś i 2 poprzednie dni)."""
    try:
        s = json.loads(STEPS_FILE.read_text())
    except Exception as e:
        return f"Nie mogę odczytać pliku z Garmina ({e.__class__.__name__})."
    today = datetime.now(TZ).date()
    changed = []
    for k in range(3):
        d = (today - timedelta(days=k)).isoformat()
        cur = get_doc("activity/" + d)
        steps = s.get("days", {}).get(d)
        gc = [{"type": c.get("type"), "min": c.get("min"), "src": "garmin", "gid": c.get("id")} for c in s.get("cardio", {}).get(d, [])]
        if cur is None:
            if steps is None and not gc:
                continue
            new = {"steps": steps, "bw": None, "cardio": gc}
        else:
            new = dict(cur)
            if steps is not None:
                new["steps"] = steps
            new["cardio"] = [c for c in (cur.get("cardio") or []) if c.get("src") != "garmin"] + gc
        if new != cur:
            put_doc("activity/" + d, new)
            changed.append(d)
    return ("Zapisano: " + ", ".join(changed) + ".") if changed else "Bez zmian – dane z Garmina były już wpisane."


_gr_lock = threading.Lock()


def garmin_refresh_job():
    if not _gr_lock.acquire(blocking=False):
        return
    try:
        put_doc("settings/garminlog", {"at": datetime.now(TZ).isoformat(timespec="seconds"), "step": "start"})
        err = ""
        try:
            subprocess.run(["systemctl", "start", GARMIN_SERVICE], timeout=240, check=True, capture_output=True)
        except Exception as e:
            err = f"Pobieranie z Garmina nie powiodło się ({e.__class__.__name__}). "
        res = garmin_import()
        put_doc("settings/garminlog", {"at": datetime.now(TZ).isoformat(timespec="seconds"), "step": "koniec", "result": err + res})
    finally:
        _gr_lock.release()
    sleep_sync(force_today=True)


# ---------- sen z Garmina ----------
_sl_lock = threading.Lock()


def sleep_sync(force_today=False, back=3):
    """Pobiera sen z ostatnich dni (brakujące) – dane zostają na serwerze."""
    if not _sl_lock.acquire(blocking=False):
        return
    try:
        import garmin_sleep
        today = datetime.now(TZ).date()
        if not kv_get("sleep_backfill"):
            back = 30
        n = 0
        for i in range(back):
            d = (today - timedelta(days=i)).isoformat()
            if get_doc("sleep/" + d) and not (force_today and i == 0):
                continue
            s = garmin_sleep.sleep_for(d)
            if s:
                put_doc("sleep/" + d, {**s, "at": datetime.now(TZ).isoformat(timespec="seconds")})
                n += 1
        kv_set("sleep_backfill", "1")
        kv_set("sleep_status", json.dumps({"at": datetime.now(TZ).isoformat(timespec="seconds"), "ok": True, "n": n}))
    except Exception as e:
        kv_set("sleep_status", json.dumps({"at": datetime.now(TZ).isoformat(timespec="seconds"), "ok": False,
                                           "err": e.__class__.__name__ + (": " + str(e)[:120] if "logowania" in str(e) or "garminconnect" in str(e) else "")}))
        print("sleep sync error", e.__class__.__name__, flush=True)
    finally:
        _sl_lock.release()


@app.get("/api/sleep/status")
def sleep_status():
    v = kv_get("sleep_status")
    return json.loads(v) if v else {}


@app.post("/api/sleep/refresh")
def sleep_refresh():
    threading.Thread(target=sleep_sync, kwargs={"force_today": True}, daemon=True).start()
    return {"ok": True}


@app.post("/api/garmin/refresh")
def garmin_refresh():
    threading.Thread(target=garmin_refresh_job, daemon=True).start()
    return {"ok": True}


def watcher():
    """Po każdej automatycznej synchronizacji (timer Garmina) wczytuje nowe dane."""
    last = 0
    while True:
        try:
            m = STEPS_FILE.stat().st_mtime
            if m != last:
                if last and not _gr_lock.locked():
                    res = garmin_import()
                    put_doc("settings/garminlog", {"at": datetime.now(TZ).isoformat(timespec="seconds"), "step": "koniec", "result": res})
                last = m
        except Exception:
            pass
        time.sleep(60)


threading.Thread(target=watcher, daemon=True).start()

# ---------- waga z Apple Health (skrót iPhone) ----------
def hook_key():
    k = kv_get("hook_key")
    if not k:
        k = secrets.token_urlsafe(18)
        kv_set("hook_key", k)
    return k


@app.get("/api/hook/info")
def hook_info():
    last = kv_get("hook_last")
    return {"url": f"https://{DOMAIN}/api/hook/weight?key={hook_key()}", "last": json.loads(last) if last else None}


@app.post("/api/hook/newkey")
def hook_newkey():
    kv_set("hook_key", secrets.token_urlsafe(18))
    return hook_info()


# ---------- montaż filmu dnia (TikTok) ----------
MZ_DIR = ROOT / "montaz"
MZ_JOBS = MZ_DIR / "zadania"
MZ_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$")
MZ_KIND = {"clip": {".mov", ".mp4", ".m4v"}, "card": {".png", ".jpg", ".jpeg"}}


def _mz_env():
    env = dict(os.environ)
    try:
        for line in (ROOT / ".env").read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                env["GROQ_API_KEY"] = line.split("=", 1)[1].strip()
    except Exception:
        pass
    env["MONTAZ_MUSIC"] = str(MZ_DIR / "muzyka_tlo.mp3")
    env["MONTAZ_FONT"] = str(MZ_DIR / "Montserrat.ttf")
    return env


def _mz_job(jid):
    if not MZ_ID.match(jid or ""):
        raise HTTPException(404)
    d = MZ_JOBS / jid
    if not d.is_dir():
        raise HTTPException(404)
    return d


def _mz_status(d):
    try:
        st = json.loads((d / "status.json").read_text())
    except Exception:
        st = {}
    st["id"] = d.name
    if st.get("state") == "working" and time.time() - st.get("at", 0) > 1800:
        st.update(state="error", error="Montaż przestał odpowiadać (ponad 30 min bez postępu).")
    return st


def _mz_cleanup(keep):
    """Zostawia gotowe filmy z 7 dni; klipy tylko z ostatniego zadania (miejsce na dysku)."""
    if not MZ_JOBS.is_dir():
        return
    for d in sorted(MZ_JOBS.iterdir()):
        if not d.is_dir() or d.name == keep:
            continue
        age = time.time() - d.stat().st_mtime
        if age > 7 * 86400:
            shutil.rmtree(d, ignore_errors=True)
            continue
        for day in d.glob("dzien_*"):
            for f in day.iterdir():
                if f.suffix.lower() in MZ_KIND["clip"]:
                    f.unlink(missing_ok=True)


@app.get("/api/montaz/info")
def montaz_info():
    env = _mz_env()
    last = None
    if MZ_JOBS.is_dir():
        jobs = sorted(d for d in MZ_JOBS.iterdir() if d.is_dir() and MZ_ID.match(d.name))
        if jobs:
            last = _mz_status(jobs[-1])
    return {"groq": bool(env.get("GROQ_API_KEY")), "music": (MZ_DIR / "muzyka_tlo.mp3").is_file(),
            "start": kv_get("mz_start") or "2026-09-08", "last": last,
            "ffmpeg": shutil.which("ffmpeg") is not None}


@app.post("/api/montaz/settings")
async def montaz_settings(req: Request):
    b = await req.json()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(b.get("start", ""))):
        kv_set("mz_start", b["start"])
    return montaz_info()


@app.post("/api/montaz/music")
async def montaz_music(req: Request):
    MZ_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MZ_DIR / "muzyka_tlo.part"
    n = 0
    with tmp.open("wb") as f:
        async for chunk in req.stream():
            n += len(chunk)
            if n > 40 * 1024 * 1024:
                f.close(); tmp.unlink(missing_ok=True)
                return JSONResponse({"error": "Plik muzyki za duży (max 40 MB)."}, status_code=413)
            f.write(chunk)
    tmp.replace(MZ_DIR / "muzyka_tlo.mp3")
    return {"ok": True}


@app.post("/api/montaz/new")
async def montaz_new(req: Request):
    b = await req.json()
    n = int(b.get("n") or 0)
    title = [str(x).strip()[:60] for x in (b.get("title") or []) if str(x).strip()][:4]
    if not title or n < 0 or n > 9999:
        return JSONResponse({"error": "Brak tytułu."}, status_code=400)
    jid = datetime.now(TZ).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    day = MZ_JOBS / jid / f"dzien_{n}"
    day.mkdir(parents=True)
    (MZ_JOBS / jid / "title.json").write_text(json.dumps(title, ensure_ascii=False))
    (MZ_JOBS / jid / "status.json").write_text(json.dumps({"state": "uploading", "step": "Wysyłanie klipów…",
                                                           "date": str(b.get("date", ""))[:10], "at": time.time()}))
    _mz_cleanup(jid)
    return {"id": jid}


@app.put("/api/montaz/{jid}/file")
async def montaz_file(jid: str, req: Request, kind: str = "clip", i: int = 0, name: str = ""):
    d = _mz_job(jid)
    day = next(d.glob("dzien_*"))
    ext = Path(name).suffix.lower()
    if kind not in MZ_KIND or ext not in MZ_KIND[kind]:
        return JSONResponse({"error": "Nieobsługiwany plik: " + name[:40]}, status_code=400)
    if kind == "clip":
        fn = f"klip_{i:02d}{ext}"
    else:
        base = re.sub(r"[^a-z0-9ąćęłńóśźż-]+", "-", Path(name).stem.lower())[:60].strip("-") or f"karta-{i}"
        fn = base + ext
    tmp = day / (fn + ".part")
    with tmp.open("wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    tmp.replace(day / fn)
    return {"ok": True, "name": fn, "size": (day / fn).stat().st_size}


@app.post("/api/montaz/{jid}/start")
def montaz_start(jid: str):
    d = _mz_job(jid)
    st = _mz_status(d)
    if st.get("state") == "working":
        return st
    day = next(d.glob("dzien_*"))
    if not any(f.suffix.lower() in MZ_KIND["clip"] for f in day.iterdir()):
        return JSONResponse({"error": "Nie ma żadnego klipu."}, status_code=400)
    env = _mz_env()
    if not env.get("GROQ_API_KEY"):
        return JSONResponse({"error": "Brak klucza Groq na serwerze."}, status_code=400)
    # tytuł jako opis.txt – nowszy niż klipy, więc skrypt go użyje
    (day / "opis.txt").write_text("\n".join(json.loads((d / "title.json").read_text())) + "\n", encoding="utf-8")
    for f in ("kolejnosc.txt", "raport.txt"):
        (day / f).unlink(missing_ok=True)
    if (d / "gotowe").is_dir():
        shutil.rmtree(d / "gotowe", ignore_errors=True)
    st = {k: v for k, v in st.items() if k in ("date",)}
    st.update(state="working", step="Start…", at=time.time())
    (d / "status.json").write_text(json.dumps(st, ensure_ascii=False))
    subprocess.Popen(["nice", "-n", "15", sys.executable, str(HERE / "montaz_job.py"), str(d)], env=env,
                     stdout=subprocess.DEVNULL, stderr=open(d / "job_err.txt", "w"), start_new_session=True)
    return _mz_status(d)


@app.post("/api/montaz/{jid}/title")
async def montaz_title(jid: str, req: Request):
    d = _mz_job(jid)
    title = [str(x).strip()[:60] for x in ((await req.json()).get("title") or []) if str(x).strip()][:4]
    if title:
        (d / "title.json").write_text(json.dumps(title, ensure_ascii=False))
    return {"ok": True}


@app.get("/api/montaz/{jid}")
def montaz_get(jid: str):
    return _mz_status(_mz_job(jid))


@app.get("/api/montaz/{jid}/film")
def montaz_film(jid: str, dl: int = 0):
    d = _mz_job(jid)
    st = _mz_status(d)
    f = d / "gotowe" / str(st.get("film", ""))
    if st.get("state") != "done" or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, media_type="video/mp4", filename=f.name if dl else None)


# ---------- aktualizacja aplikacji z telefonu ----------
SRC_DIR = HERE.parent
UPD_LOG = ROOT / "update.log"
UPD_UNIT = "dz-selfupdate"


def _git(*a):
    try:
        return subprocess.run(["git", "-C", str(SRC_DIR), *a], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def _upd_running():
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", UPD_UNIT], timeout=10).returncode == 0
    except Exception:
        return False


@app.get("/api/update/status")
async def update_status():
    cur = {"sha": _git("rev-parse", "HEAD"), "msg": _git("log", "-1", "--format=%s"), "date": _git("log", "-1", "--format=%cI")}
    latest = None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.github.com/repos/stnczvk/garmin-steps/commits/dziennik-app",
                            headers={"Accept": "application/vnd.github+json", "User-Agent": "dziennik"})
            if r.status_code == 200:
                j = r.json()
                latest = {"sha": j["sha"], "msg": j["commit"]["message"].split("\n")[0], "date": j["commit"]["committer"]["date"]}
    except Exception:
        pass
    tail = ""
    try:
        tail = "\n".join(UPD_LOG.read_text(errors="replace").splitlines()[-6:])
    except Exception:
        pass
    return {"current": cur, "latest": latest, "running": _upd_running(),
            "last_run": kv_get("upd_at"), "log": re.sub(r"\x1b\[[0-9;]*m", "", tail)}


@app.post("/api/update")
def update_run():
    if _upd_running():
        return {"ok": True, "text": "Aktualizacja już trwa."}
    subprocess.run(["systemctl", "reset-failed", UPD_UNIT], capture_output=True, timeout=10)
    # systemd-run: aktualizacja działa poza usługą dziennika, więc przeżyje jej restart
    r = subprocess.run(["systemd-run", "--unit", UPD_UNIT, "--collect", "/bin/bash", "-c",
                        f"/usr/local/bin/dz-update > {UPD_LOG} 2>&1 < /dev/null"], capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return JSONResponse({"error": "Nie udało się uruchomić aktualizacji: " + (r.stderr or r.stdout)[:200]}, status_code=500)
    kv_set("upd_at", datetime.now(TZ).isoformat(timespec="seconds"))
    return {"ok": True, "text": "Aktualizacja ruszyła."}


@app.post("/api/hook/weight")
async def hook_weight(req: Request, key: str = ""):
    if not hmac.compare_digest(key, hook_key()):
        return JSONResponse({"error": "zły klucz"}, status_code=403)
    raw = (await req.body()).decode("utf-8", "replace")
    try:
        body = json.loads(raw) if raw.strip().startswith("{") else {"kg": raw}
    except Exception:
        body = {"kg": raw}
    val = body.get("kg", body.get("value", body.get("waga", "")))
    m = re.search(r"\d+(?:[.,]\d+)?", str(val))
    kg = float(m.group(0).replace(",", ".")) if m else 0
    if not 20 <= kg <= 400:
        return JSONResponse({"error": f"Nie rozpoznałem wagi w: {str(val)[:60]}"}, status_code=400)
    kg = round(kg, 1)
    d = str(body.get("date", ""))[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        d = datetime.now(TZ).date().isoformat()
    cur = get_doc("activity/" + d) or {"steps": None, "cardio": []}
    if cur.get("bw") != kg:
        put_doc("activity/" + d, {**cur, "bw": kg, "bwSrc": "xiaomi"})
    kv_set("hook_last", json.dumps({"at": datetime.now(TZ).isoformat(timespec="seconds"), "kg": kg, "date": d}))
    return {"ok": True, "text": f"Zapisano wagę {str(kg).replace('.', ',')} kg ({d})"}


# ---------- powiadomienia (Web Push) i przypomnienia ----------
_con.execute("CREATE TABLE IF NOT EXISTS push_subs(endpoint TEXT PRIMARY KEY, sub TEXT, created REAL, agent TEXT)")
_con.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
VAPID_PEM = ROOT / "vapid.pem"
DOMAIN = os.environ.get("DZ_DOMAIN", "localhost")


def kv_get(k):
    r = _con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r[0] if r else None


def kv_set(k, v):
    _con.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def vapid_public():
    from py_vapid import Vapid02
    from cryptography.hazmat.primitives import serialization
    if not VAPID_PEM.exists():
        v = Vapid02()
        v.generate_keys()
        v.save_key(str(VAPID_PEM))
        os.chmod(VAPID_PEM, 0o600)
    v = Vapid02.from_file(str(VAPID_PEM))
    raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def send_push(title, body, tag="dziennik", url="/"):
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return 0
    n = 0
    for ep, sub in _con.execute("SELECT endpoint, sub FROM push_subs").fetchall():
        try:
            webpush(subscription_info=json.loads(sub), data=json.dumps({"title": title, "body": body, "tag": tag, "url": url}, ensure_ascii=False),
                    vapid_private_key=str(VAPID_PEM), vapid_claims={"sub": "https://" + DOMAIN}, ttl=6 * 3600)
            n += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):
                _con.execute("DELETE FROM push_subs WHERE endpoint=?", (ep,))
        except Exception:
            pass
    return n


@app.get("/api/push/key")
def push_key():
    return {"key": vapid_public()}


@app.post("/api/push/subscribe")
async def push_sub(req: Request):
    sub = await req.json()
    ep = sub.get("endpoint", "")
    if not ep.startswith("https://"):
        raise HTTPException(400, "bad subscription")
    _con.execute("INSERT INTO push_subs VALUES(?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET sub=excluded.sub",
                 (ep, json.dumps(sub), time.time(), req.headers.get("user-agent", "")[:200]))
    return {"ok": True, "devices": _con.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0]}


@app.post("/api/push/unsubscribe")
async def push_unsub(req: Request):
    sub = await req.json()
    _con.execute("DELETE FROM push_subs WHERE endpoint=?", (sub.get("endpoint", ""),))
    return {"ok": True}


@app.post("/api/push/test")
def push_test():
    n = send_push("Dziennik treningowy", "✅ Powiadomienia działają. Tak będą wyglądać przypomnienia.", "test")
    return {"sent": n}


def _rem():
    return {"evening": True, "morning": True, "meals": True, **(get_doc("settings/reminders") or {})}


def _plateau_msg(today):
    t = get_doc("settings/targets") or {}
    if t.get("plateauSnooze") and t["plateauSnooze"] > today.isoformat():
        return None
    mode = (get_doc("settings/mode") or {}).get("mode", "red")
    start = today - timedelta(days=21)
    pts = []
    for p, d in _con.execute("SELECT path,data FROM docs WHERE coll='activity' AND data IS NOT NULL").fetchall():
        day = p.split("/")[1]
        v = (json.loads(d) or {}).get("bw")
        if v and start.isoformat() <= day <= today.isoformat():
            pts.append((day, float(v)))
    pts.sort()
    if len(pts) < 4 or (datetime.fromisoformat(pts[-1][0]) - datetime.fromisoformat(pts[0][0])).days < 14:
        return None
    d0 = datetime.fromisoformat(pts[0][0]) + timedelta(days=7)
    d1 = datetime.fromisoformat(pts[-1][0]) - timedelta(days=7)
    first = [v for d, v in pts if datetime.fromisoformat(d) < d0]
    last = [v for d, v in pts if datetime.fromisoformat(d) > d1]
    if not first or not last:
        return None
    a, b = sum(first) / len(first), sum(last) / len(last)
    if mode == "masa":
        stalled = b - a < 0.2
    else:
        if t.get("goalBw") and b <= float(t["goalBw"]) + 0.2:
            return None
        stalled = a - b < 0.2
    return "⚖️ Waga stoi od ~3 tygodni – zajrzyj do zakładki Jedzenie, jest propozycja zmiany." if stalled else None


def morning_check(now):
    today = now.date()
    y = (today - timedelta(days=1)).isoformat()
    r = _rem()
    parts = []
    if r.get("morning"):
        if not ((get_doc("activity/" + y) or {}).get("steps") or 0) > 0:
            parts.append("kroki z Garmina nie wczytały się za wczoraj – wpisz je ręcznie")
        if r.get("meals") and not (get_doc("food/" + y) or {}).get("items"):
            parts.append("brakuje posiłków z wczoraj")
    j = ", ".join(parts)
    msg = ("🔔 " + j[:1].upper() + j[1:] + ".") if parts else ""
    if r.get("morning") and today.isoweekday() == 1:
        pm = _plateau_msg(today)
        if pm:
            msg = (msg + " " + pm).strip()
    put_doc("settings/remindlog_morning", {"at": now.isoformat(timespec="seconds"), "result": msg or "OK"})
    if msg:
        send_push("Dziennik – poranne przypomnienie", msg, "morning")


def evening_check(now):
    r = _rem()
    msg = ""
    if r.get("evening") and r.get("meals") and not (get_doc("food/" + now.date().isoformat()) or {}).get("items"):
        msg = "🔔 Nie ma dziś jeszcze żadnego posiłku w Dzienniku – dodaj produkty albo wrzuć screen z Fitatu."
    put_doc("settings/remindlog_evening", {"at": now.isoformat(timespec="seconds"), "result": msg or "OK"})
    if msg:
        send_push("Dziennik – wieczorne przypomnienie", msg, "evening")


def workout_check():
    w = get_doc("settings/workout") or {}
    st = w.get("start")
    if not st:
        return
    mins = (time.time() * 1000 - float(st)) / 60000
    if mins > 360:
        put_doc("settings/workout", {**w, "start": None, "at": int(time.time() * 1000)})
        return
    n = int(w.get("notified") or 0)
    if (n == 0 and mins >= 100) or (n == 1 and mins >= 160):
        put_doc("settings/workout", {**w, "notified": n + 1})
        send_push("Dziennik – trening", f"🏋️ Trening trwa już {int(mins // 60)} h {int(mins % 60)} min – skończyłeś? Zapisz trening albo anuluj stoper.", "workout")


def _hm(v, default):
    try:
        a, b = str(v).split(":")
        return int(a) * 60 + int(b)
    except Exception:
        return default


def scheduler():
    while True:
        try:
            now = datetime.now(TZ)
            day = now.date().isoformat()
            r = _rem()
            nm = now.hour * 60 + now.minute
            mt, et = _hm(r.get("morningAt"), 8 * 60), _hm(r.get("eveningAt"), 21 * 60 + 30)
            if mt <= nm <= mt + 60 and kv_get("morning") != day:
                kv_set("morning", day)
                morning_check(now)
            if et <= nm <= et + 60 and kv_get("evening") != day:
                kv_set("evening", day)
                evening_check(now)
            if now.day == 1 and now.hour == 4 and kv_get("foodbuild") != now.strftime("%Y-%m"):
                kv_set("foodbuild", now.strftime("%Y-%m"))
                subprocess.Popen(["nice", "-n", "15", sys.executable, str(HERE / "food_build.py"), str(FOOD_DIR)],
                                 stdout=open(ROOT / "food_build.log", "a"), stderr=subprocess.STDOUT)
            workout_check()
            # sen: co godzinę między 7 a 13, dopóki nie ma dzisiejszej nocy (raz o 13 także odśwież dzisiejszą)
            if 7 <= now.hour <= 13 and now.minute == 25 and (not get_doc("sleep/" + day) or now.hour == 13):
                threading.Thread(target=sleep_sync, kwargs={"force_today": now.hour == 13}, daemon=True).start()
            if now.hour == 3 and kv_get("backup") != day:
                kv_set("backup", day)
                bd = ROOT / "backups"; bd.mkdir(exist_ok=True)
                _con.execute("VACUUM INTO ?", (str(bd / f"dziennik-{day}.db"),))
                for old in sorted(bd.glob("dziennik-*.db"))[:-14]:
                    old.unlink()
        except Exception as e:
            print("scheduler error", e, flush=True)
        time.sleep(60)


threading.Thread(target=scheduler, daemon=True).start()


# ---------- pliki ----------
for sub in ("food-bc", "food-sx"):
    if (FOOD_DIR / sub).is_dir():
        app.mount("/" + sub, StaticFiles(directory=FOOD_DIR / sub), name=sub)


@app.get("/{name}")
def top_file(name: str):
    if name in ("food-pl.json", "food-gen.json") and (FOOD_DIR / name).is_file():
        return FileResponse(FOOD_DIR / name)
    p = HERE / "web" / name
    if p.is_file() and p.resolve().parent == (HERE / "web").resolve():
        return FileResponse(p)
    raise HTTPException(404)


app.mount("/ocrlib", StaticFiles(directory=HERE / "web" / "ocrlib"), name="ocrlib")


@app.get("/")
def index():
    return FileResponse(HERE / "web" / "index.html")
