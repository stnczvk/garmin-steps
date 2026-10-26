"""Centrum social mediów – osobna, prywatna aplikacja (FastAPI). Logowanie hasłem, dane w /root/social."""
import asyncio, hashlib, hmac, os, secrets, socket, sqlite3, time
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

# IPv4 najpierw: na VPS połączenia po IPv6 do Instagrama/Google potrafiły kończyć się „[Errno 104] Connection reset by peer”
# (curl przełącza się sam, httpx nie). Adresy IPv6 zostają jako zapas.
_gai = socket.getaddrinfo


def _gai_v4_first(*a, **k):
    res = _gai(*a, **k)
    return sorted(res, key=lambda r: r[0] != socket.AF_INET)


if os.environ.get("PREFER_IPV6") != "1":
    socket.getaddrinfo = _gai_v4_first

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
    if req.url.path.startswith("/api/") and req.url.path != "/api/login" and not req.url.path.startswith(("/api/agency/hook/", "/api/mont-hook/")) and not authed(req):
        return JSONResponse({"error": "auth"}, status_code=401)
    try:
        resp = await call_next(req)
    except Exception as e:  # zapis błędu (czytany przez diagnostykę MCP: errors_log.json) zamiast gołego „Internal Server Error”
        import traceback
        from social import _read as _rd, _write as _wr
        try:
            L = _rd("errors_log.json", [])
            L.append({"t": time.time(), "path": req.url.path, "method": req.method, "err": f"{type(e).__name__}: {e}"[:300],
                      "tb": traceback.format_exc()[-1800:]})
            _wr("errors_log.json", L[-60:])
        except Exception:
            pass
        return JSONResponse({"error": f"Błąd serwera ({type(e).__name__}) – zapisany w errors_log.json"}, status_code=500)
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


BOOT = str(int(time.time()))


@app.get("/api/version")
def version():
    return {"v": BOOT}


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
from guardians import router as guardians_router  # noqa: E402
app.include_router(guardians_router)
from n8n_control import router as n8n_router  # noqa: E402
app.include_router(n8n_router)
from montaz import router as montaz_router  # noqa: E402
app.include_router(montaz_router)
from agency_extra import router as agency_extra_router  # noqa: E402
app.include_router(agency_extra_router)
from costs_full import router as costs_full_router  # noqa: E402
app.include_router(costs_full_router)
from notes import router as notes_router  # noqa: E402
app.include_router(notes_router)
from scanner_team import router as scanner_team_router  # noqa: E402
app.include_router(scanner_team_router)
from wallets import router as wallets_router  # noqa: E402
app.include_router(wallets_router)
from prefs import router as prefs_router  # noqa: E402
app.include_router(prefs_router)
from updater import router as updater_router  # noqa: E402
app.include_router(updater_router)
from hermes_chat import router as hermes_router  # noqa: E402
app.include_router(hermes_router)
from etsy_manage import router as etsy_manage_router  # noqa: E402
app.include_router(etsy_manage_router)
from pinterest import router as pinterest_router  # noqa: E402
app.include_router(pinterest_router)
from dlugi import router as dlugi_router  # noqa: E402
app.include_router(dlugi_router)
from etsy import router as etsy_router  # noqa: E402
app.include_router(etsy_router)
from planer import router as planer_router  # noqa: E402
app.include_router(planer_router)


@app.get("/")
def index():
    return FileResponse(HERE / "web" / "index.html")


PAGES = {"home": "home.html", "agencja": "agencja.html", "pozaskala": "pozaskala.html", "skaner": "skaner.html", "skaner-stats": "skaner-stats.html", "skaner-sygnaly": "skaner-sygnaly.html", "skaner-boty": "skaner-boty.html", "straznicy": "straznicy.html", "powiadomienia": "powiadomienia.html", "montazownia": "montazownia.html", "notatki": "notatki.html", "hasla": "hasla.html", "etsy": "etsy.html"}


def _page(req: Request, name: str):
    # zakładki osadzane w index.html (iframe) – tylko po zalogowaniu
    if not authed(req):
        return HTMLResponse('<p style="font:14px system-ui;color:#888;padding:20px">Zaloguj się w centrum.</p>', status_code=401)
    return FileResponse(HERE / "web" / PAGES[name], headers={"Cache-Control": "no-cache"})


@app.get("/pins/{name}")
def pin_image(name: str):
    # grafiki pinów na Pinterest – publiczne (Pinterest pobiera je przez link z pliku CSV)
    f = HERE / "web" / "pins" / os.path.basename(name)
    if f.suffix.lower() not in (".jpg", ".png") or not f.is_file():
        return JSONResponse({"error": "brak"}, status_code=404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/home")
def home_page(req: Request):
    return _page(req, "home")


@app.get("/agencja")
def agencja(req: Request):
    return _page(req, "agencja")


@app.get("/pozaskala")
def pozaskala(req: Request):
    return _page(req, "pozaskala")


@app.get("/straznicy")
def straznicy(req: Request):
    return _page(req, "straznicy")


@app.get("/montazownia")
def montazownia(req: Request):
    return _page(req, "montazownia")


@app.get("/notatki")
def notatki(req: Request):
    return _page(req, "notatki")


@app.get("/hasla")
def hasla(req: Request):
    return _page(req, "hasla")


@app.get("/etsy")
def etsy_page(req: Request):
    return _page(req, "etsy")


@app.get("/powiadomienia")
def powiadomienia(req: Request):
    return _page(req, "powiadomienia")


@app.get("/skaner-stats")
def skaner_stats(req: Request):
    return _page(req, "skaner-stats")


@app.get("/skaner-sygnaly")
def skaner_sygnaly(req: Request):
    return _page(req, "skaner-sygnaly")


@app.get("/skaner-boty")
def skaner_boty(req: Request):
    return _page(req, "skaner-boty")


@app.get("/skaner")
def skaner(req: Request):
    return _page(req, "skaner")


STATIC = {"manifest.webmanifest": "application/manifest+json", "icon-192.png": "image/png", "icon-512.png": "image/png",
          "icon-maskable-512.png": "image/png", "apple-touch-icon.png": "image/png", "favicon.png": "image/png",
          "sw.js": "text/javascript", "ikona-180.png": "image/png", "ikona-192.png": "image/png", "ikona-512.png": "image/png",
          "ikona-maskable-512.png": "image/png", "ikona-64.png": "image/png", "glass.css": "text/css", "v3.css": "text/css", "glass.js": "text/javascript", "biuro3d_montaz.js": "text/javascript"}


@app.get("/{name}")
def static_file(name: str):
    # ikony i manifest – potrzebne, żeby stronę dało się dodać do Docka / ekranu głównego
    if name not in STATIC:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    cc = "no-cache" if name in ("sw.js", "glass.css", "v3.css", "glass.js", "biuro3d_montaz.js") else "public, max-age=86400"
    return FileResponse(HERE / "web" / name, media_type=STATIC[name], headers={"Cache-Control": cc})
