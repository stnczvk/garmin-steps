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
DEF = {"hours": {"jedzenie": "18:00", "karuzela": "09:00", "vlog": "20:00", "cheat": "19:00", "przepis": "17:00", "klip": "18:30"},
       "platforms": {"film": ["instagram", "youtube"], "karuzela": ["instagram"]}, "auto": True}
_lock = threading.RLock()


def cfg():
    c = _read(CFG, {})
    return {**DEF, **c, "hours": {**DEF["hours"], **(c.get("hours") or {})}, "platforms": {**DEF["platforms"], **(c.get("platforms") or {})}}


def plan():
    return _read(PLAN, [])


def save(P):
    _write(PLAN, P[-150:])


def _film_pl(series=""):
    L = list(cfg()["platforms"]["film"])
    if series == "vlog":          # vlogi Bartłomiej wrzuca na TikToka sam
        return L
    try:
        import tiktok_pub
        if tiktok_pub.connected() and "tiktok" not in L:
            L.append("tiktok")
    except Exception:
        pass
    return L


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
                      "file": str(f), "when": w, "platforms": _film_pl(),
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
        if kind == "klip":   # klipy z vloga YT: opis gotowy z Maca, po jednym dziennie (kolejne dni po ostatnim zaplanowanym)
            o = m.get("opis") or {}
            last = max([x["when"] for x in plan() if x.get("series") == "klip" and x.get("status") != "published"] + [0])
            when = _when("klip", datetime.fromtimestamp(last, TZ) + timedelta(hours=2)) if last > time.time() else _when("klip")
            _add({"src": f"vf:{nm}", "kind": "film", "series": "klip", "label": o.get("hook") or nm, "file": str(f), "when": when,
                  "platforms": _film_pl("klip"), "caption": o.get("instagram", ""), "tiktok": o.get("tiktok", ""), "yt_title": o.get("yt_title", "")})
            have.add(f"vf:{nm}")
            continue
        ep = re.search(r"#\s*(\d+)", nm)
        job = next((j for j in reversed(V) if (j.get("kind") or "vlog") == kind and ep and str(j.get("ep")) == ep.group(1) and j.get("state") == "done"), None)
        o = _opis(kind, job["id"]) if job else {"ok": False}
        _add({"src": f"vf:{nm}", "kind": "film", "series": kind, "label": nm, "file": str(f), "when": _when(kind),
              "platforms": _film_pl(kind), "caption": (o.get("instagram") or "") if o.get("ok") else "", "tiktok": o.get("tiktok", ""), "yt_title": ""})


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
    for _ in range(120):   # trwa wcześniejsza wysyłka do TikToka – czekamy, żeby nie wysłać dwa razy
        if pid not in _pre:
            break
        time.sleep(5)
    with _lock:
        P = plan()
        it = next((x for x in P if x["id"] == pid), None)
        if not it or it["status"] not in ("approved", "error"):
            return
        it["status"], it["step"] = "publishing", "publikuję…"
        save(P)
    res, errs = dict(it.get("results") or {}), []
    if res.get("tiktok", {}).get("pre") and not res["tiktok"].get("told"):
        res["tiktok"]["told"] = True
        try:
            from extra import push_all
            push_all("🎵 TikTok: czas publikacji", f"„{it['label']}” czeka w skrzynce TikToka – opublikuj teraz", "/#home")
        except Exception:
            pass
    for p in it["platforms"]:
        if res.get(p, {}).get("ok"):
            continue
        try:
            if p == "tiktok":
                import tiktok_pub
                mid, note = tiktok_pub.publish(it["file"], (it.get("tiktok") or it.get("caption") or "").strip(), opts=it.get("tt") or {})
                res[p] = {"ok": True, "id": mid, "at": time.time(), "note": note}
                if "skrzynce" in note:
                    try:
                        from extra import push_all
                        push_all("🎵 TikTok: film czeka w skrzynce", f"„{it['label']}” – otwórz TikToka → skrzynka i opublikuj", "/#home")
                    except Exception:
                        pass
                continue
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


TT_LEAD = 30 * 60   # TikTok przetwarza film w skrzynce nawet ~40 min, więc wysyłamy go wcześniej
_pre = set()


