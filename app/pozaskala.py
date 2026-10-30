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
                out.append({"time": f"{int(h):02d}:{int(m):02d}", "every": r.get("field", "days"),
                            **({"days": [int(d) for d in r.get("triggerAtDay") or []]} if r.get("field") == "weeks" else {})})
            else:
                out.append({"every": r.get("field"), "n": r.get(r.get("field", "") + "Interval")})
    return out


def _triggers(w):
    """Harmonogramy workflow (nazwa + godzina) – do przycisków „Uruchom jak …”."""
    try:
        from n8n_control import triggers
        out = []
        for t in triggers(w):
            s = _schedules([t])
            out.append({"name": t["name"], "time": (s[0].get("time") or s[0].get("cron") or s[0].get("every")) if s else ""})
        return out
    except Exception:
        return []


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
                  "schedules": _schedules(w.get("nodes")), "triggers": _triggers(w)} for w in wf["data"]]
    names = {w["id"]: w["name"] for w in workflows}
    execs = [{"id": e.get("id"), "workflowId": e.get("workflowId"), "workflow": names.get(e.get("workflowId"), "?"),
              "status": e.get("status") or ("success" if e.get("finished") else "error"), "mode": e.get("mode"),
              "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt"), "retryOf": e.get("retryOf")} for e in ex.get("data", [])]
    # wykonania w toku (część wersji n8n nie zwraca ich na zwykłej liście)
    try:
        run = httpx.get(f"{base}/api/v1/executions", params={"status": "running", "limit": 10}, headers=h, timeout=8).json()
        have = {e["id"] for e in execs}
        for e in run.get("data", []):
            if e.get("id") not in have:
                execs.insert(0, {"id": e.get("id"), "workflowId": e.get("workflowId"), "workflow": names.get(e.get("workflowId"), "?"),
                                 "status": "running", "mode": e.get("mode"), "startedAt": e.get("startedAt"), "stoppedAt": None})
    except Exception:
        pass
    # ręczne starty z centrum, dla których n8n jeszcze nie pokazał zakończonego wykonania
    from social import _read, _write
    from datetime import datetime as _dt
    S, started = _read("n8n_started.json", {}), []
    for wid, m in list(S.items()):
        def _ts(x):
            try:
                return _dt.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
            except Exception:
                return 0
        done = next((e for e in execs if e["workflowId"] == wid and _ts(e["startedAt"]) >= m["t"] - 90
                     and e["status"] not in ("running", "waiting", "new")), None)
        if done or time.time() - m["t"] > 3600:
            S.pop(wid)
            continue
        started.append({"workflowId": wid, "workflow": names.get(wid, m.get("name")), "t": m["t"], "trig": m.get("trig")})
    _write("n8n_started.json", S)
    data = {"ok": True, "workflows": workflows, "executions": execs, "started": started, "fetched": time.time()}
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
        # powiadomienie tylko, gdy publisher naprawdę coś wrzucił (o 0:00 jest film; puste przebiegi bez powiadomień)
        try:
            from agency import n8n
            rd = (n8n("GET", f"/api/v1/executions/{e['id']}", params={"includeData": "true"}).json().get("data") or {}).get("resultData", {}).get("runData", {})
            if not any("publish" in k.lower() and "container" in k.lower() for k in rd):
                return None
            try:
                t = rd["Parse Queue Entry"][0]["data"]["main"][0][0]["json"].get("title")
            except Exception:
                t = None
            return "🚀 Opublikowano", (f"„{t[:80]}” poszedł na Instagram." if t else "Film poszedł na Instagram.")
        except Exception:
            return None
    if re.search(r"analytic", wf, re.I):
        return "📊 Analityka gotowa", "Zaktualizowano listę FAVOR / REDUCE."
    return None


# ---------- samo-ponawianie po chwilowych błędach sieci (DNS, timeout, zerwane połączenie) ----------
NET_ERR = re.compile(r"DNS|ENOTFOUND|EAI_AGAIN|getaddrinfo|ETIMEDOUT|ECONNRESET|ECONNREFUSED|socket hang up|timed? ?out|"
                     r"network|server is offline|503|502|504|Bad Gateway|Service Unavailable", re.I)
RETRY_AFTER = [5 * 60, 15 * 60]          # 1. ponowienie po 5 min, 2. po kolejnych 15 min


def _net_error(eid):
    try:
        from n8n_control import execution
        d = execution(str(eid))
        txt = f"{d.get('message', '')} {d.get('description', '')}"
        return (d.get("node") or "?", txt) if NET_ERR.search(txt) else None
    except Exception:
        return None


def _autoretry_tick(push_all, _read, _write):
    """Zaplanowane ponowienia, których czas minął → n8n retry (od miejsca błędu)."""
    R = _read("n8n_autoretry.json", {})
    ch = False
    for eid, r in list(R.items()):
        if r.get("done") or time.time() < r.get("at", 0):
            if time.time() - r.get("t0", 0) > 86400:
                R.pop(eid); ch = True
            continue
        from n8n_control import retry
        res = retry(str(eid))
        r.update(done=True, ok=res.get("ok"))
        ch = True
        if not res.get("ok"):
            push_all("❌ Błąd w n8n", f"{r['wf']} – samo ponowienie nie ruszyło ({res.get('error') or ''}). Ponów ręcznie w Pipeline."[:180], url="/#pozaskala")
    if ch:
        _write("n8n_autoretry.json", R)


def _watch():
    from extra import push_all
    from social import _read, _write
    from agency import log
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
                    if not first and e["status"] in ("error", "crashed"):
                        R = _read("n8n_autoretry.json", {})
                        prev = R.get(str(e.get("retryOf") or ""), {})
                        n = prev.get("n", 0) if prev else 0
                        net = _net_error(e["id"])
                        if net and n < len(RETRY_AFTER):
                            R[str(e["id"])] = {"wf": e["workflow"], "n": n + 1, "at": time.time() + RETRY_AFTER[n],
                                               "t0": prev.get("t0") or time.time(), "node": net[0], "msg": net[1][:200]}
                            _write("n8n_autoretry.json", R)
                            log("op", f"{e['workflow']}: błąd sieci w „{net[0]}” – ponowię sam za {RETRY_AFTER[n] // 60} min "
                                      f"(próba {n + 1}/{len(RETRY_AFTER)})", "warn")
                            continue          # bez powiadomienia – najpierw próbujemy sami
                        if net:
                            push_all("❌ Błąd w n8n", f"{e['workflow']} – błąd sieci w „{net[0]}” nie minął mimo {n} ponowień. "
                                                     "Sprawdź serwer / ponów ręcznie.", url="/#pozaskala")
                            continue
                    if not first and e["status"] == "success" and e.get("retryOf"):
                        R = _read("n8n_autoretry.json", {})
                        if str(e["retryOf"]) in R:
                            log("op", f"{e['workflow']}: samo ponowienie po błędzie sieci wyszło ✅", "ok")
                    msg = None if first else _notify(e)
                    if msg:
                        push_all(*msg, url="/#pozaskala")
                _write("n8n_seen.json", sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)[-300:])
            _autoretry_tick(push_all, _read, _write)
        except Exception as ex:
            print("n8n watch:", ex, flush=True)
        time.sleep(60)


if os.environ.get("N8N_API_KEY"):
    threading.Thread(target=_watch, daemon=True).start()
