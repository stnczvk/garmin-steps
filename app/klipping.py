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
]


def _streamers(d=None):
    d = d or _rd()
    have = {x["id"]: x for x in d.get("streamers", [])}
    return [have.get(x["id"], x) for x in STREAMERS] + [x for x in d.get("streamers", []) if x["id"] not in {y["id"] for y in STREAMERS}]


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
         "title_hint": (b.get("title_hint") or "")[:300]}
    with _lock:
        d = _rd()
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
        _wr(d)
    return j


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
    trans = _energy_lines(b.get("transkrypcja") or "", b.get("energia"))
    P = _parts(trans, size)
    sysm = __import__("skills").sk("hook", "caption", "trend", "slop")

    def one(i, part):
        return claude(f"Fragment {i + 1} z {len(P)} transkrypcji streama ({dur_min:.0f} min całość). Każda linia: [sekunda] tekst.\n\n"
                      + part + "\n\n" + ask, max_tokens=4000, system=sysm)
    R = await asyncio.gather(*[asyncio.to_thread(one, i, p) for i, p in enumerate(P)], return_exceptions=True)
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
                   "Nie ucinaj w środku, ale nie dokładaj innego tematu ani reklam/sponsorów. Min 5 min, max 60 min.\n"
                   'Odpowiedz tylko JSON: {"start": s, "end": s}', max_tokens=300)
        j = as_json(r) or {}
        a, e = float(j.get("start", k["start"])), float(j.get("end", k["end"]))
        if 300 <= e - a <= 62 * 60 and a < k["end"] and e > k["start"]:
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


async def _wybor_yt(b, n, dur_min):
    """Poziome filmy na YouTube: CAŁE segmenty (np. od początku do końca oglądania jednego filmu / ogłoszenia), 3–60 min."""
    who = b.get("who") or "streamer"
    hint = (b.get("title_hint") or "").strip()
    zas = _zasady(b.get("streamer"))
    ask = (f"Znajdź w TYM fragmencie najlepsze segmenty (0–3, zero jeśli nic nie jest naprawdę ciekawe) na osobne POZIOME filmy na YouTube. "
           f"Segment to zamknięta całość: {who} od początku do końca ogląda jeden film / klip i na niego reaguje, gra jedną rundę/mecz, "
           "(gdy ogląda film: segment obejmuje CAŁE oglądanie od włączenia do końca filmu, nigdy pojedynczy kawałek; jeśli koniec jest poza tym "
           "fragmentem, daj end jako ostatnią sekundę fragmentu, później go dociągnę), "
           "ogłasza coś ważnego, opowiada jedną historię albo prowadzi jedną dyskusję. W środku musi być kilka mocnych momentów, a nie spokojne gadanie. "
           + DOBRE + " Start: chwila przed rozpoczęciem tematu (żeby widz wiedział, o co chodzi), koniec: gdy temat się zamyka. Długość 5–60 min (krócej niż 5 min nie wybieraj, rozszerz segment o naturalny wstęp i komentarz po). "
           "Oceń każdy segment szczerze 1–10 (10 = ludzie obejrzą do końca i udostępnią, 5 = przeciętny fragment streama). "
           "Do każdego: tytuł YouTube po polsku " + STYL(who) + " "
           + (f"Styl tytułów dla tego kanału: {hint}. " if hint else "") +
           (f"ZASADY wyciągnięte z moich poprzednich ocen i poprawek (stosuj je):\n{zas}\n" if zas else "") +
           "opis YouTube (2–3 krótkie zdania + 3–5 hashtagów, styl opisu jak wyżej), krótki napis na miniaturę (2–4 słowa, WERSALIKI), hook na pierwsze 4 s filmu (max 7 słów) "
           "i jednym zdaniem, co konkretnie się dzieje (pole dlaczego).\n"
           'Odpowiedz tylko JSON: {"klipy": [{"start": s, "end": s, "ocena": 1-10, "dlaczego": "...", "yt_title": "...", "yt_desc": "...", "thumb": "...", "hook": "..."}]}')
    try:
        K = await _kandydaci(b, dur_min, 60000, ask, 240, 62 * 60)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    from montaz import human
    P = _pick(K, n, 6)
    try:
        P = await _granice(b, who, P)
    except Exception as e:
        print("klipping granice:", e, flush=True)
    out = []
    for k in P:
        o = {x: human(str(k.get(x) or "")) for x in ("yt_title", "yt_desc", "thumb", "hook")}
        out.append({"start": k["start"], "end": k["end"], **o, "tiktok": "", "instagram": o["yt_desc"], "ocena": k["ocena"], "dlaczego": str(k.get("dlaczego") or "")[:200]})
    return {"klipy": sorted(out, key=lambda k: k["start"])}


async def _wybor_reakcje(b, n, dur_min):
    """Polskie streamy: najmocniejsze momenty na pionowe shorty – z całego streama, z oceną, z podpowiedzią głośności."""
    who = b.get("who") or "streamer"
    zas = _zasady(b.get("streamer"))
    ask = (f"Znajdź w TYM fragmencie najlepsze momenty (0–6, zero jeśli nic nie jest naprawdę mocne) na pionowe shorty, w których {who} "
           "ogląda coś, gra albo rozmawia i dzieje się coś mocnego. " + DOBRE +
           " Klip 20–60 s, zrozumiały bez reszty streama, mocny początek (reakcja albo zapowiedź w pierwszych 2 s), koniec na puencie. "
           "Oceń każdy szczerze 1–10 (10 = viral, 7 = dobry short, 5 = przeciętny fragment streama). "
           "Do każdego: krótki napis na górę ekranu po polsku w stylu "
           f"„{who} ogląda …” / „{who} reaguje na …” albo sedno sytuacji (max 7 słów, bez clickbaitu, bez myślników), opis TikTok (1 zdanie + 3–5 hashtagów), "
           "opis Instagram (1–2 zdania + hashtagi), tytuł YouTube Shorts " + STYL(who) + " i jednym zdaniem, co się dzieje (pole dlaczego). "
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
                                                                            "dur": round(_num(o.get("end")) - _num(o.get("start")), 1)}]
            _wr(d)
    if j and j.get("auto"):
        try:
            import planer
            c = next(c for c in j["clips"] if c["name"] == nm)
            th = _dir(jid) / (nm + ".jpg")
            mail = _rd().get("mail")
            c = {**c, **{k: _uklad(c.get(k), j, mail) for k in ("yt_desc", "instagram", "tiktok") if c.get(k)}}
            planer.add_strimy(jid, {**c, "thumb_file": str(th) if th.is_file() else ""}, f, long=c["dur"] > 90, streamer=j.get("streamer", ""))
        except Exception as e:
            print("klipping → planer:", e, flush=True)
    return {"ok": True}


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
                upd.setdefault(x["id"], {})["ig"] = {"views": int(views or 0), "likes": int(m.get("like_count", 0)),
                                                     "comments": int(m.get("comments_count", 0)), "alert": "" if "id" in m else "post zniknął z Instagrama"}
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
