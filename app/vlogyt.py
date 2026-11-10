"""Vlog YT: przycisk w centrum → Mac robi wstępne cięcie pod CapCut (każde nagranie osobnym blokiem, wycięta cisza).
Mac co kilka minut zgłasza listę folderów z nagraniami (Biurko/ŁysyVlog/...), centrum zleca zadanie, Mac je wykonuje."""
import secrets, threading, time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
F = "vlogyt.json"
_lock = threading.Lock()


def _rd():
    from social import _read
    return _read(F, {"folders": [], "seen": 0, "jobs": []})


def _wr(d):
    from social import _write
    _write(F, d)


def _tok(req):
    from montaz import _tok as t
    return t(req)


@router.get("/api/vlogyt")
def api_state():
    d = _rd()
    return {"folders": d["folders"], "mac": time.time() - d.get("seen", 0) < 600, "jobs": list(reversed(d["jobs"][-10:]))}


@router.post("/api/vlogyt/job")
async def api_job(req: Request):
    b = await req.json()
    f = (b.get("folder") or "").strip()
    with _lock:
        d = _rd()
        if f not in [x["name"] for x in d["folders"]]:
            return JSONResponse({"error": "Nie ma takiego folderu na Macu"}, status_code=400)
        if any(j["state"] in ("queued", "working") for j in d["jobs"]):
            return JSONResponse({"error": "Jedno cięcie już trwa – poczekaj, aż się skończy"}, status_code=409)
        j = {"id": secrets.token_hex(4), "folder": f, "state": "queued", "step": "Czeka na Maca…", "created": time.time()}
        d["jobs"] = (d["jobs"] + [j])[-30:]
        _wr(d)
    return j


@router.post("/api/mont-hook/vlogyt/folders")
async def hook_folders(req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    with _lock:
        d = _rd()
        d.update(folders=(b.get("folders") or [])[:60], seen=time.time())
        _wr(d)
    return {"ok": True}


@router.get("/api/mont-hook/vlogyt/next")
def hook_next(req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    with _lock:
        d = _rd()
        for j in d["jobs"]:
            if j["state"] == "working" and time.time() - j.get("at", 0) > 3 * 3600:
                j.update(state="error", error="przerwane (Mac się wyłączył?) – kliknij jeszcze raz")
        j = next((j for j in d["jobs"] if j["state"] == "queued"), None)
        if j:
            j.update(state="working", step="Mac zaczyna…", at=time.time())
        _wr(d)
    return {"job": j}


@router.post("/api/mont-hook/vlogyt/{jid}/status")
async def hook_status(jid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    with _lock:
        d = _rd()
        j = next((j for j in d["jobs"] if j["id"] == jid), None)
        if j:
            j.update({k: v for k, v in b.items() if k in ("state", "step", "error", "project")}, at=time.time())
            _wr(d)
    if j and b.get("state") in ("done", "error"):
        try:
            from extra import push_all
            push_all("🎬 Vlog YT" + (" – kawałki gotowe do CapCut" if b["state"] == "done" else ": błąd"),
                     (b.get("project") or b.get("error") or "")[:120], "/#montazownia")
        except Exception:
            pass
    return {"ok": True}
