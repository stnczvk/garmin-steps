"""Ręczne sterowanie n8n z centrum: uruchom dowolny workflow, ponów nieudane uruchomienie, podgląd błędu.

Publiczne API n8n nie ma „uruchom workflow”, więc przy pierwszym użyciu centrum dokłada do workflow
webhook „Centrum: start” (+ sprawdzenie tokenu), podpięty do tych samych node'ów co jego harmonogram.
Harmonogram działa dalej bez zmian.
"""
import copy, os, time, uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import agency_n8n as AN
from agency import http, log, n8n, settings

router = APIRouter()
TAG = "Centrum: "
TRIGGERS = ("scheduleTrigger", "cron", "manualTrigger", "intervalTrigger")


def _base():
    return os.environ.get("N8N_URL", "http://127.0.0.1:5678").rstrip("/")


def _bust():
    try:
        from pozaskala import _cache
        _cache["t"] = 0
    except Exception:
        pass


# Ręczny start ma dać workflow te same dane, co jego harmonogram (Schedule Trigger zwraca datę/godzinę
# uruchomienia – z niej np. Publisher liczy godzinę publikacji). Bez tego publikacja wypadała na 0:00.
SLOT_JS = r"""// Centrum – ręczne uruchomienie udaje start z harmonogramu: najbliższy termin z harmonogramu (__H__:__M__)
const tz = '__TZ__';
const now = DateTime.now().setZone(tz);
let d = now;
if (__H__ >= 0) {
  d = now.set({ hour: __H__, minute: __M__, second: 0, millisecond: 0 });
  if (d < now) d = d.plus({ days: 1 });
}
return [{ json: {
  timestamp: d.toISO(), 'Readable date': d.toFormat('MMMM d yyyy, h:mm:ss a'), 'Readable time': d.toFormat('h:mm:ss a'),
  'Day of week': d.toFormat('cccc'), Year: d.toFormat('yyyy'), Month: d.toFormat('MMMM'), 'Day of month': d.toFormat('dd'),
  Hour: d.toFormat('HH'), Minute: d.toFormat('mm'), Second: d.toFormat('ss'), Timezone: tz + ' (UTC' + d.toFormat('ZZ') + ')',
  manualFromCentrum: true,
} }];"""


def _hook_path(wf):
    """Ścieżka webhooka, którym centrum może wystartować workflow (albo None, gdy trzeba go dodać/odświeżyć)."""
    names = {n["name"] for n in wf.get("nodes", [])}
    for n in wf.get("nodes", []):
        if n["type"].endswith("webhook") and n["name"] == "Agencja: start":
            return n["parameters"].get("path")
        if n["type"].endswith("webhook") and n["name"] == TAG + "start" and TAG + "jak harmonogram" in names:
            return n["parameters"].get("path")
    return None


def _slot(trig):
    for r in ((trig.get("parameters") or {}).get("rule") or {}).get("interval") or []:
        if r.get("field") in ("days", "weeks", None) or "triggerAtHour" in r:
            return int(r.get("triggerAtHour", 0)), int(r.get("triggerAtMinute", 0))
    return -1, 0


