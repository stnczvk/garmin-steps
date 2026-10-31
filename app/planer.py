"""Planer postów (konta prywatne): Montażownia → szkic posta → Twoje zatwierdzenie w centrum → publikacja o ustalonej godzinie.
Instagram: Reels (film) i karuzela zdjęć (Instagram API z logowaniem przez Instagram, uprawnienie instagram_business_content_publish).
YouTube: Shorts (upload przez YouTube Data API, uprawnienie youtube.force-ssl, które już mamy).
Pliki wystawiamy platformom pod jednorazowymi linkami /pub/<token>/… (ważne 24 h)."""
import asyncio, json, os, re, secrets, threading, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from social import _read, _write, ROOT as SOCIAL_ROOT

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
IG = "https://graph.instagram.com"
PLAN, CFG = "posts_plan.json", "posts_cfg.json"
DEF = {"hours": {"jedzenie": "18:00", "karuzela": "09:00", "vlog": "20:00", "cheat": "19:00", "przepis": "17:00"},
       "platforms": {"film": ["instagram", "youtube"], "karuzela": ["instagram"]}, "auto": True}
_lock = threading.RLock()


def cfg():
    c = _read(CFG, {})
    return {**DEF, **c, "hours": {**DEF["hours"], **(c.get("hours") or {})}, "platforms": {**DEF["platforms"], **(c.get("platforms") or {})}}


def plan():
    return _read(PLAN, [])


def save(P):
    _write(PLAN, P[-150:])


def _when(kind, after=None):
    hh, mm = map(int, cfg()["hours"].get(kind, "18:00").split(":"))
    base = after or datetime.now(TZ)
    t = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if t <= base + timedelta(minutes=20):
        t += timedelta(days=1)
    return t.timestamp()


def _public():
    d = os.environ.get("SOCIAL_DOMAIN", "") or os.environ.get("PUBLIC_URL", "").split("//")[-1]
    return "https://" + d.strip("/") if d else ""


# ---------- opisy (te same co w Montażowni: osobny do filmu, osobny do karuzeli) ----------
def _opis(kind, jid):
    from montaz import mont_opis

    class R:
        async def json(self):
            return {"kind": kind, "id": jid}
    try:
        r = asyncio.run(mont_opis(R()))
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}
    return r


# ---------- szkice z gotowych filmów ----------
def _add(item):
    with _lock:
        P = plan()
        if any(x["src"] == item["src"] for x in P):
            return None
        item.update(id=secrets.token_hex(4), status="draft", created=time.time())
        P.append(item)
        save(P)
    try:
        from extra import push_all
        push_all("📤 Post do zatwierdzenia", f"{item['label']} – publikacja {datetime.fromtimestamp(item['when'], TZ):%d.%m %H:%M}", "/#home")
    except Exception:
        pass
    return item


