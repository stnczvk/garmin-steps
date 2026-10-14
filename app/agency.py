"""Agencja AI – agenci, którzy realnie sterują PozaSkalą.

Agenci (Claude API, klucz jak w extra._ai_key):
- Trend Scout   – układa kolejkę tematów (analityka kanału + trendy z sieci), generator n8n bierze z niej kolejny temat
- Hook Master / Scenarzysta – poprawia scenariusz z generatora (hook w 1-2 s, eskalacja skali, payoff)
- Kontroler jakości – ocenia scenariusz 1-10; poniżej progu wymusza przepisanie
- n8n Operator  – włącza/wyłącza workflow, uruchamia generator na żądanie

n8n woła endpointy /api/agency/hook/* z nagłówkiem X-Agency-Token (token widać w Agencji → Ustawienia).
"""
import json, os, re, secrets, threading, time, uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from social import _read, _write
from extra import _ai_key, insights, push_all

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
SMART = os.environ.get("AGENCY_MODEL_SMART", "claude-sonnet-5-5")
FAST = os.environ.get("AGENCY_MODEL_FAST", "claude-haiku-4-5-20251001")
FALLBACK = "claude-haiku-4-5-20251001"
_lock = threading.Lock()
_busy = {}  # agent -> opis bieżącej pracy

CHANNEL = """Kanał „Poza Skalą” (Beyond Scale) – YouTube Shorts + Instagram Reels po ANGIELSKU. Obietnica: widz nie tylko słyszy,
że coś jest ogromne, ale WIDZI, jak absurdalnie jest ogromne. Film 25–40 s, 5 scen: HOOK → REFERENCE (znany punkt odniesienia:
człowiek/auto/Everest/Ziemia) → FIRST ESCALATION → BIG REVEAL (jedyna scena wideo, ruch kamery pokazuje skok skali) → OPEN ENDING
(bez CTA, echo hooka). Jedno główne pytanie na film. Lektor 72–92 słów.
Filary (pillar): cosmic_scale, earth_scale, animal_scale, mega_scale.
Kategorie (category): space, ocean, geology, biology, physics, technology, human_body, extreme_weather."""

DEFAULT = {"autopilot": True, "min_score": 7, "queue_target": 6, "research_hour": 6}


# ---------- stan ----------
def settings():
    s = {**DEFAULT, **_read("agency_settings.json", {})}
    if not s.get("token"):
        s["token"] = secrets.token_urlsafe(24)
        _write("agency_settings.json", s)
    return s


def log(agent, text, kind="info", extra=None):
    with _lock:
        L = _read("agency_log.json", [])
        L.append({"t": time.time(), "agent": agent, "text": text, "kind": kind, **({"extra": extra} if extra else {})})
        _write("agency_log.json", L[-300:])


def topics():
    return _read("agency_topics.json", [])


def save_topics(t):
    _write("agency_topics.json", t[-400:])


# ---------- Claude ----------
def claude(prompt, model=FAST, max_tokens=1500, system="", web=False):
    key, url = _ai_key()
    if not key:
        raise RuntimeError("Brak ANTHROPIC_API_KEY (dziennik/.env albo social/.env)")
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    if web:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    h = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    j = httpx.post(url, headers=h, json=body, timeout=180).json()
    if j.get("type") == "error":
        if web:  # bez wyszukiwania
            return claude(prompt, model, max_tokens, system, web=False)
        if model != FALLBACK:
            return claude(prompt, FALLBACK, max_tokens, system)
        raise RuntimeError(j["error"].get("message", str(j))[:200])
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


def as_json(txt):
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    txt = m.group(1) if m else txt
    a, b = min([i for i in (txt.find("["), txt.find("{")) if i >= 0] or [0]), max(txt.rfind("]"), txt.rfind("}"))
    return json.loads(txt[a:b + 1])


