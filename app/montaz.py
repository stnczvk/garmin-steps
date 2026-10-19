"""Montażownia: filmy montowane automatycznie (daily jedzenie z Dziennika na tym samym VPS, vlog z Maca – etap 2).

Jedzenie: zadania montażu Dziennika (/root/dziennik/montaz/zadania) – lista, podgląd, pobieranie filmu.
Strażnik montażu sprawdza każdy gotowy film (obraz, dźwięk, czarne klatki, cisza, długość) i czyta raport
z cięć; uwagi daje jako gotową poprawkę, którą jednym kliknięciem wysyłasz do montażu w Dzienniku.
Akcje w Dzienniku (ponów / popraw) wymagają w /root/social/.env:  DZIENNIK_PASS=<hasło do dziennika>
"""
import json, os, re, subprocess, threading, time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()
DZ = Path(os.environ.get("DZIENNIK_HOME", "/root/dziennik"))
JOBS = DZ / "montaz" / "zadania"
DZ_LOCAL = os.environ.get("DZIENNIK_LOCAL", "http://127.0.0.1:8100")
DZ_URL = os.environ.get("DZIENNIK_URL", "https://bartek-dziennik.duckdns.org")
JID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9]{4}$")
_dz = {"cookie": None}


def _rd(name, default):
    from social import _read
    return _read(name, default)


def _wr(name, data):
    from social import _write
    _write(name, data)


def _job(jid):
    if not JID.match(jid or ""):
        return None
    d = JOBS / jid
    return d if d.is_dir() else None


def _status(d):
    try:
        st = json.loads((d / "status.json").read_text())
    except Exception:
        st = {}
    if st.get("state") == "working" and time.time() - st.get("at", 0) > 1800:
        st.update(state="error", error="Montaż przestał odpowiadać (ponad 30 min bez postępu).")
    try:
        st["title"] = json.loads((d / "title.json").read_text())
    except Exception:
        st["title"] = []
    st["id"] = d.name
    st["kind"] = "jedzenie"
    return st


def _film(d, st):
    f = d / "gotowe" / str(st.get("film", ""))
    return f if st.get("state") == "done" and f.is_file() else None


@router.get("/api/mont/jobs")
def jobs():
    out = []
    hidden = set(_rd("mont_hidden.json", []))
    if JOBS.is_dir():
        for d in sorted((x for x in JOBS.iterdir() if x.is_dir() and JID.match(x.name)), reverse=True)[:30]:
            if d.name in hidden:
                continue
            st = _status(d)
            st.pop("log", None)
            st["check"] = _rd("mont_checks.json", {}).get(d.name)
            out.append(st)
    return {"ok": JOBS.is_dir(), "jobs": out, "dziennik": DZ_URL, "can_act": bool(os.environ.get("DZIENNIK_PASS")),
            "vlog": _rd("mont_vlog.json", {"helper": None, "jobs": []}),
            "error": None if JOBS.is_dir() else f"Nie widzę montaży Dziennika w {JOBS}"}


@router.post("/api/mont-clean")
async def mont_clean(req: Request):
    """Usuwa nieudane próby montażu (vlog/przepis z listy, jedzenie – ukrywa) razem z ich powiadomieniami.
    {"vjob": id} | {"job": id} | {"all_failed": true}"""
    b = await req.json()
    vj, fj = set(), set()
    with _vl:
        v = _V()
        for j in v["jobs"]:
            if j["state"] == "error" and (b.get("all_failed") or j["id"] == b.get("vjob")):
                vj.add(j["id"])
        v["jobs"] = [j for j in v["jobs"] if j["id"] not in vj]
        _wr("mont_vlog.json", v)
    for jid in vj:
        shutil.rmtree(VUP / os.path.basename(jid), ignore_errors=True)
    if JOBS.is_dir():
        for d in JOBS.iterdir():
            if d.is_dir() and JID.match(d.name) and (b.get("all_failed") or d.name == b.get("job")) and _status(d).get("state") == "error":
                fj.add(d.name)
    if fj:
        _wr("mont_hidden.json", sorted({*_rd("mont_hidden.json", []), *fj})[-300:])
    from social import _read as sr, _write as sw
    N = sr("notifs.json", [])
    keep = [n for n in N if not ((n.get("data") or {}).get("vjob") in vj or (n.get("data") or {}).get("job") in fj
                                 or (b.get("all_failed") and n.get("kind") == "montaz" and str(n.get("title", "")).startswith("❌")))]
    sw("notifs.json", keep)
    return {"ok": True, "vlog": len(vj), "food": len(fj), "notifs": len(N) - len(keep)}


@router.get("/api/mont/{jid}")
def job(jid: str):
    d = _job(jid)
    if not d:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    st = _status(d)
    day = next(d.glob("dzien_*"), None)
    if day and (day / "raport.txt").exists():
        st["raport"] = (day / "raport.txt").read_text(encoding="utf-8")[:4000]
    if day and (day / "transkrypcja.txt").exists():
        st["transkrypcja"] = (day / "transkrypcja.txt").read_text(encoding="utf-8")[:4000]
    st["check"] = _rd("mont_checks.json", {}).get(jid)
    return st


@router.get("/api/mont/{jid}/film")
def film(jid: str, dl: int = 0):
    d = _job(jid)
    f = d and _film(d, _status(d))
    if not f:
        return JSONResponse({"error": "Film nie jest gotowy."}, status_code=404)
    return FileResponse(f, media_type="video/mp4", filename=f.name if dl else None)


