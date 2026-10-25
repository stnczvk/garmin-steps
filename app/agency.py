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

from skills import sk  # skrócone zasady skilli social media dla agentów
router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
# dostawca AI: OpenAI gdy jest OPENAI_API_KEY (albo AGENCY_PROVIDER=openai), inaczej Claude
def provider():
    p = os.environ.get("AGENCY_PROVIDER", "").lower()
    return p if p in ("openai", "claude") else ("openai" if os.environ.get("OPENAI_API_KEY") else "claude")


MODELS = {"openai": (os.environ.get("AGENCY_MODEL_SMART", "gpt-4.1"), os.environ.get("AGENCY_MODEL_FAST", "gpt-4.1-mini")),
          "claude": (os.environ.get("AGENCY_MODEL_SMART", "claude-sonnet-5-5"), os.environ.get("AGENCY_MODEL_FAST", "claude-haiku-4-5-20251001"))}
SMART, FAST = "smart", "fast"
FALLBACK = "claude-haiku-4-5-20251001"


def ai_ready():
    return bool(os.environ.get("OPENAI_API_KEY")) if provider() == "openai" else bool(_ai_key()[0])
_lock = threading.Lock()
_busy = {}  # agent -> opis bieżącej pracy

CHANNEL = """Kanał „Poza Skalą” (Beyond Scale) – YouTube Shorts + Instagram Reels po ANGIELSKU. Obietnica: widz nie tylko słyszy,
że coś jest ogromne, ale WIDZI, jak absurdalnie jest ogromne. Film 25–40 s, 5 scen: HOOK → REFERENCE (znany punkt odniesienia:
człowiek/auto/Everest/Ziemia) → FIRST ESCALATION → BIG REVEAL (jedyna scena wideo, ruch kamery pokazuje skok skali) → OPEN ENDING
(bez CTA, echo hooka). Jedno główne pytanie na film. Lektor 72–92 słów.
Filary (pillar): cosmic_scale, earth_scale, animal_scale, mega_scale.
Kategorie (category): space, ocean, geology, biology, physics, technology, human_body, extreme_weather."""

DEFAULT = {"autopilot": True, "min_score": 7, "queue_target": 6, "research_hour": 6, "inbox": True, "inbox_auto": True, "ab": True, "daily_limit": 1.0}


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
# ---------- sieć: ponawianie przy chwilowych błędach (DNS, zerwane połączenie) ----------
_http = httpx.Client(transport=httpx.HTTPTransport(retries=3), timeout=180)


def _curl(method, url, params=None, headers=None, json=None, data=None, timeout=60, **_):
    """Zapas, gdy httpx dostaje [Errno 104] (zerwane połączenie): ta sama prośba przez curl (IPv4, HTTP/1.1, własny TLS)."""
    import json as _j, subprocess, tempfile, urllib.parse
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    cmd = ["curl", "-sS", "-4", "--http1.1", "--retry", "2", "--retry-all-errors", "-m", str(int(timeout or 60)), "-X", method,
           "-o", "-", "-w", "\n%{http_code}", url]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    body = None
    if json is not None:
        body = _j.dumps(json).encode()
        cmd += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    elif data is not None:
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
        cmd += ["--data-binary", "@-"]
    r = subprocess.run(cmd, input=body, capture_output=True, timeout=(timeout or 60) + 30)
    if r.returncode:
        raise httpx.ConnectError(f"curl: {r.stderr.decode(errors='ignore').strip()[:150]}")
    out, _, code = r.stdout.rpartition(b"\n")
    return httpx.Response(int(code or 0), content=out, request=httpx.Request(method, url))


def http(method, url, **kw):
    for attempt in range(3):
        try:
            return _http.request(method, url, **kw)
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteError) as e:
            if attempt == 2:
                try:
                    return _curl(method, url, **kw)
                except Exception:
                    raise e
            time.sleep(3 * (attempt + 1))


def _transient(e):
    return bool(re.search(r"Errno -?\d+|reset by peer|name resolution|timed out|Timeout|temporarily|502|503|504", str(e)))


# ---------- licznik i dzienny limit kosztów AI ----------
# ceny w $ za 1 mln tokenów (wejście, wyjście); wyszukiwanie w sieci liczone osobno za wywołanie
PRICES = {"gpt-4.1-mini": (0.4, 1.6), "gpt-4.1-nano": (0.1, 0.4), "gpt-4.1": (2.0, 8.0), "gpt-4o-mini": (0.15, 0.6), "gpt-4o": (2.5, 10.0),
          "haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}
WEB_CALL = 0.03
_cost_lock = threading.Lock()


def _price(model):
    m = model.lower()
    for k in sorted(PRICES, key=len, reverse=True):
        if k in m:
            return PRICES[k]
    return (3.0, 12.0)


def _today():
    return datetime.now(TZ).date().isoformat()


def costs():
    C = _read("agency_costs.json", {})
    t = C.get(_today(), {})
    return {"today": round(t.get("usd", 0), 4), "calls": t.get("calls", 0), "limit": settings().get("daily_limit", 1.0),
            "days": [{"d": d, "usd": round(v.get("usd", 0), 3), "calls": v.get("calls", 0)} for d, v in sorted(C.items())[-14:]],
            "by_agent": t.get("by", {})}


def _spend(model, tin, tout, web_calls=0):
    usd = tin / 1e6 * _price(model)[0] + tout / 1e6 * _price(model)[1] + web_calls * WEB_CALL
    who = threading.current_thread().name
    agent = next((k for k, v in _busy.items() if v), "inne")
    with _cost_lock:
        C = _read("agency_costs.json", {})
        t = C.setdefault(_today(), {"usd": 0, "calls": 0, "by": {}})
        t["usd"] += usd
        t["calls"] += 1
        t["by"][agent] = round(t["by"].get(agent, 0) + usd, 4)
        _write("agency_costs.json", dict(sorted(C.items())[-60:]))
        over = t["usd"] >= settings().get("daily_limit", 1.0)
        if over and not t.get("alerted"):
            t["alerted"] = True
            _write("agency_costs.json", dict(sorted(C.items())[-60:]))
            try:
                push_all("💸 Limit kosztów AI osiągnięty", f"Dziś {t['usd']:.2f} $ – agenci wstrzymani do północy. Zmienisz w Automatyzacjach.", "/#agencja")
            except Exception:
                pass
    return usd


def _guard():
    t = _read("agency_costs.json", {}).get(_today(), {})
    if t.get("usd", 0) >= settings().get("daily_limit", 1.0):
        raise RuntimeError(f"dzienny limit kosztów AI ({settings().get('daily_limit', 1.0)} $) osiągnięty – agenci czekają do północy")


def claude(prompt, model=FAST, max_tokens=1500, system="", web=False):
    """Wywołanie modelu agencji (OpenAI albo Claude – patrz provider())."""
    _guard()
    prov = provider()
    if model in (SMART, FAST):
        model = MODELS[prov][0 if model == SMART else 1]
    if prov == "openai":
        return _openai(prompt, model, max_tokens, system, web)
    key, url = _ai_key()
    if not key:
        raise RuntimeError("Brak ANTHROPIC_API_KEY (dziennik/.env albo social/.env)")
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    if web:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    h = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    j = http("POST", url, headers=h, json=body).json()
    if j.get("type") == "error":
        if web:  # bez wyszukiwania
            return claude(prompt, model, max_tokens, system, web=False)
        if model != FALLBACK:
            return claude(prompt, FALLBACK, max_tokens, system)
        raise RuntimeError(j["error"].get("message", str(j))[:200])
    u = j.get("usage") or {}
    _spend(model, u.get("input_tokens", 0), u.get("output_tokens", 0), (u.get("server_tool_use") or {}).get("web_search_requests", 0))
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


def _openai(prompt, model, max_tokens, system="", web=False):
    body = {"model": model, "input": prompt, "max_output_tokens": max_tokens}
    if system:
        body["instructions"] = system
    if web:
        body["tools"] = [{"type": "web_search"}]
    r = http("POST", "https://api.openai.com/v1/responses", json=body,
                   headers={"Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")})
    j = r.json()
    if r.status_code >= 400:
        if web:
            return _openai(prompt, model, max_tokens, system, web=False)
        raise RuntimeError("OpenAI: " + str((j.get("error") or {}).get("message", j))[:200])
    u = j.get("usage") or {}
    _spend(model, u.get("input_tokens", 0), u.get("output_tokens", 0), sum(1 for o in j.get("output", []) if o.get("type") == "web_search_call"))
    return "".join(c.get("text", "") for o in j.get("output", []) if o.get("type") == "message"
                   for c in o.get("content", []) if c.get("type") == "output_text")


