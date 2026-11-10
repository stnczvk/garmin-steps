"""Klipping: kampanie Content Rewards (Whop) → najlepsze na powiadomienie → link do filmu → Mac robi pionowe klipy po angielsku.
Wyszukiwanie przez Apify (parsebird/whop-content-rewards-scraper, ok. 0,14 $ za 100 kampanii), raz dziennie albo na żądanie."""
import json, math, os, secrets, threading, time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()
F = "klipping.json"
ACTOR = "parsebird~whop-content-rewards-scraper"
PLATFORMS = ["tiktok", "instagram", "youtube"]
_lock = threading.Lock()


def _rd():
    from social import _read
    return _read(F, {"campaigns": [], "scanned": 0, "seen": [], "jobs": []})


def _wr(d):
    from social import _write
    _write(F, d)


def _num(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def score(c):
    """Opłacalność: stawka za 1000 wyświetleń × ile budżetu zostało × ile osób już klipuje (mniej = lepiej)."""
    if c.get("opportunityScore") is not None:
        base = _num(c["opportunityScore"])
    else:
        rate = _num(c.get("rewardPerThousandViews") or c.get("rewardPerThousandMaxUsd"))
        left = _num(c.get("budgetLeftUsd"))
        base = rate * math.log10(max(10.0, left)) * 10
    crowd = _num(c.get("creatorsCount") or c.get("creators") or 0)
    return round(base / (1 + crowd / 200), 2)


def _slim(c):
    pay = c.get("payouts") or []
    return {"id": str(c.get("id") or c.get("campaignId") or c.get("campaignUrl")), "name": c.get("title") or c.get("name") or "kampania",
            "url": c.get("campaignUrl") or "", "rate": _num(c.get("rewardPerThousandViews") or c.get("rewardPerThousandMaxUsd")),
            "rate_min": _num(c.get("rewardPerThousandMinUsd")), "left": _num(c.get("budgetLeftUsd")), "total": _num(c.get("totalBudgetUsd")),
            "platforms": [p for p in (c.get("platforms") or []) if isinstance(p, str)],
            "max_payout": max([_num(p.get("maxPayoutUsd")) for p in pay if isinstance(p, dict)] or [0]),
            "days": c.get("estimatedDaysLeft"), "category": c.get("category") or "", "rules": (c.get("description") or "")[:1500],
            "score": score(c)}


def scan(notify=True):
    from social import cfg
    tok = cfg("APIFY_TOKEN")
    if not tok:
        raise RuntimeError("Wklej token Apify w Ustawieniach (ten sam co do statystyk TikToka)")
    r = httpx.post(f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items", params={"token": tok, "timeout": 280},
                   json={"status": "active", "platforms": PLATFORMS, "payoutTypes": ["cpm"], "sortBy": "opportunity",
                         "minBudgetLeftUsd": 200, "includeMetrics": True, "maxItems": 100}, timeout=320)
    if r.status_code >= 300:
        raise RuntimeError({401: "zły token Apify", 402: "skończyły się kredyty Apify"}.get(r.status_code, f"Apify {r.status_code}: {r.text[:150]}"))
    items = [x for x in (r.json() or []) if isinstance(x, dict)]
    C = sorted((_slim(c) for c in items), key=lambda c: -c["score"])[:40]
    with _lock:
        d = _rd()
        new = [c for c in C[:10] if c["id"] not in set(d["seen"])]
        d.update(campaigns=C, scanned=time.time(), seen=(d["seen"] + [c["id"] for c in C])[-500:])
        _wr(d)
    try:
        explain(C[:12])
    except Exception as e:
        print("klipping agent:", e, flush=True)
    if notify and new:
        try:
            from extra import push_all
            top = new[0]
            push_all("✂️ Nowe kampanie klipowe", f"{len(new)} nowych. Najlepsza: {top['name']} – ${top['rate']:.2f}/1000 wyśw., "
                     f"zostało ${top['left']:.0f}", "/#klipping")
        except Exception:
            pass
    return C


def explain(C):
    """Agent „Łowca kampanii”: zasady każdej z najlepszych kampanii po polsku – co klipować, wymagania, na co uważać, ocena."""
    from agency import claude, as_json
    d = _rd()
    have = d.setdefault("pl", {})
    todo = [c for c in C if c["id"] not in have and c.get("rules")]
    if not todo:
        return
    txt = "\n\n".join(f"### {c['id']}\nNazwa: {c['name']}\nStawka: ${c['rate']}/1000, budżet zostało ${c['left']:.0f}, "
                       f"platformy: {', '.join(c['platforms'])}\nZasady: {c['rules'][:1200]}" for c in todo[:12])
    res = as_json(claude("Jesteś agentem, który ocenia kampanie klipowe (Whop Content Rewards) dla początkującego klipera z Polski "
                         "(słaby angielski, automat robi pionowe klipy z napisami z dowolnego linku). Dla każdej kampanii napisz PO POLSKU, "
                         "krótko i konkretnie: co dokładnie klipować i skąd brać materiał, najważniejsze wymagania (min. wyświetlenia, "
                         "długość, hashtagi, oznaczenia, język), na co uważać (np. zakaz AI, ręczna akceptacja) i ocenę 1–10, czy warto "
                         "na start. Bez myślników jako przerw w zdaniach.\n\n" + txt +
                         '\n\nOdpowiedz tylko JSON: {"ID": {"co": "...", "wymagania": "...", "uwaga": "...", "ocena": 7}}', max_tokens=3000)) or {}
    with _lock:
        d = _rd()
        d.setdefault("pl", {}).update({k: v for k, v in res.items() if isinstance(v, dict)})
        d["pl"] = dict(list(d["pl"].items())[-200:])
        _wr(d)


def loop():
    while True:
        try:
            if os.environ.get("KLIPPING_WHOP") == "1" and time.time() - _rd().get("scanned", 0) > 24 * 3600:   # wstrzymane (11.2026) – tylko „Szukaj teraz”
                scan()
        except Exception as e:
            print("klipping:", e, flush=True)
        time.sleep(1800)


threading.Thread(target=loop, daemon=True).start()


@router.get("/api/klipping")
def api_state():
    d = _rd()
    pl = d.get("pl", {})
    C = d.get("manual", []) + sorted(d.get("external", []) + d["campaigns"], key=lambda c: -c.get("score", 0))
    return {"campaigns": [dict(c, pl=pl.get(c["id"])) for c in C], "scanned": d["scanned"], "jobs": list(reversed(d["jobs"][-30:]))}


@router.post("/api/klipping/scan")
async def api_scan():
    import asyncio
    try:
        C = await asyncio.to_thread(scan, False)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    return {"ok": True, "n": len(C)}


STREAMERS = [   # domyślne profile – wszystko można zmienić w zakładce
    {"id": "dzinold", "name": "Dzinold", "link": "twitch.tv/mrdzinold", "cam": "dzinold", "fmt": "oba"},
    {"id": "bandura", "name": "Bandura", "link": "", "cam": "auto", "fmt": "oba"},
    {"id": "tuszol", "name": "Tuszol", "link": "kick.com/tuszol", "cam": "auto", "fmt": "oba"},
    {"id": "merghani", "name": "Merghani", "link": "kick.com/xmerghani", "cam": "auto", "fmt": "oba"},
    {"id": "neex", "name": "Neex", "link": "", "cam": "auto", "fmt": "oba"},
    {"id": "remsua", "name": "Remsua", "link": "twitch.tv/remsua", "cam": "auto", "fmt": "oba"},
]


def _streamers(d=None):
    d = d or _rd()
    have = {x["id"]: x for x in d.get("streamers", [])}
    return [have.get(x["id"], x) for x in STREAMERS] + [x for x in d.get("streamers", []) if x["id"] not in {y["id"] for y in STREAMERS} | USUNIECI]


USUNIECI = {"discokarol", "pirlo", "amar", "klon", "mario", "anglik"}   # dodani i od razu wycofani – nie pokazujemy, nawet jeśli zdążyli się zapisać


@router.get("/api/klipping/kanal")
def api_kanal():
    return {"mail": _rd().get("mail", "")}


@router.post("/api/klipping/kanal")
async def api_kanal_save(req: Request):
    b = await req.json()
    m = (b.get("mail") or "").strip()[:120]
    with _lock:
        d = _rd()
        d["mail"] = m
        _wr(d)
    return {"mail": m}


@router.get("/api/klipping/streamers")
def api_streamers():
    return _streamers()


@router.post("/api/klipping/streamers")
async def api_streamer_save(req: Request):
    import re
    b = await req.json()
    sid = re.sub(r"[^a-z0-9_]", "", (b.get("id") or b.get("name") or "").lower())[:30]
    if not sid:
        return JSONResponse({"error": "Podaj nazwę"}, status_code=400)
    cam = b.get("cam")
    if isinstance(cam, list) and len(cam) == 4:
        cam = [max(0.0, min(1.0, _num(v))) for v in cam]
    elif not isinstance(cam, str):
        cam = "auto"
    x = {"id": sid, "name": (b.get("name") or sid)[:40], "link": (b.get("link") or "").strip().replace("https://", "").replace("www.", "")[:80],
         "cam": cam, "fmt": b.get("fmt") if b.get("fmt") in ("yt", "short", "oba") else "oba",
         "title_hint": (b.get("title_hint") or "")[:300], "auto_vod": bool(b.get("auto_vod"))}
    with _lock:
        d = _rd()
        old = next((y for y in _streamers(d) if y["id"] == sid), {})
        if x["auto_vod"] and not old.get("auto_vod"):   # od teraz: starsze nagrania pomijamy
            d.setdefault("watch_since", {})[sid] = time.time()
        d["streamers"] = [y for y in d.get("streamers", []) if y["id"] != sid] + [x]
        _wr(d)
    return x


@router.post("/api/klipping/job")
async def api_job(req: Request):
    b = await req.json()
    url = (b.get("url") or "").strip()
    if not url.startswith("http"):
        return JSONResponse({"error": "Wklej link do filmu albo nagrania live"}, status_code=400)
    with _lock:
        d = _rd()
        j = _mk_job(d, b, url)
        _wr(d)
    return j


def _mk_job(d, b, url):
    if True:
        camp = next((c for c in d.get("manual", []) + d.get("external", []) + d["campaigns"] if c["id"] == str(b.get("campaign") or "")), None)
        mode = "reakcje" if b.get("mode") == "reakcje" else "kampania"
        st = next((x for x in _streamers(d) if x["id"] == b.get("streamer")), None) if mode == "reakcje" else None
        if st:   # profil streamera: nick, kamerka, link na start filmu, styl tytułów
            b = {**b, "who": st["name"], "cam": st["cam"], "format": b.get("format") or st.get("fmt")}
        j = {"id": secrets.token_hex(4), "url": url, "n": max(0, min(15, int(b.get("n") or 0))), "campaign": None if mode == "reakcje" else camp,
             "mode": mode, "lang": "pl" if mode == "reakcje" else "en", "cam": b.get("cam") if isinstance(b.get("cam"), list) else (b.get("cam") or "auto")[:12], "who": (b.get("who") or "")[:40],
             "format": b.get("format") if b.get("format") in ("yt", "short", "oba") else "short",
             "auto": bool(b.get("auto")) and mode == "reakcje",   # sama publikacja na kanale Strimy w Pigułce
             "streamer": st["id"] if st else "", "link": st.get("link", "") if st else "", "title_hint": st.get("title_hint", "") if st else "",
             "state": "queued", "step": "Czeka na Maca…", "created": time.time(), "clips": []}   # n=0 → AI decyduje
        d["jobs"] = (d["jobs"] + [j])[-60:]
    return j


# ---------- automatyczne wykrywanie nowych streamów (Mac sprawdza kanały co 30 min) ----------
@router.get("/api/mont-hook/klipping/watch")
def hook_watch(req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    d = _rd()
    return {"streamers": [{"id": x["id"], "link": x["link"], "since": (d.get("watch_since") or {}).get(x["id"], time.time())}
                          for x in _streamers(d) if x.get("auto_vod") and x.get("link")],
            "seen": (d.get("seen_vods") or [])[-300:]}


@router.post("/api/mont-hook/klipping/found")
async def hook_found(req: Request):
    """Mac znalazł zakończone nagranie streama → zadanie jak po kliknięciu „Zrób filmy” (AI sama decyduje ile, auto publikacja)."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    return _found(await req.json())


def _found(b):
    """Nowe zakończone nagranie (zgłoszone przez serwer albo przez Maca) → zadanie w kolejce; Mac weźmie je, gdy będzie włączony."""
    vid, sid, url = str(b.get("vid") or "")[:80], b.get("streamer"), (b.get("url") or "").strip()
    if not vid or not url.startswith("http"):
        return {"ok": False}
    with _lock:
        d = _rd()
        seen = d.setdefault("seen_vods", [])
        if vid in seen or any(j["url"] == url for j in d["jobs"]):
            return {"ok": True, "new": False}
        seen.append(vid); d["seen_vods"] = seen[-500:]
        since = (d.get("watch_since") or {}).get(sid, 0)
        st = next((x for x in _streamers(d) if x["id"] == sid), None)
        # liczy się KONIEC streama (start + długość): stream, który trwał w chwili włączenia 📡, też ma wejść
        if not st or float(b.get("ts") or 0) + float(b.get("dur") or 0) < since:
            _wr(d)
            return {"ok": True, "new": False}
        j = _mk_job(d, {"mode": "reakcje", "streamer": sid, "n": 0, "auto": True}, url)
        j["title"] = (b.get("title") or "")[:120]; j["found"] = True
        _wr(d)
    try:
        from extra import push_all
        push_all(f"📡 Nowy stream: {st['name']}", (b.get("title") or url)[:100] + " – robię filmy", "/#klipping")
    except Exception:
        pass
    return {"ok": True, "new": True, "job": j["id"]}


# ---------- wykrywanie nowych streamów NA SERWERZE (działa 24/7, także gdy Mac jest wyłączony) ----------
_GQL_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"   # publiczny identyfikator strony twitch.tv


def _tw_vods(login):
    q = ('query{user(login:"%s"){stream{id} videos(first:5,type:ARCHIVE,sort:TIME){edges{node{id title lengthSeconds createdAt}}}}}' % login)
    r = httpx.post("https://gql.twitch.tv/gql", json={"query": q}, timeout=30,
                   headers={"Client-ID": _GQL_ID, "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"})
    u = ((r.json().get("data") or {}).get("user") or {})
    live = bool(u.get("stream"))
    out = []
    for i, e in enumerate(((u.get("videos") or {}).get("edges") or [])):
        n = e.get("node") or {}
        if live and i == 0:   # trwający stream (jego nagranie jest najnowsze) – weźmiemy po zakończeniu
            continue
        from datetime import datetime
        try:
            ts = datetime.fromisoformat(str(n.get("createdAt")).replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = 0
        out.append({"vid": f"tw:v{n['id']}", "url": f"https://www.twitch.tv/videos/{n['id']}", "title": n.get("title") or "",
                    "ts": ts, "dur": n.get("lengthSeconds") or 0})
    return out


def _kick_vods(slug):
    from datetime import datetime
    r = httpx.get(f"https://kick.com/api/v2/channels/{slug}/videos", timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36", "Accept": "application/json"})
    if r.status_code >= 300:
        raise RuntimeError(f"kick {r.status_code}")   # Cloudflare potrafi blokować serwery – wtedy wykrywa Mac
    L = r.json()
    out = []
    for e in (L if isinstance(L, list) else L.get("data") or [])[:5]:
        if e.get("is_live"):
            continue
        uid = (e.get("video") or {}).get("uuid") or e.get("uuid")
        if not uid:
            continue
        try:
            ts = datetime.fromisoformat(str(e.get("start_time") or e.get("created_at")).replace("Z", "+00:00").replace(" ", "T")).timestamp()
        except Exception:
            ts = 0
        out.append({"vid": "kick:" + uid, "url": f"https://kick.com/{slug}/videos/{uid}", "title": e.get("session_title") or "",
                    "ts": ts, "dur": (e.get("duration") or 0) / 1000})
    return out


def watch_server():
    import re
    d = _rd()
    seen = set(d.get("seen_vods") or [])
    for st in _streamers(d):
        if not st.get("auto_vod") or not st.get("link"):
            continue
        m = re.match(r"(?:https?://)?(?:www\.)?(twitch\.tv|kick\.com)/([\w-]+)", st["link"].strip(), re.I)
        if not m:
            continue
        try:
            V = _tw_vods(m.group(2).lower()) if m.group(1).lower() == "twitch.tv" else _kick_vods(m.group(2).lower())
        except Exception as e:
            print("klipping watch", st["id"], e, flush=True)
            continue
        for v in V:
            if v["vid"] in seen or (v["dur"] and v["dur"] < 20 * 60):
                continue
            r = _found({**v, "streamer": st["id"]})
            if r.get("new"):
                print("klipping watch: nowy stream w kolejce", st["id"], v["url"], flush=True)


def _seen_reset():
    """Jednorazowo: nagrania odrzucone przez złe porównanie (start zamiast końca streama) do ponownego sprawdzenia.
    Już zrobione streamy nie wejdą drugi raz – zadania sprawdzają też adres."""
    with _lock:
        d = _rd()
        if d.get("seen_reset_v1"):
            return
        d["seen_vods"] = []
        d["seen_reset_v1"] = True
        _wr(d)


def _auto_wszyscy():
    """Jednorazowo: 📡 włączone u wszystkich 7 streamerów; starsze nagrania pomijamy (od teraz)."""
    with _lock:
        d = _rd()
        if d.get("auto_wszyscy_v1"):
            return
        now = time.time()
        have = {x["id"]: x for x in d.get("streamers", [])}
        for st in STREAMERS:
            x = {**st, **have.get(st["id"], {}), "auto_vod": True}
            have[st["id"]] = x
            d.setdefault("watch_since", {}).setdefault(st["id"], now)
        d["streamers"] = list(have.values())
        d["auto_wszyscy_v1"] = True
        _wr(d)


def _watch_loop():
    try:
        _auto_wszyscy()
        _seen_reset()
    except Exception as e:
        print("klipping auto:", e, flush=True)
    time.sleep(60)
    while True:
        try:
            watch_server()
        except Exception as e:
            print("klipping watch:", e, flush=True)
        time.sleep(1800)


threading.Thread(target=_watch_loop, daemon=True).start()


@router.post("/api/klipping/manual")
async def api_manual(req: Request):
    """Kampania dodana ręcznie (np. z clipping.net – panel za logowaniem, nie da się go pobierać automatycznie)."""
    import asyncio
    b = await req.json()
    name = (b.get("name") or "").strip()[:120]
    if not name:
        return JSONResponse({"error": "Podaj nazwę kampanii"}, status_code=400)
    c = {"id": "m-" + secrets.token_hex(4), "name": name, "url": (b.get("url") or "").strip(), "rate": _num(b.get("rate")), "rate_min": 0,
         "left": _num(b.get("left")), "total": 0, "platforms": [p for p in PLATFORMS if p in (b.get("platforms") or PLATFORMS)],
         "max_payout": _num(b.get("max_payout")), "days": None, "category": (b.get("source") or "ręcznie")[:30],
         "rules": (b.get("rules") or "")[:3000], "manual": True}
    c["score"] = round(c["rate"] * math.log10(max(10.0, c["left"] or 1000)) * 10, 2)
    with _lock:
        d = _rd()
        d["manual"] = (d.get("manual", []) + [c])[-30:]
        _wr(d)
    try:
        await asyncio.to_thread(explain, [c])
    except Exception:
        pass
    return c


@router.post("/api/klipping/manual/{cid}/delete")
def api_manual_del(cid: str):
    with _lock:
        d = _rd()
        d["manual"] = [c for c in d.get("manual", []) if c["id"] != cid]
        _wr(d)
    return {"ok": True}


# ---------- nauka: Twoje oceny i poprawki → zasady dla AI (osobno dla każdego streamera) ----------
def _zasady(sid):
    return (_rd().get("zasady") or {}).get(sid or "_", "")


def _learn(sid):
    """Z ostatnich ocen i poprawek AI wyciąga max 10 krótkich zasad, które trafiają do kolejnych wyborów fragmentów i tytułów."""
    from agency import claude
    d = _rd()
    F = [f for f in d.get("feedback", []) if f.get("sid") == sid][-60:]
    if len(F) < 3:
        return
    txt = "\n".join(f"- {f['kind']}: {f.get('what', '')}" + (f" | przed: {f['before']}" if f.get("before") else "") +
                     (f" | po: {f['after']}" if f.get("after") else "") + (f" | uwaga: {f['note']}" if f.get("note") else "") for f in F)
    old = (d.get("zasady") or {}).get(sid, "")
    res = claude("Prowadzę kanał z fragmentami streamów. Poniżej moje oceny i poprawki filmów, które zrobiło AI "
                 f"(streamer: {sid}). Na ich podstawie napisz max 10 krótkich, konkretnych zasad dla AI na przyszłość: "
                 "jakie momenty wybierać, a jakich unikać, jak długie, jak pisać tytuły, opisy i napisy na miniaturę. "
                 "Zasady mają wynikać z danych, bez ogólników. Bez myślników jako przerw w zdaniach.\n\n"
                 + (f"Dotychczasowe zasady (popraw/uzupełnij):\n{old}\n\n" if old else "") + "Oceny i poprawki:\n" + txt +
                 "\n\nOdpowiedz samą listą zasad, każda w nowej linii zaczynającej się od „• ”.", max_tokens=900)
    with _lock:
        d = _rd()
        d.setdefault("zasady", {})[sid] = (res or "").strip()[:2500]
        d["learned_at"] = {**d.get("learned_at", {}), sid: time.time()}
        _wr(d)


def feedback(sid, kind, **kw):
    """Zapisuje ocenę/poprawkę; co 3 nowe – AI odświeża zasady w tle."""
    with _lock:
        d = _rd()
        d["feedback"] = (d.get("feedback", []) + [{"sid": sid or "_", "kind": kind, "at": time.time(),
                                                    **{k: str(v)[:400] for k, v in kw.items() if v}}])[-400:]
        n = sum(1 for f in d["feedback"] if f["sid"] == (sid or "_") and f["at"] > (d.get("learned_at") or {}).get(sid or "_", 0))
        _wr(d)
    if n >= 3:
        threading.Thread(target=lambda: _safe_learn(sid or "_"), daemon=True).start()


def _safe_learn(sid):
    try:
        _learn(sid)
    except Exception as e:
        print("klipping nauka:", e, flush=True)


@router.post("/api/klipping/ocena")
async def api_ocena(req: Request):
    b = await req.json()
    d = _rd()
    j = next((j for j in d["jobs"] if j["id"] == b.get("job")), {})
    c = next((c for c in j.get("clips", []) if c["name"] == b.get("clip")), {})
    good = b.get("ocena") == "dobry"
    feedback(j.get("streamer"), "dobry film" if good else "słaby film",
             what=f"{c.get('yt_title') or c.get('hook')} ({c.get('dur')} s)", note=b.get("uwaga"))
    with _lock:
        d = _rd()
        for jj in d["jobs"]:
            if jj["id"] == b.get("job"):
                for cc in jj["clips"]:
                    if cc["name"] == b.get("clip"):
                        cc["ocena"] = "dobry" if good else "slaby"
        _wr(d)
    return {"ok": True}


@router.get("/api/klipping/zasady")
def api_zasady():
    return _rd().get("zasady", {})


@router.post("/api/klipping/job/{jid}/delete")
def api_job_del(jid: str):
    with _lock:
        d = _rd()
        d["jobs"] = [j for j in d["jobs"] if j["id"] != jid]
        _wr(d)
    return {"ok": True}


# ---------- Mac ----------
def _tok(req):
    from montaz import _tok as t
    return t(req)


@router.get("/api/mont-hook/klipping/next")
def hook_next(req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    with _lock:
        d = _rd()
        for j in d["jobs"]:   # zawieszone (Mac się wyłączył) → po 2 h wracają do kolejki
            if j["state"] == "working" and time.time() - j.get("at", 0) > 7200:
                j["state"] = "queued"
        j = next((j for j in d["jobs"] if j["state"] == "queued"), None)
        if j:
            j.update(state="working", step="Mac pobiera film…", at=time.time())
        _wr(d)
    return {"job": j}


@router.post("/api/mont-hook/klipping/{jid}/status")
async def hook_status(jid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    with _lock:
        d = _rd()
        j = next((j for j in d["jobs"] if j["id"] == jid), None)
        if j:
            j.update({k: v for k, v in b.items() if k in ("state", "step", "error", "title")}, at=time.time())
            if b.get("clip"):
                j["clips"].append(b["clip"])
            _wr(d)
    if j and b.get("state") == "done":
        try:
            from extra import push_all
            push_all("✂️ Klipy gotowe", f"{len(j['clips'])} klipów z „{j.get('title') or j['url'][:40]}”", "/#klipping")
        except Exception:
            pass
    return {"ok": True}


@router.post("/api/mont-hook/klipping/import")
async def hook_import(req: Request):
    """Kampanie z innych stron (clipping.net) – codzienny import przez przeglądarkę na Macu. Stawki za 100 tys. → za 1000."""
    import asyncio
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    src = (b.get("source") or "clipping.net")[:30]
    out = []
    for x in b.get("campaigns") or []:
        if not x.get("name") or x.get("status") not in (None, "Active"):
            continue
        if __import__("re").search(r"(?i)stake|csgo ?roll|wpt|casino|gambl|igaming|bet", x["name"]):   # hazard pomijamy
            continue
        rate = _num(x.get("rate_100k")) / 100
        c = {"id": f"{src}:{x.get('slug') or x['name']}"[:120], "name": x["name"][:120], "url": x.get("url") or "", "rate": rate, "rate_min": 0,
             "left": _num(x.get("pot")), "total": 0, "platforms": [p for p in (x.get("platforms") or PLATFORMS) if p in PLATFORMS] or PLATFORMS,
             "max_payout": 0, "days": x.get("days") or None, "category": src, "min_views": int(_num(x.get("min_views"))),
             "rules": (x.get("rules") or x.get("summary") or "")[:3000], "external": True}
        # ranking: stawka, ale wysoki próg wejścia (np. 100 tys. wyśw.) mocno obniża szanse świeżego konta
        c["score"] = round(rate * 40 / max(1.0, c["min_views"] / 50000), 2)
        out.append(c)
    with _lock:
        d = _rd()
        old = {c["id"] for c in d.get("external", [])}
        d["external"] = [c for c in d.get("external", []) if not c["id"].startswith(src + ":")] + out
        _wr(d)
    new = [c for c in out if c["id"] not in old]
    try:
        await asyncio.to_thread(explain, sorted(out, key=lambda c: -c["score"])[:12])
    except Exception as e:
        print("klipping agent:", e, flush=True)
    if new:
        try:
            from extra import push_all
            top = max(new, key=lambda c: c["score"])
            push_all("✂️ Nowe kampanie na " + src, f"{len(new)} nowych. Najlepsza: {top['name']} – ${top['rate']:.2f}/1000, próg {top['min_views'] // 1000} tys.", "/#klipping")
        except Exception:
            pass
    return {"ok": True, "n": len(out), "new": len(new)}


DOBRE = ("DOBRY moment to szczyt emocji albo akcji: głośny śmiech, wkurzenie, szok, kłótnia, mocna puenta, wygrana albo porażka w grze, "
         "absurdalna sytuacja, ostra opinia o czymś znanym, historia z zaskakującym końcem. Linie oznaczone 🔊 to miejsca, gdzie na streamie "
         "robi się wyraźnie głośniej niż zwykle (śmiech, krzyk, emocje), to mocna wskazówka, ale sprawdź w tekście, czy coś się tam dzieje. "
         "Linie z ⭐ to miejsca, które WIDZOWIE SAMI wycięli jako klipy na Twitchu (z tytułem i liczbą wyświetleń): to najpewniejszy znak "
         "dobrego momentu, wybieraj je w pierwszej kolejności (więcej wyświetleń = lepiej) i dawaj im wyższą ocenę. "
         "ZŁY moment (nigdy): czekanie, start i koniec streama, czytanie donejtów i czatu, dziękowanie za suby, sprawy techniczne, menu i "
         "ładowanie gry, spokojne gadanie o niczym, rzeczy niezrozumiałe bez kontekstu. SEGMENTY SPONSORSKIE pomijaj całkowicie: "
         "reklamy, współprace, kody rabatowe, linki w opisie, promocje sklepów, kasyn, bukmacherów i aplikacji. Klip ani segment nie może "
         "zawierać żadnego kawałka takiej reklamy: zacznij po niej albo skończ przed nią. Lepiej mniej klipów niż słabe klipy.")


def _energy_lines(trans, energia):
    """Dokleja 🔊 do linii transkrypcji, w których dźwięk jest wyraźnie głośniejszy niż typowo na tym streamie."""
    import re
    if not energia:
        return trans
    try:
        vals = sorted(float(v) for _, v in energia)
        thr = vals[int(len(vals) * 0.88)]
        hot = {int(float(t)) // 10 for t, v in energia if float(v) >= thr}
    except Exception:
        return trans
    out = []
    for ln in trans.split("\n"):
        m = re.match(r"\[(\d+)\]", ln)
        out.append(ln + " 🔊" if m and int(m.group(1)) // 10 in hot else ln)
    return "\n".join(out)


def _widz_lines(trans, W):
    """⭐ przy liniach, które widzowie sami zaklipowali na Twitchu (z tytułem i liczbą wyświetleń przy pierwszej linii)."""
    import re
    if not W:
        return trans
    W = sorted(W, key=lambda w: -w.get("views", 0))[:60]
    out, first = [], set()
    for ln in trans.split("\n"):
        m = re.match(r"\[(\d+)\]", ln)
        if m:
            t = int(m.group(1))
            hit = next((w for w in W if w["t"] - 3 <= t <= w["t"] + w.get("dur", 30)), None)
            if hit:
                k = (hit["t"], hit.get("title"))
                ln += " ⭐" if k in first else f" ⭐ KLIP WIDZÓW: „{hit.get('title', '')}” ({hit.get('views', 0)} wyśw.)"
                first.add(k)
        out.append(ln)
    return "\n".join(out)


def _parts(trans, size):
    """Cała transkrypcja (nie tylko początek) w kawałkach po ~size znaków z małą zakładką."""
    L, out, cur, n = trans.split("\n"), [], [], 0
    for ln in L:
        cur.append(ln); n += len(ln) + 1
        if n >= size:
            out.append("\n".join(cur)); cur = cur[-15:]; n = sum(len(x) + 1 for x in cur)
    if len(cur) > 15 or not out:
        out.append("\n".join(cur))
    return out


async def _kandydaci(b, dur_min, size, ask, lo, hi):
    """Każdy kawałek streama oceniany osobno (równolegle) → kandydaci z oceną 1–10 → najlepsi z całego streama."""
    from agency import claude, as_json
    import asyncio
    trans = _widz_lines(_energy_lines(b.get("transkrypcja") or "", b.get("energia")), b.get("widzowie"))
    P = _parts(trans, size)
    sysm = __import__("skills").sk("hook", "caption", "trend", "slop")

    def one(i, part):
        return claude(f"Fragment {i + 1} z {len(P)} transkrypcji streama ({dur_min:.0f} min całość). Każda linia: [sekunda] tekst.\n\n"
                      + part + "\n\n" + ask, max_tokens=4000, system=sysm)
    sem = asyncio.Semaphore(6)

    async def lim(i, p):
        async with sem:
            return await asyncio.to_thread(one, i, p)
    R = await asyncio.gather(*[lim(i, p) for i, p in enumerate(P)], return_exceptions=True)
    if all(isinstance(r, Exception) for r in R):
        raise R[0]
    K = []
    for r in R:
        if isinstance(r, Exception):
            continue
        for k in (as_json(r) or {}).get("klipy") or []:
            try:
                k["start"], k["end"], k["ocena"] = float(k["start"]), float(k["end"]), float(k.get("ocena") or 0)
            except Exception:
                continue
            if lo <= k["end"] - k["start"] <= hi:
                K.append(k)
    K.sort(key=lambda k: -k["ocena"])
    out = []
    for k in K:   # bez nakładania (zakładka kawałków może dać ten sam moment dwa razy)
        if all(k["end"] <= o["start"] or k["start"] >= o["end"] for o in out):
            out.append(k)
    return out


def _pick(K, n, auto_max):
    if n:
        return [k for k in K if k["ocena"] >= 6][:n] or K[:1]
    good = [k for k in K if k["ocena"] >= 8] or K[:1]
    return good[:auto_max]


def _window(trans, a, b):
    import re
    out = []
    for ln in trans.split("\n"):
        m = re.match(r"\[(\d+)\]", ln)
        if m and a <= int(m.group(1)) <= b:
            out.append(ln)
    return "\n".join(out)


async def _granice(b, who, K):
    """Drugie podejście do każdego segmentu YT: AI widzi szersze okno (10 min przed, do 65 min po) i ustala pełne granice,
    żeby całe oglądanie filmu / cała gra była jednym filmem, a nie 2-minutowym kawałkiem."""
    from agency import claude, as_json
    import asyncio
    trans = b.get("transkrypcja") or ""
    end_all = float(b.get("dur") or 0) or 1e9

    def one(k):
        w = _window(trans, k["start"] - 600, k["start"] + 3900)
        r = claude(f"Transkrypcja streama ({who}), każda linia: [sekunda] tekst.\n\n{w}\n\n"
                   f"Wstępnie wybrany segment na film YouTube: {k['start']:.0f}–{k['end']:.0f} s. Co się dzieje: {k.get('dlaczego') or k.get('yt_title')}.\n"
                   f"Ustal PEŁNE granice tej jednej całości. Jeśli {who} ogląda film / klip: start chwilę przed włączeniem (gdy zapowiada, co ogląda), "
                   "koniec dopiero gdy film się skończy i skończy się komentarz do niego, a stream przechodzi do czegoś innego. Jeśli gra: cała runda/mecz. "
                   "Nie ucinaj w środku, ale nie dokładaj innego tematu ani reklam/sponsorów. Celuj w min 5 min, max 60 min.\n"
                   'Odpowiedz tylko JSON: {"start": s, "end": s}', max_tokens=300)
        j = as_json(r) or {}
        a, e = float(j.get("start", k["start"])), float(j.get("end", k["end"]))
        if 170 <= e - a <= 62 * 60 and a < k["end"] and e - a >= k["end"] - k["start"] - 5 and e > k["start"]:
            k = dict(k, start=max(0, a), end=min(e, end_all))
        return k
    R = await asyncio.gather(*[asyncio.to_thread(one, k) for k in K], return_exceptions=True)
    out = []
    for k, r in zip(K, R):
        r = k if isinstance(r, Exception) else r
        if all(r["end"] <= o["start"] or r["start"] >= o["end"] for o in out):
            out.append(r)
    return out


def _uklad(t, j, mail):
    """Układ opisu: tekst, pod nim link do streamera, odstęp, kontakt do współpracy, hashtagi na końcu."""
    import re
    t = (t or "").strip()
    if not t:
        return t
    m = re.search(r"(\s*(#\w+\s*)+)$", t)
    body, tags = (t[:m.start()].strip(), " ".join(m.group(1).split())) if m else (t, "")
    st = next((x for x in _streamers() if x["id"] == j.get("streamer")), {})
    link = (st.get("link") or j.get("link") or "").strip()
    if link and link not in body:
        url = link if link.startswith("http") else "https://" + link
        body += f"\n🔴 {st.get('name') or j.get('who') or 'Streamer'} na żywo: {url}"
    if mail:
        body += f"\n\n\n📩 Współpraca: {mail}"
    return body + ("\n" + tags if tags else "")


def STYL(who):
    """Tytuły i opisy jak od człowieka, który prowadzi kanał z klipami, a nie od AI."""
    return (f"(max 60 znaków). Tytuł ma przyciągnąć kliknięcie jak na polskich kanałach z klipami ze streamów: konkretna sytuacja, "
            f"emocja albo cytat, imię {who} na początku albo naturalnie w środku, potoczny język widzów (np. „{who} NIE WYTRZYMAŁ po tym meczu”, "
            f"„{who} o Doncicu: to jest kosmita”, „{who} przegrał wszystko w ostatniej sekundzie”). Możesz dać 1–2 słowa WERSALIKAMI dla emocji. "
            "Ciekawość ma wynikać z sytuacji, ale nie kłam i nie obiecuj czegoś, czego nie ma w filmie. "
            "Opisy piszesz jak fan na luzie, krótko i konkretnie, co się dzieje. ZAKAZANE (brzmią jak AI): „Sprawdź”, „Zobacz, jak”, „imponujący”, "
            "„unikalny”, „niesamowity”, „prawdziwy król”, „analiza”, „nie uwierzysz”, „w tym filmie”, „przekonaj się”, myślniki jako przerwy w zdaniu, "
            "wykrzykniki co zdanie, pytania retoryczne na koniec.")


def _kompakt(trans, step=60, chars=180):
    """Mapa całego streama: co minutę ~180 znaków mowy + liczniki 🔊 i ⭐ (z klipem widzów) – mieści się cały stream w jednym zapytaniu."""
    import re
    B = {}
    for ln in trans.split("\n"):
        m = re.match(r"\[(\d+)\]\s*(.*)", ln)
        if not m:
            continue
        k = int(m.group(1)) // step
        x = B.setdefault(k, {"t": [], "loud": 0, "star": 0, "klip": ""})
        txt = m.group(2)
        x["loud"] += "🔊" in txt
        x["star"] += "⭐" in txt
        km = re.search(r"⭐ KLIP WIDZÓW: (.*)$", txt)
        if km and not x["klip"]:
            x["klip"] = km.group(1)[:90]
        x["t"].append(re.sub(r"\s*(🔊|⭐.*)$", "", txt))
    out = []
    for k in sorted(B):
        x = B[k]
        t = " ".join(x["t"])
        t = t[:chars] + ("…" if len(t) > chars else "")
        out.append(f"[{k * step // 60} min] {t}" + (" 🔊" * min(x["loud"], 3)) + (f" ⭐{x['klip']}" if x["klip"] else (" ⭐" if x["star"] else "")))
    return "\n".join(out)


async def _wybor_yt(b, n, dur_min):
    """Poziome filmy na YouTube. Krok 1: AI dostaje mapę CAŁEGO streama (minuta po minucie) i dzieli go na całe segmenty
    (oglądanie jednego filmu, jeden mecz, jedna historia, IRL: jedna sytuacja). Krok 2: dla najlepszych segmentów pełna
    transkrypcja tylko tego kawałka → dokładne granice, tytuł, opis, miniatura."""
    from agency import claude, as_json
    import asyncio
    who = b.get("who") or "streamer"
    hint = (b.get("title_hint") or "").strip()
    zas = _zasady(b.get("streamer"))
    trans = _widz_lines(_energy_lines(b.get("transkrypcja") or "", b.get("energia")), b.get("widzowie"))
    # 48-godzinny stream to ~2900 minut: co 3 min i krócej, żeby AI widziało cały stream, a nie pierwsze godziny
    step = 60 if dur_min <= 400 else int(min(240, 60 * (dur_min / 400) ** 0.7))
    ln = max(1, dur_min * 60 / step)
    mapa = _kompakt(trans, step, int(max(70, min(180, 110000 / ln - 25))))
    ask1 = (f"Mapa całego streama {who} ({dur_min:.0f} min), linia co {step // 60} min: początek tego, co mówi. 🔊 = głośno (śmiech, krzyk), "
            "⭐ = widzowie zrobili tu klip na Twitchu (z tytułem).\n\n" + mapa[:120000] +
            f"\n\nPodziel stream na kolejne SEGMENTY tematyczne, czyli całe rzeczy od początku do końca: {who} ogląda jeden film / klip / odcinek "
            "(od włączenia do końca filmu i komentarza po), gra jeden mecz / rundę, prowadzi jedną rozmowę / historię / dyskusję, na IRL jedno miejsce "
            "albo jedna sytuacja. Nie tnij oglądania ani meczu na kawałki: jeśli oglądał film 25 minut, to jeden segment 25 min. "
            "Segment ma max 60 min: gdy jedna rzecz trwa dłużej (np. wielogodzinne granie w jedną grę), podziel ją na kilka segmentów "
            "po 15–60 min w naturalnych miejscach (osobne mecze, rundy, misje, etapy, ważne wydarzenia w grze). "
            "Pomiń jako segmenty: start i koniec streama, czekanie, przerwy, czytanie donejtów, reklamy i współprace sponsorskie. "
            "Każdy segment oceń 1–10 jako osobny film na YouTube (10 = ludzie obejrzą do końca i udostępnią; dużo ⭐ i 🔊 w środku = wyżej; "
            "spokojne gadanie o niczym = nisko). " + (f"ZASADY z moich ocen (stosuj):\n{zas}\n" if zas else "") +
            'Odpowiedz tylko JSON: {"segmenty": [{"od_min": m, "do_min": m, "co": "krótko co się dzieje", "ocena": 1-10}]}')
    try:
        r1 = await asyncio.to_thread(lambda: claude(ask1, max_tokens=4000))
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    S = []
    for x in (as_json(r1) or {}).get("segmenty") or []:
        try:
            a_, e_ = float(x["od_min"]) * 60, float(x["do_min"]) * 60 + step
        except Exception:
            continue
        if e_ - a_ > 62 * 60:   # AI dało za długi kawałek – bierzemy pierwsze 60 min, granice i tak poprawi drugi krok
            e_ = a_ + 60 * 60
        if 170 <= e_ - a_ <= 62 * 60:
            S.append({"start": a_, "end": e_, "co": str(x.get("co") or ""), "ocena": float(x.get("ocena") or 0)})
    S.sort(key=lambda x: -x["ocena"])
    # bez ręcznej liczby: oceny 7+ (max 6); gdy żadna nie dobiła do 7 – 2 najlepsze z 5+, żeby długi stream nie skończył się bez filmu
    P = [x for x in S if x["ocena"] >= 6][:n] if n else ([x for x in S if x["ocena"] >= 7][:6] or [x for x in S if x["ocena"] >= 5][:2] or S[:1])
    print(f"klipping yt: segmentów {len(S)}, wybrane {len(P)}", flush=True)
    sysm = __import__("skills").sk("hook", "caption", "trend", "slop")

    def one(k):
        w = _window(trans, k["start"] - 240, k["end"] + 240)
        if len(w) > 90000:   # bardzo długi segment: środek skrócony, początek i koniec pełne (tam są granice)
            w = w[:45000] + "\n[…]\n" + w[-45000:]
        r = claude(f"Transkrypcja fragmentu streama {who}, każda linia: [sekunda] tekst (🔊 głośno, ⭐ klip widzów).\n\n{w}\n\n"
                   f"W tym fragmencie jest segment: {k['co']} (mniej więcej {k['start']:.0f}–{k['end']:.0f} s). Ustal jego DOKŁADNE granice: start chwilę "
                   "przed rozpoczęciem (zapowiedź, co włącza / w co gra / o czym mówi), koniec gdy ta rzecz i komentarz do niej się kończą. Całość, nie kawałek. "
                   "Bez reklam i sponsorów na początku i końcu. Celuj w min 5 min, max 60 min.\n"
                   "Potem napisz tytuł YouTube po polsku " + STYL(who) + " " + (f"Styl tytułów dla tego kanału: {hint}. " if hint else "") +
                   (f"ZASADY z moich ocen (stosuj):\n{zas}\n" if zas else "") +
                   "opis YouTube (2–3 krótkie zdania + 3–5 hashtagów), napis na miniaturę (2–4 słowa, WERSALIKI) i hook na pierwsze 4 s (max 7 słów). "
                   "Tytuł i opis mają mówić o najmocniejszym momencie z tego segmentu.\n"
                   "Dodatkowo WŁASNA WARSTWA KANAŁU (to odróżnia nas od surowego VOD): notatki = 3–6 krótkich napisów na ekranie z kontekstem albo "
                   f"komentarzem kanału w chwili, gdy się przydają (np. „{who} jeszcze nie wie, co zaraz zobaczy”, „to jest ten moment z klipu”, "
                   "„tu zaczyna się kłótnia”), max 8 słów, naturalnie, z humorem, bez myślników; rozdzialy = 3–6 rozdziałów filmu (pierwszy na starcie), "
                   "tytuł rozdziału max 5 słów.\n"
                   "Na koniec PRZESTOJE do wycięcia (dynamiczny film, jak u dobrych klipperów): fragmenty min. 8 s, w których widz się nudzi, "
                   f"np. {who} nie żyje w grze i tylko czeka na koniec rundy albo nowej gry (\"nie żyję\", \"dead\", cisza, ogląda kolegów bez emocji), "
                   "przerwy taktyczne i między połowami, lobby, kolejka, ładowanie, menu, kupowanie bez rozmowy, odchodzenie od kompa. "
                   "NIE wycinaj: zabawnych rozmów, reakcji, clutchy kolegów, które komentuje, ani niczego potrzebnego do zrozumienia akcji. "
                   "Jeśli nie ma pewnych przestojów, zostaw pustą listę.\n"
                   'Odpowiedz tylko JSON: {"start": s, "end": s, "yt_title": "...", "yt_desc": "...", "thumb": "...", "hook": "...", '
                   '"notatki": [{"t": s, "tekst": "..."}], "rozdzialy": [{"t": s, "tytul": "..."}], "wytnij": [{"od": s, "do": s}]}',
                   max_tokens=3000, system=sysm)
        j = as_json(r) or {}
        try:
            a_, e_ = float(j.get("start", k["start"])), float(j.get("end", k["end"]))
        except Exception:
            a_, e_ = k["start"], k["end"]
        if not (170 <= e_ - a_ <= 62 * 60 and a_ < k["end"] and e_ > k["start"]):
            a_, e_ = k["start"], k["end"]
        def tl(L, f):
            out = []
            for z in L or []:
                try:
                    t = float(z.get("t"))
                except Exception:
                    continue
                if a_ <= t <= e_ and str(z.get(f) or "").strip():
                    out.append({"t": round(t - a_, 1), f: str(z[f]).strip()[:60]})
            return sorted(out, key=lambda z: z["t"])[:8]
        W = []
        for z in j.get("wytnij") or []:
            try:
                x0, x1 = max(float(z["od"]), a_ + 5), min(float(z["do"]), e_ - 5)
            except Exception:
                continue
            if x1 - x0 >= 8:
                W.append([round(x0 - a_, 1), round(x1 - a_, 1)])
        W = sorted(W)[:80]
        if sum(y - x for x, y in W) > 0.4 * (e_ - a_):   # AI chce wyciąć prawie wszystko – nie ufamy, zostaje tylko cięcie ciszy
            W = []
        return {**k, **{x: j.get(x) for x in ("yt_title", "yt_desc", "thumb", "hook")}, "start": max(0.0, a_), "end": e_,
                "notatki": tl(j.get("notatki"), "tekst"), "rozdzialy": tl(j.get("rozdzialy"), "tytul"), "wytnij": W}
    R = await asyncio.gather(*[asyncio.to_thread(one, k) for k in P], return_exceptions=True)
    from montaz import human
    out = []
    for k, r in zip(P, R):
        if isinstance(r, Exception) or not r.get("yt_title"):
            continue
        if not all(r["end"] <= o["start"] or r["start"] >= o["end"] for o in out):
            continue
        o = {x: human(str(r.get(x) or "")) for x in ("yt_title", "yt_desc", "thumb", "hook")}
        out.append({"start": r["start"], "end": r["end"], **o, "tiktok": "", "instagram": o["yt_desc"], "ocena": r["ocena"], "dlaczego": r["co"][:200],
                    "notatki": [{"t": z["t"], "tekst": human(z["tekst"])} for z in r.get("notatki") or []],
                    "rozdzialy": [{"t": z["t"], "tytul": human(z["tytul"])} for z in r.get("rozdzialy") or []],
                    "wytnij": r.get("wytnij") or []})
    return {"klipy": sorted(out, key=lambda k: k["start"])}


async def _wybor_reakcje(b, n, dur_min):
    """Polskie streamy: najmocniejsze momenty na pionowe shorty – z całego streama, z oceną, z podpowiedzią głośności."""
    who = b.get("who") or "streamer"
    zas = _zasady(b.get("streamer"))
    ask = (f"Znajdź w TYM fragmencie najlepsze momenty (0–6, zero jeśli nic nie jest naprawdę mocne) na pionowe shorty, w których {who} "
           "ogląda coś, gra, rozmawia albo jest na IRL (na mieście, w podróży, z ludźmi: zaczepki, reakcje przechodniów, wpadki, "
           "spięcia, zabawne sytuacje) i dzieje się coś mocnego. " + DOBRE +
           " Klip 20–60 s, zrozumiały bez reszty streama, mocny początek (reakcja albo zapowiedź w pierwszych 2 s), koniec na puencie. "
           "Oceń każdy szczerze 1–10 (10 = viral, 7 = dobry short, 5 = przeciętny fragment streama). "
           "Do każdego: krótki napis na górę ekranu po polsku w stylu "
           f"„{who} ogląda …” / „{who} reaguje na …” albo sedno sytuacji (max 7 słów, bez clickbaitu, bez myślników), opis TikTok (1 zdanie + 3–5 hashtagów), "
           "opis Instagram i YouTube Shorts (1–2 zdania jak od fana, co się dzieje i dlaczego warto obejrzeć, + 4–6 hashtagów tematycznych: o czym jest "
           "klip, gra, film, osoba; imię streamera i #shorts dokleję sam), tytuł YouTube Shorts " + STYL(who) + " i jednym zdaniem, co się dzieje (pole dlaczego). "
           "Naturalnie, bez myślników jako przerw w zdaniu.\n"
           + (f"ZASADY wyciągnięte z moich poprzednich ocen i poprawek (stosuj je):\n{zas}\n" if zas else "") +
           'Odpowiedz tylko JSON: {"klipy": [{"start": s, "end": s, "ocena": 1-10, "dlaczego": "...", "hook": "...", "tiktok": "...", "instagram": "...", "yt_title": "..."}]}')
    try:
        K = await _kandydaci(b, dur_min, 45000, ask, 6, 90)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    from montaz import human
    out = [{"start": k["start"], "end": k["end"], **{x: human(str(k.get(x) or "")) for x in ("hook", "tiktok", "instagram", "yt_title")},
            "ocena": k["ocena"], "dlaczego": str(k.get("dlaczego") or "")[:200]} for k in _pick(K, n, 12)]
    return {"klipy": sorted(out, key=lambda k: k["start"])}


@router.post("/api/mont-hook/klipping/wybor")
async def hook_wybor(req: Request):
    """Transkrypcja filmu (angielski) → AI wybiera N fragmentów pod kampanię i pisze hooki, opisy, tytuły."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    from agency import claude, as_json
    import asyncio
    b = await req.json()
    n, camp = max(0, min(15, int(b.get("n") or 0))), b.get("campaign") or {}
    dur_min = float(b.get("dur") or 0) / 60
    if b.get("mode") == "reakcje" and b.get("format") == "yt":
        return await _wybor_yt(b, n, dur_min)
    if b.get("mode") == "reakcje":
        return await _wybor_reakcje(b, n, dur_min)
    how = (f"Pick the {n} best moments" if n else
           f"Decide yourself how many clips this material really supports ({dur_min:.0f} min of content): pick ONLY moments strong enough "
           "to perform on their own, never filler. Roughly 1 clip per 4–8 min of good content, minimum 1, maximum 15. Pick the best moments")
    rules = (camp.get("rules") or "")[:1200]
    prompt = (f"Transcript of a video/stream „{b.get('name', '')}” ({float(b.get('dur') or 0) / 60:.0f} min). Each line: [second] text.\n\n"
              + (b.get("transkrypcja") or "")[:70000] +
              f"\n\n{how} for separate vertical short clips (TikTok, Reels, YouTube Shorts) for a paid clipping campaign"
              + (f" „{camp.get('name')}”. Campaign rules (follow them):\n{rules}\n" if camp else ".\n") +
              "Each clip 15–60 s, understandable on its own, strong hook in the first 2 seconds, ends on a complete thought, "
              "no overlaps, prefer funny/surprising/emotional/controversial moments. For each: on-screen hook text (max 7 words, English), "
              "TikTok caption (1 sentence + 3–5 hashtags), Instagram caption (1–2 sentences + hashtags), YouTube Shorts title (max 70 chars). "
              "Natural wording, no em dashes, no AI clichés.\n"
              'Answer only JSON: {"klipy": [{"start": s, "end": s, "hook": "...", "tiktok": "...", "instagram": "...", "yt_title": "..."}]}')
    try:
        txt = await asyncio.to_thread(lambda: claude(prompt, max_tokens=4000, system=__import__("skills").sk("hook", "caption", "trend", "slop")))
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    out = []
    for k in (as_json(txt) or {}).get("klipy") or []:
        try:
            a, e = float(k["start"]), float(k["end"])
        except Exception:
            continue
        if 6 <= e - a <= 90:
            out.append({"start": a, "end": e, **{x: str(k.get(x) or "").replace("—", ",") for x in ("hook", "tiktok", "instagram", "yt_title")}})
    return {"klipy": out[:n or 15]}


def _dir(jid):
    from social import ROOT
    import os
    p = ROOT / "klipping" / os.path.basename(jid)
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.put("/api/mont-hook/klipping/{jid}/clip")
async def hook_clip(jid: str, req: Request, name: str = "", opis: str = ""):
    """Gotowy klip z Maca → zapis na serwerze + dopisanie do zadania (z hookiem, opisami i tytułem)."""
    import re
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", name)[:80] or f"klip_{int(time.time())}"
    f = _dir(jid) / (nm + ".mp4")
    tmp = f.with_suffix(".part")
    with tmp.open("wb") as fh:
        async for ch in req.stream():
            fh.write(ch)
    if tmp.stat().st_size < 10000:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": "pusty plik"}
    tmp.replace(f)
    try:
        o = json.loads(opis) if opis else {}
    except Exception:
        o = {}
    with _lock:
        d = _rd()
        j = next((j for j in d["jobs"] if j["id"] == jid), None)
        if j:
            j["clips"] = [c for c in j["clips"] if c.get("name") != nm] + [{"name": nm, "has_thumb": (_dir(jid) / (nm + ".jpg")).is_file(), **{k: o.get(k, "") for k in ("hook", "tiktok", "instagram", "yt_title", "yt_desc", "thumb")},
                                                                            "dur": round(_num(o.get("end")) - _num(o.get("start")), 1),
                                                                            "src_start": _num(o.get("start")), "src_end": _num(o.get("end")), "ai_ocena": _num(o.get("ocena")),
                                                                            "at": time.time()}]
            _wr(d)
    if j and j.get("auto"):
        _do_planu(j, nm)
    return {"ok": True}


def _opis_short(c, j):
    """Opis shorta: zdanie o tym, co się dzieje + hashtagi streamera i tematu. Gdy AI dało pusty / sam hashtag – składamy z hooka."""
    import re
    st = next((x for x in _streamers() if x["id"] == j.get("streamer")), {}) or {}
    name = st.get("name") or j.get("who") or ""
    nick = re.sub(r"^.*/", "", (st.get("link") or "").strip("/"))
    plat = "kick" if "kick" in (st.get("link") or "") else "twitch"
    base = (c.get("instagram") or c.get("tiktok") or c.get("yt_desc") or "").strip()
    body = re.sub(r"(\s*#\w+)+\s*$", "", base).strip()
    tags = re.findall(r"#\w+", base)
    if len(body) < 15:
        body = (c.get("hook") or c.get("yt_title") or "").strip().rstrip(".") + "."
    nm_tag = re.sub(r"\W", "", name.lower())
    want = [("#" + nm_tag) if nm_tag else "", f"#{nick.lower()}" if nick and nick.lower() != name.lower() else "",
            f"#{plat}", "#strimywpigulce", "#shorts"]
    for t in want:
        if t and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    txt = body + "\n\n" + " ".join(tags[:10])
    return {"instagram": txt, "yt_desc": txt, "tiktok": c.get("tiktok") or txt}


def _do_planu(j, nm):
    """Gotowy klip → kalendarz kanału Strimy. Błąd zapisuje się przy zadaniu (widać go w zakładce), a nie tylko w logu."""
    import traceback
    try:
        import planer
        c = next(c for c in j["clips"] if c["name"] == nm)
        f = _dir(j["id"]) / (nm + ".mp4")
        th = _dir(j["id"]) / (nm + ".jpg")
        mail = _rd().get("mail")
        if _num(c.get("dur")) <= 90:   # shorty: tylko zdanie + hashtagi, bez linku do streamera i maila do współpracy
            c = {**c, **_opis_short(c, j)}
        else:
            c = {**c, **{k: _uklad(c.get(k), j, mail) for k in ("yt_desc", "instagram", "tiktok") if c.get(k)}}
        planer.add_strimy(j["id"], {**c, "thumb_file": str(th) if th.is_file() else ""}, f, long=_num(c.get("dur")) > 90, streamer=j.get("streamer", ""))
        return None
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:200]
        print("klipping → planer:", err, traceback.format_exc()[-600:], flush=True)
        with _lock:
            d = _rd()
            for jj in d["jobs"]:
                if jj["id"] == j["id"]:
                    jj["plan_err"] = err
            _wr(d)
        return err


@router.post("/api/klipping/job/{jid}/plan")
def api_job_plan(jid: str):
    """Ręcznie: wszystkie klipy z tego streama, których nie ma w kalendarzu → do kalendarza."""
    import planer
    d = _rd()
    j = next((j for j in d["jobs"] if j["id"] == jid), None)
    if not j:
        return JSONResponse({"error": "Nie ma takiego zadania"}, status_code=404)
    have = {x.get("src") for x in planer.plan()}
    added, errs = 0, []
    for c in j.get("clips", []):
        if f"kl:{jid}:{c['name']}" in have:
            continue
        e = _do_planu(j, c["name"])
        if e:
            errs.append(f"{c['name']}: {e}")
        else:
            added += 1
    if not errs:
        with _lock:
            d = _rd()
            for jj in d["jobs"]:
                if jj["id"] == jid:
                    jj.pop("plan_err", None)
            _wr(d)
    return {"ok": not errs, "added": added, "errors": errs[:5]}


@router.put("/api/mont-hook/klipping/{jid}/thumb")
async def hook_thumb(jid: str, req: Request, name: str = ""):
    """Miniatura (JPG) do długiego filmu – Mac wysyła ją przed samym filmem, planer ustawia ją po publikacji na YouTube."""
    import re
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", name)[:80]
    f = _dir(jid) / (nm + ".jpg")
    f.write_bytes(await req.body())
    return {"ok": True}


@router.get("/api/klipping/{jid}/thumb/{name}")
def api_thumb(jid: str, name: str):
    from fastapi.responses import FileResponse
    import os
    f = _dir(jid) / (os.path.basename(name) + ".jpg")
    return FileResponse(f, media_type="image/jpeg") if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)