# ---------- akcje w Dzienniku (przez jego API, z hasłem z .env) ----------
def _dz_call(method, path, **kw):
    pw = os.environ.get("DZIENNIK_PASS")
    if not pw:
        raise RuntimeError("Dopisz DZIENNIK_PASS=<hasło do dziennika> do /root/social/.env i zrób social-update.")
    for attempt in range(2):
        if not _dz["cookie"]:
            r = httpx.post(DZ_LOCAL + "/api/login", json={"password": pw}, timeout=15)
            if r.status_code >= 300 or "dz" not in r.cookies:
                raise RuntimeError("Dziennik nie przyjął hasła (DZIENNIK_PASS).")
            _dz["cookie"] = r.cookies["dz"]
        r = httpx.request(method, DZ_LOCAL + path, cookies={"dz": _dz["cookie"]}, timeout=60,
                          headers={"x-forwarded-proto": "https"}, **kw)
        if r.status_code == 401 and attempt == 0:
            _dz["cookie"] = None
            continue
        return r
    return r


@router.post("/api/mont/{jid}/{action}")
async def act(jid: str, action: str, req: Request):
    if not _job(jid) or action not in ("redo", "fix", "check", "start"):
        return JSONResponse({"error": "nie ma"}, status_code=404)
    if action == "check":
        C = _rd("mont_checks.json", {})
        C.pop(jid, None)
        _wr("mont_checks.json", C)
        return check(jid) or {"error": "Film nie jest gotowy."}
    if action in ("redo", "start"):
        C = _rd("mont_checks.json", {})
        C.pop(jid, None)
        _wr("mont_checks.json", C)
        return start(jid)
    try:
        if False:
            pass
        else:
            b = await req.json()
            r = _dz_call("POST", f"/api/montaz/{jid}/fix", json={"comment": str(b.get("comment", ""))[:1500]})
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    ok = r.status_code < 300
    if ok:
        C = _rd("mont_checks.json", {})
        C.pop(jid, None)
        _wr("mont_checks.json", C)
    try:
        err = None if ok else (r.json().get("error") or r.text)[:200]
    except Exception:
        err = r.text[:200]
    return {"ok": ok, "error": err}


# ---------- nowy montaż prosto z centrum (silnik montażu Dziennika na tym samym serwerze) ----------
import secrets, shutil, sys, zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

CLIP, CARD = {".mov", ".mp4", ".m4v"}, {".png", ".jpg", ".jpeg"}
DZ_APP = DZ / "src" / "app"
START_DAY = os.environ.get("MONTAZ_START", "2026-09-08")


def _env():
    env = dict(os.environ)
    for f in (DZ / ".env",):
        try:
            for line in f.read_text().splitlines():
                if line.startswith("GROQ_API_KEY="):
                    env["GROQ_API_KEY"] = line.split("=", 1)[1].strip()
        except Exception:
            pass
    env["MONTAZ_MUSIC"] = str(DZ / "montaz" / "muzyka_tlo.mp3")
    env["MONTAZ_FONT"] = str(DZ / "montaz" / "Montserrat.ttf")
    env["DZ_HOME"] = str(DZ)
    return env


@router.get("/api/mont-suggest")
def suggest(d: str = ""):
    """Podpowiedź tytułu jak w Dzienniku: „Redukcja dzień N” / opis / dzień (nie)treningowy."""
    try:
        day = date.fromisoformat(d) if d else datetime.now(ZoneInfo("Europe/Warsaw")).date()
    except ValueError:
        day = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    n = (day - date.fromisoformat(START_DAY)).days + 1
    train = day.weekday() in (0, 2, 5)
    return {"date": day.isoformat(), "n": n, "dziennik": DZ_URL,
            "title": [f"Redukcja dzień {n}", "2100 kcal" if train else "2000 kcal", "dzień treningowy" if train else "dzień nietreningowy"]}


@router.post("/api/mont-new")
async def new(req: Request):
    b = await req.json()
    title = [str(x).strip()[:60] for x in (b.get("title") or []) if str(x).strip()][:4]
    n = int(b.get("n") or 0)
    if not title:
        return JSONResponse({"error": "Wpisz tytuł."}, status_code=400)
    if not (DZ_APP / "montaz_job.py").exists():
        return JSONResponse({"error": f"Nie widzę silnika montażu Dziennika ({DZ_APP})."}, status_code=500)
    jid = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    day = JOBS / jid / f"dzien_{n}"
    day.mkdir(parents=True)
    (JOBS / jid / "title.json").write_text(json.dumps(title, ensure_ascii=False))
    (JOBS / jid / "status.json").write_text(json.dumps({"state": "uploading", "step": "Wysyłanie klipów…", "src": "centrum",
                                                       "autotitle": bool(b.get("autotitle")),
                                                       "date": str(b.get("date", ""))[:10], "at": time.time()}))
    # miejsce na dysku: klipy zostają tylko w 2 ostatnich zadaniach (gotowe filmy zostają)
    for d in sorted(x for x in JOBS.iterdir() if x.is_dir() and JID.match(x.name))[:-3]:
        for dd in d.glob("dzien_*"):
            for f in dd.iterdir():
                if f.suffix.lower() in CLIP:
                    f.unlink(missing_ok=True)
    return {"id": jid}


