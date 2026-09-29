"""Dziennik treningowy – prywatny serwer (FastAPI + SQLite).

Baza dokumentów w stylu kolekcja/dokument, logowanie hasłem, pośrednik do API Claude
(odczyt etykiet i zrzutów), import kroków i cardio z Garmina.
"""
import asyncio, base64, hashlib, hmac, io, json, os, re, secrets, sqlite3, subprocess, threading, time
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
FOOD_DIR = Path(os.environ.get("FOOD_DIR", "/root/garmin-sync/food_out"))
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
    if p.startswith("/api/") and p not in ("/api/login", "/api/me") and not authed(req):
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
    _con.execute("INSERT INTO ai_usage VALUES(?,?,?,?) ON CONFLICT(day) DO UPDATE SET n=n+1, tin=tin+excluded.tin, tout=tout+excluded.tout",
                 (day, 1, u.get("input_tokens", 0), u.get("output_tokens", 0)))
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    try:
        return {"json": _json_from(text)}
    except Exception:
        return JSONResponse({"error": "Nie udało się odczytać odpowiedzi.", "code": "ai_error", "raw": text[:500]}, status_code=502)


@app.get("/api/ai/usage")
def ai_usage():
    rows = _con.execute("SELECT day,n,tin,tout FROM ai_usage ORDER BY day DESC LIMIT 31").fetchall()
    return [{"day": d, "n": n, "in": i, "out": o} for d, n, i, o in rows]


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