def scan():
    """Dodaje szkice dla nowych gotowych filmów (jedzenie z serwera, vlogi/cheat z Maca) i karuzel."""
    if not cfg().get("auto"):
        return
    from montaz import JOBS, VFILM, _status, _film, _rd
    P = plan()
    have = {x["src"] for x in P}
    # daily jedzenie (film + karuzela)
    if JOBS.is_dir():
        for d in sorted(JOBS.iterdir(), key=lambda p: p.stat().st_mtime)[-6:]:
            try:
                st = _status(d)
                f = _film(d, st)
            except Exception:
                continue
            if not f or time.time() - f.stat().st_mtime > 3 * 86400:
                continue
            jid = d.name
            if f"food:{jid}" not in have and not _dropped(f"food:{jid}", f):
                o = _opis("jedzenie", jid)
                w = _when("jedzenie")
                _add({"src": f"food:{jid}", "kind": "film", "series": "jedzenie", "label": "Daily jedzenie " + " ".join((st.get("title") or [])[:1]),
                      "file": str(f), "when": w, "platforms": cfg()["platforms"]["film"],
                      "caption": (o.get("instagram") or "") if o.get("ok") else "", "tiktok": o.get("tiktok", ""), "yt_title": ""})
            k = d / "gotowe" / "karuzela"
            if k.is_dir() and any(k.glob("*.jpg")) and f"karu:{jid}" not in have and not _dropped(f"karu:{jid}", max(k.glob("*.jpg"), key=lambda q: q.stat().st_mtime)):
                o = _opis("karuzela", jid)
                film = next((x for x in plan() if x["src"] == f"food:{jid}"), None)
                w = _when("karuzela", datetime.fromtimestamp(film["when"], TZ) if film else None)
                _add({"src": f"karu:{jid}", "kind": "karuzela", "series": "jedzenie", "label": "Karuzela jedzenia " + " ".join((st.get("title") or [])[:1]),
                      "files": [str(x) for x in sorted(k.glob("*.jpg"))[:10]], "when": w, "platforms": cfg()["platforms"]["karuzela"],
                      "caption": (o.get("instagram") or "") if o.get("ok") else ""})
    # vlogi / cheat / przepisy – kopia 1080p z Maca
    M = _rd("mont_vlog_films.json", {})
    V = _rd("mont_vlog.json", {}).get("jobs", [])
    for nm, m in M.items():
        f = VFILM / (nm + ".pub.mp4")
        if not f.is_file() or f"vf:{nm}" in have or time.time() - f.stat().st_mtime > 3 * 86400 or _dropped(f"vf:{nm}", f):
            continue
        kind = m.get("kind") or "vlog"
        ep = re.search(r"#\s*(\d+)", nm)
        job = next((j for j in reversed(V) if (j.get("kind") or "vlog") == kind and ep and str(j.get("ep")) == ep.group(1) and j.get("state") == "done"), None)
        o = _opis(kind, job["id"]) if job else {"ok": False}
        _add({"src": f"vf:{nm}", "kind": "film", "series": kind, "label": nm, "file": str(f), "when": _when(kind),
              "platforms": cfg()["platforms"]["film"], "caption": (o.get("instagram") or "") if o.get("ok") else "", "tiktok": o.get("tiktok", ""), "yt_title": ""})


DROP = "posts_dropped.json"


def drop(srcs=(), series=None, ep=None, why="nowy montaż"):
    """Montaż puszczony od nowa → stare, nieopublikowane posty z tego filmu znikają z planu.
    Stary plik nie wróci przy skanie (zapamiętany czas usunięcia; wróci dopiero nowszy plik)."""
    n = 0
    with _lock:
        P, D = plan(), _read(DROP, {})
        keep = []
        for x in P:
            hit = x["src"] in srcs or (series and ep is not None and x.get("series") == series
                                         and re.search(r"#\s*" + re.escape(str(ep)) + r"\b", x.get("label", "")))
            if hit and x.get("status") not in ("published", "publishing"):
                D[x["src"]] = time.time()
                n += 1
                continue
            keep.append(x)
        for s_ in srcs:
            D.setdefault(s_, time.time())
            D[s_] = time.time()
        if n:
            save(keep)
        _write(DROP, {k: v for k, v in D.items() if time.time() - v < 14 * 86400})
    return n


def _dropped(src, f):
    t = _read(DROP, {}).get(src)
    try:
        return bool(t) and Path(f).stat().st_mtime <= t
    except Exception:
        return False


def on_film(name, kind):
    threading.Thread(target=scan, daemon=True).start()


# ---------- publikacja ----------
def _acc(platform):
    from social import _accounts
    for k, a in _accounts().items():
        if a.get("platform") == platform and a.get("group") == "prywatne" and not a.get("error"):
            return k, a
    return None, None


def _pub_url(it, path):
    tok = secrets.token_urlsafe(16)
    L = _read("posts_pub.json", {})
    L = {k: v for k, v in L.items() if v["exp"] > time.time()}
    L[tok] = {"path": str(path), "exp": time.time() + 86400}
    _write("posts_pub.json", L)
    return f"{_public()}/pub/{tok}/{os.path.basename(str(path))}"


@router.get("/pub/{tok}/{name}")
def pub_file(tok: str, name: str):
    v = _read("posts_pub.json", {}).get(tok)
    if not v or v["exp"] < time.time() or os.path.basename(v["path"]) != name or not os.path.isfile(v["path"]):
        return JSONResponse({"error": "nie ma"}, status_code=404)
    mt = "image/jpeg" if name.lower().endswith(".jpg") else "video/mp4"
    return FileResponse(v["path"], media_type=mt)