@router.put("/api/mont/{jid}/file")
async def upload(jid: str, req: Request, kind: str = "clip", i: int = 0, name: str = ""):
    d = _job(jid)
    if not d:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    day = next(d.glob("dzien_*"))
    ext = Path(name).suffix.lower()
    if ext not in (CLIP if kind == "clip" else CARD):
        return JSONResponse({"error": "Nieobsługiwany plik: " + name[:40]}, status_code=400)
    fn = f"klip_{i:02d}{ext}" if kind == "clip" else (re.sub(r"[^a-z0-9ąćęłńóśźż-]+", "-", Path(name).stem.lower())[:60].strip("-") or f"karta-{i}") + ext
    tmp = day / (fn + ".part")
    with tmp.open("wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    tmp.replace(day / fn)
    return {"ok": True, "name": fn, "size": (day / fn).stat().st_size}


def start(jid):
    d = _job(jid)
    st = _status(d)
    if st.get("state") == "working":
        return {"ok": True}
    day = next(d.glob("dzien_*"))
    if not any(f.suffix.lower() in CLIP for f in day.iterdir()):
        return {"ok": False, "error": "Nie ma żadnego klipu (stare klipy są usuwane – wgraj je jeszcze raz w nowym montażu)."}
    env = _env()
    if not env.get("GROQ_API_KEY"):
        return {"ok": False, "error": "Brak klucza Groq w /root/dziennik/.env (potrzebny do transkrypcji)."}
    (day / "opis.txt").write_text("\n".join(json.loads((d / "title.json").read_text())) + "\n", encoding="utf-8")
    ko = day / "kolejnosc.txt"
    if ko.exists() and ko.read_text(encoding="utf-8").startswith(("# AUTO", "# RĘCZNY")):
        ko.unlink()
    (day / "raport.txt").unlink(missing_ok=True)
    shutil.rmtree(d / "gotowe", ignore_errors=True)
    keep = {k: v for k, v in st.items() if k in ("date", "src")}
    keep.update(state="working", step="Start…", at=time.time())
    (d / "status.json").write_text(json.dumps(keep, ensure_ascii=False))
    py = DZ / "venv" / "bin" / "python"
    subprocess.Popen(["nice", "-n", "15", str(py if py.exists() else sys.executable), str(DZ_APP / "montaz_job.py"), str(d)], env=env,
                     cwd=str(DZ_APP), stdout=subprocess.DEVNULL, stderr=open(d / "job_err.txt", "w"), start_new_session=True)
    return {"ok": True}


@router.get("/api/mont/{jid}/capcut")
def capcut(jid: str):
    """Paczka do ręcznej poprawki w CapCut: klipy w kolejności montażu, karty, muzyka, napisy i raport."""
    d = _job(jid)
    if not d:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    day = next(d.glob("dzien_*"))
    clips = sorted(f for f in day.iterdir() if f.suffix.lower() in CLIP)
    if not clips:
        return JSONResponse({"error": "Klipy tego montażu zostały już usunięte z serwera (zostają tylko w 2 ostatnich)."}, status_code=410)
    order = []
    ko = day / "kolejnosc.txt"
    if ko.exists():
        for line in ko.read_text(encoding="utf-8").splitlines():
            t = line.split("#")[0].split()
            if t and (day / t[0]).exists() and t[0] not in order:
                order.append(t[0])
    order += [c.name for c in clips if c.name not in order]
    out = d / "capcut.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for k, n in enumerate(order, 1):
            z.write(day / n, f"{k:02d}_{n}")
        for f in day.iterdir():
            if f.suffix.lower() in CARD:
                z.write(f, "karty/" + f.name)
        for f, arc in ((DZ / "montaz" / "muzyka_tlo.mp3", "muzyka_tlo.mp3"), (day / "raport.txt", "raport.txt"),
                       (day / "transkrypcja.txt", "transkrypcja.txt"), (day / "kolejnosc.txt", "plan_ciec.txt")):
            if f.exists():
                z.write(f, arc)
        z.writestr("JAK_POPRAWIC.txt", "1. Rozpakuj paczkę i otwórz CapCut → Nowy projekt (9:16).\n"
                   "2. Przeciągnij klipy 01, 02, 03… w tej kolejności na oś czasu.\n"
                   "3. plan_ciec.txt pokazuje, co montaż wyciął (od= / do= / wytnij= w sekundach), raport.txt – dlaczego.\n"
                   "4. Karty posiłków są w folderze karty/, muzyka: muzyka_tlo.mp3 (cicho w tle).\n")
    return FileResponse(out, media_type="application/zip", filename=f"capcut_{jid}.zip")


# ---------- strażnik montażu ----------
def _probe(f):
    probs, warns = [], []
    try:
        pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
                                        "-of", "json", str(f)], capture_output=True, text=True, timeout=60).stdout)
    except Exception as e:
        return 0, [f"nie da się odczytać pliku: {e}"], []
    dur = float(pr.get("format", {}).get("duration") or 0)
    v = next((s for s in pr.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in pr.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        probs.append("brak obrazu")
    elif (v.get("width"), v.get("height")) != (1080, 1920):
        warns.append(f"rozdzielczość {v.get('width')}x{v.get('height')} (pion 1080x1920 najlepszy)")
    if not a:
        probs.append("brak dźwięku")
    if dur < 10:
        probs.append(f"film za krótki ({dur:.0f} s)")
    elif dur > 180:
        warns.append(f"długi film ({dur:.0f} s) – na TikToka/Reels lepiej do 90 s")
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(f), "-vf", "blackdetect=d=0.8:pix_th=0.10",
                          "-af", "silencedetect=noise=-45dB:d=2.5", "-f", "null", "-"], capture_output=True, text=True, timeout=600).stderr
    blacks = [float(m) for m in re.findall(r"black_start:([\d.]+)", out) if 0.3 < float(m) < dur - 1.5]
    if blacks:
        probs.append("czarny ekran od " + ", ".join(f"{b:.1f}s" for b in blacks[:4]))
    sil = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", out) if float(m) < dur - 3]
    if sil:
        warns.append("cisza od " + ", ".join(f"{s:.1f}s" for s in sil[:4]))
    return dur, probs, warns


# ---------- nauka strażnika: wzorce z Twoich gotowych filmów ----------
def measure(f, ff="ffmpeg"):
    """Głośność (LUFS), tempo cięć (zmian ujęć na minutę), cisza i długość – te same liczby liczy pomocnik na Macu."""
    out = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(f), "-vf", "scale=160:-2,select='gt(scene,0.3)',showinfo",
                          "-af", "ebur128,silencedetect=noise=-45dB:d=2.5", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=1800).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", out)
    cuts = len(re.findall(r"pts_time:", out))
    sil = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", out))
    return {"dur": round(dur, 1), "lufs": float(lufs[-1]) if lufs else None, "cpm": round(cuts / dur * 60, 1) if dur else 0,
            "sil": round(sil / dur * 100, 1) if dur else 0}