def as_json(txt):
    """Pierwszy poprawny obiekt/tablica JSON z odpowiedzi modelu (ignoruje tekst i źródła dopisane przed/po)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(txt):
        if ch in "[{":
            try:
                return dec.raw_decode(txt[i:])[0]
            except Exception:
                continue
    raise ValueError("brak JSON w odpowiedzi modelu")


# ---------- Trend Scout: kolejka tematów ----------
STICK_BRIEF = ("Kanał to od 25.10 seria „What If?” ze stickmanem Stickiem: każdy temat podaj jako scenariusz „What if…” (EN), "
               "który Stick przeżywa (np. What if you fell into the Mariana Trench). Skala i nauka nadal są puentą, liczby prawdziwe.")

def research(n=8, why="harmonogram"):
    STICK_TXT = STICK_BRIEF if settings().get("stickman") else ""
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
{lessons_text()}

Tematy już użyte (nie powtarzaj ani nie rób bliskich wariantów):
{chr(10).join('- ' + u for u in used) or '- brak'}

Jesteś Trend Scoutem. Możesz krótko sprawdzić w sieci, co teraz jest głośne w nauce/kosmosie/przyrodzie.
{STICK_TXT}
Zaproponuj {n} nowych tematów, faworyzując to, co działa najlepiej, i unikając tego, co słabe.
Każdy temat musi dać się POKAZAĆ obrazami (skala przez porównanie ze znanym obiektem) i mieć jedno konkretne pytanie. Rotuj filary.
Odpowiedz WYŁĄCZNIE tablicą JSON: [{{"topic": "...(EN, konkretny)", "pillar": "jeden z filarów", "category": "jedna z kategorii", "hook": "pierwsze zdanie lektora EN, max 12 słów",
"reference": "punkt odniesienia znany widzowi", "payoff": "zaskakujący fakt na koniec", "why": "dlaczego teraz / dlaczego zadziała (PL, 1 zdanie)"}}]"""
        items = as_json(claude(prompt, SMART, 2500, system=sk('trend', 'hook'), web=True))
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
    if isinstance(judge, str):
        judge = as_json(judge) or {}
    if isinstance(judge, list):   # n8n potrafi przysłać ocenę sędziego jako listę [ {...} ]
        judge = next((x for x in judge if isinstance(x, dict)), {})
    if not isinstance(judge, dict):
        judge = {}
    topic = str(script.get("topic") or script.get("title") or "")[:80]
    try:
        view = {k: script.get(k) for k in ("topic", "pillar", "format", "title", "hook", "main_question", "voiceover", "key_numbers", "timeline")}
        prompt = f"""{CHANNEL}

{lessons_text()}

SCENARIUSZ z generatora (JSON):
{json.dumps(view, ensure_ascii=False)}

Niezależny sędzia n8n: wynik {judge.get('score')}/100, zaliczony={judge.get('pass')}, problemy: {judge.get('problems') or 'brak'}

Jesteś Kontrolerem jakości i Hook Masterem agencji. Oceń 1–10: siła hooka w 1–2 s, luka ciekawości, punkt odniesienia,
eskalacja, payoff zgodny z hookiem, naturalna mowa, zakończenie-pętla, POPRAWNOŚĆ LICZB (sprawdź w sieci wątpliwe liczby).
Jeśli ocena < {s['min_score']} albo sędzia odrzucił scenariusz albo jest błędna liczba: popraw TYLKO pola {', '.join(EDITABLE)}.
Zasady poprawki: ten sam temat, te same fakty i kolejność co sceny w timeline (obrazy już tak zaplanowano), voiceover 72–92 słów,
title max 60 znaków, hook = pierwsze zdanie voiceover. Jeśli ocena ≥ {s['min_score']} i brak błędów – nie zmieniaj nic.
Odpowiedz WYŁĄCZNIE JSON: {{"score": liczba, "issues": ["krótko po polsku"], "changes": {{tylko zmienione pola albo pusty obiekt}}}}"""
        r = as_json(claude(prompt, SMART, 2500, system=sk('reel', 'hook'), web=True))
        if isinstance(r, list):       # model czasem odpowiada [ {...} ] zamiast {...}
            r = next((x for x in r if isinstance(x, dict)), {})
        r = r if isinstance(r, dict) else {}
        ch = {k: v for k, v in (r.get("changes") or {}).items() if k in EDITABLE and v}
        if "voiceover" in ch and not 60 <= _words(ch["voiceover"]) <= 100:
            ch.pop("voiceover")
        if "title" in ch and len(str(ch["title"])) > 60:
            ch.pop("title")
        new = {**script, **ch}
        ab_ch, ab_entry = ab_apply(new, "hook" in ch)
        new.update(ab_ch)
        seo_ch = seo(new)
        new.update(seo_ch)
        try:  # Copywriter: osobny opis pod Instagram Reels (nie kopia opisu z YouTube)
            from agency_extra import ig_caption
            cap = ig_caption(new)
            if cap:
                new["ig_caption"] = cap
                seo_ch["ig_caption"] = cap
        except Exception as e:
            log("copy", f"Opis IG pominięty: {str(e)[:120]}", "warn")
        ab_log(ab_entry, new.get("title"))
        score = float(r.get("score") or 0)
        # pełny autopilot: agencja może zaliczyć scenariusz, który sędzia odrzucił, jeśli go poprawiła
        struct_ok = isinstance(new.get("timeline"), list) and len(new["timeline"]) == 5 and 60 <= _words(new.get("voiceover")) <= 100
        passed = bool(judge.get("pass")) or (bool(ch) and struct_ok and score >= s["min_score"] and
                                             not any("timeline" in str(p) or "hashtags" in str(p) or "missing" in str(p) for p in (judge.get("problems") if isinstance(judge.get("problems"), list) else [])))
        out = {"score": score, "issues": [str(i) for i in (r.get("issues") or [])][:6], "rewritten": bool(ch or seo_ch or ab_ch), "changed": list(ch) + list(seo_ch) + (["hook B (test A/B)"] if ab_ch else []),
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


# ---------- SEO Hunter: tytuł, opis, hashtagi pod realne wyszukiwania ----------
def yt_suggest(q):
    try:
        r = http("GET", "https://suggestqueries.google.com/complete/search", params={"client": "firefox", "ds": "yt", "q": q}, timeout=10)
        return r.json()[1][:8]
    except Exception:
        return []


def seo(script):
    _busy["seo"] = "dobiera słowa kluczowe"
    try:
        seeds = [str(x) for x in (script.get("search_keywords") or [])[:2]] + [str(script.get("topic") or "")]
        sug = list(dict.fromkeys(x for q in seeds if q for x in yt_suggest(q)))[:20]
        view = {k: script.get(k) for k in ("topic", "title", "description", "hashtags", "search_keywords", "ig_caption")}
        prompt = f"""Jesteś SEO Hunterem kanału YouTube Shorts/Reels „Poza Skala” (EN).
Metadane filmu: {json.dumps(view, ensure_ascii=False)}
Prawdziwe podpowiedzi wyszukiwania YouTube dla tego tematu: {json.dumps(sug, ensure_ascii=False)}
Popraw metadane pod te wyszukiwania, zachowując ciekawość (bez clickbaitu, bez zmiany tematu):
title max 60 znaków z głównym słowem kluczowym, description 2–3 zdania z naturalnymi frazami, dokładnie 8 hashtagów, 6–8 search_keywords.
Jeśli już są dobre – zwróć pusty obiekt. Odpowiedz WYŁĄCZNIE JSON z polami do zmiany: {{"title":..,"description":..,"hashtags":[..],"search_keywords":[..]}}"""
        r = as_json(claude(prompt, FAST, 900, system=sk('seo')))
        ch = {}
        if isinstance(r, dict):
            if r.get("title") and len(str(r["title"])) <= 60:
                ch["title"] = str(r["title"])
            if r.get("description"):
                ch["description"] = str(r["description"])
            if isinstance(r.get("hashtags"), list) and len(r["hashtags"]) == 8:
                ch["hashtags"] = [str(h) for h in r["hashtags"]]
            if isinstance(r.get("search_keywords"), list) and 6 <= len(r["search_keywords"]) <= 8:
                ch["search_keywords"] = [str(h) for h in r["search_keywords"]]
        if ch:
            log("seo", f"Dopasował {', '.join(ch)} pod wyszukiwania ({len(sug)} podpowiedzi YT)", "ok",
                {"before": script.get("title"), "after": ch.get("title"), "suggest": sug[:8]})
        return ch
    except Exception as e:
        log("seo", f"SEO pominięte: {e}", "error")
        return {}
    finally:
        _busy.pop("seo", None)


# ---------- Analityk retencji: gdzie widzowie odpadają ----------
def lessons_text():
    L = _read("agency_lessons.json", {})
    out = ("LEKCJE Z RETENCJI (co trzyma widzów na tym kanale – stosuj):\n" + L["text"]) if L.get("text") else ""
    try:
        ab = ab_text()
    except Exception:
        ab = ""
    return (out + "\n\n" + ab).strip()


def _pz_youtube():
    from social import _accounts
    for k, a in _accounts().items():
        if a.get("platform") == "youtube" and a.get("group") == "pozaskala" and a.get("refresh_token"):
            return k, a
    return None, None


def retention(force=False):
    from social import _google_access
    key, acc = _pz_youtube()
    if not acc:
        log("retention", "Retencja: brak połączonego YouTube PozaSkala", "error")
        return 0
    if _busy.get("retention"):
        return 0
    _busy["retention"] = "liczy retencję"
    try:
        posts = (_read("cache.json", {}).get("accounts", {}).get(key) or {}).get("posts") or []
        R = _read("agency_retention.json", {})
        h = {"Authorization": "Bearer " + _google_access(acc)}
        today = datetime.now(TZ).date()
        n = 0
        for p in posts:
            try:
                age = (today - datetime.fromisoformat(p["date"]).date()).days
            except Exception:
                continue
            if age < 3 or age > 60 or (p["id"] in R and not force and time.time() - R[p["id"]]["at"] < 5 * 86400):
                continue
            rep = http("GET", "https://youtubeanalytics.googleapis.com/v2/reports", headers=h, timeout=30, params={
                "ids": "channel==MINE", "startDate": p["date"], "endDate": today.isoformat(), "dimensions": "elapsedVideoTimeRatio",
                "metrics": "audienceWatchRatio,relativeRetentionPerformance", "filters": f"video=={p['id']}"}).json()
            rows = rep.get("rows") or []
            if len(rows) < 3:
                continue
            at = lambda x: min(rows, key=lambda r: abs(r[0] - x))
            dur = p.get("dur") or 30
            drop = max(((rows[i][0], rows[i - 1][1] - rows[i][1]) for i in range(1, len(rows))), key=lambda x: x[1])[0]
            R[p["id"]] = {"at": time.time(), "title": p.get("title"), "views": p.get("views"), "dur": dur, "url": p.get("url"),
                          "hold3s": round(at(min(3 / dur, 1))[1] * 100), "mid": round(at(.5)[1] * 100), "end": round(at(.95)[1] * 100),
                          "rel": round(sum(r[2] for r in rows) / len(rows), 2), "drop": round(drop * 100)}
            n += 1
        _write("agency_retention.json", dict(sorted(R.items(), key=lambda kv: -kv[1]["at"])[:80]))
        vids = sorted(R.values(), key=lambda v: -v["hold3s"])
        if (n or force) and len(vids) >= 3:
            desc = "\n".join(f"- „{v['title']}” {v['dur']}s: po 3 s zostaje {v['hold3s']}%, w połowie {v['mid']}%, na końcu {v['end']}%, "
                             f"vs podobne Shorts {v['rel']}, największy odpływ przy {v['drop']}% filmu" for v in vids[:25])
            txt = claude("Jesteś Analitykiem retencji kanału YouTube Shorts „Poza Skala” (filmy o skali, EN). Dane retencji filmów:\n" + desc +
                         "\n\nNapisz 5 krótkich, konkretnych lekcji (po polsku, każda od „• ”): jakie hooki i tematy trzymają widzów "
                         "w pierwszych 3 s, gdzie ludzie odpadają i co zmienić w scenariuszach. Bez wstępu.", SMART, 700, system=sk('perf', 'hook'))
            if txt.strip():
                _write("agency_lessons.json", {"at": time.time(), "text": txt.strip(), "n": len(vids)})
        # zawsze wpis – inaczej strażnik nie odróżni „nic nowego” od „nie uruchomił się”
        log("retention", f"Przeanalizował retencję {n} filmów" if n else
            "Retencja: brak nowych filmów do analizy (3–60 dni, poprzednie aktualne)", "ok")
        return n
    except Exception as e:
        log("retention", f"Retencja nie zadziałała: {e}", "error")
        return 0
    finally:
        _busy.pop("retention", None)


# ---------- Inbox: odpowiedzi na komentarze YouTube i Instagram (autopilot wysyła sam, limit dzienny) ----------
DAILY_REPLIES = 40


def _pz_account(platform):
    from social import _accounts
    for k, a in _accounts().items():
        if a.get("platform") == platform and a.get("group") == "pozaskala":
            return k, a
    return None, None


def _reply_text(video, comment):
    return claude(f"""Jesteś autorem kanału „Poza Skala” (Shorts/Reels o niewyobrażalnej skali rzeczy, EN). Komentarz pod filmem „{video}”:
"{comment[:500]}"
Napisz krótką (max 2 zdania), naturalną odpowiedź ZAWSZE PO ANGIELSKU (kanał jest anglojęzyczny), nawet gdy komentarz jest w innym języku: podziękuj, odpowiedz rzeczowo na pytanie albo dorzuć ciekawy fakt,
czasem zadaj pytanie, żeby rozkręcić dyskusję. Bez linków, bez emoji-spamu, bez proszenia o suba.
Jeśli to spam, hejt, coś obraźliwego, prowokacja polityczna albo nie ma na co odpowiedzieć – odpowiedz dokładnie: SKIP""", FAST, 150, system=sk('slop')).strip()


def _send(item, text):
    """Wysyła odpowiedź na platformę. Zwraca (ok, błąd). Chwilowe błędy sieci oznacza do ponowienia."""
    from social import _google_access, _ig_token
    pl = "Instagram" if item.get("platform") == "instagram" else "YouTube"
    stage = "logowanie"
    try:
        if item.get("platform") == "instagram":
            k, acc = _pz_account("instagram")
            tok = _ig_token(k, acc)
            stage = "wysyłka"
            r = http("POST", f"https://graph.instagram.com/{item['id']}/replies", timeout=30,
                     params={"message": text, "access_token": tok})
        else:
            _, acc = _pz_youtube()
            tok = _google_access(acc)
            stage = "wysyłka"
            r = http("POST", "https://www.googleapis.com/youtube/v3/comments", params={"part": "snippet"}, timeout=30,
                     headers={"Authorization": "Bearer " + tok},
                     json={"snippet": {"parentId": item["id"], "textOriginal": text}})
        item.pop("retry", None)
        if r.status_code < 300:
            return True, ""
        if r.status_code in (400, 403) and ("scope" in r.text.lower() or "permission" in r.text.lower() or r.status_code == 403):
            return False, f"{pl}: brak uprawnień – połącz konto ponownie w ustawieniach centrum"
        if r.status_code >= 500 or r.status_code == 429:
            item["retry"] = item.get("retry", 0) + 1
            return False, f"{pl} ({stage}): serwer chwilowo nie odpowiada ({r.status_code}) – ponowię automatycznie"
        return False, f"{pl} ({stage}): {r.text[:150]}"
    except Exception as e:
        if _transient(e):
            item["retry"] = item.get("retry", 0) + 1
            return False, f"{pl} ({stage}): zerwane połączenie – ponowię automatycznie przy następnym sprawdzeniu"
        return False, f"{pl} ({stage}): {str(e)[:150]}"


def _yt_new_comments(I, done):
    from social import _google_access
    key, acc = _pz_youtube()
    if not acc:
        return []
    h = {"Authorization": "Bearer " + _google_access(acc)}
    th = http("GET", "https://www.googleapis.com/youtube/v3/commentThreads", headers=h, timeout=30, params={
        "part": "snippet", "allThreadsRelatedToChannelId": acc["channel_id"], "maxResults": 40, "order": "time"}).json()
    if "error" in th:
        raise RuntimeError("YouTube: " + th["error"].get("message", "")[:150])
    titles = {p["id"]: p.get("title") for p in (_read("cache.json", {}).get("accounts", {}).get(key) or {}).get("posts", [])}
    out = []
    for t in th.get("items", []):
        sn = t["snippet"]
        c = sn["topLevelComment"]["snippet"]
        if t["id"] in done or sn.get("totalReplyCount", 0) or c.get("authorChannelId", {}).get("value") == acc["channel_id"]:
            continue
        if time.time() - datetime.fromisoformat(c["publishedAt"].replace("Z", "+00:00")).timestamp() > 7 * 86400:
            continue
        out.append({"id": t["id"], "platform": "youtube", "video": titles.get(sn.get("videoId"), ""),
                    "author": c.get("authorDisplayName"), "text": c.get("textOriginal", "")})
    return out


def _ig_new_comments(I, done):
    from social import _ig_token
    key, acc = _pz_account("instagram")
    if not acc:
        return []
    tok = _ig_token(key, acc)
    me = acc.get("name", "").lstrip("@").lower()
    media = http("GET", "https://graph.instagram.com/me/media", timeout=30,
                      params={"fields": "id,caption,timestamp,comments_count", "limit": 10, "access_token": tok}).json()
    if "error" in media:
        raise RuntimeError("Instagram: " + media["error"].get("message", "")[:150])
    out, hidden = [], 0
    for m in media.get("data", []):
        cs = http("GET", f"https://graph.instagram.com/{m['id']}/comments", timeout=30, params={
            "fields": "id,text,username,timestamp,replies{username}", "limit": 30, "access_token": tok}).json()
        if "error" in cs:
            raise RuntimeError("Instagram komentarze: " + cs["error"].get("message", "")[:150])
        if (m.get("comments_count") or 0) > 0 and not cs.get("data"):
            hidden += 1
        for c in cs.get("data", []):
            replied = any((r.get("username") or "").lower() == me for r in (c.get("replies") or {}).get("data", []))
            if c["id"] in done or replied or (c.get("username") or "").lower() == me:
                continue
            try:
                if time.time() - datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00")).timestamp() > 7 * 86400:
                    continue
            except Exception:
                pass
            out.append({"id": c["id"], "platform": "instagram", "video": (m.get("caption") or "")[:60],
                        "author": "@" + (c.get("username") or "?"), "text": c.get("text", "")})
    if hidden:
        I["_warn"] = (f"Instagram: {hidden} postów ma komentarze, ale API ich nie oddaje. Ponowne łączenie konta nie pomoże – "
                      "aplikacja Meta jest prawdopodobnie w trybie deweloperskim (widzi tylko komentarze testerów). "
                      "developers.facebook.com → Twoja aplikacja → przełącz App Mode na Live")
    return out


def inbox():
    s = settings()
    if _busy.get("inbox") or not s.get("inbox", True):
        return 0
    _busy["inbox"] = "czyta komentarze"
    try:
        I = _read("agency_inbox.json", {"done": [], "items": []})
        done = set(I["done"])
        today = datetime.now(TZ).date().isoformat()
        sent_today = sum(1 for x in I["items"] if x.get("day") == today and x.get("status") == "sent")
        new, errs = [], []
        for name, fetch in (("YouTube", _yt_new_comments), ("Instagram", _ig_new_comments)):
            try:
                new += fetch(I, done)
            except Exception as e:
                m = str(e)[:120]
                errs.append(m if m.startswith(name) else f"{name}: {m}")
        if I.get("_warn"):
            errs.append(I.pop("_warn"))
        auto = s.get("autopilot") and s.get("inbox_auto", True)
        n = sent = 0
        # szkice, których wysyłka padła na chwilowym błędzie sieci (np. [Errno 104]) – ponów (max 5 razy)
        for it in I["items"]:
            if it.get("status") == "draft" and it.get("reply") and (it.get("retry") or "Errno 104" in (it.get("why") or "")) \
                    and it.get("retry", 0) < 5 and auto and sent_today < DAILY_REPLIES:
                ok, why = _send(it, it["reply"])
                if ok:
                    it["status"], sent_today, sent = "sent", sent_today + 1, sent + 1
                    it.pop("why", None)
                else:
                    it["why"] = why
        for c in new[:20]:
            try:
                txt = _reply_text(c["video"], c["text"])
            except Exception as e:
                errs.append(f"AI (odpowiedź na komentarz): {str(e)[:110]}")
                break
            done.add(c["id"])
            item = {**c, "day": today, "t": time.time(), "text": c["text"][:300],
                    "reply": "" if txt == "SKIP" else txt[:400], "status": "skip" if txt == "SKIP" else "draft"}
            if item["reply"] and auto and sent_today < DAILY_REPLIES:
                try:
                    ok, why = _send(item, item["reply"])
                except Exception as e:
                    ok, why = False, f"{c['platform']} (wysyłka): {str(e)[:110]}"
                    errs.append(why)
                if ok:
                    item["status"] = "sent"
                    sent_today += 1
                    sent += 1
                else:
                    item["why"] = why
            I["items"].append(item)
            n += 1
        I["checked"], I["errs"] = time.time(), errs[:5]
        I["done"] = list(done)[-3000:]
        I["items"] = I["items"][-200:]
        _write("agency_inbox.json", I)
        drafts = sum(1 for x in I["items"][-n:] if x["status"] == "draft") if n else 0
        if n:
            log("inbox", f"Nowe komentarze: {n}, odpowiedział na {sent}" + (f", {drafts} czeka na Ciebie" if drafts else ""), "ok")
            if drafts:
                push_all("💬 Odpowiedzi do zatwierdzenia", f"{drafts} komentarzy czeka w Agencji (autopilot nie mógł wysłać).", "/#agencja")
        for e in errs:
            log("inbox", e, "warn" if _transient(e) else "error")
        return n
    except Exception as e:
        log("inbox", "Chwilowy problem z siecią – spróbuję za 30 min" if _transient(e) else f"Inbox nie zadziałał: {e}",
            "warn" if _transient(e) else "error")
        return 0
    finally:
        _busy.pop("inbox", None)


@router.post("/api/agency/inbox/{cid}")
async def inbox_send(cid: str, req: Request):
    """Ręczna wysyłka szkicu (albo pominięcie)."""
    b = await req.json()
    I = _read("agency_inbox.json", {"done": [], "items": []})
    it = next((x for x in I["items"] if x["id"] == cid), None)
    if not it:
        return {"ok": False}
    if b.get("action") == "skip":
        it["status"] = "skip"
    else:
        text = str(b.get("reply") or it["reply"])[:500]
        ok, why = _send(it, text)
        if not ok:
            return {"ok": False, "error": why}
        it.update(status="sent", reply=text)
    _write("agency_inbox.json", I)
    return {"ok": True}


# ---------- Analityk kont prywatnych: raport tygodniowy z Twoich prywatnych kont ----------
def private_report(force=False):
    from social import _read as sread
    if _busy.get("private"):
        return 0
    _busy["private"] = "analizuje konta prywatne"
    try:
        accs = {k: a for k, a in sread("cache.json", {}).get("accounts", {}).items() if a.get("group") == "prywatne"}
        if not accs:
            log("private", "Brak połączonych kont w grupie Prywatne", "warn")
            return 0
        try:
            ins = insights("prywatne")
        except Exception:
            ins = {"ok": False}
        posts = []
        for a in accs.values():
            for p in (a.get("posts") or [])[:20]:
                posts.append(f"- [{a.get('platform')}/{a.get('name')}] {p.get('date', '')} „{(p.get('title') or 'bez opisu')[:90]}” "
                             f"wyśw. {p.get('views') or 0}, polub. {p.get('likes') or 0}, kom. {p.get('comments') or 0}, "
                             f"zapis. {p.get('saved') or 0}, udost. {p.get('shares') or 0}{', ' + str(int(p['dur'])) + ' s' if p.get('dur') else ''}")
        acc_txt = "\n".join(f"- {a.get('name')} ({a.get('platform')}): {a.get('followers')} obserwujących, zmiana 28 dni {a.get('followers_28d')}, "
                            f"wyświetlenia 28 dni {a.get('views_28d')}" for a in accs.values())
        extra_txt = ""
        if ins.get("ok"):
            extra_txt = ("Formaty (wynik względem mediany konta): " + ", ".join(f"{f['kind']}/{f['platform']} {f['rel']}× (n={f['n']})" for f in ins["formats"]) +
                         ("\nDługości: " + ", ".join(f"{l['label']} {l['rel']}×" for l in ins["lengths"]) if ins.get("lengths") else "") +
                         (f"\nNajlepszy moment publikacji: {ins['slot']}" if ins.get("slot") else ""))
        txt = claude(f"""Jesteś analitykiem social media agencji. Analizujesz PRYWATNE konta właściciela (jego własne nagrania, po polsku).
KONTA:
{acc_txt}

OSTATNIE POSTY:
{chr(10).join(posts[:60]) or 'brak'}

{extra_txt}

Napisz po polsku zwięzły raport tygodniowy:
1. Wynik w skrócie (2 zdania z liczbami).
2. Co działa – 3 punkty (tematy, format, długość, hook, pora) oparte na danych powyżej.
3. Co nie działa – 2 punkty.
4. Plan na najbliższy tydzień: 3 konkretne pomysły na posty (temat + hook na pierwsze 2 s + format) dopasowane do tego, co działa.
Każdy punkt od „• ”. Bez wstępu, bez ogólników, nie wymyślaj liczb spoza danych.""", SMART, 1400, system=sk('perf', 'hook', 'repurpose'))
        rep = {"at": time.time(), "text": txt.strip(), "accounts": [{"name": a.get("name"), "platform": a.get("platform"),
               "followers": a.get("followers"), "followers_28d": a.get("followers_28d"), "views_28d": a.get("views_28d")} for a in accs.values()]}
        hist = _read("agency_private.json", [])
        hist.append(rep)
        _write("agency_private.json", hist[-8:])
        log("private", f"Raport kont prywatnych gotowy ({len(accs)} kont, {len(posts)} postów)", "ok")
        push_all("👤 Raport Twoich kont", "Analityk przygotował wnioski i plan postów na tydzień.", "/#agencja")
        return 1
    except Exception as e:
        log("private", f"Raport prywatny nie zadziałał: {e}", "error")
        return 0
    finally:
        _busy.pop("private", None)


@router.post("/api/agency/private")
def do_private():
    threading.Thread(target=private_report, kwargs={"force": True}, daemon=True).start()
    return {"ok": True}


# ---------- Test A/B hooków: co drugi film dostaje alternatywny hook z generatora ----------
import re as _re


def _split_first(text):
    m = _re.match(r"\s*(.+?[.!?…])(\s+|$)(.*)", text or "", _re.S)
    return (m.group(1), m.group(3)) if m else (text, "")


def ab_apply(script, kontroler_changed_hook):
    """Zwraca (zmiany, wpis). Wariant B = alt_hooks[0] jako pierwsze zdanie lektora."""
    s = settings()
    if not s.get("ab", True):
        return {}, None
    H = _read("agency_ab.json", [])
    alts = [str(a).strip() for a in (script.get("alt_hooks") or []) if str(a).strip()]
    variant = "B" if (len(H) % 2 == 1 and alts and not kontroler_changed_hook) else "A"
    ch = {}
    if variant == "B":
        _, rest = _split_first(script.get("voiceover") or "")
        alt = alts[0] if alts[0][-1:] in ".!?…" else alts[0] + "."
        vo = (alt + " " + rest).strip()
        if 60 <= len(vo.split()) <= 100:
            ch = {"hook": alt, "voiceover": vo}
        else:
            variant = "A"
    entry = {"at": time.time(), "title": (ch.get("title") or script.get("title") or "")[:120], "topic": str(script.get("topic") or "")[:100],
             "variant": variant, "hook": ch.get("hook") or script.get("hook"), "hook_type": script.get("hook_type") if variant == "A" else "ALT",
             "other": script.get("hook") if variant == "B" else (alts[0] if alts else None)}
    return ch, entry


def ab_log(entry, final_title):
    if not entry:
        return
    entry["title"] = (final_title or entry["title"])[:120]
    H = _read("agency_ab.json", [])
    H.append(entry)
    _write("agency_ab.json", H[-200:])


def ab_results():
    """Łączy filmy z testu z retencją z YouTube (po tytule) – średnie utrzymanie po 3 s dla A i B."""
    H = _read("agency_ab.json", [])
    R = list(_read("agency_retention.json", {}).values())
    norm = lambda t: _re.sub(r"\W+", "", str(t or "").lower())
    byt = {norm(r.get("title")): r for r in R}
    rows = []
    for h in H:
        r = byt.get(norm(h["title"]))
        rows.append({**h, "hold3s": r["hold3s"] if r else None, "views": r.get("views") if r else None})
    agg = {}
    for v in ("A", "B"):
        xs = [r["hold3s"] for r in rows if r["variant"] == v and r["hold3s"] is not None]
        agg[v] = {"n": len(xs), "hold3s": round(sum(xs) / len(xs), 1) if xs else None}
    types = {}
    for r in rows:
        if r["hold3s"] is not None and r.get("hook_type"):
            types.setdefault(r["hook_type"], []).append(r["hold3s"])
    agg["by_type"] = sorted([{"type": k, "n": len(v), "hold3s": round(sum(v) / len(v), 1)} for k, v in types.items()], key=lambda x: -x["hold3s"])
    agg["rows"] = rows[-20:][::-1]
    a, b = agg["A"], agg["B"]
    if a["n"] >= 3 and b["n"] >= 3:
        diff = b["hold3s"] - a["hold3s"]
        agg["verdict"] = (f"Alternatywne hooki (B) trzymają {'lepiej' if diff > 0 else 'gorzej'} o {abs(diff):.1f} pp po 3 s "
                          f"(A {a['hold3s']}%, n={a['n']} vs B {b['hold3s']}%, n={b['n']}).")
    else:
        agg["verdict"] = f"Za mało danych: A {a['n']}, B {b['n']} filmów z retencją (potrzeba min. 3+3)."
    return agg


def ab_text():
    r = ab_results()
    t = [r["verdict"]] if r["A"]["n"] + r["B"]["n"] else []
    if r["by_type"]:
        t.append("Typy hooków wg utrzymania po 3 s: " + ", ".join(f"{x['type']} {x['hold3s']}% (n={x['n']})" for x in r["by_type"][:6]))
    return ("WYNIKI TESTU A/B HOOKÓW:\n" + "\n".join(t)) if t else ""


# ---------- Kopiarz trendów: co działa w niszy na YouTube Shorts (ostatnie 7 dni) ----------
TREND_Q = ["how big is", "scale comparison", "size comparison universe", "deepest ocean", "largest star", "biggest animal ever"]


def trends(why="harmonogram"):
    from social import _google_access
    if _busy.get("trends"):
        return 0
    _, acc = _pz_youtube()
    if not acc:
        log("trends", "Brak połączonego kanału YouTube PozaSkali", "warn")
        return 0
    _busy["trends"] = "przegląda trendy w niszy"
    try:
        h = {"Authorization": "Bearer " + _google_access(acc)}
        after = datetime.utcfromtimestamp(time.time() - 7 * 86400).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids = []
        for q in TREND_Q[:4]:
            r = http("GET", "https://www.googleapis.com/youtube/v3/search", headers=h, timeout=30, params={
                "part": "id", "q": q, "type": "video", "videoDuration": "short", "order": "viewCount",
                "publishedAfter": after, "maxResults": 15, "relevanceLanguage": "en"}).json()
            if "error" in r:
                raise RuntimeError("YouTube: " + r["error"].get("message", "")[:120])
            ids += [i["id"]["videoId"] for i in r.get("items", []) if i.get("id", {}).get("videoId")]
        ids = list(dict.fromkeys(ids))[:50]
        vids = http("GET", "https://www.googleapis.com/youtube/v3/videos", headers=h, timeout=30,
                         params={"part": "snippet,statistics", "id": ",".join(ids)}).json().get("items", []) if ids else []
        top = sorted([{"id": v["id"], "title": v["snippet"]["title"], "channel": v["snippet"]["channelTitle"],
                       "views": int(v["statistics"].get("viewCount", 0)), "likes": int(v["statistics"].get("likeCount", 0)),
                       "url": "https://youtu.be/" + v["id"]} for v in vids if v["snippet"]["channelId"] != acc["channel_id"]],
                     key=lambda x: -x["views"])[:20]
        if not top:
            log("trends", "Brak filmów z niszy w ostatnim tygodniu", "warn")
            return 0
        used = [t["topic"] for t in topics()][-80:]
        prompt = f"""{CHANNEL}

Najpopularniejsze Shorts z niszy w ostatnich 7 dniach (cudze kanały):
{chr(10).join(f"- „{t['title']}” – {t['views']:,} wyświetleń ({t['channel']})" for t in top)}

Tematy już użyte: {', '.join(used[-40:]) or 'brak'}

Jesteś Kopiarzem trendów. Nie kopiuj tytułów – wyciągnij, CO w nich działa (obiekt, porównanie, mechanika, typ hooka),
i zaproponuj 4 NOWE tematy w formacie Poza Skalą, które wykorzystują ten sam mechanizm na innym obiekcie.
Odpowiedz WYŁĄCZNIE tablicą JSON: [{{"topic":"(EN)","pillar":"","category":"","hook":"(EN, max 12 słów)","reference":"","payoff":"",
"why":"(PL, 1 zdanie: jaki trend i dlaczego zadziała)","inspired_by":"tytuł filmu z listy"}}]"""
        items = as_json(claude(prompt, SMART, 1800, system=sk('trend', 'hook')))
        new = [{"id": uuid.uuid4().hex[:8], "status": "queued", "created": time.time(), "source": "trend",
                **{k: str(x.get(k, ""))[:300] for k in ("topic", "pillar", "category", "hook", "reference", "payoff", "why", "inspired_by")}}
               for x in items if x.get("topic")]
        save_topics(topics() + new)
        _write("agency_trends.json", {"at": time.time(), "top": top[:12], "added": [n["topic"] for n in new]})
        log("trends", f"Dorzucił {len(new)} tematów z trendów w niszy ({why})", "ok")
        return len(new)
    except Exception as e:
        log("trends", f"Trendy nie zadziałały: {e}", "error")
        return 0
    finally:
        _busy.pop("trends", None)


@router.post("/api/agency/trends")
def do_trends():
    threading.Thread(target=trends, kwargs={"why": "ręcznie z apki"}, daemon=True).start()
    return {"ok": True}


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


@router.post("/api/agency/hook/tech")
async def hook_tech(req: Request):
    """Wynik kontroli technicznej final.mp4 z workflow Montaż film."""
    if not _tok_ok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    ok = bool(b.get("ok"))
    probs = [str(x) for x in (b.get("problems") or [])]
    log("tech", (f"Film OK ({b.get('duration', '?')} s)" if ok else "Zatrzymał film: " + "; ".join(probs)) +
        (f" · {str(b.get('movie', ''))[:60]}" if b.get("movie") else ""), "ok" if ok else "error",
        {"ok": ok, "problems": probs, "warnings": b.get("warnings"), "movie": b.get("movie"), "duration": b.get("duration")})
    if not ok:
        push_all("🛑 Film zatrzymany", "Kontrola techniczna: " + "; ".join(probs)[:150], "/#agencja")
    return {"ok": True}


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
    R = _read("agency_retention.json", {})
    I = _read("agency_inbox.json", {"items": []})
    return {"settings": {k: v for k, v in s.items() if k != "token"}, "token": s["token"], "busy": _busy,
            "queue": [x for x in t if x["status"] == "queued"], "used": [x for x in t if x["status"] == "used"][-15:][::-1],
            "log": L[-60:][::-1], "last": last,
            "lessons": _read("agency_lessons.json", {}), "private": _read("agency_private.json", [])[-3:][::-1],
            "ab": ab_results(), "trends": _read("agency_trends.json", {}), "costs": costs(), "backups": backups_list(), "retention": sorted(R.values(), key=lambda v: -v["at"])[:12],
            "inbox": I["items"][-25:][::-1], "tech": [e["extra"] for e in L if e["agent"] == "tech" and e.get("extra")][-8:][::-1], "reviews": reviews[::-1], "ai": ai_ready(), "provider": provider(), "models": dict(zip(("smart", "fast"), MODELS[provider()])),
            "plan": _read("agency_plan.json", {}), "spokes": _read("agency_spokes.json", {}),
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
    for k in ("autopilot", "min_score", "queue_target", "inbox", "inbox_auto", "ab", "daily_limit"):
        if k in b:
            s[k] = type(DEFAULT[k])(b[k])
    s["daily_limit"] = max(0.1, min(float(s.get("daily_limit", 1.0)), 50.0))
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
        r = http("POST", f"{base}/webhook/agency-{name}", json={"from": "agencja", "token": settings()["token"]}, timeout=15)
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
    m = AN.find_montage(wfs, g)
    return {"ok": bool(g), "generator": g and g["name"], "connected": bool(g) and AN.is_connected(g),
            "montage": m and m["name"], "montage_connected": bool(m) and AN.is_connected(m),
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
    m = AN.find_montage(wfs, g)
    if action == "connect":
        if not _public_url():
            return {"ok": False, "error": "Brak SOCIAL_DOMAIN w /root/social/.env"}
        if not AN.is_connected(g):
            _write("agency_backup.json", g)
        if m and not AN.is_connected(m):
            _write("agency_backup_montage.json", m)
        jobs = [(g, AN.patch(g, _public_url(), settings()["token"]))]
        if m:
            jobs.append((m, AN.patch_montage(m, _public_url(), settings()["token"])))
    elif action == "disconnect":
        jobs = [(g, AN.strip(g))] + ([(m, AN.strip_montage(m))] if m and AN.is_connected(m) else [])
    else:
        return JSONResponse({"error": "akcja"}, status_code=400)
    for wf, new in jobs:
        r = n8n("PUT", f"/api/v1/workflows/{wf['id']}", json=AN.put_body(new))
        if r.status_code >= 400:
            r = n8n("PUT", f"/api/v1/workflows/{wf['id']}", json=AN.put_body(new, minimal=True))
        if r.status_code >= 400:
            log("op", f"n8n nie przyjął zmian w {wf['name']}: {r.text[:200]}", "error")
            return {"ok": False, "error": f"{wf['name']}: {r.text[:300]}"}
        if wf.get("active"):
            n8n("POST", f"/api/v1/workflows/{wf['id']}/activate")
    log("op", ("Podpiął agencję do " if action == "connect" else "Odpiął agencję od ") + " i ".join(w["name"] for w, _ in jobs), "ok")
    return {"ok": True}


# ---------- kopie zapasowe danych centrum (codziennie 3:30, 14 ostatnich) ----------
import zipfile
from pathlib import Path
from fastapi.responses import FileResponse
from social import DIR as DATA_DIR, ROOT as SOCIAL_ROOT

BK = SOCIAL_ROOT / "backups"


def backup():
    BK.mkdir(parents=True, exist_ok=True)
    name = BK / f"centrum-{datetime.now(TZ).strftime('%Y-%m-%d_%H%M%S')}.zip"
    tmp = name.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in DATA_DIR.glob("*.json"):
            try:  # plik mógł zniknąć/zmienić się w trakcie (zapis z innego wątku) – pomijamy go zamiast wywalać całą kopię
                z.writestr(f"dane/{f.name}", f.read_bytes())
            except Exception as e:
                print("backup skip", f.name, e, flush=True)
        for extra in ("sesje.db",):
            src = SOCIAL_ROOT / extra
            if src.exists():
                try:
                    import sqlite3
                    snap = BK / "_sesje_snap.db"
                    with sqlite3.connect(src) as a, sqlite3.connect(snap) as b:
                        a.backup(b)
                    z.write(snap, extra)
                    snap.unlink(missing_ok=True)
                except Exception as e:
                    print("backup skip", extra, e, flush=True)
    tmp.replace(name)
    os.chmod(name, 0o600)
    for old in sorted(BK.glob("centrum-*.zip"), key=lambda f: f.stat().st_mtime)[:-14]:
        old.unlink()
    log("op", f"Kopia zapasowa: {name.name} ({name.stat().st_size // 1024} KB)", "ok")
    return name


def backups_list():
    if not BK.exists():
        return []
    fs = sorted(BK.glob("centrum-*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)[:14]
    return [{"name": f.name, "kb": f.stat().st_size // 1024, "at": f.stat().st_mtime} for f in fs]


@router.post("/api/agency/backup")
def do_backup():
    try:
        return {"ok": True, "name": backup().name}
    except Exception as e:
        log("op", f"Kopia zapasowa nie wyszła: {e}", "error")
        return {"ok": False, "error": str(e)[:200]}


@router.get("/api/agency/backup/{name}")
def get_backup(name: str):
    f = BK / name
    if not re.fullmatch(r"centrum-[\d_-]+\.zip", name) or not f.exists():
        return JSONResponse({"error": "nie ma"}, status_code=404)
    return FileResponse(f, media_type="application/zip", filename=name)


# ---------- harmonogram: codziennie rano dopełnia kolejkę ----------
def _loop():
    time.sleep(90)
    done = done_ret = done_priv = done_tr = done_bk = None
    last_inbox = 0
    while True:
        try:
            s = settings()
            now = datetime.now(TZ)
            q = [x for x in topics() if x["status"] == "queued"]
            if s["autopilot"] and ai_ready() and done != now.date() and (now.hour == s["research_hour"] or len(q) < 2):
                done = now.date()  # najwyżej raz dziennie z harmonogramu (next-topic sam dopełnia kolejkę)
                if len(q) < s["queue_target"]:
                    if research(max(4, s["queue_target"] - len(q) + 2)):
                        push_all("🔭 Nowe tematy w kolejce", "Trend Scout dorzucił tematy dla PozaSkali.", "/#agencja")
        except Exception as e:
            print("agency loop", e, flush=True)
        try:
            if now.hour == 3 and now.minute >= 20 and done_bk != now.date():
                done_bk = now.date()
                backup()
            if ai_ready() and s["autopilot"] and now.weekday() in (0, 3) and now.hour == 5 and done_tr != now.date():
                done_tr = now.date()
                trends()
            if ai_ready() and now.weekday() == 0 and now.hour == 8 and done_priv != now.date():
                done_priv = now.date()
                private_report()
            if ai_ready() and now.hour == 7 and done_ret != now.date():
                done_ret = now.date()
                retention()
            if ai_ready() and settings().get("inbox", True) and time.time() - last_inbox > 1800:
                last_inbox = time.time()
                inbox()
        except Exception as e:
            print("agency loop 2", e, flush=True)
        time.sleep(600)


@router.post("/api/agency/retention")
def do_retention():
    threading.Thread(target=retention, kwargs={"force": True}, daemon=True).start()
    return {"ok": True}


@router.post("/api/agency/inbox-run")
def do_inbox():
    threading.Thread(target=inbox, daemon=True).start()
    return {"ok": True}


threading.Thread(target=_loop, daemon=True).start()