@router.get("/api/klipping/{jid}/clip/{name}")
def api_clip(jid: str, name: str, dl: int = 0):
    from fastapi.responses import FileResponse
    import os
    f = _dir(jid) / (os.path.basename(name) + ".mp4")
    if not f.is_file():
        return JSONResponse({"error": "nie ma"}, status_code=404)
    if dl:
        return FileResponse(f, media_type="video/mp4", filename=f.name)
    from lite import lite
    return FileResponse(lite(str(f)), media_type="video/mp4")


# ---------- statystyki kanału Strimy w Pigułce + pilnowanie blokad (co 6 h) ----------
def stats_refresh():
    """Wyświetlenia/polubienia z YouTube i Instagrama dla opublikowanych filmów kanału + wykrywanie blokad
    (odrzucony, usunięty, zablokowany w krajach – typowe skutki zgłoszeń praw autorskich). Dobre i słabe wyniki uczą AI."""
    import planer
    from social import _google_access, _ig_token
    P = [x for x in planer.plan() if x.get("group") == "strimy" and x.get("status") == "published"]
    if not P:
        return
    upd = {}
    _, yacc = planer._acc("youtube", "strimy")
    if yacc:
        ids = {(x.get("results") or {}).get("youtube", {}).get("id"): x["id"] for x in P}
        ids.pop(None, None)
        tok = _google_access(yacc)
        L = list(ids)
        for i in range(0, len(L), 50):
            r = httpx.get("https://www.googleapis.com/youtube/v3/videos", timeout=30, headers={"Authorization": "Bearer " + tok},
                          params={"id": ",".join(L[i:i + 50]), "part": "statistics,status,contentDetails"}).json()
            seen = set()
            for v in r.get("items", []):
                seen.add(v["id"])
                st, cd, s = v.get("statistics", {}), v.get("contentDetails", {}), v.get("status", {})
                alert = ""
                if s.get("uploadStatus") == "rejected":
                    alert = f"YouTube odrzucił film ({s.get('rejectionReason', '')})"
                elif (cd.get("regionRestriction") or {}).get("blocked"):
                    alert = "film zablokowany w części krajów – prawdopodobnie zgłoszenie praw autorskich"
                elif s.get("privacyStatus") != "public":
                    alert = f"film nie jest publiczny ({s.get('privacyStatus')})"
                upd.setdefault(ids[v["id"]], {})["yt"] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                                                         "comments": int(st.get("commentCount", 0)), "alert": alert}
            if "items" in r:
                for vid in L[i:i + 50]:
                    if vid not in seen:
                        upd.setdefault(ids[vid], {})["yt"] = {"views": 0, "alert": "film zniknął z YouTube (usunięty?)"}
    key, iacc = planer._acc("instagram", "strimy")
    if iacc:
        tok = _ig_token(key, iacc)
        for x in P:
            mid = (x.get("results") or {}).get("instagram", {}).get("id")
            if not mid:
                continue
            try:
                m = httpx.get(f"https://graph.facebook.com/v21.0/{mid}", timeout=30,
                              params={"fields": "like_count,comments_count", "access_token": tok}).json()
                ins = httpx.get(f"https://graph.facebook.com/v21.0/{mid}/insights", timeout=30,
                                params={"metric": "views", "access_token": tok}).json()
                views = next((v["values"][0]["value"] for v in ins.get("data", []) if v.get("name") == "views"), 0)
                er = m.get("error") or {}
                # „zniknął” tylko gdy Instagram wprost mówi, że takiego posta nie ma (kod 100 / podkod 33).
                # Inne błędy (limit zapytań, token, chwilowa awaria) to nie usunięcie – wcześniej dawały fałszywy alarm.
                gone = er.get("code") == 100 and er.get("error_subcode") == 33
                if er and not gone:
                    continue
                old = ((x.get("stats") or {}).get("ig") or {})
                upd.setdefault(x["id"], {})["ig"] = {"views": int(views or old.get("views", 0) or 0), "likes": int(m.get("like_count", old.get("likes", 0))),
                                                     "comments": int(m.get("comments_count", old.get("comments", 0))),
                                                     "alert": "post zniknął z Instagrama" if gone else ""}
            except Exception:
                pass
    if not upd:
        return
    with planer._lock:
        PP = planer.plan()
        for x in PP:
            if x["id"] in upd:
                x["stats"] = {**(x.get("stats") or {}), **upd[x["id"]], "at": time.time()}
        planer.save(PP)
    # nauka z wyników: po 3 dniach od publikacji najlepsze 30% = „dobry wynik”, najsłabsze 30% = „słaby wynik”
    old = [x for x in PP if x.get("group") == "strimy" and x.get("stats") and not x.get("graded") and time.time() - x["when"] > 3 * 86400]
    allv = sorted(sum((x["stats"].get(p) or {}).get("views", 0) for p in ("yt", "ig")) for x in PP if x.get("stats"))
    if len(allv) >= 6 and old:
        hi, lo = allv[int(len(allv) * 0.7)], allv[int(len(allv) * 0.3)]
        for x in old:
            v = sum((x["stats"].get(p) or {}).get("views", 0) for p in ("yt", "ig"))
            if v >= hi or v <= lo:
                feedback(x.get("streamer"), "dobry wynik (dużo wyświetleń)" if v >= hi else "słaby wynik (mało wyświetleń)",
                         what=f"{x.get('yt_title') or x.get('label')} ({'film' if x.get('long') else 'short'}) – {v} wyświetleń")
        with planer._lock:
            PP = planer.plan()
            for x in PP:
                if x["id"] in {o["id"] for o in old}:
                    x["graded"] = True
            planer.save(PP)
    bad = [(x, (x.get("stats") or {})) for x in PP if x.get("group") == "strimy"
           and any(((x.get("stats") or {}).get(p) or {}).get("alert") for p in ("yt", "ig"))]
    seen_a = set(_rd().get("alerted", []))
    new = [x for x, _ in bad if x["id"] not in seen_a]
    if new:
        try:
            from extra import push_all
            a = new[0]["stats"]
            push_all("⚠️ Strimy w Pigułce: problem z filmem", f"{new[0].get('label', '')[:60]}: " +
                     ((a.get("yt") or {}).get("alert") or (a.get("ig") or {}).get("alert") or ""), "/#klipping")
        except Exception:
            pass
        with _lock:
            d = _rd()
            d["alerted"] = (d.get("alerted", []) + [x["id"] for x in new])[-300:]
            _wr(d)


