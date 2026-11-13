"""Planer postów (konta prywatne): Montażownia → szkic posta → Twoje zatwierdzenie w centrum → publikacja o ustalonej godzinie.
Instagram: Reels (film) i karuzela zdjęć (Instagram API z logowaniem przez Instagram, uprawnienie instagram_business_content_publish).
YouTube: Shorts (upload przez YouTube Data API, uprawnienie youtube.force-ssl, które już mamy).
Pliki wystawiamy platformom pod jednorazowymi linkami /pub/<token>/… (ważne 24 h)."""
import asyncio, json, os, re, secrets, threading, time
from datetime import datetime, timedelta, timezone
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
DEF = {"hours": {"jedzenie": "18:00", "karuzela": "09:00", "vlog": "20:00", "cheat": "19:00", "przepis": "17:00", "klip": "18:30", "vlogyt": "17:00", "strimy_film": "17:00", "strimy_short": "12:30", "strimy_short2": "20:30"},
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


SLOTS_DEF = {"film": ["14:00", "17:30", "20:00"], "short": ["11:30", "15:30", "21:30"]}   # min. 1,5 h odstępu film ↔ short


def strimy_slots():
    c = _read(CFG, {}).get("strimy_slots") or {}
    ok = lambda L: sorted({x for x in L if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(x))}, key=lambda t: tuple(map(int, t.split(":"))))
    return {k: (ok(c.get(k) or []) or SLOTS_DEF[k]) for k in SLOTS_DEF}


@router.get("/api/planer/strimy-slots")
def api_slots_get():
    return strimy_slots()


@router.post("/api/planer/strimy-slots")
async def api_slots_set(req: Request):
    """Godziny okienek Strimów (filmy YT / shorty) – po zapisaniu kolejka układa się od nowa."""
    b = await req.json()
    with _lock:
        c = _read(CFG, {})
        c["strimy_slots"] = {k: [str(x).strip() for x in (b.get(k) or [])][:6] for k in ("film", "short")}
        _write(CFG, c)
    moved = replan_strimy()
    return {"ok": True, "slots": strimy_slots(), "moved": moved}


def _film_slot(P, streamer, long=True):
    """Kanał Strimy: 3 okienka dziennie na filmy YT (13, 17, 20) i 3 na shorty (12:30, 16:30, 20:30).
    Jeden streamer dziennie max 1 film i max 2 shorty – reszta okienek zostaje dla innych streamerów albo kolejnych dni."""
    H = strimy_slots()["film" if long else "short"]
    hk = ("strimy_film",) if long else ("strimy_short", "strimy_short2")
    lim = 1 if long else 2
    Q = [x for x in P if x.get("group") == "strimy" and x.get("hkind") in hk and x.get("status") not in ("published", "skipped", "error")]
    used = {datetime.fromtimestamp(x["when"], TZ).strftime("%Y-%m-%d %H:%M") for x in Q}
    now = datetime.now(TZ)
    for dd in range(0, 400):
        day = (now + timedelta(days=dd)).date()
        mine = sum(1 for x in Q if (x.get("streamer") or "") == (streamer or "") and datetime.fromtimestamp(x["when"], TZ).date() == day)
        if mine >= lim:
            continue
        for h in H:
            hh, mm = map(int, h.split(":"))
            t = datetime(day.year, day.month, day.day, hh, mm, tzinfo=TZ)
            if t > now + timedelta(minutes=20) and t.strftime("%Y-%m-%d %H:%M") not in used:
                return t.timestamp()
    return _when("strimy_film")


def replan_strimy(long=None):
    """Przeplanowanie kolejki Strimów od nowa: streamerzy na zmianę, limity dzienne (1 film, 2 shorty na streamera),
    najwcześniejsze wolne okienka. Nie rusza wpisów na najbliższe 3 h ani tych z ręcznie ustawioną godziną."""
    moved = 0
    with _lock:
        P = plan()
        for lg in ([long] if long is not None else [True, False]):
            hk = ("strimy_film",) if lg else ("strimy_short", "strimy_short2")
            Q = sorted([x for x in P if x.get("group") == "strimy" and x.get("hkind") in hk and not x.get("pin")
                        and x.get("status") in ("approved", "draft") and x["when"] > time.time() + 3 * 3600], key=lambda x: x["when"])
            ids = {x["id"] for x in Q}
            G = {}
            for x in Q:
                G.setdefault(x.get("streamer") or "", []).append(x)
            order, last = [], None
            while any(G.values()):
                c = sorted([k for k in G if G[k]], key=lambda k: (k == last, -len(G[k])))
                last = c[0]
                order.append(G[last].pop(0))
            order.sort(key=lambda x: not x.get("prio"))   # skróty streamów przed resztą (sort stabilny – kolejność reszty bez zmian)
            base = [x for x in P if x["id"] not in ids]
            for x in order:
                w = _film_slot(base, x.get("streamer"), lg)
                if w != x["when"]:
                    moved += 1
                x["when"] = w
                base.append(x)
        save(P)
    return moved