def _ig_wait(cid, tok, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        r = httpx.get(f"{IG}/{cid}", params={"fields": "status_code,status", "access_token": tok}, timeout=30).json()
        sc = r.get("status_code")
        if sc == "FINISHED":
            return
        if sc in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram odrzucił plik: {r.get('status', sc)}")
        time.sleep(10)
    raise RuntimeError("Instagram za długo przetwarza plik")


def _ig_err(r):
    e = r.get("error") or {}
    m = e.get("message", "")
    if "permission" in m.lower() or e.get("code") in (10, 200):
        return f"Instagram: brak uprawnienia do publikacji – połącz konto prywatne IG ponownie (Statystyki → Konta). Szczegóły: {m[:160]} (kod {e.get('code')})"
    return "Instagram: " + (m or json.dumps(r)[:150])


def pub_instagram(it):
    from social import _ig_token
    key, acc = _acc("instagram")
    if not acc:
        raise RuntimeError("brak połączonego konta Instagram w grupie Prywatne")
    tok, uid = _ig_token(key, acc), key.split(":", 1)[1]
    cap = (it.get("caption") or "")[:2200]
    if it["kind"] == "film":
        r = httpx.post(f"{IG}/{uid}/media", timeout=60, data={"media_type": "REELS", "video_url": _pub_url(it, it["file"]),
                                                               "caption": cap, "share_to_feed": "true", "access_token": tok}).json()
        if "id" not in r:
            raise RuntimeError(_ig_err(r))
        cid = r["id"]
        _ig_wait(cid, tok)
    else:
        kids = []
        for f in it["files"][:10]:
            r = httpx.post(f"{IG}/{uid}/media", timeout=60, data={"image_url": _pub_url(it, f), "is_carousel_item": "true", "access_token": tok}).json()
            if "id" not in r:
                raise RuntimeError(_ig_err(r))
            kids.append(r["id"])
        for k in kids:
            _ig_wait(k, tok, 180)
        r = httpx.post(f"{IG}/{uid}/media", timeout=60, data={"media_type": "CAROUSEL", "children": ",".join(kids), "caption": cap, "access_token": tok}).json()
        if "id" not in r:
            raise RuntimeError(_ig_err(r))
        cid = r["id"]
        _ig_wait(cid, tok, 180)
    r = httpx.post(f"{IG}/{uid}/media_publish", timeout=60, data={"creation_id": cid, "access_token": tok}).json()
    if "id" not in r:
        raise RuntimeError(_ig_err(r))
    return r["id"]


def pub_youtube(it):
    from social import _google_access
    _, acc = _acc("youtube")
    if not acc:
        raise RuntimeError("brak połączonego kanału YouTube w grupie Prywatne")
    tok = _google_access(acc)
    cap = it.get("caption") or ""
    title = (it.get("yt_title") or re.split(r"[\n.!?]", cap.strip())[0] or it["label"]).strip()[:90]
    tags = re.findall(r"#(\w+)", cap)[:10]
    desc = cap + ("" if "#shorts" in cap.lower() else "\n\n#shorts")
    meta = {"snippet": {"title": title, "description": desc[:4900], "tags": tags, "categoryId": "17", "defaultLanguage": "pl"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    size = os.path.getsize(it["file"])
    r = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=60,
                   headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(size)}, json=meta)
    if r.status_code >= 300 or "location" not in r.headers:
        raise RuntimeError("YouTube: " + r.text[:200])
    with open(it["file"], "rb") as f:
        u = httpx.put(r.headers["location"], content=f.read(), timeout=1800, headers={"Content-Type": "video/mp4"})
    if u.status_code >= 300:
        raise RuntimeError("YouTube (wysyłka): " + u.text[:200])
    return u.json().get("id")


def publish(pid):
    with _lock:
        P = plan()
        it = next((x for x in P if x["id"] == pid), None)
        if not it or it["status"] not in ("approved", "error"):
            return
        it["status"], it["step"] = "publishing", "publikuję…"
        save(P)
    res, errs = dict(it.get("results") or {}), []
    for p in it["platforms"]:
        if res.get(p, {}).get("ok"):
            continue
        try:
            mid = pub_instagram(it) if p == "instagram" else pub_youtube(it) if p == "youtube" else None
            res[p] = {"ok": True, "id": mid, "at": time.time()}
        except Exception as e:
            res[p] = {"ok": False, "error": str(e)[:250]}
            errs.append(f"{p}: {str(e)[:150]}")
    with _lock:
        P = plan()
        it = next((x for x in P if x["id"] == pid), None)
        if it:
            it["results"] = res
            it["status"] = "error" if errs else "published"
            it["step"] = "; ".join(errs) if errs else "opublikowane"
            it["done_at"] = time.time()
            save(P)
    try:
        from agency import log
        from extra import push_all
        log("planer", (f"Nie opublikował „{it['label']}”: " + "; ".join(errs)) if errs else f"Opublikował „{it['label']}” ({', '.join(it['platforms'])})",
            "error" if errs else "ok")
        push_all("❌ Publikacja nie wyszła" if errs else "✅ Opublikowane", it["label"], "/#home")
    except Exception:
        pass


def _loop():
    time.sleep(45)
    while True:
        try:
            scan()
        except Exception as e:
            print("planer scan", e, flush=True)
        try:
            now = time.time()
            for it in plan():
                if it["status"] == "approved" and it["when"] <= now:
                    publish(it["id"])
        except Exception as e:
            print("planer pub", e, flush=True)
        time.sleep(60)


threading.Thread(target=_loop, daemon=True).start()


# ---------- API dla apki ----------
@router.get("/api/planer")
def api_plan():
    P = sorted(plan(), key=lambda x: x["when"])
    d0 = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()   # dzisiejsza północ (PL)
    P = [x for x in P if x["status"] != "skipped"
         and not (x["status"] in ("published", "error") and (x.get("done_at") or x.get("when") or 0) < d0)]
    return {"items": P[-40:], "d0": d0, "cfg": cfg(),
            "public": bool(_public())}


@router.post("/api/planer/{pid}/{action}")
async def api_act(pid: str, action: str, req: Request):
    b = await req.json() if action in ("save", "approve") else {}
    with _lock:
        P = plan()
        it = next((x for x in P if x["id"] == pid), None)
        if not it:
            return {"ok": False, "error": "nie ma"}
        if action in ("save", "approve"):
            if "caption" in b:
                it["caption"] = str(b["caption"])[:2200]
            if b.get("yt_title") is not None:
                it["yt_title"] = str(b["yt_title"])[:100]
            if b.get("when"):
                try:
                    it["when"] = datetime.fromisoformat(b["when"]).replace(tzinfo=TZ).timestamp()
                except ValueError:
                    return {"ok": False, "error": "zła data"}
            if isinstance(b.get("platforms"), list):
                it["platforms"] = [p for p in b["platforms"] if p in ("instagram", "youtube")]
        if action == "approve":
            if not it["platforms"]:
                return {"ok": False, "error": "wybierz platformę"}
            it["status"] = "approved"
        elif action == "unapprove" and it["status"] == "approved":
            it["status"] = "draft"
        elif action == "skip":
            it["status"] = "skipped"
        elif action == "retry" and it["status"] == "error":
            it["status"] = "approved"
            it["when"] = min(it["when"], time.time())
        save(P)
    if action == "now":
        with _lock:
            P = plan()
            it = next((x for x in P if x["id"] == pid), None)
            if it and it["status"] in ("draft", "approved", "error"):
                it["status"], it["when"] = "approved", time.time()
                save(P)
        threading.Thread(target=publish, args=(pid,), daemon=True).start()
    return {"ok": True}


@router.post("/api/planer-cfg")
async def api_cfg(req: Request):
    b = await req.json()
    c = _read(CFG, {})
    h = b.get("hours") or {}
    c["hours"] = {**(c.get("hours") or {}), **{k: v for k, v in h.items() if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(v))}}
    if "auto" in b:
        c["auto"] = bool(b["auto"])
    _write(CFG, c)
    return {"ok": True, "cfg": cfg()}


@router.get("/api/planer/{pid}/preview")
def api_prev(pid: str, i: int = 0):
    it = next((x for x in plan() if x["id"] == pid), None)
    if not it:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    f = it.get("file") if it["kind"] == "film" else (it.get("files") or [None])[min(i, len(it.get("files") or [1]) - 1)]
    if not f or not os.path.isfile(f):
        return JSONResponse({"error": "plik zniknął"}, status_code=404)
    return FileResponse(f, media_type="video/mp4" if it["kind"] == "film" else "image/jpeg")
