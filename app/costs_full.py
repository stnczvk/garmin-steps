"""Pełny licznik kosztów: wszystko, co kosztuje pieniądze – nie tylko agenci centrum.

  • n8n: każde wykonanie (generator, montaż, analityka…) jest rozbierane na node'y:
      OpenAI tekst (tokeny z odpowiedzi albo szacunek z długości), obrazy gpt-image, lektor (TTS), transkrypcja,
      Kling przez PiAPI (każde zlecenie wideo)
  • agenci centrum (agency_costs.json)
  • Apify – zużycie miesięczne z API
Środki: OpenAI nie udostępnia salda przez API, więc wpisujesz saldo po doładowaniu, a centrum odejmuje wydatki.
PiAPI (Kling) – saldo z API konta, a jeśli się nie da, tak samo jak OpenAI. Apify – limit minus zużycie.
Gdy zostanie mniej niż próg (domyślnie 5 $ i 1 $) → powiadomienie (raz na próg).
"""
import json, os, re, threading, time
from datetime import datetime

import httpx
from fastapi import APIRouter, Request

from social import _read, _write

router = APIRouter()
F = "costs_full.json"
CFG = "costs_cfg.json"
DEF = {"levels": [5, 1], "kling_price": 0.20, "tts_price": 0.015, "openai": {}, "piapi": {}}
TXT = {"gpt-4.1-mini": (0.4, 1.6), "gpt-4.1-nano": (0.1, 0.4), "gpt-4.1": (2.0, 8.0), "gpt-4o-mini": (0.15, 0.6), "gpt-4o": (2.5, 10.0),
       "gpt-5-mini": (0.25, 2.0), "gpt-5-nano": (0.05, 0.4), "gpt-5": (1.25, 10.0), "o4-mini": (1.1, 4.4), "o3": (2.0, 8.0)}
IMG = {"low": 0.016, "medium": 0.063, "high": 0.25}
LBL = {"openai": "OpenAI", "piapi": "Kling (PiAPI)", "apify": "Apify"}
_wf = {}
_lock = threading.Lock()


def cfg():
    c = {**DEF, **_read(CFG, {})}
    return c


def _n8n(method, path, **kw):
    from agency import n8n
    return n8n(method, path, **kw)


def _workflow(wid):
    w = _wf.get(wid)
    if not w or time.time() - w["t"] > 3600:
        r = _n8n("GET", f"/api/v1/workflows/{wid}")
        j = r.json() if r.status_code < 300 else {}
        w = {"t": time.time(), "name": j.get("name", "?"), "nodes": {n["name"]: n for n in j.get("nodes", [])}}
        _wf[wid] = w
    return w


def _tprice(model):
    m = (model or "").lower()
    for k in sorted(TXT, key=len, reverse=True):
        if k in m:
            return TXT[k]
    return TXT["gpt-4.1"]


def _usage(o):
    """Szuka w odpowiedzi pola usage (Responses API / Chat Completions / tokenUsage z n8n)."""
    if isinstance(o, dict):
        for k in ("usage", "tokenUsage"):
            u = o.get(k)
            if isinstance(u, dict):
                tin = u.get("input_tokens") or u.get("prompt_tokens") or u.get("promptTokens")
                tout = u.get("output_tokens") or u.get("completion_tokens") or u.get("completionTokens")
                if tin or tout:
                    return int(tin or 0), int(tout or 0)
        for v in o.values():
            if isinstance(v, (dict, list)):
                r = _usage(v)
                if r:
                    return r
    elif isinstance(o, list):
        for v in o[:5]:
            r = _usage(v)
            if r:
                return r
    return None


def _model(node, out):
    m = (node.get("parameters") or {}).get("modelId") or (node.get("parameters") or {}).get("model")
    if isinstance(m, dict):
        m = m.get("value")
    if isinstance(out, dict) and isinstance(out.get("model"), str):
        m = out["model"]
    return str(m or "")