def _q(vals, p):
    v = sorted(x for x in vals if x is not None)
    return v[min(len(v) - 1, int(p * (len(v) - 1) + .5))] if v else None


def baseline(kind):
    W = [x["m"] for x in _rd("mont_wzorce.json", {}).get(kind, [])]
    if len(W) < 3:
        return None
    return {k: (_q([w.get(k) for w in W], .1), _q([w.get(k) for w in W], .5), _q([w.get(k) for w in W], .9)) for k in ("dur", "lufs", "cpm", "sil")} | {"n": len(W)}


def compare(m, kind):
    """Uwagi, gdy film wyraźnie odstaje od Twoich gotowych filmów tego rodzaju."""
    b = baseline(kind)
    if not b:
        return [], None
    w = []
    lo, med, hi = b["lufs"]
    if m.get("lufs") is not None and med is not None and abs(m["lufs"] - med) > 3:
        w.append(f"głośność {m['lufs']:.0f} LUFS – Twoje gotowe filmy mają ok. {med:.0f} ({'za cicho' if m['lufs'] < med else 'za głośno'})")
    lo, med, hi = b["cpm"]
    if med and m.get("cpm") is not None and (m["cpm"] < lo * .7 or m["cpm"] > hi * 1.3):
        w.append(f"tempo {m['cpm']:.0f} cięć/min – zwykle {lo:.0f}–{hi:.0f} ({'wolniej' if m['cpm'] < lo else 'szybciej'} niż Twoje filmy)")
    lo, med, hi = b["dur"]
    if med and (m["dur"] < lo * .7 or m["dur"] > hi * 1.3):
        w.append(f"długość {m['dur']:.0f} s – zwykle {lo:.0f}–{hi:.0f} s")
    lo, med, hi = b["sil"]
    if m.get("sil", 0) > max(5, (hi or 0) * 1.5):
        w.append(f"cisza przez {m['sil']:.0f}% filmu – w gotowych zwykle do {hi:.0f}%")
    return w, b


def _btxt(b):
    return (f"Wzorzec z {b['n']} Twoich gotowych filmów: długość {b['dur'][0]:.0f}–{b['dur'][2]:.0f} s, głośność ok. {b['lufs'][1]:.0f} LUFS, "
            f"{b['cpm'][0]:.0f}–{b['cpm'][2]:.0f} cięć/min.") if b else ""


