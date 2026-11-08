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
    if notify and new:
        try:
            from extra import push_all
            top = new[0]
            push_all("✂️ Nowe kampanie klipowe", f"{len(new)} nowych. Najlepsza: {top['name']} – ${top['rate']:.2f}/1000 wyśw., "
                     f"zostało ${top['left']:.0f}", "/#klipping")
        except Exception:
            pass
    return C


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
    return {"campaigns": d["campaigns"], "scanned": d["scanned"], "jobs": list(reversed(d["jobs"][-30:]))}


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
        camp = next((c for c in d["campaigns"] if c["id"] == str(b.get("campaign") or "")), None)
        j = {"id": secrets.token_hex(4), "url": url, "n": max(0, min(15, int(b.get("n") or 0))), "campaign": camp,   # 0 = AI decyduje
             "state": "queued", "step": "Czeka na Maca…", "created": time.time(), "clips": []}
        d["jobs"] = (d["jobs"] + [j])[-60:]
        _wr(d)
    return j


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
            j["clips"] = [c for c in j["clips"] if c.get("name") != nm] + [{"name": nm, **{k: o.get(k, "") for k in ("hook", "tiktok", "instagram", "yt_title")},
                                                                            "dur": round(_num(o.get("end")) - _num(o.get("start")), 1)}]
            _wr(d)
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
