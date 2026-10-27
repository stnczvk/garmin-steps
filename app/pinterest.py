"""Pinterest – automat do pinów: 2 piny dziennie (10:00 i 19:00) z puli grafik sklepu PlanPerfect, z linkiem do oferty Etsy.
Pinterest API v5 (OAuth). Uwaga: z dostępem „Trial” piny widzi tylko właściciel – publicznie dopiero po „Standard”."""
import base64, json, secrets, threading, time, urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from social import _read, _write

router = APIRouter()
CFG = "pinterest_secret.json"
DATA = "pinterest.json"
API = "https://api.pinterest.com/v5"
SCOPES = "boards:read,boards:write,pins:read,pins:write,user_accounts:read"
TZ = ZoneInfo("Europe/Warsaw")
MAN = Path(__file__).parent / "pins_manifest.json"
DEF = {"auto": False, "hours": [10, 19]}   # Pinterest nie pozwala na automatyczne publikowanie z puli (wytyczne dla deweloperów)
_lk = threading.Lock()


def _cfg():
    return _read(CFG, {})


def _redirect(req):
    host = req.headers.get("x-forwarded-host") or req.headers.get("host") or ""
    return f"https://{host}/api/pinterest/callback"


def _basic(c):
    return "Basic " + base64.b64encode(f"{c['app_id']}:{c['secret']}".encode()).decode()


def _token(c):
    if c.get("access") and time.time() < c.get("exp", 0) - 300:
        return c
    r = httpx.post(API + "/oauth/token", headers={"Authorization": _basic(c)}, timeout=30,
                   data={"grant_type": "refresh_token", "refresh_token": c["refresh"]})
    j = r.json()
    if r.status_code >= 300 or "access_token" not in j:
        raise RuntimeError("Pinterest: odświeżenie tokenu nie wyszło – połącz ponownie")
    c.update(access=j["access_token"], exp=time.time() + int(j.get("expires_in", 2592000)))
    if j.get("refresh_token"):
        c["refresh"] = j["refresh_token"]
    _write(CFG, c)
    return c