def pre_tiktok(pid):
    """Wysyła film do skrzynki TikToka przed czasem publikacji – o czasie przychodzi już tylko przypomnienie."""
    if pid in _pre:
        return
    _pre.add(pid)
    try:
        it = next((x for x in plan() if x["id"] == pid), None)
        if not it or it["status"] != "approved" or (it.get("results") or {}).get("tiktok", {}).get("ok"):
            return
        import tiktok_pub
        try:
            mid, note = tiktok_pub.publish(it["file"], (it.get("tiktok") or it.get("caption") or "").strip(), opts=it.get("tt") or {})
            r = {"ok": True, "id": mid, "at": time.time(), "note": note, "pre": True}
        except Exception as e:
            print("planer pre tiktok", e, flush=True)
            return   # spróbuje normalnie o czasie publikacji
        with _lock:
            P = plan()
            x = next((y for y in P if y["id"] == pid), None)
            if x:
                x.setdefault("results", {})["tiktok"] = r
                save(P)
    finally:
        _pre.discard(pid)


def _clean_once():
    """Jednorazowo: opisy zaplanowanych postów bez „AI-owych” myślników (nowe opisy są już czyszczone przy tworzeniu)."""
    c = _read(CFG, {})
    if c.get("human_v1"):
        return
    from montaz import human
    with _lock:
        P = plan()
        for it in P:
            if it.get("status") in ("draft", "approved"):
                for k in ("caption", "tiktok"):
                    if it.get(k):
                        it[k] = human(it[k])
        save(P)
    c["human_v1"] = time.time()
    _write(CFG, c)