def _cost_node(node, runs, c):
    """Zwraca listę (dostawca, rodzaj, usd) dla jednego node'a."""
    t = node.get("type", "")
    p = node.get("parameters") or {}
    ps = json.dumps(p)
    res = []
    for run in runs or []:
        if run.get("error"):
            continue
        items = [it.get("json") or {} for o in ((run.get("data") or {}).get("main") or []) for it in (o or [])]
        if not items:
            continue
        if "langchain.openAi" in t or t.endswith(".openAi"):
            rs = p.get("resource") or "text"
            if rs == "image":
                q = (p.get("options") or {}).get("quality") or "medium"
                res += [("openai", "obrazy", IMG.get(q, IMG["medium"]))] * len(items)
            elif rs == "audio":
                op = p.get("operation") or "generate"
                res += [("openai", "transkrypcja" if op == "transcribe" else "lektor",
                         0.006 if op == "transcribe" else c["tts_price"])] * len(items)
            else:
                for j in items:
                    u = _usage(j)
                    pi, po = _tprice(_model(node, j))
                    if u:
                        tin, tout = u
                    else:  # n8n uprościł odpowiedź – szacunek z długości (ok. 3,5 znaku na token)
                        tout = len(json.dumps(j, ensure_ascii=False)) / 3.5
                        tin = len(ps) / 3.5 + 1500
                    res.append(("openai", "tekst", tin / 1e6 * pi + tout / 1e6 * po))
        elif "httpRequest" in t:
            url = str(p.get("url", ""))
            meth = str(p.get("method", "GET")).upper()
            if "api.openai.com" in url:
                if "transcriptions" in url:
                    res += [("openai", "transkrypcja", 0.006)] * len(items)
                elif "images" in url:
                    res += [("openai", "obrazy", IMG["medium"])] * len(items)
                elif "speech" in url:
                    res += [("openai", "lektor", c["tts_price"])] * len(items)
                else:
                    for j in items:
                        u = _usage(j)
                        if u:
                            pi, po = _tprice(_model(node, j))
                            res.append(("openai", "tekst", u[0] / 1e6 * pi + u[1] / 1e6 * po))
            elif "piapi.ai" in url and meth == "POST" and re.search(r"/task/?$", url) and "kling" in ps.lower():
                ok = [j for j in items if (j.get("body") or j).get("data", {}).get("task_id") or (j.get("body") or j).get("code") == 200]
                res += [("piapi", "kling", c["kling_price"])] * len(ok or items)
    return res


def scan(limit=15):
    """Przetwarza nowe zakończone wykonania n8n (po kilkanaście na raz, żeby nie obciążać serwera)."""
    with _lock:
        S = _read(F, {"seen": [], "ex": []})
        seen = set(S["seen"])
        c = cfg()
        r = _n8n("GET", "/api/v1/executions", params={"limit": 100})
        if r.status_code >= 300:
            return S
        new = [e for e in r.json().get("data", []) if str(e["id"]) not in seen and (e.get("finished") or e.get("status") in ("success", "error", "crashed", "canceled"))]
        for e in new[:limit]:
            seen.add(str(e["id"]))
            try:
                d = _n8n("GET", f"/api/v1/executions/{e['id']}", params={"includeData": "true"}).json()
                wf = _workflow(d.get("workflowId") or e.get("workflowId"))
                rd = ((d.get("data") or {}).get("resultData") or {}).get("runData") or {}
                parts = []
                for nm, runs in rd.items():
                    if nm in wf["nodes"]:
                        parts += _cost_node(wf["nodes"][nm], runs, c)
                if not parts:
                    continue
                by = {}
                for prov, kind, usd in parts:
                    by[f"{prov}|{kind}"] = round(by.get(f"{prov}|{kind}", 0) + usd, 5)
                st = d.get("startedAt") or e.get("startedAt")
                ts = datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp() if st else time.time()
                S["ex"].append({"id": str(e["id"]), "wf": wf["name"], "t": ts, "usd": round(sum(by.values()), 4), "by": by,
                                "status": d.get("status") or e.get("status")})
            except Exception as x:
                print("costs_full", e.get("id"), x, flush=True)
        S["seen"] = sorted(seen, key=lambda x: int(x) if x.isdigit() else 0)[-600:]
        S["ex"] = sorted(S["ex"], key=lambda x: x["t"])[-1500:]
        S["at"] = time.time()
        _write(F, S)
        return S