# ---------- Trend Scout: kolejka tematów ----------
def research(n=8, why="harmonogram"):
    if _busy.get("scout"):
        return 0
    _busy["scout"] = "szuka tematów"
    try:
        used = [t["topic"] for t in topics()][-80:]
        try:
            for a in _read("cache.json", {}).get("accounts", {}).values():
                if a.get("group") == "pozaskala":
                    used += [p["title"] for p in (a.get("posts") or [])[:30] if p.get("title")]
        except Exception:
            pass
        used = list(dict.fromkeys(used))[-120:]
        try:
            ins = insights("pozaskala")
        except Exception:
            ins = {"ok": False}
        perf = ""
        if ins.get("ok"):
            perf = ("NAJLEPSZE filmy (wynik względem mediany):\n" + "\n".join(f"- {x['title']} – {x['rel']:.1f}×" for x in ins["top"]) +
                    "\nNAJSŁABSZE:\n" + "\n".join(f"- {x['title']} – {x['rel']:.1f}×" for x in ins["bottom"]) +
                    (f"\nWnioski analityka:\n{ins['ai']}" if ins.get("ai") else ""))
        prompt = f"""{CHANNEL}

{perf or 'Brak jeszcze danych o wynikach.'}

Tematy już użyte (nie powtarzaj ani nie rób bliskich wariantów):
{chr(10).join('- ' + u for u in used) or '- brak'}

Jesteś Trend Scoutem. Możesz krótko sprawdzić w sieci, co teraz jest głośne w nauce/kosmosie/przyrodzie.
Zaproponuj {n} nowych tematów, faworyzując to, co działa najlepiej, i unikając tego, co słabe.
Każdy temat musi dać się POKAZAĆ obrazami (skala przez porównanie ze znanym obiektem) i mieć jedno konkretne pytanie. Rotuj filary.
Odpowiedz WYŁĄCZNIE tablicą JSON: [{{"topic": "...(EN, konkretny)", "pillar": "jeden z filarów", "category": "jedna z kategorii", "hook": "pierwsze zdanie lektora EN, max 12 słów",
"reference": "punkt odniesienia znany widzowi", "payoff": "zaskakujący fakt na koniec", "why": "dlaczego teraz / dlaczego zadziała (PL, 1 zdanie)"}}]"""
        items = as_json(claude(prompt, SMART, 2500, web=True))
        new = [{"id": uuid.uuid4().hex[:8], "status": "queued", "created": time.time(), **{k: str(x.get(k, ""))[:300] for k in
                ("topic", "pillar", "category", "hook", "reference", "payoff", "why")}} for x in items if x.get("topic")]
        t = topics() + new
        save_topics(t)
        log("scout", f"Dodał {len(new)} tematów do kolejki ({why})", "ok")
        return len(new)
    except Exception as e:
        log("scout", f"Nie udało się znaleźć tematów: {e}", "error")
        return 0
    finally:
        _busy.pop("scout", None)


def next_topic():
    t = topics()
    q = [x for x in t if x["status"] == "queued"]
    if not q:
        research(why="pusta kolejka")
        t = topics()
        q = [x for x in t if x["status"] == "queued"]
    if not q:
        return None
    q.sort(key=lambda x: (-x.get("prio", 0), x["created"]))
    pick = q[0]
    for x in t:
        if x["id"] == pick["id"]:
            x.update(status="used", used=time.time())
    save_topics(t)
    log("scout", f"Generator dostał temat: {pick['topic']}", "ok")
    if len(q) - 1 < settings()["queue_target"] // 2:
        threading.Thread(target=research, kwargs={"why": "kolejka się kończy"}, daemon=True).start()
    return pick


# ---------- Hook Master + Kontroler jakości ----------
EDITABLE = ("title", "hook", "voiceover", "alt_hooks", "ig_caption", "pinned_comment", "description", "main_question")


def _words(t):
    return len(str(t or "").split())


def review(script, judge=None):
    """script = obiekt JSON z „Message a model” (topic, hook, voiceover, timeline...). Zwraca {score, pass, rewritten, script}."""
    _busy["qa"] = "ocenia scenariusz"
    s = settings()
    judge = judge or {}
    topic = str(script.get("topic") or script.get("title") or "")[:80]
    try:
        view = {k: script.get(k) for k in ("topic", "pillar", "format", "title", "hook", "main_question", "voiceover", "key_numbers", "timeline")}
        prompt = f"""{CHANNEL}

SCENARIUSZ z generatora (JSON):
{json.dumps(view, ensure_ascii=False)}

Niezależny sędzia n8n: wynik {judge.get('score')}/100, zaliczony={judge.get('pass')}, problemy: {judge.get('problems') or 'brak'}

Jesteś Kontrolerem jakości i Hook Masterem agencji. Oceń 1–10: siła hooka w 1–2 s, luka ciekawości, punkt odniesienia,
eskalacja, payoff zgodny z hookiem, naturalna mowa, zakończenie-pętla, POPRAWNOŚĆ LICZB (sprawdź w sieci wątpliwe liczby).
Jeśli ocena < {s['min_score']} albo sędzia odrzucił scenariusz albo jest błędna liczba: popraw TYLKO pola {', '.join(EDITABLE)}.
Zasady poprawki: ten sam temat, te same fakty i kolejność co sceny w timeline (obrazy już tak zaplanowano), voiceover 72–92 słów,
title max 60 znaków, hook = pierwsze zdanie voiceover. Jeśli ocena ≥ {s['min_score']} i brak błędów – nie zmieniaj nic.
Odpowiedz WYŁĄCZNIE JSON: {{"score": liczba, "issues": ["krótko po polsku"], "changes": {{tylko zmienione pola albo pusty obiekt}}}}"""
        r = as_json(claude(prompt, SMART, 2500, web=True))
        ch = {k: v for k, v in (r.get("changes") or {}).items() if k in EDITABLE and v}
        if "voiceover" in ch and not 60 <= _words(ch["voiceover"]) <= 100:
            ch.pop("voiceover")
        if "title" in ch and len(str(ch["title"])) > 60:
            ch.pop("title")
        new = {**script, **ch}
        score = float(r.get("score") or 0)
        # pełny autopilot: agencja może zaliczyć scenariusz, który sędzia odrzucił, jeśli go poprawiła
        struct_ok = isinstance(new.get("timeline"), list) and len(new["timeline"]) == 5 and 60 <= _words(new.get("voiceover")) <= 100
        passed = bool(judge.get("pass")) or (bool(ch) and struct_ok and score >= s["min_score"] and
                                             not any("timeline" in p or "hashtags" in p or "missing" in p for p in judge.get("problems") or []))
        out = {"score": score, "issues": [str(i) for i in (r.get("issues") or [])][:6], "rewritten": bool(ch), "changed": list(ch),
               "pass": passed, "script": new}
        log("qa", f"Ocena {score:.0f}/10 – " + (f"poprawił: {', '.join(ch)}" if ch else "zatwierdził bez zmian") + f" · {topic}",
            "ok" if score >= s["min_score"] else "warn",
            {"score": score, "issues": out["issues"], "topic": topic, "changed": list(ch), "judge": judge.get("score"),
             "before": script.get("hook"), "after": new.get("hook")})
        return out
    except Exception as e:
        log("qa", f"Kontrola nie zadziałała, puszczam oryginał: {e}", "error")
        return {"score": None, "pass": None, "rewritten": False, "script": script, "error": str(e)[:200]}
    finally:
        _busy.pop("qa", None)