@router.post("/api/planer/strimy-replan")
def api_strimy_replan():
    return {"ok": True, "moved": replan_strimy()}


def _strimy_tags(streamer, long):
    """Tagi YT dla Strimów: imię i nick streamera (ludzie wpisują je w wyszukiwarkę, często z literówkami) + nazwa kanału."""
    try:
        from klipping import _streamers
        st = next((x for x in _streamers() if x["id"] == streamer), {}) or {}
    except Exception:
        st = {}
    nick = re.sub(r"^.*/", "", (st.get("link") or "").strip("/"))
    name = st.get("name") or streamer or ""
    T = [name, nick, f"{name} klipy", f"{name} stream", f"{name} {'reakcja' if long else 'shorts'}", "Strimy w Pigułce", "klipy ze streamów",
         "polski stream", "twitch polska" if "twitch" in (st.get("link") or "") else "kick polska"]
    out = []
    for t in T:
        t = (t or "").strip()
        if t and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out


def add_strimy(jid, clip, path, long, auto=True, streamer=""):
    """Klipping „Strimy w Pigułce”: długie filmy → YouTube codziennie 17:00, shorty → YouTube Shorts + Reels 2× dziennie (12:30, 20:30)."""
    P = plan()
    if long:
        kind, plats = "strimy_film", ["youtube"]
    else:
        kind, plats = "strimy_short", ["youtube", "instagram"]
    when = _film_slot(P, streamer, long)
    it = _add({"src": f"kl:{jid}:{clip['name']}", "kind": "film", "series": "strimy_film" if long else "strimy_short", "hkind": kind,
               "group": "strimy", "label": clip.get("yt_title") or clip.get("hook") or clip["name"], "file": str(path), "when": when,
               "platforms": plats, "caption": clip.get("instagram") or clip.get("yt_desc") or "", "tiktok": clip.get("tiktok", ""),
               "yt_title": clip.get("yt_title", ""), "yt_desc": clip.get("yt_desc", ""), "long": bool(long), "yt_cat": "24",
               "thumb_file": clip.get("thumb_file") or "", "streamer": streamer, "yt_tags": _strimy_tags(streamer, long),
               "prio": str(clip.get("name") or "").startswith("skrót")})   # skrót streama – pierwszy wolny termin
    if it and auto:
        with _lock:
            P = plan()
            x = next((y for y in P if y["id"] == it["id"]), None)
            if x:
                x["status"] = "approved"
                save(P)
    if it:
        try:
            replan_strimy(bool(long))
        except Exception as e:
            print("strimy przeplanowanie:", e, flush=True)
    return it


def _przeplot(kind):
    """Kolejka kanału Strimy: streamerzy na zmianę (Dzinold, Bandura, Dzinold, Tuszol…), a nie najpierw wszystko od jednego.
    Te same terminy, tylko inna kolejność; nie rusza wpisów na najbliższe 3 h i tych z ręcznie ustawioną godziną."""
    with _lock:
        P = plan()
        Q = sorted([x for x in P if x.get("group") == "strimy" and (x.get("hkind") == kind or (kind == "strimy_short" and x.get("hkind") == "strimy_short2")) and not x.get("pin")
                    and x.get("status") in ("approved", "draft") and x["when"] > time.time() + 3 * 3600], key=lambda x: x["when"])
        if len({x.get("streamer") for x in Q}) < 2:
            return
        slots = [x["when"] for x in Q]
        G = {}
        for x in Q:
            G.setdefault(x.get("streamer") or "", []).append(x)
        order, last = [], None
        while any(G.values()):
            # następny: streamer z najdłuższą kolejką, ale nie ten sam co przed chwilą (jeśli się da)
            c = sorted([k for k in G if G[k]], key=lambda k: (k == last, -len(G[k])))
            last = c[0]
            order.append(G[last].pop(0))
        for x, w in zip(order, slots):
            x["when"] = w
        save(P)


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
            # promocja vloga: klipy dopiero po premierze odcinka na YouTube
            PP = [x for x in plan() if x.get("series") in ("klip", "vlogyt") and x.get("status") != "published"]
            lastx = max(PP, key=lambda x: x["when"], default=None)
            last = lastx["when"] if lastx else 0
            if lastx and lastx.get("series") == "vlogyt" and last > time.time():
                when = last + 90 * 60   # pierwszy klip 1,5 h po premierze vloga (kop w pierwszych godzinach), kolejne po jednym dziennie
            else:
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