def _agency_since(ts):
    """Wydatki agentów centrum od chwili ts (z dokładnością do dnia) – liczą się do OpenAI tylko gdy agencja używa OpenAI."""
    from agency import provider
    if provider() != "openai":
        return 0.0
    day = datetime.fromtimestamp(ts).date().isoformat()
    C = _read("agency_costs.json", {})
    return sum(v.get("usd", 0) for d, v in C.items() if d >= day)


def _piapi_key():
    k = (os.environ.get("PIAPI_KEY") or "").strip()
    if k:
        return k
    for w in list(_wf.values()):
        for n in w["nodes"].values():
            s = json.dumps(n.get("parameters") or {})
            if "piapi.ai" in s:
                m = re.search(r'"name": "X-API-Key", "value": "([^"]+)"', s, re.I)
                if m and not m.group(1).startswith("="):
                    return m.group(1)
    return ""


def balances(S=None):
    S = S or _read(F, {"ex": []})
    c = cfg()
    out = {}
    spent = lambda prov, ts: sum(v for x in S["ex"] if x["t"] >= ts for k, v in x["by"].items() if k.startswith(prov + "|"))
    o = c.get("openai") or {}
    if o.get("balance") is not None:
        sp = spent("openai", o["at"]) + _agency_since(o["at"])
        out["openai"] = {"left": round(o["balance"] - sp, 2), "spent": round(sp, 2), "set": o["balance"], "at": o["at"], "how": "saldo wpisane − wydatki"}
    key = _piapi_key()
    if key:
        try:
            j = httpx.get("https://api.piapi.ai/account/info", headers={"X-API-Key": key}, timeout=15).json()
            d = j.get("data") or {}
            usd = d.get("equivalent_in_usd")
            if usd is None and d.get("credits") is not None:
                usd = float(d["credits"]) / 1e6 if float(d["credits"]) > 1e4 else float(d["credits"])
            if usd is not None:
                out["piapi"] = {"left": round(float(usd), 2), "how": "saldo z konta PiAPI"}
        except Exception:
            pass
    p = c.get("piapi") or {}
    if "piapi" not in out and p.get("balance") is not None:
        sp = spent("piapi", p["at"])
        out["piapi"] = {"left": round(p["balance"] - sp, 2), "spent": round(sp, 2), "set": p["balance"], "at": p["at"], "how": "saldo wpisane − wydatki"}
    tok = os.environ.get("APIFY_TOKEN", "")
    if tok:
        try:
            j = httpx.get("https://api.apify.com/v2/users/me/limits", params={"token": tok}, timeout=15).json().get("data", {})
            used, lim = (j.get("current") or {}).get("monthlyUsageUsd"), (j.get("limits") or {}).get("maxMonthlyUsageUsd")
            if used is not None and lim:
                out["apify"] = {"left": round(lim - used, 2), "spent": round(used, 2), "set": lim, "how": "limit miesięczny − zużycie"}
        except Exception:
            pass
    return out


def alerts(B):
    from extra import push_all
    c = cfg()
    st = _read("costs_alerts.json", {})
    for prov, b in B.items():
        left = b["left"]
        done = st.get(prov, [])
        crossed = [lv for lv in c["levels"] if left <= lv and lv not in done]
        if crossed:
            lv = min(crossed)
            low = lv <= min(c["levels"])
            push_all(f"🪫 {LBL[prov]}: zostało ~{max(left, 0):.2f} $",
                     ("Środki prawie się skończyły – doładuj, zanim generator stanie." if low else f"Poniżej {lv:g} $ – czas pomyśleć o doładowaniu.")
                     + " Po doładowaniu wpisz nowe saldo w Strażnikach → Koszty AI.", "/#straznicy", kind="agencja")
            done = sorted({*done, *crossed})
        done = [l for l in done if left <= l + 0.5]  # po doładowaniu progi się zerują
        st[prov] = done
    _write("costs_alerts.json", st)