# ---------- n8n Operator ----------
def n8n(method, path, **kw):
    base = os.environ.get("N8N_URL", "http://127.0.0.1:5678").rstrip("/")
    return httpx.request(method, base + path, headers={"X-N8N-API-KEY": os.environ.get("N8N_API_KEY", "")}, timeout=30, **kw)


# ---------- endpointy dla n8n (token) ----------
def _tok_ok(req: Request):
    t = req.headers.get("x-agency-token") or req.query_params.get("token", "")
    return t and secrets.compare_digest(t, settings()["token"])


@router.get("/api/agency/hook/next-topic")
def hook_next(req: Request):
    if not _tok_ok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    if not settings()["autopilot"]:
        return {"ok": False, "reason": "autopilot wyłączony"}
    p = next_topic()
    return {"ok": True, **p} if p else {"ok": False, "reason": "brak tematów"}


@router.post("/api/agency/hook/review")
async def hook_review(req: Request):
    if not _tok_ok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    sc = b.get("script")
    if isinstance(sc, str):
        try:
            sc = json.loads(sc)
        except Exception:
            sc = None
    if not settings()["autopilot"] or not isinstance(sc, dict):
        return {"score": None, "script": None, "rewritten": False}
    return review(sc, b.get("judge"))


@router.post("/api/agency/hook/event")
async def hook_event(req: Request):
    if not _tok_ok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    log(str(b.get("agent", "n8n"))[:20], str(b.get("text", ""))[:300], str(b.get("kind", "info"))[:10])
    return {"ok": True}


# ---------- endpointy dla apki (sesja) ----------
@router.get("/api/agency/state")
def state():
    s = settings()
    t = topics()
    L = _read("agency_log.json", [])
    last = {}
    for e in L:
        last[e["agent"]] = e
    reviews = [e["extra"] for e in L if e["agent"] == "qa" and e.get("extra")][-20:]
    return {"settings": {k: v for k, v in s.items() if k != "token"}, "token": s["token"], "busy": _busy,
            "queue": [x for x in t if x["status"] == "queued"], "used": [x for x in t if x["status"] == "used"][-15:][::-1],
            "log": L[-60:][::-1], "last": last, "reviews": reviews[::-1], "ai": bool(_ai_key()[0]),
            "avg_score": round(sum(r["score"] for r in reviews) / len(reviews), 1) if reviews else None}


@router.post("/api/agency/research")
def do_research():
    threading.Thread(target=research, kwargs={"why": "ręcznie z apki"}, daemon=True).start()
    return {"ok": True}


@router.post("/api/agency/topics/{tid}")
async def topic_action(tid: str, req: Request):
    a = (await req.json()).get("action")
    t = topics()
    for x in t:
        if x["id"] == tid:
            if a == "skip":
                x["status"] = "rejected"
            elif a == "top":
                x["prio"] = time.time()
    save_topics(t)
    return {"ok": True}