# ---------- vlog YouTube (ŁysyVlog) z Maca: film + miniatura + opis → szkic w planerze, klipy z niego jako promocja po premierze ----------
@router.put("/api/mont-hook/vlogyt-thumb")
async def hook_vlogyt_thumb(req: Request, name: str = ""):
    from montaz import _tok, VFILM
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:100] or "vlog"
    VFILM.mkdir(parents=True, exist_ok=True)
    data = await req.body()
    if len(data) < 2000:
        return {"ok": False, "error": "pusta miniatura"}
    n = int(req.query_params.get("n") or 0)
    if n:   # propozycje miniatur do wyboru w planerze (1 = domyślna)
        (VFILM / f"{nm}.yt.{n}.jpg").write_bytes(data)
        if n == 1:
            (VFILM / (nm + ".yt.jpg")).write_bytes(data)
        with _lock:
            P = plan()
            it = next((x for x in P if x.get("src") == f"vyt:{nm}" and x["status"] not in ("published", "publishing")), None)
            if it and n == 1 and not it.get("thumb_pick"):
                it["thumb_file"] = str(VFILM / (nm + ".yt.jpg"))
                save(P)
        return {"ok": True}
    (VFILM / (nm + ".yt.jpg")).write_bytes(data)
    return {"ok": True}


def _vyt(pid):
    from montaz import VFILM
    it = next((x for x in plan() if x["id"] == pid), None)
    if not it or not str(it.get("src", "")).startswith("vyt:"):
        return None, None, None
    return it, it["src"][4:], VFILM


@router.get("/api/planer/{pid}/thumbs")
def api_thumbs(pid: str):
    it, nm, V = _vyt(pid)
    if not it:
        return {"thumbs": []}
    L = sorted(V.glob(f"{glob_escape(nm)}.yt.*.jpg"), key=lambda f: f.name)
    cur = it.get("thumb_file") or ""
    return {"thumbs": [{"n": f.name.split(".yt.")[1][:-4], "on": os.path.realpath(cur) == os.path.realpath(str(f)) or
                        (it.get("thumb_pick") or "1") == f.name.split(".yt.")[1][:-4], "t": int(f.stat().st_mtime)} for f in L]}


def glob_escape(x):
    import glob
    return glob.escape(x)


@router.get("/api/planer/{pid}/thumbv/{n}")
def api_thumbv(pid: str, n: str):
    it, nm, V = _vyt(pid)
    if not it or not re.fullmatch(r"\d|own", n):
        return JSONResponse({"error": "nie ma"}, status_code=404)
    f = V / f"{nm}.yt.{n}.jpg"
    return FileResponse(str(f), media_type="image/jpeg") if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)


@router.post("/api/planer/{pid}/thumb-pick")
async def api_thumb_pick(pid: str, req: Request):
    it, nm, V = _vyt(pid)
    if not it:
        return {"ok": False, "error": "to nie vlog YT"}
    n = str((await req.json()).get("n") or "")
    f = V / f"{nm}.yt.{n}.jpg"
    if not re.fullmatch(r"\d|own", n) or not f.is_file():
        return {"ok": False, "error": "nie ma tej miniatury"}
    with _lock:
        P = plan()
        x = next((y for y in P if y["id"] == pid), None)
        if x:
            x["thumb_file"], x["thumb_pick"] = str(f), n
            save(P)
    return {"ok": True}


@router.post("/api/planer/{pid}/thumb-own")
async def api_thumb_own(pid: str, req: Request):
    """Własna miniatura z telefonu/komputera (JPG/PNG) → 1280×720 JPG."""
    import shutil, subprocess, tempfile
    it, nm, V = _vyt(pid)
    if not it:
        return {"ok": False, "error": "to nie vlog YT"}
    data = await req.body()
    if len(data) < 2000:
        return {"ok": False, "error": "pusty plik"}
    f = V / f"{nm}.yt.own.jpg"
    with tempfile.NamedTemporaryFile(suffix=".img") as t:
        t.write(data); t.flush()
        r = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-y", "-v", "error", "-i", t.name, "-frames:v", "1", "-vf",
                            "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720", "-q:v", "3", str(f)], capture_output=True, text=True)
    if r.returncode or not f.is_file():
        return {"ok": False, "error": "zły obrazek: " + (r.stderr.strip()[-80:] or "?")}
    with _lock:
        P = plan()
        x = next((y for y in P if y["id"] == pid), None)
        if x:
            x["thumb_file"], x["thumb_pick"] = str(f), "own"
            save(P)
    return {"ok": True}


@router.post("/api/planer/{pid}/thumb-nowe")
def api_thumb_nowe(pid: str):
    it, nm, V = _vyt(pid)
    if not it:
        return {"ok": False, "error": "to nie vlog YT"}
    with _lock:
        Q = [x for x in _read("klipy_popraw.json", []) if not (x["name"] == nm and x.get("typ") == "miniatury")]
        Q.append({"name": nm, "typ": "miniatury", "at": time.time()})
        _write("klipy_popraw.json", Q[-20:])
    return {"ok": True}