@router.post("/api/mont-hook/wzorzec")
async def hook_wzorzec(req: Request):
    """Pomocnik na Macu zgłasza gotowy film (vlog WNBF albo jedzenie) – strażnik uczy się z niego i sprawdza nowe eksporty."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    kind = "vlog" if b.get("kind") == "vlog" else "jedzenie"
    name, m, mt = str(b.get("name", ""))[:120], b.get("m") or {}, float(b.get("mtime") or 0)
    if not m.get("dur"):
        return {"ok": False}
    W = _rd("mont_wzorce.json", {})
    L = W.setdefault(kind, [])
    if any(x["name"] == name for x in L):
        return {"ok": True, "known": True}
    warns, base = compare(m, kind)
    L.append({"name": name, "m": m, "mtime": mt, "at": time.time()})
    W[kind] = L[-60:]
    _wr("mont_wzorce.json", W)
    if kind == "vlog" and base and time.time() - mt < 3 * 86400:  # świeży eksport – raport strażnika
        from extra import push_all
        V = _rd("mont_vlog_checks.json", [])
        V.append({"name": name, "m": m, "warnings": warns, "at": time.time()})
        _wr("mont_vlog_checks.json", V[-30:])
        push_all("🎬 Strażnik: " + name + (" – OK" if not warns else " – uwagi"),
                 "Zgodny z Twoimi gotowymi vlogami." if not warns else "; ".join(warns)[:170], "/#montazownia", kind="montaz")
    return {"ok": True, "warnings": warns}


from social import ROOT as SOCIAL_ROOT  # noqa: E402
VFILM = SOCIAL_ROOT / "vlog_films"


@router.put("/api/mont-hook/film")
async def hook_film(req: Request, name: str = "", kind: str = "vlog", mtime: float = 0):
    """Mac wysyła lekką kopię (720p) gotowego, wyeksportowanego vloga/przepisu – do obejrzenia w Montażowni."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    nm = re.sub(r"[^\w .()#-]+", "_", os.path.splitext(os.path.basename(name))[0])[:100] or "film"
    VFILM.mkdir(parents=True, exist_ok=True)
    tmp = VFILM / (nm + ".part")
    with tmp.open("wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    if tmp.stat().st_size < 10000:
        tmp.unlink(missing_ok=True)
        return {"ok": False, "error": "pusty plik"}
    tmp.replace(VFILM / (nm + ".mp4"))
    M = _rd("mont_vlog_films.json", {})
    M[nm] = {"name": nm, "kind": kind if kind in ("vlog", "przepis") else "vlog", "mtime": mtime or time.time(), "at": time.time()}
    keep = sorted(M.values(), key=lambda x: -x["mtime"])[:20]   # 20 najnowszych, starsze kasujemy
    for x in M.values():
        if x not in keep:
            (VFILM / (x["name"] + ".mp4")).unlink(missing_ok=True)
    _wr("mont_vlog_films.json", {x["name"]: x for x in keep})
    return {"ok": True}


VPOD = SOCIAL_ROOT / "vlog_podglad"


@router.post("/api/mont-hook/{jid}/raport")
async def hook_raport(jid: str, req: Request):
    """Mac po montażu: co zostało / co wycięte, napisy, uwagi z montażu – do oceny strażnika."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    with _vl:
        v = _V()
        j = _vjob(v, jid)
        if j:
            j["raport"] = str(b.get("raport", ""))[:6000]
            j["napisy"] = str(b.get("napisy", ""))[:6000]
            _wr("mont_vlog.json", v)
    return {"ok": True}


@router.put("/api/mont-hook/{jid}/podglad")
async def hook_podglad(jid: str, req: Request):
    """Podgląd zmontowanego vloga (540x960, jak projekt w CapCut) – do obejrzenia i sprawdzenia przez strażnika."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    VPOD.mkdir(parents=True, exist_ok=True)
    f = VPOD / (os.path.basename(jid) + ".mp4")
    tmp = f.with_suffix(".part")
    with tmp.open("wb") as fh:
        async for chunk in req.stream():
            fh.write(chunk)
    if tmp.stat().st_size < 10000:
        tmp.unlink(missing_ok=True)
        return {"ok": False}
    tmp.replace(f)
    for old in sorted(VPOD.glob("*.mp4"), key=lambda x: x.stat().st_mtime)[:-12]:   # 12 ostatnich podglądów
        old.unlink(missing_ok=True)
    with _vl:
        v = _V()
        j = _vjob(v, jid)
        if j:
            j["podglad"] = True
            j["check"] = None
            _wr("mont_vlog.json", v)
    threading.Thread(target=vlog_check, args=(jid,), daemon=True).start()
    return {"ok": True}


@router.get("/api/mont-vlog/{jid}/podglad")
def vlog_podglad(jid: str):
    f = VPOD / (os.path.basename(jid) + ".mp4")
    return FileResponse(f, media_type="video/mp4") if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)


def vlog_check(jid):
    """Strażnik montażu dla vloga: technika podglądu (czarne klatki, cisza, długość) + ocena AI cięć i napisów."""
    f = VPOD / (os.path.basename(jid) + ".mp4")
    j = _vjob(_V(), jid) or {}
    try:
        dur, probs, warns = _probe(f)
    except Exception as e:
        dur, probs, warns = 0, [], [f"nie udało się sprawdzić podglądu ({str(e)[:60]})"]
    res = {"at": time.time(), "duration": round(dur), "problems": probs, "warnings": warns, "ok": not probs, "review": "", "fix": ""}
    try:
        from agency import ai_ready, claude, as_json
        if ai_ready() and (j.get("raport") or j.get("napisy")):
            txt = claude("Jesteś strażnikiem montażu vloga „Daily WNBF” (codzienny vlog fit po polsku: posiłki, trening, kardio, "
                         "na końcu podsumowanie dnia z planszą liczb). Dostajesz raport automatycznego montażu: które zdania zostały, "
                         "które wycięto (i dlaczego) oraz napisy w kolejności. Sprawdź: czy nie wycięto czegoś ważnego (liczby, posiłki, "
                         "podsumowanie), czy nie zostały dwa podejścia do tego samego zdania, czy napisy nie mają dziur/błędów w liczbach, "
                         "czy kolejność ma sens.\n\n"
                         f"Odcinek #{j.get('ep')} z {j.get('date')}, długość podglądu {dur:.0f} s\n\nRAPORT:\n{j.get('raport', '')[:3500]}\n\n"
                         f"NAPISY:\n{j.get('napisy', '')[:3000]}\n\n"
                         'Odpowiedz JSON: {"ok": true/false, "review": "2-3 zdania oceny po polsku", '
                         '"fix": "jeśli coś trzeba poprawić – polecenie dla montażu po polsku (np. zostaw zdanie o krokach, wytnij drugie podejście do zdania o obiedzie), inaczej pusty"}',
                         max_tokens=500)
            a = as_json(txt) or {}
            res["review"] = str(a.get("review", ""))[:600]
            res["fix"] = str(a.get("fix", ""))[:600]
            if a.get("ok") is False and res["fix"]:
                res["ok"] = False
    except Exception as e:
        res["review"] = f"(ocena AI niedostępna: {str(e)[:80]})"
    with _vl:
        v = _V()
        jj = _vjob(v, jid)
        if jj:
            jj["check"] = res
            _wr("mont_vlog.json", v)
    try:
        from notes import auto_note
        nm = ("Przepis" if j.get("kind") == "przepis" else "Vlog") + f" #{j.get('ep')}"
        auto_note("🎥 Vlog", f"{nm} – {j.get('date')}", f"# {nm} ({j.get('date')})\n\n**Strażnik:** {'OK' if res['ok'] and not res['warnings'] else 'są uwagi'} · {res['duration']} s\n\n"
                  + "\n".join(f"- {x}" for x in res["problems"] + res["warnings"]) + (f"\n\n{res['review']}" if res["review"] else "")
                  + (f"\n\n**Sugerowana poprawka:** {res['fix']}" if res["fix"] else "") + (f"\n\n**Poprawka zlecona:** {j.get('fix')}" if j.get("fix") else "")
                  + "\n\n## Moje notatki do odcinka\n\n#vlog")
    except Exception:
        pass
    try:
        from extra import push_all
        nm = ("Przepis" if j.get("kind") == "przepis" else "Vlog") + f" #{j.get('ep')}"
        push_all(f"🛡️ Strażnik: {nm} – " + ("OK" if res["ok"] and not res["warnings"] else "są uwagi"),
                 (res["review"] or "; ".join(res["problems"] + res["warnings"]) or "Podgląd gotowy w Montażowni.")[:170],
                 "/#montazownia", kind="montaz", data={"vjob": jid})
    except Exception:
        pass
    return res


@router.post("/api/mont-vlog/{jid}/check")
def vlog_recheck(jid: str):
    threading.Thread(target=vlog_check, args=(jid,), daemon=True).start()
    return {"ok": True}


@router.get("/api/mont-vlog/films")
def vlog_films():
    M = _rd("mont_vlog_films.json", {})
    W = {x["name"]: x.get("m") for x in _rd("mont_wzorce.json", {}).get("vlog", [])}
    C = {x["name"]: x.get("warnings") for x in _rd("mont_vlog_checks.json", [])}
    out = []
    for x in sorted(M.values(), key=lambda x: -x["mtime"]):
        f = VFILM / (x["name"] + ".mp4")
        if f.is_file():
            out.append({**x, "size": f.stat().st_size, "m": W.get(x["name"]), "warnings": C.get(x["name"])})
    return {"films": out}


@router.get("/api/mont-vlog/film/{name}")
def vlog_film(name: str):
    f = VFILM / (os.path.basename(name) + ".mp4")
    return FileResponse(f, media_type="video/mp4") if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)