@router.post("/api/agency/settings")
async def set_settings(req: Request):
    b = await req.json()
    s = settings()
    for k in ("autopilot", "min_score", "queue_target"):
        if k in b:
            s[k] = type(DEFAULT[k])(b[k])
    if b.get("new_token"):
        s["token"] = secrets.token_urlsafe(24)
    _write("agency_settings.json", s)
    log("op", "Zmieniono ustawienia agencji", "info")
    return {"ok": True}


@router.post("/api/agency/n8n/{wid}/{action}")
def n8n_action(wid: str, action: str):
    if action not in ("activate", "deactivate"):
        return JSONResponse({"error": "akcja"}, status_code=400)
    r = n8n("POST", f"/api/v1/workflows/{wid}/{action}")
    ok = r.status_code < 300
    name = r.json().get("name", wid) if ok else wid
    log("op", f"{'Włączył' if action == 'activate' else 'Wyłączył'} workflow {name}" if ok else f"n8n odmówił: {r.text[:150]}",
        "ok" if ok else "error")
    try:
        from pozaskala import _cache
        _cache["t"] = 0
    except Exception:
        pass
    return {"ok": ok}


@router.post("/api/agency/run/{name}")
def run_now(name: str):
    """Uruchamia workflow przez jego Webhook (ścieżka agency-<name>)."""
    if name != "generator":
        return JSONResponse({"error": "nazwa"}, status_code=400)
    base = os.environ.get("N8N_URL", "http://127.0.0.1:5678").rstrip("/")
    try:
        r = httpx.post(f"{base}/webhook/agency-{name}", json={"from": "agencja", "token": settings()["token"]}, timeout=15)
        ok = r.status_code < 300
    except Exception as e:
        ok, r = False, type("R", (), {"text": str(e)})()
    log("op", f"Uruchomił {name} na żądanie" if ok else f"Nie udało się uruchomić {name}: {r.text[:120]}", "ok" if ok else "error")
    return {"ok": ok}


# ---------- podpięcie do generatora w n8n ----------
import agency_n8n as AN


def _public_url():
    d = os.environ.get("SOCIAL_DOMAIN", "")
    return ("https://" + d) if d else ""


@router.get("/api/agency/link")
def link_state():
    try:
        wfs = n8n("GET", "/api/v1/workflows", params={"limit": 100}).json().get("data", [])
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}
    g = AN.find_generator(wfs)
    return {"ok": bool(g), "generator": g and g["name"], "connected": bool(g) and AN.is_connected(g),
            "backup": bool(_read("agency_backup.json", None)), "url": _public_url()}


@router.post("/api/agency/link/{action}")
def link(action: str):
    try:
        wfs = n8n("GET", "/api/v1/workflows", params={"limit": 100}).json().get("data", [])
    except Exception as e:
        return {"ok": False, "error": f"n8n: {e}"}
    g = AN.find_generator(wfs)
    if not g:
        return {"ok": False, "error": "Nie znalazłem workflow generatora (Merge Context + Judge Gate)."}
    if action == "connect":
        if not _public_url():
            return {"ok": False, "error": "Brak SOCIAL_DOMAIN w /root/social/.env"}
        if not AN.is_connected(g):
            _write("agency_backup.json", g)
        new = AN.patch(g, _public_url(), settings()["token"])
    elif action == "disconnect":
        new = AN.strip(g)
    else:
        return JSONResponse({"error": "akcja"}, status_code=400)
    r = n8n("PUT", f"/api/v1/workflows/{g['id']}", json=AN.put_body(new))
    if r.status_code >= 400:
        r = n8n("PUT", f"/api/v1/workflows/{g['id']}", json=AN.put_body(new, minimal=True))
    if r.status_code >= 400:
        log("op", f"n8n nie przyjął zmian: {r.text[:200]}", "error")
        return {"ok": False, "error": r.text[:300]}
    if g.get("active"):
        n8n("POST", f"/api/v1/workflows/{g['id']}/activate")
    log("op", ("Podpiął agencję do " if action == "connect" else "Odpiął agencję od ") + g["name"], "ok")
    return {"ok": True}


# ---------- harmonogram: codziennie rano dopełnia kolejkę ----------
def _loop():
    time.sleep(90)
    done = None
    while True:
        try:
            s = settings()
            now = datetime.now(TZ)
            q = [x for x in topics() if x["status"] == "queued"]
            if s["autopilot"] and _ai_key()[0] and done != now.date() and (now.hour == s["research_hour"] or len(q) < 2):
                done = now.date()  # najwyżej raz dziennie z harmonogramu (next-topic sam dopełnia kolejkę)
                if len(q) < s["queue_target"]:
                    if research(max(4, s["queue_target"] - len(q) + 2)):
                        push_all("🔭 Nowe tematy w kolejce", "Trend Scout dorzucił tematy dla PozaSkali.", "/#agencja")
        except Exception as e:
            print("agency loop", e, flush=True)
        time.sleep(600)


threading.Thread(target=_loop, daemon=True).start()
