"""Ustawienia widoku wspólne dla wszystkich urządzeń (np. ukryte pozycje na ekranie głównym)."""
from fastapi import APIRouter, Request

from social import _read, _write

router = APIRouter()
F = "ui_prefs.json"


@router.get("/api/ui/hidden")
def hidden_get():
    return {"keys": _read(F, {}).get("hidden", [])}


@router.post("/api/ui/hidden")
async def hidden_add(req: Request):
    b = await req.json()
    keys = [str(k)[:120] for k in (b.get("keys") or []) if k][:200]
    P = _read(F, {})
    H = [k for k in P.get("hidden", []) if k not in keys] + keys
    P["hidden"] = H[-600:]
    _write(F, P)
    return {"ok": True, "keys": P["hidden"]}
