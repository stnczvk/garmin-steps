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


# Ręczny start ma zachować się jak start z wybranego harmonogramu. Workflow potrafią sprawdzać, KTÓRY
# trigger ruszył (np. generator: $('Schedule Trigger 10:00').isExecuted → slot EXPERIMENT i publikacja 21:00,
# inaczej SAFE i 0:00). Dlatego dla każdego harmonogramu jest osobny start, a odwołania $('Trigger')
# w workflow są owinięte tak, że ręczny start z tego harmonogramu liczy się jak jego uruchomienie.
VER = "centrum-v2"
SLOT_JS = r"""// __VER__ – ręczne uruchomienie z centrum udaje start z „__TRIG__” (dane jak z harmonogramu)
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


def triggers(wf):
    C = wf.get("connections", {})
    return [n for n in wf.get("nodes", []) if n["type"].split(".")[-1] in TRIGGERS and not n.get("disabled")
            and not n["name"].startswith(TAG) and n["name"] in C]


def _slot(trig):
    for r in ((trig.get("parameters") or {}).get("rule") or {}).get("interval") or []:
        if r.get("field") in ("days", "weeks", None) or "triggerAtHour" in r:
            return int(r.get("triggerAtHour", 0)), int(r.get("triggerAtMinute", 0))
    return -1, 0


def _wrapped(name, q):
    return f"({_ref(name, q)}.isExecuted ? {_ref(name, q)} : {_ref(TAG + 'jak ' + name, q)})"


def _ref(name, q):
    return f"$({q}{name}{q})"


def _rewrite(obj, names, wrap):
    """Owija (wrap=True) albo odwija odwołania $('Trigger') w wyrażeniach i kodzie node'ów."""
    if isinstance(obj, dict):
        return {k: _rewrite(v, names, wrap) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rewrite(v, names, wrap) for v in obj]
    if isinstance(obj, str):
        for nm in names:
            for q in ("'", '"'):
                obj = obj.replace(_wrapped(nm, q), _ref(nm, q))  # najpierw odwijamy (bez podwójnego owijania)
                if wrap:
                    obj = obj.replace(_ref(nm, q), _wrapped(nm, q))
        return obj
    return obj


def _hooks(wf):
    """{nazwa harmonogramu: ścieżka webhooka} – albo None, gdy przyciski trzeba dodać lub odświeżyć."""
    N = {n["name"]: n for n in wf.get("nodes", [])}
    T = triggers(wf)
    out = {}
    for t in T:
        s, j = N.get(TAG + "start " + t["name"]), N.get(TAG + "jak " + t["name"])
        if not s or not j or VER not in j["parameters"].get("jsCode", ""):
            return None
        out[t["name"]] = s["parameters"]["path"]
    return out or None