@router.post("/api/mont-hook/vlogyt-meta")
async def hook_vlogyt_meta(req: Request, name: str = ""):
    from montaz import _tok, VFILM
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:100] or "vlog"
    VFILM.mkdir(parents=True, exist_ok=True)
    b = await req.json()
    (VFILM / (nm + ".yt.json")).write_text(json.dumps({k: b.get(k) for k in ("yt_title", "yt_desc", "tags")}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@router.put("/api/mont-hook/vlogyt")
async def hook_vlogyt(req: Request, name: str = "", meta: str = ""):
    from montaz import _tok, VFILM
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:100] or "vlog"
    VFILM.mkdir(parents=True, exist_ok=True)
    tmp = VFILM / (nm + ".yt.part")
    with tmp.open("wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    if tmp.stat().st_size < 100000:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": "pusty plik"}
    f = VFILM / (nm + ".yt.mp4")
    tmp.replace(f)
    try:
        m = json.loads(meta or (VFILM / (nm + ".yt.json")).read_text(encoding="utf-8"))
    except Exception:
        m = {}
    th = VFILM / (nm + ".yt.jpg")
    src = f"vyt:{nm}"
    with _lock:
        P = plan()
        old = next((x for x in P if x["src"] == src), None)
        if old and old["status"] not in ("published", "publishing"):
            P.remove(old)
            save(P)
    it = _add({"src": src, "kind": "film", "series": "vlogyt", "label": m.get("yt_title") or nm, "file": str(f), "when": _when("vlogyt"),
               "platforms": ["youtube"], "caption": vlog_opis(m.get("yt_desc")), "yt_title": m.get("yt_title") or nm, "yt_desc": vlog_opis(m.get("yt_desc")),
               "yt_tags": m.get("tags") or [], "long": True, "yt_cat": "22", "thumb_file": str(th) if th.is_file() else ""})
    return {"ok": True, "id": (it or {}).get("id"), "when": (it or {}).get("when")}


VLOG_LINKI = ("Instagram: https://www.instagram.com/st4nczuk_/\n"
              "TikTok: https://www.tiktok.com/@lysyzsilowni\n"
              "Współpraca: bartlomiej_kontakt@icloud.com")


def vlog_opis(d):
    """Opis vloga YT: stałe linki przed rozdziałami (albo przed hashtagami, gdy rozdziałów brak)."""
    d = d or ""
    if "#łysyvlog" not in d.lower():   # znak serii: klik w hashtag nad tytułem → wszystkie odcinki
        L0 = d.rstrip().split("\n")
        if L0 and L0[-1].strip().startswith("#"):
            L0[-1] = L0[-1].rstrip() + " #łysyvlog"
        else:
            L0.append("\n#łysyvlog")
        d = "\n".join(L0)
    if "instagram.com/st4nczuk_" in d:
        return d
    L = d.split("\n")
    i = next((k for k, x in enumerate(L) if re.match(r"\s*(ROZDZIA|0:00)", x, re.I)), None)
    if i is None:
        i = next((k for k, x in enumerate(L) if x.strip().startswith("#")), len(L))
    return "\n".join(L[:i]).rstrip() + "\n\n" + VLOG_LINKI + "\n\n" + "\n".join(L[i:]).lstrip()


def klipy_po_vlogu(v, P=None):
    """Klipy z vloga YT (promocja) idą za premierą: pierwszy 1,5 h po vlogu, kolejne po jednym dziennie o godzinie klipów."""
    own = P is None
    if own:
        P = plan()
    base = v["src"][4:] if v.get("src", "").startswith("vyt:") else ""
    def _ord(x):   # klip 1, mem 1, klip 2, mem 2… (memy przeplatane z klipami)
        m = re.search(r" (klip|mem) (\d+)$", str(x.get("src")))
        return (int(m.group(2)), m.group(1) != "klip") if m else (99, True)
    K = sorted([x for x in P if x.get("series") == "klip" and x.get("status") in ("draft", "approved") and not x.get("pin")
                and base and (str(x.get("src", "")).startswith(f"vf:{base} klip") or str(x.get("src", "")).startswith(f"vf:{base} mem"))], key=_ord)
    t = v["when"] + 90 * 60
    for x in K:
        x["when"] = t
        t = _when("klip", datetime.fromtimestamp(t, TZ) + timedelta(hours=2))
    if own:
        save(P)
    return len(K)


def _klip_base(src):
    src = str(src or "")
    if src.startswith("vyt:"):
        return src[4:]
    m = re.match(r"vf:(.+) (?:klip|mem) \d+$", src)
    return m.group(1) if m else ""


@router.post("/api/mont-hook/vlogyt-klipy-reset")
def hook_vlogyt_klipy_reset(req: Request, name: str = ""):
    """Nowe klipy z tego samego vloga → stare NIEZATWIERDZONE znikają z planu; zatwierdzone i opublikowane zostają (Mac numeruje dalej)."""
    from montaz import _tok
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:100]
    P = plan()
    res = {"ok": True}
    srcs = []
    for typ, ile0, key in (("klip", 5, ""), ("mem", 5, "_mem")):
        K = [x for x in P if str(x.get("src", "")).startswith(f"vf:{nm} {typ} ")]
        dr = [x["src"] for x in K if x.get("status") in ("draft", "error", "skipped")]   # usunięte ręcznie też (inaczej blokują nowe pliki o tej nazwie)
        zost = [x for x in K if x["src"] not in dr]
        nums = [int(m.group(1)) for x in zost for m in [re.search(rf"{typ} (\d+)$", x["src"])] if m]
        srcs += dr
        res.update({"next" + key: max(nums + [0]) + 1, "ile" + key: max(0, ile0 - len(zost)), "zostaja" + key: [x.get("label") or "" for x in zost]})
    if srcs:
        drop(srcs, why="nowe klipy z vloga")
    res["usuniete"] = len(srcs)
    return res


