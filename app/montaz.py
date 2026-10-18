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
    return {"date": day.isoformat(), "n": n, "title": [f"Redukcja dzień {n}", "", "dzień treningowy" if train else "dzień nietreningowy"]}


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
