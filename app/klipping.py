"""Klipping: kampanie Content Rewards (Whop) → najlepsze na powiadomienie → link do filmu → Mac robi pionowe klipy po angielsku.
Wyszukiwanie przez Apify (parsebird/whop-content-rewards-scraper, ok. 0,14 $ za 100 kampanii), raz dziennie albo na żądanie."""
import json, math, secrets, threading, time

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
            if time.time() - _rd().get("scanned", 0) > 24 * 3600:
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
        j = {"id": secrets.token_hex(4), "url": url, "n": max(0, min(15, int(b.get("n") or 0))), "campaign": None if mode == "reakcje" else camp,
             "mode": mode, "lang": "pl" if mode == "reakcje" else "en", "cam": (b.get("cam") or "auto")[:12], "who": (b.get("who") or "")[:40],
             "format": b.get("format") if b.get("format") in ("yt", "short", "oba") else "short",
             "auto": bool(b.get("auto")) and mode == "reakcje",   # sama publikacja na kanale Strimy w Pigułce
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


async def _wybor_yt(b, n, dur_min):
    """Poziome filmy na YouTube: CAŁE segmenty (np. od początku do końca oglądania jednego filmu / ogłoszenia), 3–25 min."""
    from agency import claude, as_json
    import asyncio
    who = b.get("who") or "streamer"
    how = (f"Wybierz {n} segmentów" if n else "Sam oceń, ile pełnych segmentów warto wyciąć (od 1 do 8, tylko naprawdę ciekawe)")
    prompt = (f"Transkrypcja streama ({dur_min:.0f} min), prowadzący: {who}. Każda linia: [sekunda] tekst.\n\n" + (b.get("transkrypcja") or "")[:90000] +
              f"\n\n{how} na osobne POZIOME filmy na YouTube. Segment to zamknięta całość: {who} od początku do końca ogląda jeden film / klip "
              "i na niego reaguje, gra jedną rundę/mecz, ogłasza coś ważnego, opowiada jedną historię albo prowadzi jedną dyskusję. "
              "Start: chwila przed rozpoczęciem tematu (żeby widz wiedział, o co chodzi), koniec: gdy temat się zamyka. Długość 3–60 min "
              "(cały mecz / cała rozgrywka może trwać do 60 min, oglądanie i rozmowy zwykle krócej). "
              "Bez nakładania się, pomiń przerwy, czytanie donejtów i nudne fragmenty. Do każdego: tytuł YouTube po polsku "
              f"(max 70 znaków, ciekawość, w stylu „{who} ogląda …” / „{who} reaguje na …”, bez clickbaitu i bez myślników), "
              "opis YouTube (2–4 zdania + 3–5 hashtagów), krótki napis na miniaturę (2–4 słowa, WERSALIKI) i hook na pierwsze 4 s filmu (max 7 słów).\n"
              'Odpowiedz tylko JSON: {"klipy": [{"start": s, "end": s, "yt_title": "...", "yt_desc": "...", "thumb": "...", "hook": "..."}]}')
    try:
        txt = await asyncio.to_thread(lambda: claude(prompt, max_tokens=4000, system=__import__("skills").sk("hook", "caption", "trend", "slop")))
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    from montaz import human
    out = []
    for k in (as_json(txt) or {}).get("klipy") or []:
        try:
            a, e = float(k["start"]), float(k["end"])
        except Exception:
            continue
        if 90 <= e - a <= 62 * 60:
            o = {x: human(str(k.get(x) or "")) for x in ("yt_title", "yt_desc", "thumb", "hook")}
            out.append({"start": a, "end": e, **o, "tiktok": "", "instagram": o["yt_desc"]})
    return {"klipy": out[:n or 8]}


async def _wybor_reakcje(b, n, dur_min):
    """Polskie streamy (np. Bungee): momenty, w których streamer ogląda film / gra i reaguje – na klipy „X ogląda…”."""
    from agency import claude, as_json
    import asyncio
    who = b.get("who") or "streamer"
    how = (f"Wybierz {n} najlepszych momentów" if n else
           f"Sam oceń, ile dobrych klipów da ten materiał ({dur_min:.0f} min): tylko momenty, które obronią się same, bez zapychaczy, "
           "mniej więcej 1 klip na 5–10 min dobrego materiału, od 1 do 15. Wybierz najlepsze momenty")
    prompt = (f"Transkrypcja streama ({dur_min:.0f} min), prowadzący: {who}. Każda linia: [sekunda] tekst.\n\n" + (b.get("transkrypcja") or "")[:70000] +
              f"\n\n{how}, w których {who} OGLĄDA coś (film z YouTube, klip, tiktoka) albo GRA i na to reaguje: śmiech, zaskoczenie, ostry komentarz, "
              "kłótnia, puenta. Klip 20–60 s, zrozumiały bez reszty streama, mocny początek (reakcja albo zapowiedź w pierwszych 2 s), "
              "koniec na puencie. Bez nakładania się. Do każdego: krótki napis na górę ekranu po polsku w stylu "
              f"„{who} ogląda …” / „{who} reaguje na …” (max 7 słów, bez clickbaitu, bez myślników), opis TikTok (1 zdanie + 3–5 hashtagów), "
              "opis Instagram (1–2 zdania + hashtagi), tytuł YouTube Shorts (max 70 znaków). Naturalnie, bez myślników jako przerw w zdaniu.\n"
              'Odpowiedz tylko JSON: {"klipy": [{"start": s, "end": s, "hook": "...", "tiktok": "...", "instagram": "...", "yt_title": "..."}]}')
    try:
        txt = await asyncio.to_thread(lambda: claude(prompt, max_tokens=4000, system=__import__("skills").sk("hook", "caption", "trend", "slop")))
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=502)
    from montaz import human
    out = []
    for k in (as_json(txt) or {}).get("klipy") or []:
        try:
            a, e = float(k["start"]), float(k["end"])
        except Exception:
            continue
        if 6 <= e - a <= 90:
            out.append({"start": a, "end": e, **{x: human(str(k.get(x) or "")) for x in ("hook", "tiktok", "instagram", "yt_title")}})
    return {"klipy": out[:n or 15]}


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
            j["clips"] = [c for c in j["clips"] if c.get("name") != nm] + [{"name": nm, **{k: o.get(k, "") for k in ("hook", "tiktok", "instagram", "yt_title", "yt_desc", "thumb")},
                                                                            "dur": round(_num(o.get("end")) - _num(o.get("start")), 1)}]
            _wr(d)
    if j and j.get("auto"):
        try:
            import planer
            c = next(c for c in j["clips"] if c["name"] == nm)
            planer.add_strimy(jid, c, f, long=c["dur"] > 90)
        except Exception as e:
            print("klipping → planer:", e, flush=True)
    return {"ok": True}


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