@router.post("/api/mont-hook/vlogyt-ulozenie")
def hook_vlogyt_ulozenie(req: Request, name: str = ""):
    """Po wrzuceniu nowych klipów i memów: ułóż je za premierą vloga (przeplatane, po jednym dziennie)."""
    from montaz import _tok
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:100]
    with _lock:
        P = plan()
        v = next((x for x in P if x.get("src") == f"vyt:{nm}"), None)
        if not v:
            return {"ok": False, "error": "brak vloga w planie"}
        n = klipy_po_vlogu(v, P)
        save(P)
    return {"ok": True, "ulozone": n}


def nauka_klipu(it, dobry):
    """Twoje decyzje (zatwierdzony = dobry, usunięty = zły) przy klipach i memach z vloga → przykłady dla AI przy kolejnych vlogach."""
    from montaz import VFILM
    m = re.match(r"vf:(.+ (klip|mem) \d+)$", str(it.get("src") or ""))
    if not m:
        return
    f = VFILM / (m.group(1) + ".meta.json")
    d = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    wpis = {"typ": m.group(2), "ocena": "dobry" if dobry else "zły", "tytul": (d.get("tytul") or it.get("label") or "")[:120],
            "tekst": (d.get("tekst") or "")[:220], "src": it["src"], "at": time.time()}
    L = [x for x in _read("klipy_nauka.json", []) if x.get("src") != it["src"]] + [wpis]
    _write("klipy_nauka.json", L[-120:])


def klipy_przyklady(typ):
    """Ostatnie dobre i złe przykłady + moje stałe uwagi (dla promptu)."""
    U = [x["uwagi"] for x in _read("klipy_uwagi.json", [])][-6:]
    uw = ("\nMOJE UWAGI Z WCZEŚNIEJSZYCH POPRAWEK (pilnuj ich zawsze):\n" + "\n".join("- " + u for u in U) + "\n") if U else ""
    L = [x for x in _read("klipy_nauka.json", []) if x.get("typ") == typ]
    dob = [x for x in L if x["ocena"] == "dobry"][-8:]
    zle = [x for x in L if x["ocena"] == "zły"][-8:]
    if not dob and not zle:
        return uw
    f = lambda x: f"„{x['tytul']}” → „{x['tekst'][:140]}”"
    return ("\nMOJE WCZEŚNIEJSZE DECYZJE (ucz się mojego gustu – rób więcej takich jak DOBRE, unikaj takich jak ZŁE):\n"
            + ("DOBRE (zatwierdziłem):\n" + "\n".join("- " + f(x) for x in dob) + "\n" if dob else "")
            + ("ZŁE (usunąłem):\n" + "\n".join("- " + f(x) for x in zle) + "\n" if zle else "")) + uw


@router.post("/api/mont-hook/slownik")
async def hook_slownik_add(req: Request):
    """Mac: pary „źle usłyszane → poprawne” z ręcznych poprawek napisów."""
    from montaz import _tok
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    D = _read("napisy_slownik.json", {})
    for zle, dobre in (b.get("pary") or []):
        if 2 <= len(str(zle)) <= 40 and str(zle).lower() != str(dobre).lower():
            D[str(zle).lower()] = str(dobre)[:40]
    _write("napisy_slownik.json", dict(list(D.items())[-300:]))
    return {"ok": True, "n": len(D)}


@router.get("/api/mont-hook/slownik")
def hook_slownik_get(req: Request):
    from montaz import _tok
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    return {"slownik": _read("napisy_slownik.json", {})}