def summary():
    S = _read(F, {"ex": []})
    now = time.time()
    t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def agg(since):
        tot, prov, wf, kinds = 0.0, {}, {}, {}
        for x in S["ex"]:
            if x["t"] < since:
                continue
            tot += x["usd"]
            wf[x["wf"]] = wf.get(x["wf"], 0) + x["usd"]
            for k, v in x["by"].items():
                pv, kd = k.split("|")
                prov[pv] = prov.get(pv, 0) + v
                kinds[k] = kinds.get(k, 0) + v
        r2 = lambda d: {k: round(v, 3) for k, v in sorted(d.items(), key=lambda a: -a[1])}
        return {"n8n": round(tot, 3), "prov": r2(prov), "wf": r2(wf), "kinds": r2(kinds)}

    C = _read("agency_costs.json", {})
    ag = lambda days: round(sum(v.get("usd", 0) for d, v in sorted(C.items())[-days:]), 3)
    gen = [x for x in S["ex"] if re.search(r"^generator", x["wf"], re.I) and x.get("status") == "success" and x["t"] > now - 30 * 86400]
    days = {}
    for x in S["ex"]:
        if x["t"] > now - 14 * 86400:
            d = datetime.fromtimestamp(x["t"]).date().isoformat()
            days[d] = round(days.get(d, 0) + x["usd"], 3)
    for d, v in sorted(C.items())[-14:]:
        days[d] = round(days.get(d, 0) + v.get("usd", 0), 3)
    return {"today": {**agg(t0), "agents": ag(1)}, "week": {**agg(now - 7 * 86400), "agents": ag(7)}, "month": {**agg(now - 30 * 86400), "agents": ag(30)},
            "per_film": round(sum(x["usd"] for x in gen) / len(gen), 3) if gen else None, "films": len(gen),
            "days": [{"d": d, "usd": v} for d, v in sorted(days.items())[-14:]],
            "last": [{k: x[k] for k in ("wf", "t", "usd", "by")} for x in S["ex"][-12:]][::-1],
            "balances": _read("costs_bal.json", {}).get("b", {}), "cfg": {k: v for k, v in cfg().items()}, "scanned": S.get("at")}


@router.get("/api/costs/full")
def get_full():
    return summary()


@router.post("/api/costs/cfg")
async def set_cfg(req: Request):
    b = await req.json()
    c = _read(CFG, {})
    for prov in ("openai", "piapi"):
        if prov in b:
            v = b[prov]
            c[prov] = {} if v in (None, "") else {"balance": float(str(v).replace(",", ".")), "at": time.time()}
    if "levels" in b:
        c["levels"] = sorted({float(str(x).replace(",", ".")) for x in b["levels"] if str(x).strip()}, reverse=True) or DEF["levels"]
    for k in ("kling_price", "tts_price"):
        if k in b:
            c[k] = float(str(b[k]).replace(",", "."))
    _write(CFG, c)
    st = _read("costs_alerts.json", {})
    for prov in ("openai", "piapi"):
        if prov in b:
            st.pop(prov, None)
    _write("costs_alerts.json", st)
    tick()
    return summary()


def tick():
    S = scan()
    B = balances(S)
    _write("costs_bal.json", {"at": time.time(), "b": B})
    alerts(B)
    return B


def _loop():
    time.sleep(90)
    while True:
        try:
            tick()
        except Exception as e:
            print("costs_full", e, flush=True)
        time.sleep(600)


threading.Thread(target=_loop, daemon=True).start()