@router.get("/api/mont-wzorce")
def wzorce():
    W = _rd("mont_wzorce.json", {})
    return {k: {"n": len(W.get(k, [])), "base": baseline(k), "names": [x["name"] for x in W.get(k, [])][-8:]} for k in ("vlog", "jedzenie")} | \
        {"vlog_checks": _rd("mont_vlog_checks.json", [])[-6:][::-1]}


def check(jid):
    d = _job(jid)
    st = _status(d) if d else {}
    f = d and _film(d, st)
    if not f:
        return None
    dur, probs, warns = _probe(f)
    try:
        mm = measure(f)
        w2, base = compare(mm, "jedzenie")
        warns += w2
    except Exception:
        mm, base = {}, None
    res = {"at": time.time(), "duration": round(dur), "problems": probs, "warnings": warns, "ok": not probs, "review": "", "fix": ""}
    day = next(d.glob("dzien_*"), None)
    rap = (day / "raport.txt").read_text(encoding="utf-8")[:3000] if day and (day / "raport.txt").exists() else ""
    tr = (day / "transkrypcja.txt").read_text(encoding="utf-8")[:3000] if day and (day / "transkrypcja.txt").exists() else ""
    try:
        from agency import ai_ready, claude, as_json
        if ai_ready() and (rap or tr):
            txt = claude("Jesteś strażnikiem montażu krótkich filmów „daily jedzenie” (TikTok/Reels, po polsku). "
                         "Dostajesz raport automatycznych cięć i transkrypcję klipów. Sprawdź: czy nie zostały powtórzone zdania, "
                         "czy nie wycięto czegoś ważnego, czy kolejność posiłków ma sens, czy są przekleństwa/pomyłki.\n\n"
                         f"Tytuł: {' / '.join(st.get('title') or [])}\nDługość: {dur:.0f} s\n{_btxt(base)}\n\nRAPORT:\n{rap}\n\nTRANSKRYPCJA:\n{tr}\n\n"
                         'Odpowiedz JSON: {"ok": true/false, "review": "2-3 zdania oceny po polsku", '
                         '"fix": "jeśli coś trzeba poprawić – polecenie dla montażysty po polsku (np. wytnij w klipie 2 drugie podejście do zdania o obiedzie), inaczej pusty"}',
                         max_tokens=500)
            j = as_json(txt) or {}
            res["review"] = str(j.get("review", ""))[:600]
            res["fix"] = str(j.get("fix", ""))[:600]
            if j.get("ok") is False and res["fix"]:
                res["ok"] = False
    except Exception as e:
        res["review"] = f"(ocena AI niedostępna: {str(e)[:80]})"
    res["m"] = mm
    C = _rd("mont_checks.json", {})
    C[jid] = res
    _wr("mont_checks.json", dict(sorted(C.items())[-40:]))
    return res


def _watch():
    from extra import push_all
    time.sleep(90)
    while True:
        try:
            C = _rd("mont_checks.json", {})
            seen = _rd("mont_seen.json", {})
            if JOBS.is_dir():
                for d in sorted(x for x in JOBS.iterdir() if x.is_dir() and JID.match(x.name))[-5:]:
                    st = _status(d)
                    key = f"{st.get('state')}|{st.get('film', '')}|{st.get('at', 0):.0f}"
                    if st.get("state") == "done" and d.name not in C:
                        r = check(d.name)
                        if r and seen.get(d.name) != key:
                            t = " / ".join(st.get("title") or []) or d.name
                            push_all("🎬 Film gotowy" + (" – strażnik: OK" if r["ok"] else " – strażnik ma uwagi"),
                                     f"{t} · {r['duration']} s" + ("" if r["ok"] else " · " + "; ".join(r["problems"] or [r["fix"]])[:120]),
                                     "/#montazownia", kind="montaz", data={"job": d.name})
                    elif st.get("state") == "error" and seen.get(d.name) != key and seen.get(d.name) is not None:
                        push_all("❌ Montaż nie wyszedł", str(st.get("error", ""))[:150], "/#montazownia", kind="montaz", data={"job": d.name})
                    seen[d.name] = key
            _wr("mont_seen.json", dict(sorted(seen.items())[-60:]))
        except Exception as e:
            print("montaz watch", e, flush=True)
        time.sleep(60)


threading.Thread(target=_watch, daemon=True).start()


# ---------- vlog WNBF: zlecenia dla pomocnika na Macu (montaż robi niezmieniony zmontuj.py w CapCut) ----------
from social import ROOT as SOCIAL_ROOT

VUP = SOCIAL_ROOT / "vlog_upload"
VCLIP = {".mov", ".mp4", ".m4v", ".png", ".jpg", ".jpeg"}
_vl = threading.Lock()


def _V():
    v = _rd("mont_vlog.json", {})
    v.setdefault("helper", {})
    v.setdefault("jobs", [])
    return v


def _vjob(v, jid):
    return next((j for j in v["jobs"] if j["id"] == jid), None)


def _tok(req: Request):
    t, want = req.headers.get("x-mont-token", ""), os.environ.get("MONT_TOKEN", "")
    return bool(want) and secrets.compare_digest(t, want)