@router.post("/api/mont-hook/klip-meta")
async def hook_klip_meta(req: Request, name: str = ""):
    """Mac zapisuje przy klipie z vloga jego napisy (słowa z czasami) i tytuł – do ręcznej poprawki w planerze."""
    from montaz import _tok, VFILM
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:120]
    VFILM.mkdir(parents=True, exist_ok=True)
    (VFILM / (nm + ".meta.json")).write_text(json.dumps(await req.json(), ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@router.get("/api/planer/{pid}/napisy")
def api_napisy_get(pid: str):
    from montaz import VFILM
    it = next((x for x in plan() if x["id"] == pid), None)
    m = re.match(r"vf:(.+ (?:klip|mem) \d+)$", str((it or {}).get("src") or ""))
    f = VFILM / ((m.group(1) if m else "") + ".meta.json")
    if not m or not f.is_file():
        return {"ok": False, "error": "brak napisów do edycji (klip zrobiony starszą wersją)"}
    d = json.loads(f.read_text(encoding="utf-8"))
    return {"ok": True, "tekst": d.get("tekst") or "", "tytul": d.get("tytul") or "", "typ": d.get("typ") or "klip"}


@router.post("/api/planer/{pid}/napisy")
async def api_napisy_set(pid: str, req: Request):
    """Poprawione napisy / tytuł → Mac renderuje ten jeden klip jeszcze raz i podmienia plik w planie."""
    b = await req.json()
    it = next((x for x in plan() if x["id"] == pid), None)
    m = re.match(r"vf:(.+) ((?:klip|mem) \d+)$", str((it or {}).get("src") or ""))
    if not m:
        return {"ok": False, "error": "to nie klip z vloga"}
    if it["status"] in ("published", "publishing"):
        return {"ok": False, "error": "klip jest już opublikowany"}
    with _lock:
        Q = _read("klipy_popraw.json", [])
        Q.append({"name": m.group(1), "typ": "napisy", "klip": m.group(2), "tekst": str(b.get("tekst") or "")[:5000],
                  "tytul": str(b.get("tytul") or "")[:150], "at": time.time()})
        _write("klipy_popraw.json", Q[-30:])
    return {"ok": True}


@router.post("/api/planer/klipy-popraw")
async def api_klipy_popraw(req: Request):
    """Guzik „✂️ Popraw klipy”: Mac robi od nowa niezatwierdzone klipy z tego vloga (z Twoimi uwagami)."""
    b = await req.json()
    nm = _klip_base(b.get("psrc"))
    if not nm:
        return {"ok": False, "error": "to nie jest klip z vloga YouTube"}
    with _lock:
        Q = [x for x in _read("klipy_popraw.json", []) if not (x["name"] == nm and not x.get("typ"))]
        Q.append({"name": nm, "uwagi": str(b.get("uwagi") or "")[:800], "at": time.time()})
        if str(b.get("uwagi") or "").strip():   # uwagi zapamiętane na przyszłe vlogi
            U = _read("klipy_uwagi.json", []) + [{"uwagi": str(b["uwagi"])[:400], "at": time.time()}]
            _write("klipy_uwagi.json", U[-10:])
        _write("klipy_popraw.json", Q[-20:])
    return {"ok": True}


@router.get("/api/mont-hook/klipy-popraw")
def hook_klipy_popraw(req: Request):
    from montaz import _tok
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    with _lock:
        Q = _read("klipy_popraw.json", [])
        _write("klipy_popraw.json", [])
    return {"zadania": Q}


DROP = "posts_dropped.json"
VLOG_LEAD = 24 * 3600   # vlog YT idzie na YouTube dobę przed premierą (prywatny + zaplanowany), żeby zdążył się przetworzyć


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
def _acc(platform, group="prywatne"):
    from social import _accounts
    for k, a in _accounts().items():
        if a.get("platform") == platform and a.get("group") == group and not a.get("error"):
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
    key, acc = _acc("instagram", it.get("group") or "prywatne")
    if not acc:
        raise RuntimeError(f"brak połączonego konta Instagram w grupie {it.get('group') or 'prywatne'}")
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
    _, acc = _acc("youtube", it.get("group") or "prywatne")
    if not acc:
        raise RuntimeError(f"brak połączonego kanału YouTube w grupie {it.get('group') or 'prywatne'}")
    tok = _google_access(acc)
    cap = it.get("yt_desc") or it.get("caption") or ""
    if it.get("series") == "vlogyt":
        cap = vlog_opis(cap)
    title = (it.get("yt_title") or re.split(r"[\n.!?]", cap.strip())[0] or it["label"]).strip()[:90]
    tags = []
    for t in (it.get("yt_tags") or []) + re.findall(r"#(\w+)", cap)[:10]:   # tagi kanału + hashtagi z opisu, limit YouTube 500 znaków
        if t.lower() not in [x.lower() for x in tags] and sum(len(x) + 3 for x in tags) + len(t) < 480:
            tags.append(t)
    desc = cap if it.get("long") or "#shorts" in cap.lower() else cap + "\n\n#shorts"   # długie filmy bez #shorts
    meta = {"snippet": {"title": title, "description": desc[:4900], "tags": tags, "categoryId": it.get("yt_cat") or "17", "defaultLanguage": "pl"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    if it.get("series") == "vlogyt" and it["when"] > time.time() + 600:   # vlog wysłany wcześniej jako prywatny, YouTube sam opublikuje o premierze
        meta["status"].update(privacyStatus="private", publishAt=datetime.fromtimestamp(it["when"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    size = os.path.getsize(it["file"])
    r = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=60,
                   headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(size)}, json=meta)
    if r.status_code >= 300 or "location" not in r.headers:
        raise RuntimeError("YouTube: " + r.text[:200])
    def _chunks(fn):   # strumieniowo – długi vlog (GB) nie ląduje w pamięci serwera
        with open(fn, "rb") as f:
            while True:
                b = f.read(8 << 20)
                if not b:
                    break
                yield b
    u = httpx.put(r.headers["location"], content=_chunks(it["file"]), timeout=httpx.Timeout(3600, connect=60),
                  headers={"Content-Type": "video/mp4", "Content-Length": str(size)})
    if u.status_code >= 300:
        raise RuntimeError("YouTube (wysyłka): " + u.text[:200])
    vid = u.json().get("id")
    th = it.get("thumb_file")
    if vid and th and os.path.isfile(th):   # własna miniatura (wymaga zweryfikowanego kanału – błąd nie blokuje publikacji)
        try:
            r = httpx.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}", timeout=120,
                           headers={"Authorization": "Bearer " + tok, "Content-Type": "image/jpeg"}, content=open(th, "rb").read())
            if r.status_code >= 300:
                print("miniatura YT:", r.text[:200], flush=True)
        except Exception as e:
            print("miniatura YT:", e, flush=True)
    return vid


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
            push_all("🎵 TikTok: czas publikacji", f"„{it['label']}” czeka w skrzynce TikToka – stuknij, skopiuj opis i opublikuj",
                     f"/tt-opis/{it['id']}")
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


TT_LEAD = 2 * 3600   # TikTok potrafi przetwarzać film w skrzynce ponad godzinę – wysyłamy 2 h wcześniej, o czasie przychodzi przypomnienie
_pre = set()


@router.get("/tt-opis/{pid}")
def tt_opis(pid: str):
    """Strona z powiadomienia: gotowy opis TikToka do skopiowania jednym stuknięciem (skrzynka TikToka nie przyjmuje opisu z API)."""
    import html
    from fastapi.responses import HTMLResponse
    it = next((x for x in plan() if x["id"] == pid), None)
    if not it:
        return HTMLResponse("<p>Nie ma takiego posta.</p>", status_code=404)
    cap = (it.get("tiktok") or it.get("caption") or "").strip()
    return HTMLResponse(f"""<!doctype html><html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Opis TikTok</title>
<style>body{{font:16px -apple-system,system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:20px}}
h1{{font-size:18px}}pre{{white-space:pre-wrap;background:#1d1d1f;padding:14px;border-radius:12px;font:inherit}}
button,a{{display:block;width:100%;box-sizing:border-box;margin-top:12px;padding:16px;border:0;border-radius:12px;
font:600 17px inherit;text-align:center;text-decoration:none}}button{{background:#E5232F;color:#fff}}a{{background:#2c2c2e;color:#fff}}</style></head>
<body><h1>{html.escape(it.get('label') or 'TikTok')}</h1><pre id="c">{html.escape(cap)}</pre>
<button id="b">📋 Kopiuj opis</button><a href="https://www.tiktok.com/">Otwórz TikToka</a>
<script>document.getElementById('b').onclick=async()=>{{try{{await navigator.clipboard.writeText(document.getElementById('c').textContent);
document.getElementById('b').textContent='✓ Skopiowane – wklej w TikToku'}}catch(e){{document.getElementById('b').textContent='Zaznacz tekst i skopiuj ręcznie'}}}}</script>
</body></html>""")


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


_last_disk = 0


def sprzatanie_plikow():
    """Dysk serwera: plik posta znika 12 h po publikacji albo pominięciu (jest już na platformach),
    a przy małej ilości miejsca (< 15 GB) od razu. Plik używany jeszcze przez inny, nieopublikowany post zostaje."""
    import shutil
    from social import ROOT
    global _last_disk
    free = shutil.disk_usage(str(ROOT)).free
    if time.time() - _last_disk < (600 if free < 15e9 else 3600):
        return
    _last_disk = time.time()
    P = plan()
    potrzebne = {x.get("file") for x in P if x.get("status") not in ("published", "skipped") and x.get("file")}
    wiek = 0 if free < 15e9 else 12 * 3600
    freed = 0
    for x in P:
        f = x.get("file")
        if not f or f in potrzebne or x.get("status") not in ("published", "skipped"):
            continue
        t = x.get("done_at") or x.get("published") or x.get("when") or 0
        if time.time() - t < wiek or not str(f).startswith(str(ROOT)):
            continue
        try:
            if os.path.isfile(f):
                freed += os.path.getsize(f); os.unlink(f)
        except Exception:
            pass
    import glob as _g
    for d_, dni in ((ROOT / "podglady", 3), (ROOT / "vlog_upload", 2)):   # lekkie podglądy i porzucone wysyłki vlogów
        if d_.is_dir():
            for q in d_.iterdir():
                try:
                    if time.time() - q.stat().st_mtime > (0.5 if free < 15e9 else dni) * 86400:
                        sz = sum(f.stat().st_size for f in ([q] if q.is_file() else q.rglob("*")) if f.is_file())
                        (q.unlink() if q.is_file() else shutil.rmtree(q, ignore_errors=True)); freed += sz
                except Exception:
                    pass
    for q in _g.glob("/tmp/tt_*.mp4") + _g.glob("/tmp/tmp*.mp4"):   # resztki po wysyłce na TikToka
        try:
            if time.time() - os.path.getmtime(q) > 3600:
                freed += os.path.getsize(q); os.unlink(q)
        except Exception:
            pass
    if freed > 50_000_000:
        print(f"planer: posprzątane pliki opublikowanych postów, {freed / 1e9:.1f} GB (wolne było {free / 1e9:.1f} GB)", flush=True)


def _loop():
    """Skan folderów i sprzątanie – mogą trwać długo, dlatego publikacje mają osobną pętlę (_pub_loop)."""
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
            sprzatanie_plikow()
        except Exception as e:
            print("planer sprzątanie", e, flush=True)
        time.sleep(60)


def _pub_loop():
    """Publikacje o czasie – co 30 s, niezależnie od skanu (wcześniej długi skan potrafił opóźnić post o godziny)."""
    time.sleep(50)
    while True:
        try:
            now = time.time()
            for it in plan():
                if it["status"] == "approved" and (it["when"] <= now or (it.get("series") == "vlogyt" and it["when"] - VLOG_LEAD <= now)):
                    threading.Thread(target=publish, args=(it["id"],), daemon=True).start()
                elif (it["status"] == "approved" and "tiktok" in it["platforms"] and it["when"] - TT_LEAD <= now
                      and not (it.get("results") or {}).get("tiktok", {}).get("ok") and it.get("file")):
                    threading.Thread(target=pre_tiktok, args=(it["id"],), daemon=True).start()
        except Exception as e:
            print("planer pub", e, flush=True)
        time.sleep(30)


threading.Thread(target=_pub_loop, daemon=True).start()
threading.Thread(target=_loop, daemon=True).start()


# ---------- API dla apki ----------
@router.get("/api/planer")
def api_plan(group: str = ""):
    # kanał „Strimy w Pigułce” ma własny kalendarz w Klippingu – w głównym planerze go nie pokazujemy
    P = sorted([x for x in plan() if (x.get("group") or "prywatne") == group] if group else
               [x for x in plan() if x.get("group") != "strimy"], key=lambda x: x["when"])
    d0 = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()   # dzisiejsza północ (PL)
    P = [x for x in P if x["status"] != "skipped"
         and not (x["status"] in ("published", "error") and (x.get("done_at") or x.get("when") or 0) < d0)]
    try:
        import tiktok_pub
        tt = tiktok_pub.connected()
    except Exception:
        tt = False
    return {"items": P[-400:], "d0": d0, "cfg": cfg(), "tiktok": tt, "tt_mode": tiktok_pub.mode() if tt else "",
            "public": bool(_public())}


@router.post("/api/planer/{pid}/{action}")
async def api_act(pid: str, action: str, req: Request):
    b = await req.json() if action in ("save", "approve") else {}
    with _lock:
        P = plan()
        it = next((x for x in P if x["id"] == pid), None)
        if not it:
            return {"ok": False, "error": "nie ma"}
        if it.get("group") == "strimy":   # nauka AI: Twoje poprawki i usunięcia z kanału Strimy w Pigułce
            try:
                import klipping
                if b.get("yt_title") is not None and b["yt_title"] != it.get("yt_title"):
                    klipping.feedback(it.get("streamer"), "poprawiony tytuł", before=it.get("yt_title"), after=b["yt_title"])
                if b.get("caption") is not None and b["caption"] != it.get("caption"):
                    klipping.feedback(it.get("streamer"), "poprawiony opis", before=it.get("caption"), after=b["caption"])
                if action == "skip":
                    klipping.feedback(it.get("streamer"), "usunięty przed publikacją", what=it.get("label"))
            except Exception:
                pass
        if action in ("save", "approve"):
            if "caption" in b:
                it["caption"] = str(b["caption"])[:2200]
                if it.get("series") == "vlogyt":   # opis vloga YT edytowany w planerze → idzie na YouTube
                    it["caption"] = str(b["caption"])[:4900]
                    it["yt_desc"] = it["caption"]
            if b.get("yt_title") is not None:
                it["yt_title"] = str(b["yt_title"])[:100]
            if b.get("when"):
                try:
                    it["when"] = datetime.fromisoformat(b["when"]).replace(tzinfo=TZ).timestamp()
                    it["pin"] = True   # ręcznie ustawiona godzina – przeplatanie streamerów jej nie rusza
                    if it.get("series") == "vlogyt":   # przesunięty vlog → klipy promujące idą za nim
                        klipy_po_vlogu(it, P)
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
        if action in ("approve", "skip"):
            try:
                nauka_klipu(it, action == "approve")
            except Exception as e:
                print("nauka klipów:", e, flush=True)
        if action == "retry" and it["status"] == "error":
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
    if it.get("thumb_file") and os.path.isfile(it["thumb_file"]):   # własna miniatura (klipping)
        return FileResponse(it["thumb_file"], media_type="image/jpeg")
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
