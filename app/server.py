"""Centrum social mediów – osobna, prywatna aplikacja (FastAPI). Logowanie hasłem, dane w /root/social."""
import asyncio, hashlib, hmac, os, secrets, sqlite3, time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("SOCIAL_HOME", "/root/social"))
ROOT.mkdir(parents=True, exist_ok=True)

for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
PASS = os.environ.get("SOCIAL_PASS", "")  # scrypt$salt$hash

_con = sqlite3.connect(ROOT / "sesje.db", check_same_thread=False, isolation_level=None)
_con.execute("CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, created REAL)")

from social import router  # noqa: E402  (po wczytaniu .env)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
COOKIE = "sm"
_fails = {}


def authed(req: Request):
    t = req.cookies.get(COOKIE)
    return bool(t) and _con.execute("SELECT 1 FROM sessions WHERE token=?", (t,)).fetchone() is not None


def check_pass(pw):
    try:
        _, salt, h = PASS.split("$")
        return hmac.compare_digest(hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex(), h)
    except Exception:
        return False


@app.middleware("http")
async def guard(req: Request, call_next):
    # /api/agency/hook/* woła n8n – tam sprawdzany jest token agencji, nie sesja
    if req.url.path.startswith("/api/") and req.url.path != "/api/login" and not req.url.path.startswith("/api/agency/hook/") and not authed(req):
        return JSONResponse({"error": "auth"}, status_code=401)
    resp = await call_next(req)
    if req.url.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


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
    _con.execute("INSERT INTO sessions VALUES(?,?)", (tok, time.time()))
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, tok, max_age=400 * 86400, httponly=True, secure=True, samesite="lax")
    return r


@app.post("/api/logout")
def logout(req: Request):
    _con.execute("DELETE FROM sessions WHERE token=?", (req.cookies.get(COOKIE, ""),))
    r = JSONResponse({"ok": True})
    r.delete_cookie(COOKIE)
    return r


app.include_router(router)
from extra import router as extra_router  # noqa: E402
app.include_router(extra_router)


from pozaskala import router as pozaskala_router  # noqa: E402
app.include_router(pozaskala_router)
from agency import router as agency_router  # noqa: E402
app.include_router(agency_router)
from scanner import router as scanner_router  # noqa: E402
app.include_router(scanner_router)


@app.get("/")
def index():
    return FileResponse(HERE / "web" / "index.html")


PAGES = {"home": "home.html", "agencja": "agencja.html", "pozaskala": "pozaskala.html", "skaner": "skaner.html", "skaner-stats": "skaner-stats.html"}


def _page(req: Request, name: str):
    # zakładki osadzane w index.html (iframe) – tylko po zalogowaniu
    if not authed(req):
        return HTMLResponse('<p style="font:14px system-ui;color:#888;padding:20px">Zaloguj się w centrum.</p>', status_code=401)
    return FileResponse(HERE / "web" / PAGES[name], headers={"Cache-Control": "no-cache"})


@app.get("/home")
def home_page(req: Request):
    return _page(req, "home")


@app.get("/agencja")
def agencja(req: Request):
    return _page(req, "agencja")


@app.get("/pozaskala")
def pozaskala(req: Request):
    return _page(req, "pozaskala")


@app.get("/skaner-stats")
def skaner_stats(req: Request):
    return _page(req, "skaner-stats")


@app.get("/skaner")
def skaner(req: Request):
    return _page(req, "skaner")


STATIC = {"manifest.webmanifest": "application/manifest+json", "icon-192.png": "image/png", "icon-512.png": "image/png",
          "icon-maskable-512.png": "image/png", "apple-touch-icon.png": "image/png", "favicon.png": "image/png",
          "sw.js": "text/javascript", "ikona-180.png": "image/png", "ikona-192.png": "image/png", "ikona-512.png": "image/png",
          "ikona-maskable-512.png": "image/png", "ikona-64.png": "image/png", "glass.css": "text/css"}


@app.get("/{name}")
def static_file(name: str):
    # ikony i manifest – potrzebne, żeby stronę dało się dodać do Docka / ekranu głównego
    if name not in STATIC:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    cc = "no-cache" if name == "sw.js" else "public, max-age=86400"
    return FileResponse(HERE / "web" / name, media_type=STATIC[name], headers={"Cache-Control": cc})