@router.get("/api/mont-vlog/state")
def vlog_state():
    v = _V()
    h = v["helper"]
    online = bool(h.get("seen")) and time.time() - h["seen"] < 90
    return {"configured": bool(os.environ.get("MONT_TOKEN")), "online": online, "seen": h.get("seen"),
            "next_ep": (h.get("last") or 0) + 1, "loose": h.get("loose", 0),
            "next_przepis": (h.get("plast") or 0) + 1, "ploose": h.get("ploose", 0), "disk": _rd("mac_disk.json", {}),
            "jobs": [{k: x for k, x in j.items() if k not in ("log_full", "raport", "napisy")} for j in v["jobs"][-12:][::-1]]}


@router.post("/api/mont-vlog/new")
async def vlog_new(req: Request):
    b = await req.json()
    with _vl:
        v = _V()
        if any(j["state"] in ("queued", "uploading", "working", "ask") for j in v["jobs"]):
            return {"ok": False, "error": "Poprzedni montaż vloga jeszcze trwa."}
        jid = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        j = {"id": jid, "kind": "przepis" if b.get("kind") == "przepis" else "vlog", "title": str(b.get("title") or "")[:120],
             "ep": int(b.get("ep") or 0), "date": str(b.get("date") or "")[:10], "voiceover": bool(b.get("voiceover")),
             "state": "uploading" if b.get("upload") else "queued", "step": "Wysyłanie klipów…" if b.get("upload") else "Czekam na Maca…",
             "files": [], "lines": [], "at": time.time()}
        if j["kind"] == "przepis" and not j["date"]:
            j["date"] = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
        if j["ep"] <= 0 or not re.match(r"^\d{4}-\d{2}-\d{2}$", j["date"]):
            return {"ok": False, "error": "Podaj numer odcinka i datę."}
        v["jobs"] = v["jobs"][-30:] + [j]
        _wr("mont_vlog.json", v)
    return {"ok": True, "id": jid}


@router.put("/api/mont-vlog/{jid}/file")
async def vlog_file(jid: str, req: Request, name: str = ""):
    name = os.path.basename(name)
    if Path(name).suffix.lower() not in VCLIP:
        return JSONResponse({"error": "Nieobsługiwany plik: " + name[:40]}, status_code=400)
    d = VUP / jid
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".part")
    with tmp.open("wb") as f:
        async for chunk in req.stream():
            f.write(chunk)
    tmp.replace(d / name)
    with _vl:
        v = _V()
        j = _vjob(v, jid)
        if j and name not in j["files"]:
            j["files"].append(name)
            _wr("mont_vlog.json", v)
    return {"ok": True}


@router.post("/api/mont-vlog/{jid}/{action}")
async def vlog_act(jid: str, action: str, req: Request):
    b = await req.json() if action in ("answer", "fix") else {}
    with _vl:
        v = _V()
        j = _vjob(v, jid)
        if not j:
            return {"ok": False, "error": "nie ma"}
        if action == "go":
            j.update(state="queued", step="Czekam na Maca…")
        elif action == "answer":
            j.update(answer={"choice": b.get("choice")}, state="working", step="Zapowiedź wybrana – montuję dalej")
        elif action == "again" and j["state"] in ("done", "error"):
            # ponowny montaż z klipów, które już leżą na Macu w „odc N” (bez wysyłania jeszcze raz)
            if any(x["state"] in ("queued", "uploading", "claimed", "working", "ask") for x in v["jobs"]):
                return {"ok": False, "error": "Poprzedni montaż jeszcze trwa."}
            nj = {k: j.get(k) for k in ("kind", "title", "ep", "date", "voiceover")}
            nj.update(id=datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2),
                      state="queued", step="Montuję ponownie – czekam na Maca…", files=[], lines=[], at=time.time(), again=jid)
            v["jobs"] = v["jobs"][-30:] + [nj]
        elif action == "fix" and j["state"] in ("done", "error") and str(b.get("comment", "")).strip():
            if any(x["state"] in ("queued", "uploading", "claimed", "working", "ask") for x in v["jobs"]):
                return {"ok": False, "error": "Poprzedni montaż jeszcze trwa."}
            nj = {k: j.get(k) for k in ("kind", "title", "ep", "date", "voiceover")}
            nj.update(id=datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2),
                      state="queued", step="Poprawka – czekam na Maca…", files=[], lines=[], at=time.time(), again=jid,
                      fix=str(b["comment"]).strip()[:600])
            v["jobs"] = v["jobs"][-30:] + [nj]
        elif action == "retry" and j["state"] == "error":
            if not all((VUP / jid / f).is_file() and (VUP / jid / f).stat().st_size for f in j.get("files", [])):
                return {"ok": False, "error": "Klipów tego zlecenia nie ma już w centrum – wyślij je jeszcze raz"}
            j.update(state="queued", step="Ponawiam – czekam na Maca…", ask=None, answer=None, previews=[])
        elif action == "cancel" and j["state"] in ("queued", "uploading"):
            j.update(state="error", step="Anulowano")
        _wr("mont_vlog.json", v)
    return {"ok": True}


# — wywoływane przez pomocnika na Macu (token X-Mont-Token) —
@router.get("/api/mont-hook/next")
def hook_next(req: Request, last: int = 0, loose: int = 0, plast: int = 0, ploose: int = 0):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    with _vl:
        v = _V()
        v["helper"] = {"seen": time.time(), "last": last, "loose": loose, "plast": plast, "ploose": ploose}
        j = next((x for x in v["jobs"] if x["state"] == "queued"), None)
        if j:
            j.update(state="claimed", step="Mac otwiera montaż…", claimed=time.time())
        _wr("mont_vlog.json", v)
    return {"job": j and {"id": j["id"]}}


@router.get("/api/mont-hook/{jid}/job")
def hook_job(jid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    j = _vjob(_V(), jid)
    return j and {k: j.get(k) for k in ("id", "kind", "title", "ep", "date", "voiceover", "files", "fix")}


@router.get("/api/mont-hook/{jid}/file")
def hook_file(jid: str, req: Request, name: str = ""):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    f = VUP / os.path.basename(jid) / os.path.basename(name)
    return FileResponse(f) if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)