def _loop():
    time.sleep(45)
    try:
        _clean_once()
    except Exception as e:
        print("planer clean", e, flush=True)
    while True:
        try:
            scan()
        except Exception as e:
            print("planer scan", e, flush=True)
        try:
            now = time.time()
            for it in plan():
                if it["status"] == "approved" and it["when"] <= now:
                    threading.Thread(target=publish, args=(it["id"],), daemon=True).start()
                elif (it["status"] == "approved" and "tiktok" in it["platforms"] and it["when"] - TT_LEAD <= now
                      and not (it.get("results") or {}).get("tiktok", {}).get("ok") and it.get("file")):
                    threading.Thread(target=pre_tiktok, args=(it["id"],), daemon=True).start()
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
    try:
        import tiktok_pub
        tt = tiktok_pub.connected()
    except Exception:
        tt = False
    return {"items": P[-40:], "d0": d0, "cfg": cfg(), "tiktok": tt, "tt_mode": tiktok_pub.mode() if tt else "",
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
            if isinstance(b.get("tt"), dict):
                t = b["tt"]
                it["tt"] = {"privacy": str(t.get("privacy") or "")[:40], **{k: bool(t.get(k)) for k in ("comment", "duet", "stitch", "disclose", "own_brand", "branded")}}
            if b.get("tiktok") is not None:
                it["tiktok"] = str(b["tiktok"])[:2200]
            if isinstance(b.get("platforms"), list):
                it["platforms"] = [p for p in b["platforms"] if p in ("instagram", "youtube") or (p == "tiktok" and it["kind"] == "film")]
        if action == "approve":
            if not it["platforms"]:
                return {"ok": False, "error": "wybierz platformę"}
            if "tiktok" in it["platforms"]:
                try:
                    import tiktok_pub
                    direct = tiktok_pub.mode() == "bezposrednio"
                except Exception:
                    direct = False
                t = it.get("tt") or {}
                if direct and not t.get("privacy"):
                    return {"ok": False, "error": "TikTok: wybierz, kto ma widzieć film"}
                if direct and t.get("disclose") and not (t.get("own_brand") or t.get("branded")):
                    return {"ok": False, "error": "TikTok: zaznacz, czy promujesz swoją markę czy cudzą (albo wyłącz oznaczenie)"}
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
    if it["kind"] == "film":
        from lite import lite
        return FileResponse(lite(f), media_type="video/mp4")
    return FileResponse(f, media_type="image/jpeg")

# ---------- własne posty (wgrane z telefonu/komputera w Treściach) ----------
UPD = SOCIAL_ROOT / "posty_wlasne"


@router.post("/api/planer/upload")
async def api_upload(req: Request, name: str = "plik.mp4"):
    """Plik wysyłany surowo w body (strumieniowo – filmy mogą mieć setki MB)."""
    ext = os.path.splitext(name.lower())[1]
    if ext not in (".mp4", ".mov", ".jpg", ".jpeg"):
        return {"ok": False, "error": "Obsługuję filmy MP4/MOV i zdjęcia JPG."}
    UPD.mkdir(parents=True, exist_ok=True)
    for f in UPD.iterdir():                      # sprzątanie: starsze niż 30 dni
        if f.is_file() and time.time() - f.stat().st_mtime > 30 * 86400 and not any(str(f) in (x.get("file", ""), *(x.get("files") or [])) for x in plan() if x["status"] in ("draft", "approved", "error")):
            f.unlink(missing_ok=True)
    fid = secrets.token_hex(6)
    path = UPD / f"{fid}{'.jpg' if ext == '.jpeg' else ext}"
    n = 0
    with open(path, "wb") as fh:
        async for chunk in req.stream():
            n += len(chunk)
            if n > 2_000_000_000:
                fh.close(); path.unlink(missing_ok=True)
                return {"ok": False, "error": "Plik za duży (max 2 GB)."}
            fh.write(chunk)
    if n < 1000:
        path.unlink(missing_ok=True)
        return {"ok": False, "error": "Pusty plik."}
    return {"ok": True, "id": path.name}


@router.post("/api/planer/add")
async def api_add(req: Request):
    b = await req.json()
    files = [UPD / os.path.basename(x) for x in (b.get("files") or [])]
    files = [f for f in files if f.is_file()]
    if not files:
        return {"ok": False, "error": "Najpierw wgraj plik."}
    kind = "film" if files[0].suffix in (".mp4", ".mov") else "karuzela"
    try:
        when = datetime.fromisoformat(b["when"]).replace(tzinfo=TZ).timestamp()
    except Exception:
        return {"ok": False, "error": "Wybierz datę i godzinę."}
    allowed = ("instagram", "youtube", "tiktok") if kind == "film" else ("instagram",)
    pl = [p for p in (b.get("platforms") or []) if p in allowed]
    if not pl:
        return {"ok": False, "error": "Wybierz platformę."}
    cap = str(b.get("caption") or "")[:2200]
    item = {"src": f"up:{files[0].name}", "kind": kind, "series": "wlasny", "label": (str(b.get("label") or "").strip() or (cap.split("\n")[0][:60] if cap else "Własny post")),
            "when": when, "platforms": pl, "caption": cap, "tiktok": str(b.get("tiktok") or cap)[:2200], "yt_title": str(b.get("yt_title") or "")[:100]}
    if kind == "film":
        item["file"] = str(files[0])
    else:
        item["files"] = [str(f) for f in files[:10]]
    it = _add(item)
    if not it:
        return {"ok": False, "error": "Ten plik już jest w planerze."}
    if b.get("approve"):
        with _lock:
            P = plan()
            x = next((x for x in P if x["id"] == it["id"]), None)
            if x:
                x["status"] = "approved"
                save(P)
    return {"ok": True, "id": it["id"]}



# ---------- lekkie miniatury JPG zamiast <video> w kalendarzu i planerze (mniej lagów) ----------
THUMBS = SOCIAL_ROOT / "thumbs"


@router.get("/api/planer/{pid}/thumb")
def api_thumb(pid: str):
    import shutil, subprocess
    it = next((x for x in plan() if x["id"] == pid), None)
    if not it:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    src = it.get("file") if it["kind"] == "film" else (it.get("files") or [None])[0]
    if not src or not os.path.isfile(src):
        return JSONResponse({"error": "plik zniknął"}, status_code=404)
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / f"{os.path.basename(pid)}.jpg"
    if not out.exists() or out.stat().st_mtime < os.path.getmtime(src):
        ff = shutil.which("ffmpeg") or "ffmpeg"
        args = ([ff, "-y", "-v", "error", "-ss", "1", "-i", src, "-frames:v", "1"] if it["kind"] == "film" else [ff, "-y", "-v", "error", "-i", src]) + \
               ["-vf", "scale=240:-2", "-q:v", "5", str(out)]
        r = subprocess.run(args, capture_output=True, timeout=30)
        if r.returncode or not out.exists():
            if it["kind"] == "film":       # film krótszy niż 1 s
                subprocess.run([ff, "-y", "-v", "error", "-i", src, "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "5", str(out)], capture_output=True, timeout=30)
            if not out.exists():
                return JSONResponse({"error": "brak klatki"}, status_code=404)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