def _call(method, path, **kw):
    c = _token(_cfg())
    r = httpx.request(method, API + path, headers={"Authorization": "Bearer " + c["access"]}, timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"Pinterest {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def _base():
    import os
    dom = os.environ.get("SOCIAL_DOMAIN") or "centrum-dowodzenia.duckdns.org"
    return f"https://{dom}"


def _queue():
    """Kolejka pinów: na zmianę produkty, najpierw pin 1 każdego produktu, potem pin 2 itd. Tylko produkty z ofertą na Etsy."""
    try:
        M = json.loads(MAN.read_text(encoding="utf-8"))
    except Exception:
        return []
    try:
        import etsy_manage as EM
        links = {k: f"https://www.etsy.com/listing/{v}" for k, v in EM._map().items() if v}
    except Exception:
        links = {}
    order = ["hb", "bp", "sb", "dp", "fit", "cb", "sg", "wp", "sp", "wo", "mp"]
    Q = []
    for i in range(5):
        for k in order:
            p = M.get(k)
            if p and k in links and i < len(p["pins"]):
                Q.append({"key": f"{k}:{i}", "code": k, "img": p["pins"][i]["img"], "title": p["pins"][i]["title"][:100],
                          "desc": p["desc"][:500], "board": p["board"], "link": links[k]})
    return Q


def _boards():
    B, bm = {}, None
    while True:
        j = _call("GET", "/boards", params={"page_size": 100, **({"bookmark": bm} if bm else {})})
        for b in j.get("items", []):
            B[b["name"]] = b["id"]
        bm = j.get("bookmark")
        if not bm:
            return B


def post_next(n=1, manual=False):
    with _lk:
        D = _read(DATA, {})
        done = set(D.get("posted", {}))
        todo = [q for q in _queue() if q["key"] not in done][:n]
        if not todo:
            D["last"] = "kolejka pusta – wszystkie piny opublikowane"; _write(DATA, D)
            return {"ok": True, "posted": 0}
        B = _boards()
        out = 0
        for q in todo:
            bid = B.get(q["board"])
            if not bid:
                bid = _call("POST", "/boards", json={"name": q["board"], "description": "Google Sheets templates by PlanPerfect Sheets"})["id"]
                B[q["board"]] = bid
            r = _call("POST", "/pins", json={"board_id": bid, "title": q["title"], "description": q["desc"], "link": q["link"],
                                             "alt_text": q["title"], "media_source": {"source_type": "image_url", "url": f"{_base()}/pins/{q['img']}"}})
            D.setdefault("posted", {})[q["key"]] = {"t": time.time(), "id": r.get("id"), "title": q["title"]}
            out += 1
        D["last"] = f"opublikowano {out} pin(y) · {datetime.now(TZ):%d.%m %H:%M}"; D["error"] = None
        _write(DATA, D)
    return {"ok": True, "posted": out}


def _loop():
    time.sleep(120)
    while True:
        try:
            c, D = _cfg(), _read(DATA, {})
            st = {**DEF, **(D.get("cfg") or {})}
            now = datetime.now(TZ)
            slot = f"{now:%Y-%m-%d}-{now.hour}"
            if False and c.get("refresh") and st["auto"] and now.hour in st["hours"] and D.get("slot") != slot:
                D["slot"] = slot; _write(DATA, D)
                post_next(1)
        except Exception as e:
            D = _read(DATA, {}); D["error"] = str(e)[:200]; _write(DATA, D)
        time.sleep(300)


threading.Thread(target=_loop, daemon=True).start()


# ---------------------------------------------------------------- API
@router.get("/api/pinterest")
def state(req: Request):
    c, D = _cfg(), _read(DATA, {})
    Q = _queue(); done = D.get("posted", {})
    left = [q for q in Q if q["key"] not in done]
    return {"has_keys": bool(c.get("app_id")), "connected": bool(c.get("refresh")), "callback": _redirect(req), "user": c.get("user"),
            "cfg": {**DEF, **(D.get("cfg") or {})}, "posted": len(done), "left": len(left), "next": left[:4], "last": D.get("last"),
            "error": D.get("error"), "recent": sorted(done.values(), key=lambda x: -x["t"])[:6]}


@router.post("/api/pinterest/keys")
async def keys(req: Request):
    b = await req.json(); c = _cfg()
    c.update(app_id=str(b.get("app_id", "")).strip(), secret=str(b.get("secret", "")).strip())
    _write(CFG, c)
    return {"ok": True}


@router.get("/api/pinterest/connect")
def connect(req: Request):
    c = _cfg()
    if not c.get("app_id"):
        return RedirectResponse("/#etsy")
    c["state"] = secrets.token_urlsafe(16); _write(CFG, c)
    q = {"client_id": c["app_id"], "redirect_uri": _redirect(req), "response_type": "code", "scope": SCOPES, "state": c["state"]}
    return RedirectResponse("https://www.pinterest.com/oauth/?" + urllib.parse.urlencode(q))


@router.get("/api/pinterest/callback")
def callback(req: Request, code: str = "", state: str = "", error: str = ""):
    c, D = _cfg(), _read(DATA, {})
    if error or not code or state != c.get("state"):
        D["error"] = "Połączenie z Pinterestem przerwane: " + (error or "zły stan"); _write(DATA, D)
        return RedirectResponse("/#etsy")
    r = httpx.post(API + "/oauth/token", headers={"Authorization": _basic(c)}, timeout=30,
                   data={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect(req)})
    j = r.json()
    if r.status_code >= 300 or "access_token" not in j:
        D["error"] = "Pinterest nie wydał tokenu: " + str(j)[:150]; _write(DATA, D)
        return RedirectResponse("/#etsy")
    c.update(access=j["access_token"], refresh=j.get("refresh_token"), exp=time.time() + int(j.get("expires_in", 2592000)))
    c.pop("state", None); _write(CFG, c)
    try:
        c["user"] = _call("GET", "/user_account").get("username"); _write(CFG, c)
    except Exception:
        pass
    D["error"] = None; _write(DATA, D)
    return RedirectResponse("/#etsy")


@router.post("/api/pinterest/cfg")
async def set_cfg(req: Request):
    b = await req.json(); D = _read(DATA, {})
    cfg = {**DEF, **(D.get("cfg") or {})}
    if "auto" in b:
        cfg["auto"] = bool(b["auto"])
    if b.get("hours"):
        cfg["hours"] = sorted({int(h) for h in b["hours"] if 0 <= int(h) <= 23})[:4]
    D["cfg"] = cfg; _write(DATA, D)
    return {"ok": True, "cfg": cfg}


@router.post("/api/pinterest/now")
def now():
    try:
        return post_next(1, manual=True)
    except Exception as e:
        D = _read(DATA, {}); D["error"] = str(e)[:200]; _write(DATA, D)
        return JSONResponse({"error": str(e)[:200]}, 400)


@router.post("/api/pinterest/disconnect")
def disconnect():
    c = _cfg()
    for k in ("access", "refresh", "exp", "user"):
        c.pop(k, None)
    _write(CFG, c)
    return {"ok": True}


@router.get("/privacy")
@router.get("/planperfectsheets-privacy-policy")
def privacy():
    return HTMLResponse("""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Privacy policy – PlanPerfect Centrum</title>
<body style="font:16px/1.6 system-ui;max-width:720px;margin:40px auto;padding:0 20px;color:#222"><h1>PlanPerfect Sheets – Privacy policy</h1>
<p>PlanPerfect Centrum is a private tool used only by the owner of the PlanPerfect Sheets shop to schedule Pins of the shop's own products on the owner's own Pinterest account and to view the shop's statistics.</p>
<p>The app stores only the OAuth tokens needed for this, on the owner's private server. No data of other users is collected, shared or sold.</p>
<p>To revoke access, disconnect the app in Pinterest settings (Security → Connected apps) or in the app itself.</p>
<p>Contact: via the PlanPerfect Sheets shop on Etsy.</p></body>""")
