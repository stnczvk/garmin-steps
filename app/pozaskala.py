"""Zakładka PozaSkala: podgląd workflow n8n (generator, Montaż film, Publisher...) przez publiczne API n8n.
Wymaga w /root/social/.env:  N8N_API_KEY=...  (n8n → Settings → n8n API → Create API key)
opcjonalnie:                  N8N_URL=http://127.0.0.1:5678
"""
import os, time

import httpx
from fastapi import APIRouter

router = APIRouter()
_cache = {"t": 0, "data": None}


def _schedules(nodes):
    """Godziny uruchomień z node'ów Schedule Trigger / Cron."""
    out = []
    for n in nodes or []:
        if n.get("disabled") or "schedule" not in n.get("type", "").lower() and "cron" not in n.get("type", "").lower():
            continue
        p = n.get("parameters") or {}
        for r in (p.get("rule") or {}).get("interval") or []:
            if r.get("field") == "cronExpression":
                out.append({"cron": r.get("expression", "")})
            elif "triggerAtHour" in r or r.get("field") in ("days", "weeks", None):
                h, m = r.get("triggerAtHour", 0), r.get("triggerAtMinute", 0)
                out.append({"time": f"{int(h):02d}:{int(m):02d}", "every": r.get("field", "days")})
            else:
                out.append({"every": r.get("field"), "n": r.get(r.get("field", "") + "Interval")})
    return out


@router.get("/api/pozaskala/status")
def status(force: int = 0):
    if _cache["data"] and not force and time.time() - _cache["t"] < 30:
        return _cache["data"]
    key = os.environ.get("N8N_API_KEY", "")
    base = os.environ.get("N8N_URL", "http://127.0.0.1:5678").rstrip("/")
    if not key:
        return {"ok": False, "error": "Brak N8N_API_KEY w /root/social/.env"}
    h = {"X-N8N-API-KEY": key, "accept": "application/json"}
    try:
        wf = httpx.get(f"{base}/api/v1/workflows", params={"limit": 100}, headers=h, timeout=15).json()
        ex = httpx.get(f"{base}/api/v1/executions", params={"limit": 60}, headers=h, timeout=15).json()
    except Exception as e:
        return {"ok": False, "error": f"n8n nie odpowiada pod {base}: {e}"}
    if "data" not in wf:
        return {"ok": False, "error": "n8n: " + str(wf.get("message", wf))[:200]}
    workflows = [{"id": w["id"], "name": w.get("name", ""), "active": w.get("active", False),
                  "updatedAt": w.get("updatedAt"), "nodes": len(w.get("nodes") or []),
                  "schedules": _schedules(w.get("nodes"))} for w in wf["data"]]
    names = {w["id"]: w["name"] for w in workflows}
    execs = [{"id": e.get("id"), "workflowId": e.get("workflowId"), "workflow": names.get(e.get("workflowId"), "?"),
              "status": e.get("status") or ("success" if e.get("finished") else "error"), "mode": e.get("mode"),
              "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt")} for e in ex.get("data", [])]
    data = {"ok": True, "workflows": workflows, "executions": execs, "fetched": time.time()}
    _cache.update(t=time.time(), data=data)
    return data
