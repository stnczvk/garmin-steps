"""Zakładka PozaSkala: podgląd workflow n8n (generator, Montaż film, Publisher...) przez publiczne API n8n.
Wymaga w /root/social/.env:  N8N_API_KEY=...  (n8n → Settings → n8n API → Create API key)
opcjonalnie:                  N8N_URL=http://127.0.0.1:5678
"""
import os, re, threading, time

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


# ---------- powiadomienia push z n8n (zamiast Telegrama) ----------
def _notify(e):
    """Treść powiadomienia dla zakończonego uruchomienia albo None (bez powiadomienia)."""
    wf, st = e["workflow"], e["status"]
    if st in ("error", "crashed"):
        return "❌ Błąd w n8n", f"{wf} – uruchomienie nie powiodło się. Szczegóły w zakładce Pipeline."
    if st != "success":
        return None
    if re.search(r"generator", wf, re.I):
        return "🎬 Nowy film gotowy", f"{wf}: materiały wygenerowane, montaż w drodze."
    if re.search(r"monta", wf, re.I):
        return "✅ Film zmontowany", "final.mp4 jest w kolejce do publikacji."
    if re.search(r"publish", wf, re.I):
        return "🚀 Opublikowano", "Film poszedł na YouTube i Instagram."
    if re.search(r"analytic", wf, re.I):
        return "📊 Analityka gotowa", "Zaktualizowano listę FAVOR / REDUCE."
    return None


def _watch():
    from extra import push_all
    from social import _read, _write
    while True:
        try:
            s = status(force=1)
            if s.get("ok"):
                done = [e for e in s["executions"] if e["status"] not in ("running", "waiting", "new")]
                seen = set(_read("n8n_seen.json", []))
                first = not seen
                for e in reversed(done):
                    if str(e["id"]) in seen:
                        continue
                    seen.add(str(e["id"]))
                    msg = None if first else _notify(e)
                    if msg:
                        push_all(*msg, url="/#pozaskala")
                _write("n8n_seen.json", sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)[-300:])
        except Exception:
            pass
        time.sleep(60)


if os.environ.get("N8N_API_KEY"):
    threading.Thread(target=_watch, daemon=True).start()