def _stats_loop():
    time.sleep(120)
    while True:
        try:
            stats_refresh()
        except Exception as e:
            print("strimy statystyki:", e, flush=True)
        time.sleep(6 * 3600)


threading.Thread(target=_stats_loop, daemon=True).start()


# ---------- kompilacja tygodnia: „TOP 10 momentów X w tym tygodniu” (niedziela, osobno dla każdego streamera) ----------
def kompilacja(sid, force=False):
    """Najlepsze shorty streamera z ostatnich 7 dni (wyświetlenia YT+IG, a bez statystyk ocena AI) → zadanie dla Maca:
    poziomy film 16:9 z numerami #10 → #1, planszami i intro. To nowa treść kanału, a nie surowy fragment streama."""
    from agency import claude, as_json
    import planer
    d = _rd()
    st = next((x for x in _streamers(d) if x["id"] == sid), None)
    if not st:
        return {"ok": False, "error": "nie ma takiego streamera"}
    wk = time.strftime("%G-%V")
    if not force and (d.get("komp_done") or {}).get(sid) == wk:
        return {"ok": False, "error": "kompilacja z tego tygodnia już jest"}
    stats = {x.get("src"): x.get("stats") or {} for x in planer.plan() if x.get("group") == "strimy"}
    C = []
    for j in d["jobs"]:
        if j.get("streamer") != sid or j.get("mode") == "kompilacja" or not str(j.get("url", "")).startswith("http"):
            continue
        for c in j.get("clips", []):
            if c.get("dur", 999) > 90 or not c.get("src_end") or time.time() - c.get("at", 0) > 7 * 86400 or c.get("ocena") == "slaby":
                continue
            s_ = stats.get(f"kl:{j['id']}:{c['name']}", {})
            views = sum((s_.get(p) or {}).get("views", 0) for p in ("yt", "ig"))
            C.append({"url": j["url"], "start": c["src_start"], "end": c["src_end"], "hook": c.get("hook") or c.get("yt_title") or "",
                      "score": views + 1000 * _num(c.get("ai_ocena")) * (0 if views else 1) + (5000 if c.get("ocena") == "dobry" else 0)})
    if len(C) < 5:
        return {"ok": False, "error": f"za mało shortów z tego tygodnia ({len(C)}, potrzeba min. 5)"}
    C = sorted(C, key=lambda c: -c["score"])[:10]
    who = st["name"]
    lst = "\n".join(f"#{i + 1}: {c['hook']}" for i, c in enumerate(C))
    r = claude(f"Robię kompilację na YouTube „TOP {len(C)} momentów {who} z tego tygodnia”. Momenty od najlepszego:\n{lst}\n\n"
               f"Napisz: tytuł YouTube po polsku {STYL(who)} (zaznacz, że to najlepsze momenty tygodnia), opis YouTube (2–3 zdania + 3–5 hashtagów), "
               "napis na miniaturę (2–4 słowa, WERSALIKI), krótkie intro na planszę (max 10 słów) i dla każdego momentu napis na planszę przed nim "
               "(max 6 słów, zapowiedź bez spoilera puenty).\n"
               'Odpowiedz tylko JSON: {"yt_title": "...", "yt_desc": "...", "thumb": "...", "intro": "...", "plansze": ["...", ...]}',
               max_tokens=1500, system=__import__("skills").sk("hook", "caption", "trend", "slop"))
    o = as_json(r) or {}
    from montaz import human
    pl = o.get("plansze") or []
    items = [{**c, "plansza": human(str(pl[i] if i < len(pl) else c["hook"]))[:60], "nr": i + 1} for i, c in enumerate(C)]
    items.reverse()   # od #10 do #1
    j = {"id": secrets.token_hex(4), "mode": "kompilacja", "url": f"kompilacja:{sid}:{wk}", "streamer": sid, "who": who,
         "link": st.get("link", ""), "auto": True, "format": "yt", "lang": "pl", "cam": st.get("cam", "auto"), "n": 0,
         "komp": {"items": items, "yt_title": human(str(o.get("yt_title") or f"TOP {len(C)} momentów {who} w tym tygodniu")),
                  "yt_desc": human(str(o.get("yt_desc") or "")), "thumb": human(str(o.get("thumb") or "TOP TYGODNIA")),
                  "intro": human(str(o.get("intro") or f"Najlepsze momenty {who} z tego tygodnia"))},
         "title": f"Kompilacja tygodnia: {who} ({wk})", "state": "queued", "step": "Czeka na Maca…", "created": time.time(), "clips": []}
    with _lock:
        d = _rd()
        d["jobs"] = (d["jobs"] + [j])[-60:]
        d.setdefault("komp_done", {})[sid] = wk
        _wr(d)
    return {"ok": True, "job": j["id"], "n": len(items)}


@router.post("/api/klipping/kompilacja/{sid}")
def api_kompilacja(sid: str):
    try:
        return kompilacja(sid, force=True)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=500)


def _komp_loop():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    time.sleep(300)
    while True:
        try:
            now = datetime.now(ZoneInfo("Europe/Warsaw"))
            if now.weekday() == 6 and now.hour >= 10:   # niedziela od 10:00
                for st in _streamers():
                    r = kompilacja(st["id"])
                    if r.get("ok"):
                        print("kompilacja tygodnia:", st["id"], r, flush=True)
        except Exception as e:
            print("kompilacja:", e, flush=True)
        time.sleep(3600)


threading.Thread(target=_komp_loop, daemon=True).start()