def _add_hook(wf, token):
    wf = copy.deepcopy(wf)
    old = {n["name"] for n in wf["nodes"] if n["name"].startswith(TAG)}  # starsza wersja przycisku – wymieniamy
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in old]
    for k in old:
        wf["connections"].pop(k, None)
    C = wf["connections"]
    trig = next((n for n in wf["nodes"] if n["type"].split(".")[-1] in TRIGGERS and not n.get("disabled") and n["name"] in C), None)
    if not trig:
        return None, None
    tx, ty = trig["position"]
    path = f"centrum-run-{wf['id']}"
    wf["nodes"].append({"parameters": {"httpMethod": "POST", "path": path, "options": {}}, "id": str(uuid.uuid4()),
                        "name": TAG + "start", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [tx, ty + 260],
                        "webhookId": str(uuid.uuid4())})
    wf["nodes"].append({"parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                        "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.body.token }}", "rightValue": token,
                                        "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "options": {}},
                        "id": str(uuid.uuid4()), "name": TAG + "token OK?", "type": "n8n-nodes-base.if", "typeVersion": 2.2,
                        "position": [tx + 220, ty + 260]})
    h, m = _slot(trig)
    tz = (wf.get("settings") or {}).get("timezone") or os.environ.get("GENERIC_TIMEZONE") or "Europe/Warsaw"
    js = SLOT_JS.replace("__H__", str(h)).replace("__M__", str(m)).replace("__TZ__", tz)
    wf["nodes"].append({"parameters": {"jsCode": js}, "id": str(uuid.uuid4()), "name": TAG + "jak harmonogram",
                        "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [tx + 440, ty + 260]})
    C[TAG + "start"] = {"main": [[{"node": TAG + "token OK?", "type": "main", "index": 0}]]}
    C[TAG + "token OK?"] = {"main": [[{"node": TAG + "jak harmonogram", "type": "main", "index": 0}], []]}
    C[TAG + "jak harmonogram"] = {"main": [copy.deepcopy(C[trig["name"]]["main"][0])]}
    return wf, path


@router.post("/api/n8n/run/{wid}")
def run(wid: str):
    r = n8n("GET", f"/api/v1/workflows/{wid}")
    if r.status_code >= 300:
        return JSONResponse({"error": f"n8n: {r.text[:150]}"}, status_code=502)
    wf = r.json()
    name, active = wf.get("name", wid), wf.get("active")
    token = settings()["token"]
    path = _hook_path(wf)
    if not path:
        new, path = _add_hook(wf, token)
        if not new:
            return {"ok": False, "error": "Ten workflow nie ma harmonogramu – startuje go inny workflow (np. generator). Uruchom tamten."}
        p = n8n("PUT", f"/api/v1/workflows/{wid}", json=AN.put_body(new))
        if p.status_code >= 300:
            p = n8n("PUT", f"/api/v1/workflows/{wid}", json=AN.put_body(new, minimal=True))
        if p.status_code >= 300:
            return {"ok": False, "error": f"n8n nie przyjął przycisku startu: {p.text[:150]}"}
        if active:  # ponowna aktywacja rejestruje nowy webhook
            n8n("POST", f"/api/v1/workflows/{wid}/deactivate")
            n8n("POST", f"/api/v1/workflows/{wid}/activate")
        log("op", f"Dodał przycisk „Uruchom” do workflow {name}", "info")
    temp = not active
    if temp:  # webhook działa tylko w aktywnym workflow – włączamy na chwilę
        a = n8n("POST", f"/api/v1/workflows/{wid}/activate")
        if a.status_code >= 300:
            return {"ok": False, "error": f"Nie da się włączyć workflow: {a.text[:150]}"}
        time.sleep(1.5)
    try:
        w = http("POST", f"{_base()}/webhook/{path}", json={"from": "centrum", "token": token}, timeout=20)
        ok, err = w.status_code < 300, w.text[:150]
    except Exception as e:
        ok, err = False, str(e)[:150]
    finally:
        if temp:
            time.sleep(2)
            n8n("POST", f"/api/v1/workflows/{wid}/deactivate")
    log("op", f"Uruchomił ręcznie {name}" if ok else f"Nie udało się uruchomić {name}: {err}", "ok" if ok else "error")
    _bust()
    return {"ok": ok, "error": None if ok else err}


@router.post("/api/n8n/retry/{eid}")
def retry(eid: str):
    r = n8n("POST", f"/api/v1/executions/{eid}/retry", json={"loadWorkflow": True})
    ok = r.status_code < 300
    if r.status_code == 404 and "retry" not in r.text.lower():
        err = "Ta wersja n8n nie ma ponawiania przez API – użyj „Uruchom” przy workflow."
    else:
        err = r.text[:200]
    log("op", f"Ponowił uruchomienie #{eid}" if ok else f"Ponowienie #{eid} nie wyszło: {err}", "ok" if ok else "error")
    _bust()
    return {"ok": ok, "error": None if ok else err}


@router.get("/api/n8n/execution/{eid}")
def execution(eid: str):
    r = n8n("GET", f"/api/v1/executions/{eid}", params={"includeData": "true"})
    if r.status_code >= 300:
        return {"ok": False, "error": r.text[:200]}
    e = r.json()
    res = ((e.get("data") or {}).get("resultData") or {})
    err = res.get("error") or {}
    node = (err.get("node") or {}).get("name") or res.get("lastNodeExecuted")
    msg = err.get("message") or ""
    desc = err.get("description") or ""
    if not msg:  # błąd bywa tylko w danych node'a
        for nm, runs in (res.get("runData") or {}).items():
            for x in runs or []:
                if x.get("error"):
                    node, msg, desc = nm, x["error"].get("message", ""), x["error"].get("description") or ""
    return {"ok": True, "status": e.get("status"), "node": node, "message": msg[:600], "description": str(desc)[:600],
            "workflowId": e.get("workflowId")}
