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
    if JOBS.is_dir():
        for d in sorted((x for x in JOBS.iterdir() if x.is_dir() and JID.match(x.name)), reverse=True)[:30]:
            st = _status(d)
            st.pop("log", None)
            st["check"] = _rd("mont_checks.json", {}).get(d.name)
            out.append(st)
    return {"ok": JOBS.is_dir(), "jobs": out, "dziennik": DZ_URL, "can_act": bool(os.environ.get("DZIENNIK_PASS")),
            "vlog": _rd("mont_vlog.json", {"helper": None, "jobs": []}),
            "error": None if JOBS.is_dir() else f"Nie widzę montaży Dziennika w {JOBS}"}


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


def check(jid):
    d = _job(jid)
    st = _status(d) if d else {}
    f = d and _film(d, st)
    if not f:
        return None
    dur, probs, warns = _probe(f)
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
                         f"Tytuł: {' / '.join(st.get('title') or [])}\nDługość: {dur:.0f} s\n\nRAPORT:\n{rap}\n\nTRANSKRYPCJA:\n{tr}\n\n"
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
            "jobs": [{k: x for k, x in j.items() if k != "log_full"} for j in v["jobs"][-12:][::-1]]}


@router.post("/api/mont-vlog/new")
async def vlog_new(req: Request):
    b = await req.json()
    with _vl:
        v = _V()
        if any(j["state"] in ("queued", "uploading", "working", "ask") for j in v["jobs"]):
            return {"ok": False, "error": "Poprzedni montaż vloga jeszcze trwa."}
        jid = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
        j = {"id": jid, "ep": int(b.get("ep") or 0), "date": str(b.get("date") or "")[:10], "voiceover": bool(b.get("voiceover")),
             "state": "uploading" if b.get("upload") else "queued", "step": "Wysyłanie klipów…" if b.get("upload") else "Czekam na Maca…",
             "files": [], "lines": [], "at": time.time()}
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
    b = await req.json() if action == "answer" else {}
    with _vl:
        v = _V()
        j = _vjob(v, jid)
        if not j:
            return {"ok": False, "error": "nie ma"}
        if action == "go":
            j.update(state="queued", step="Czekam na Maca…")
        elif action == "answer":
            j.update(answer={"choice": b.get("choice")}, state="working", step="Zapowiedź wybrana – montuję dalej")
        elif action == "cancel" and j["state"] in ("queued", "uploading"):
            j.update(state="error", step="Anulowano")
        _wr("mont_vlog.json", v)
    return {"ok": True}


# — wywoływane przez pomocnika na Macu (token X-Mont-Token) —
@router.get("/api/mont-hook/next")
def hook_next(req: Request, last: int = 0, loose: int = 0):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    with _vl:
        v = _V()
        v["helper"] = {"seen": time.time(), "last": last, "loose": loose}
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
    return j and {k: j[k] for k in ("id", "ep", "date", "voiceover", "files")}


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
                shutil.rmtree(VUP / jid, ignore_errors=True)
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
            push_all("🎥 Vlog gotowy w CapCut" if ok else "❌ Montaż vloga nie wyszedł", (res or j["step"])[:150], "/#montazownia", kind="montaz")
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