def _add_hooks(wf, token):
    wf = copy.deepcopy(wf)
    old = {n["name"] for n in wf["nodes"] if n["name"].startswith(TAG)}  # poprzednia wersja przycisków – wymieniamy
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in old]
    for k in old:
        wf["connections"].pop(k, None)
    C = wf["connections"]
    T = triggers(wf)
    if not T:
        return None, None
    tz = (wf.get("settings") or {}).get("timezone") or os.environ.get("GENERIC_TIMEZONE") or "Europe/Warsaw"
    names = [t["name"] for t in T]
    for n in wf["nodes"]:
        n["parameters"] = _rewrite(n.get("parameters") or {}, names, True)
    paths = {}
    for i, t in enumerate(T):
        tx, ty = t["position"]
        y = ty + 260 + i * 140
        nm = t["name"]
        paths[nm] = f"centrum-run-{wf['id']}-{i}"
        h, m = _slot(t)
        js = (SLOT_JS.replace("__VER__", VER).replace("__TRIG__", nm).replace("__H__", str(h))
              .replace("__M__", str(m)).replace("__TZ__", tz))
        wf["nodes"] += [
            {"parameters": {"httpMethod": "POST", "path": paths[nm], "options": {}}, "id": str(uuid.uuid4()),
             "name": TAG + "start " + nm, "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [tx, y],
             "webhookId": str(uuid.uuid4())},
            {"parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
             "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.body.token }}", "rightValue": token,
                             "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "options": {}},
             "id": str(uuid.uuid4()), "name": TAG + "token " + nm, "type": "n8n-nodes-base.if", "typeVersion": 2.2,
             "position": [tx + 220, y]},
            {"parameters": {"jsCode": js}, "id": str(uuid.uuid4()), "name": TAG + "jak " + nm,
             "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [tx + 440, y]}]
        C[TAG + "start " + nm] = {"main": [[{"node": TAG + "token " + nm, "type": "main", "index": 0}]]}
        C[TAG + "token " + nm] = {"main": [[{"node": TAG + "jak " + nm, "type": "main", "index": 0}], []]}
        C[TAG + "jak " + nm] = {"main": [copy.deepcopy(C[nm]["main"][0])]}
    return wf, paths


@router.post("/api/n8n/run/{wid}")
def run(wid: str, trigger: str = ""):
    r = n8n("GET", f"/api/v1/workflows/{wid}")
    if r.status_code >= 300:
        return JSONResponse({"error": f"n8n: {r.text[:150]}"}, status_code=502)
    wf = r.json()
    name, active = wf.get("name", wid), wf.get("active")
    token = settings()["token"]
    paths = _hooks(wf)
    if not paths:
        new, paths = _add_hooks(wf, token)
        if not new:
            return {"ok": False, "error": "Ten workflow nie ma harmonogramu – startuje go inny workflow (np. generator). Uruchom tamten."}
        p = n8n("PUT", f"/api/v1/workflows/{wid}", json=AN.put_body(new))
        if p.status_code >= 300:
            p = n8n("PUT", f"/api/v1/workflows/{wid}", json=AN.put_body(new, minimal=True))
        if p.status_code >= 300:
            return {"ok": False, "error": f"n8n nie przyjął przycisku startu: {p.text[:150]}"}
        if active:  # ponowna aktywacja rejestruje nowe webhooki
            n8n("POST", f"/api/v1/workflows/{wid}/deactivate")
            n8n("POST", f"/api/v1/workflows/{wid}/activate")
        log("op", f"Dodał przyciski „Uruchom” do workflow {name}", "info")
    trig = trigger if trigger in paths else next(iter(paths))
    temp = not active
    if temp:  # webhook działa tylko w aktywnym workflow – włączamy na chwilę
        a = n8n("POST", f"/api/v1/workflows/{wid}/activate")
        if a.status_code >= 300:
            return {"ok": False, "error": f"Nie da się włączyć workflow: {a.text[:150]}"}
        time.sleep(1.5)
    try:
        w = http("POST", f"{_base()}/webhook/{paths[trig]}", json={"from": "centrum", "token": token}, timeout=20)
        ok, err = w.status_code < 300, w.text[:150]
    except Exception as e:
        ok, err = False, str(e)[:150]
    finally:
        if temp:
            time.sleep(2)
            n8n("POST", f"/api/v1/workflows/{wid}/deactivate")
    log("op", f"Uruchomił ręcznie {name} (jak „{trig}”)" if ok else f"Nie udało się uruchomić {name}: {err}", "ok" if ok else "error")
    if ok:  # znacznik „uruchomione” – Pipeline pokazuje start od razu, zanim n8n zgłosi wykonanie
        from social import _read, _write
        S = {k: v for k, v in _read("n8n_started.json", {}).items() if time.time() - v.get("t", 0) < 3600}
        S[wid] = {"t": time.time(), "name": name, "trig": trig}
        _write("n8n_started.json", S)
    _bust()
    return {"ok": ok, "error": None if ok else err, "trigger": trig}


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
    # przebieg: ile elementów wyszło z każdego node'a (gdzie dane „znikają” przy wyniku „brak danych”)
    trace = []
    for nm, runs in (res.get("runData") or {}).items():
        for x in runs or []:
            outs = ((x.get("data") or {}).get("main") or [])
            n = sum(len(o or []) for o in outs)
            first = next((o[0].get("json") for o in outs if o), None)
            smp = json.dumps(first, ensure_ascii=False)[:180] if first else ""
            smp = re.sub(r'("?(?:access_token|token|key|secret)"?\s*[:=]\s*")[^"]+', r"\1…", smp, flags=re.I)
            trace.append({"node": nm, "items": n, "t": x.get("startTime") or 0, "ms": x.get("executionTime"),
                          "error": (x.get("error") or {}).get("message", "")[:200], "sample": smp})
    trace.sort(key=lambda r: r["t"])
    return {"ok": True, "status": e.get("status"), "node": node if msg else None, "message": msg[:600], "description": str(desc)[:600],
            "workflowId": e.get("workflowId"), "trace": trace}


# ---------- edycja: eksport, wgranie nowej wersji workflow (z kopią), przywracanie ----------
import json, re
from datetime import datetime
from fastapi import Request
from fastapi.responses import Response
from social import ROOT as SOCIAL_ROOT

WB = SOCIAL_ROOT / "n8n_backups"


def _safe(name):
    return re.sub(r"[^\w.-]+", "_", name)[:60] or "workflow"


def _save_backup(wf, why):
    WB.mkdir(parents=True, exist_ok=True)
    f = WB / f"{_safe(wf.get('name', 'wf'))}__{wf['id']}__{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    f.write_text(json.dumps({"why": why, "workflow": wf}, ensure_ascii=False))
    os.chmod(f, 0o600)
    files = sorted(WB.glob(f"*__{wf['id']}__*.json"))
    for old in files[:-15]:  # 15 ostatnich wersji każdego workflow
        old.unlink(missing_ok=True)
    return f.name


@router.get("/api/n8n/export/{wid}")
def export(wid: str):
    r = n8n("GET", f"/api/v1/workflows/{wid}")
    if r.status_code >= 300:
        return JSONResponse({"error": r.text[:200]}, status_code=502)
    wf = r.json()
    body = {k: wf.get(k) for k in ("name", "nodes", "connections", "settings", "pinData") if wf.get(k) is not None}
    return Response(json.dumps(body, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{_safe(wf.get("name", wid))}.json"'})


def _put(wid, wf, active):
    p = n8n("PUT", f"/api/v1/workflows/{wid}", json=AN.put_body(wf))
    if p.status_code >= 300:
        p = n8n("PUT", f"/api/v1/workflows/{wid}", json=AN.put_body(wf, minimal=True))
    if p.status_code < 300 and active:
        n8n("POST", f"/api/v1/workflows/{wid}/deactivate")
        n8n("POST", f"/api/v1/workflows/{wid}/activate")
    return p


@router.post("/api/n8n/import")
async def import_wf(req: Request):
    """Wgrywa plik .json wyeksportowany z n8n: podmienia istniejący workflow (po kopii) albo dodaje nowy."""
    b = await req.json()
    try:
        new = b["workflow"] if isinstance(b.get("workflow"), dict) else json.loads(b.get("workflow") or "{}")
    except Exception:
        return {"ok": False, "error": "To nie jest poprawny plik JSON z n8n."}
    if not isinstance(new.get("nodes"), list) or not isinstance(new.get("connections"), dict):
        return {"ok": False, "error": "Brakuje nodes / connections – wyeksportuj workflow z n8n (Download)."}
    target = b.get("target") or ""
    if not target:
        body = AN.put_body({"name": new.get("name") or "Nowy workflow", "nodes": new["nodes"], "connections": new["connections"],
                            "settings": new.get("settings") or {}}, minimal=True)
        r = n8n("POST", "/api/v1/workflows", json=body)
        ok = r.status_code < 300
        log("op", f"Wgrał nowy workflow {body['name']}" if ok else f"n8n nie przyjął nowego workflow: {r.text[:150]}", "ok" if ok else "error")
        _bust()
        return {"ok": ok, "error": None if ok else r.text[:200], "id": r.json().get("id") if ok else None}
    r = n8n("GET", f"/api/v1/workflows/{target}")
    if r.status_code >= 300:
        return {"ok": False, "error": f"n8n: {r.text[:150]}"}
    cur = r.json()
    bk = _save_backup(cur, "przed wgraniem nowej wersji")
    had_agency = AN.is_connected(cur)
    wf = {**cur, "name": b.get("keep_name", True) and cur["name"] or new.get("name") or cur["name"],
          "nodes": new["nodes"], "connections": new["connections"], "settings": new.get("settings") or cur.get("settings") or {}}
    p = _put(target, wf, cur.get("active"))
    ok = p.status_code < 300
    note = ""
    if ok and had_agency and not AN.is_connected(wf):
        note = " Nowa wersja nie ma node'ów Agencji – podepnij ją ponownie w zakładce Agencja AI → Automatyzacje."
    log("op", f"Wgrał nową wersję {cur['name']} (kopia: {bk})" if ok else f"n8n nie przyjął nowej wersji {cur['name']}: {p.text[:150]}",
        "ok" if ok else "error")
    _bust()
    return {"ok": ok, "error": None if ok else p.text[:200], "backup": bk, "note": note.strip()}


@router.get("/api/n8n/backups")
def backups():
    if not WB.exists():
        return {"items": []}
    out = []
    for f in sorted(WB.glob("*.json"), reverse=True)[:120]:
        name, wid, ts = (f.stem.split("__") + ["", ""])[:3]
        out.append({"file": f.name, "name": name, "id": wid, "at": ts})
    return {"items": out}


@router.post("/api/n8n/restore")
async def restore(req: Request):
    b = await req.json()
    f = WB / os.path.basename(b.get("file", ""))
    if not f.exists():
        return {"ok": False, "error": "Nie ma takiej kopii."}
    old = json.loads(f.read_text())["workflow"]
    r = n8n("GET", f"/api/v1/workflows/{old['id']}")
    if r.status_code >= 300:
        return {"ok": False, "error": f"n8n: {r.text[:150]}"}
    cur = r.json()
    bk = _save_backup(cur, "przed przywróceniem kopii")
    p = _put(old["id"], {**cur, "nodes": old["nodes"], "connections": old["connections"], "settings": old.get("settings") or {}},
             cur.get("active"))
    ok = p.status_code < 300
    log("op", f"Przywrócił {cur['name']} z kopii {f.name}" if ok else f"Przywracanie nie wyszło: {p.text[:150]}", "ok" if ok else "error")
    _bust()
    return {"ok": ok, "error": None if ok else p.text[:200], "backup": bk}


@router.post("/api/n8n/rename/{wid}")
async def rename(wid: str, req: Request):
    name = str((await req.json()).get("name", "")).strip()[:120]
    if not name:
        return {"ok": False, "error": "Pusta nazwa."}
    r = n8n("GET", f"/api/v1/workflows/{wid}")
    if r.status_code >= 300:
        return {"ok": False, "error": r.text[:150]}
    cur = r.json()
    p = _put(wid, {**cur, "name": name}, False)
    ok = p.status_code < 300
    log("op", f"Zmienił nazwę workflow „{cur['name']}” na „{name}”" if ok else f"Zmiana nazwy nie wyszła: {p.text[:150]}", "ok" if ok else "error")
    _bust()
    return {"ok": ok, "error": None if ok else p.text[:200]}


# ---------- automatyczna podmiana wycofanych modeli obrazów OpenAI w workflow ----------
# gpt-image-1 zniknął z API („model does not exist”), gpt-image-1-mini/1.5 znikają 1.12.2026.
OLD_IMG = {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5", "chatgpt-image-latest", "dall-e-3", "dall-e-2"}
NEW_IMG = os.environ.get("IMAGE_MODEL", "gpt-image-2.5-flare")


def _fix_img_node(n):
    p = n.get("parameters") or {}
    hit = False
    m = p.get("modelId")
    if isinstance(m, dict) and m.get("value") in OLD_IMG:
        p["modelId"] = {"__rl": True, "value": NEW_IMG, "mode": "id"}
        hit = True
    elif isinstance(m, str) and m in OLD_IMG:
        p["modelId"] = NEW_IMG
        hit = True
    if isinstance(p.get("model"), str) and p["model"] in OLD_IMG:
        p["model"] = NEW_IMG
        hit = True
    # node HTTP Request z JSON-em do /v1/images/… → "model": "gpt-image-1"
    for k in ("jsonBody", "body"):
        v = p.get(k)
        if isinstance(v, str) and "images" in str(p.get("url", "")) + v:
            import re as _re
            nv = _re.sub(r'("model"\s*:\s*")(%s)(")' % "|".join(map(_re.escape, OLD_IMG)), lambda mm: mm.group(1) + NEW_IMG + mm.group(3), v)
            if nv != v:
                p[k] = nv
                hit = True
    for bp in (p.get("bodyParameters") or {}).get("parameters", []) or []:
        if bp.get("name") == "model" and bp.get("value") in OLD_IMG:
            bp["value"] = NEW_IMG
            hit = True
    return hit


def fix_image_models():
    try:
        wfs = n8n("GET", "/api/v1/workflows", params={"limit": 100}).json().get("data", [])
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}
    fixed = []
    for wf in wfs:
        if wf.get("isArchived"):
            continue
        new = copy.deepcopy(wf)
        names = [n["name"] for n in new.get("nodes", []) if _fix_img_node(n)]
        if not names:
            continue
        _save_backup(wf, f"przed podmianą modelu obrazów na {NEW_IMG}")
        r = n8n("PUT", f"/api/v1/workflows/{wf['id']}", json=AN.put_body(new))
        if r.status_code >= 400:
            r = n8n("PUT", f"/api/v1/workflows/{wf['id']}", json=AN.put_body(new, minimal=True))
        if r.status_code >= 400:
            log("op", f"Nie podmieniłem modelu obrazów w {wf['name']}: {r.text[:150]}", "error")
            continue
        if wf.get("active"):
            n8n("POST", f"/api/v1/workflows/{wf['id']}/activate")
        fixed.append(f"{wf['name']} ({', '.join(names)})")
    if fixed:
        log("op", f"Podmienił wycofany model obrazów na {NEW_IMG}: " + "; ".join(fixed), "ok")
        _bust()
    return {"ok": True, "fixed": fixed, "model": NEW_IMG}


@router.post("/api/n8n/fix-image-models")
def api_fix_img():
    return fix_image_models()


def _img_loop():
    import threading, time as _t
    def run():
        _t.sleep(60)
        while True:
            try:
                fix_image_models()
            except Exception as e:
                print("fix img", e, flush=True)
            _t.sleep(6 * 3600)
    threading.Thread(target=run, daemon=True).start()


_img_loop()
