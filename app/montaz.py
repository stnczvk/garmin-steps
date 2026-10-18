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
    if not _job(jid) or action not in ("redo", "fix", "check"):
        return JSONResponse({"error": "nie ma"}, status_code=404)
    if action == "check":
        C = _rd("mont_checks.json", {})
        C.pop(jid, None)
        _wr("mont_checks.json", C)
        return check(jid) or {"error": "Film nie jest gotowy."}
    try:
        if action == "redo":
            r = _dz_call("POST", f"/api/montaz/{jid}/start", json={})
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
