"""Generator grafik do vloga (Montażownia → Grafiki do vloga).
Centrum przyjmuje zlecenie (przejścia dni / własny napis / miniatura), Mac odbiera je razem z montażami
(/api/mont-hook/next zwraca id „g-…”), renderuje u siebie i odsyła podglądy. Pliki lądują na Macu w „Animacje vlog YT”."""
import os, secrets, threading, time

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from social import _read, _write, ROOT

router = APIRouter()
F = "grafiki.json"
DIR = ROOT / "grafiki_prev"
_lk = threading.RLock()
TYPES = {"dni", "napis", "miniatura", "godzina"}


def _tok(req):
    t, want = req.headers.get("x-mont-token", ""), os.environ.get("MONT_TOKEN", "")
    return bool(want) and secrets.compare_digest(t, want)


def _jobs():
    return _read(F, [])


def claim():
    """Wołane z /api/mont-hook/next, gdy Mac nie ma montażu: następne zlecenie grafiki."""
    with _lk:
        L = _jobs()
        if any(j["state"] in ("claimed", "working") and time.time() - j.get("claimed", 0) < 900 for j in L):
            return None
        j = next((j for j in L if j["state"] == "queued"), None)
        if not j:
            return None
        j.update(state="claimed", step="Mac zaczyna…", claimed=time.time())
        _write(F, L)
        return "g-" + j["id"]


@router.get("/api/grafiki")
def api_list():
    v = _read("mont_vlog.json", {})
    seen = (v.get("helper") or {}).get("seen")
    now = time.time()
    J = [j for j in _jobs() if not (j.get("state") == "error" and now - (j.get("at") or j.get("created") or 0) > 24 * 3600)]   # stare błędy znikają po dobie
    return {"jobs": list(reversed(J[-30:])), "mac_online": bool(seen and time.time() - seen < 90)}


@router.post("/api/grafiki/clear-errors")
def api_clear_errors():
    L = [j for j in _jobs() if j.get("state") != "error"]
    _write(F, L)
    return {"ok": True}


@router.post("/api/grafiki")
async def api_new(req: Request):
    b = await req.json()
    tp = b.get("type")
    if tp not in TYPES:
        return {"ok": False, "error": "nieznany rodzaj"}
    if tp in ("napis", "miniatura") and not str(b.get("text", "")).strip():
        return {"ok": False, "error": "Wpisz tekst."}
    if tp == "godzina" and not str(b.get("times", "")).strip():
        return {"ok": False, "error": "Wpisz godziny, np. 7:30 Pobudka."}
    if tp == "dni" and not b.get("start"):
        return {"ok": False, "error": "Wybierz datę."}
    keep = {"dni": ("start", "days"), "napis": ("text", "small", "kind"), "miniatura": ("text", "sub", "tag", "layout", "accent", "count", "photo", "food", "food_folder"),
            "godzina": ("times", "style")}[tp]
    j = {"id": secrets.token_hex(4), "type": tp, "params": {k: b.get(k) for k in keep if b.get(k) not in (None, "")},
         "state": "queued", "step": "czeka na Maca", "created": time.time(), "files": [], "previews": 0}
    with _lk:
        L = _jobs(); L.append(j); _write(F, L[-60:])
    return {"ok": True, "job": j}


UP = ROOT / "grafiki_up"


@router.post("/api/grafiki/upload")
async def api_upload(req: Request):
    """Zdjęcie do miniatury (Twoje albo jedzenie) – przeglądarka wysyła już JPEG (max ~2000 px)."""
    data = await req.body()
    if not data or len(data) > 12_000_000 or not data[:3] == b"\xff\xd8\xff":
        return {"ok": False, "error": "Wyślij zdjęcie JPG (max 12 MB)."}
    UP.mkdir(parents=True, exist_ok=True)
    for f in UP.glob("*.jpg"):                      # sprzątanie: starsze niż 14 dni
        if time.time() - f.stat().st_mtime > 14 * 86400:
            f.unlink(missing_ok=True)
    fid = secrets.token_hex(6)
    (UP / f"{fid}.jpg").write_bytes(data)
    return {"ok": True, "id": fid}


@router.get("/api/mont-hook/grafika-plik/{fid}")
def hook_file(fid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    f = UP / f"{os.path.basename(fid)}.jpg"
    if not f.is_file():
        return JSONResponse({"error": "brak"}, status_code=404)
    return FileResponse(f, media_type="image/jpeg")


@router.post("/api/grafiki/{gid}/retry")
def api_retry(gid: str):
    with _lk:
        L = _jobs()
        for j in L:
            if j["id"] == gid:
                j.update(state="queued", step="czeka na Maca", error=None)
        _write(F, L)
    return {"ok": True}


@router.get("/api/grafiki/{gid}/preview/{i}")
def api_prev(gid: str, i: int):
    p = DIR / f"{gid}_{i}.jpg"
    if not p.exists() or "/" in gid:
        return JSONResponse({"error": "brak"}, status_code=404)
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# ---------- Mac ----------
def _find(L, gid):
    gid = gid[2:] if gid.startswith("g-") else gid
    return next((j for j in L if j["id"] == gid), None)


@router.get("/api/mont-hook/grafika/{gid}")
def hook_job(gid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    j = _find(_jobs(), gid)
    return j and {"id": j["id"], "type": j["type"], **(j.get("params") or {})}


@router.post("/api/mont-hook/grafika/{gid}/status")
async def hook_status(gid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    with _lk:
        L = _jobs(); j = _find(L, gid)
        if not j:
            return {"ok": False}
        for k in ("state", "step", "files", "error"):
            if k in b:
                j[k] = b[k]
        if b.get("state") in ("done", "error"):
            j["finished"] = time.time()
        _write(F, L)
    if b.get("state") == "error":   # powiadomienie tylko przy błędzie – gotowe grafiki widać w Montażowni
        try:
            from extra import push_all
            push_all("🎨 Grafika: błąd na Macu", (b.get("error") or "")[:120], "/#montazownia")
        except Exception:
            pass
    return {"ok": True}


@router.put("/api/mont-hook/grafika/{gid}/preview")
async def hook_prev(gid: str, req: Request, i: int = 0):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    DIR.mkdir(parents=True, exist_ok=True)
    data = await req.body()
    with _lk:
        L = _jobs(); j = _find(L, gid)
        if not j or len(data) > 4_000_000:
            return {"ok": False}
        (DIR / f"{j['id']}_{int(i)}.jpg").write_bytes(data)
        j["previews"] = max(j.get("previews", 0), int(i) + 1)
        _write(F, L)
    return {"ok": True}