@router.post("/api/mont-hook/{jid}/{what}")
async def hook_post(jid: str, what: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    push = None
    with _vl:
        v = _V()
        j = _vjob(v, jid)
        if not j:
            return {"ok": False}
        if what == "status":
            j["lines"] = (j.get("lines", []) + [str(x)[:300] for x in b.get("lines") or []])[-80:]
            for k in ("state", "step"):
                if b.get(k):
                    j[k] = str(b[k])[:200]
            if b.get("log"):
                j["log_full"] = str(b["log"])[-6000:]
            j["at"] = time.time()
            if b.get("state") in ("done", "error"):
                j["finished"] = time.time()
                push = j
                if b.get("state") == "done":  # przy błędzie klipy zostają (do 3 dni) – można ponowić bez wysyłania jeszcze raz
                    shutil.rmtree(VUP / jid, ignore_errors=True)
                for old in (VUP.iterdir() if VUP.is_dir() else []):
                    if old.is_dir() and time.time() - old.stat().st_mtime > 3 * 86400:
                        shutil.rmtree(old, ignore_errors=True)
        elif what == "ask":
            j.update(ask=[str(t)[:200] for t in (b.get("texts") or [])][:12], state="ask", step="Wybierz zapowiedź", answer=None,
                     previews=sorted(int(i) for i in (b.get("previews") or []) if str(i).isdigit()))
            push = "ask"
        _wr("mont_vlog.json", v)
    if push:
        from extra import push_all
        if push == "ask":
            push_all("🎬 Wybierz zapowiedź vloga", f"Odcinek #{j['ep']}: które zdanie dać na początek?", "/#montazownia", kind="montaz")
        else:
            ok = j["state"] == "done"
            res = next((l for l in reversed(j["lines"]) if l.startswith("GOTOWE")), "")
            nm = "Przepis" if j.get("kind") == "przepis" else "Vlog"
            push_all(f"🎥 {nm} gotowy w CapCut" if ok else f"❌ Montaż – {nm.lower()} nie wyszedł", (res or j["step"])[:150], "/#montazownia", kind="montaz", data={"vjob": j["id"]})
    return {"ok": True}


VPRE = SOCIAL_ROOT / "vlog_preview"


@router.put("/api/mont-hook/{jid}/preview")
async def hook_preview(jid: str, req: Request, i: int = 0):
    """Krótki podgląd kandydata na zapowiedź (mp4 360p z Maca) – do wyboru w Montażowni."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    VPRE.mkdir(parents=True, exist_ok=True)
    for old in sorted(VPRE.iterdir())[:-3]:  # trzymamy podglądy 3 ostatnich montaży
        if old.name != jid:
            shutil.rmtree(old, ignore_errors=True)
    d = VPRE / os.path.basename(jid)
    d.mkdir(exist_ok=True)
    size = 0
    with (d / f"{int(i)}.mp4").open("wb") as f:
        async for chunk in req.stream():
            size += len(chunk)
            if size > 40 * 1048576:
                return JSONResponse({"error": "za duży"}, status_code=413)
            f.write(chunk)
    return {"ok": True}


@router.get("/api/mont-vlog/{jid}/preview/{i}")
def vlog_preview(jid: str, i: int):
    f = VPRE / os.path.basename(jid) / f"{int(i)}.mp4"
    return FileResponse(f, media_type="video/mp4") if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)


@router.post("/api/mont-hook/disk")
async def hook_disk(req: Request):
    """Archiwista: raport miejsca z Maca (co godzinę)."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    b = await req.json()
    b["at"] = time.time()
    _wr("mac_disk.json", b)
    return {"ok": True}


def archivist():
    """Archiwista na VPS: sprząta tylko pliki tymczasowe centrum (nie rusza filmów ani danych)."""
    freed, cut = 0, time.time()
    def rm(p):
        nonlocal freed
        try:
            sz = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
            freed += sz
        except Exception:
            pass
    for d, days in ((VUP, 2), (SOCIAL_ROOT / "vlog_preview", 7)):
        if d.is_dir():
            for x in d.iterdir():
                if cut - x.stat().st_mtime > days * 86400:
                    rm(x)
    bk = SOCIAL_ROOT / "n8n_backups"
    if bk.is_dir():
        files = sorted(bk.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in files[40:]:  # 40 najnowszych kopii zostaje
            rm(f)
    if JOBS.is_dir():
        for d in JOBS.iterdir():
            z = d / "capcut.zip"
            if z.exists() and cut - z.stat().st_mtime > 2 * 86400:
                rm(z)
    _wr("archivist.json", {"at": time.time(), "freed_mb": round(freed / 1e6, 1)})
    return freed


@router.get("/api/mont-archivist")
def archivist_state():
    return {"mac": _rd("mac_disk.json", {}), "vps": _rd("archivist.json", {})}


@router.post("/api/mont-archivist/run")
def archivist_run():
    return {"ok": True, "freed_mb": round(archivist() / 1e6, 1)}


def _arch_loop():
    time.sleep(300)
    while True:
        try:
            if time.time() - _rd("archivist.json", {}).get("at", 0) > 86400:
                archivist()
        except Exception as e:
            print("archiwista", e, flush=True)
        time.sleep(3600)


threading.Thread(target=_arch_loop, daemon=True).start()


@router.get("/api/mont-hook/{jid}/answer")
def hook_answer(jid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    j = _vjob(_V(), jid)
    a = j and j.get("answer")
    return {"answered": a is not None, "choice": a and a.get("choice")}


@router.get("/api/mont-vlog/{jid}/log")
def vlog_log(jid: str):
    j = _vjob(_V(), jid)
    return {"log": (j or {}).get("log_full") or "\n".join((j or {}).get("lines", []))}
