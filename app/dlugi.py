"""PozaSkala – długie filmy na YouTube (seria „What If?” ze stickmanem, 6–8 min, 16:9).

Robione w centrum (bez n8n): scenariusz (AI) → lektor (OpenAI TTS, po rozdziale) → kadry (OpenAI obrazy, tania
jakość – płaski styl 2D jej nie potrzebuje) → montaż FFmpeg (ruch kamery na każdym kadrze) → kontrola techniczna →
szkic do zatwierdzenia w zakładce PozaSkala → publikacja na kanale PozaSkala. Domyślnie wtorek i piątek.
"""
import base64, concurrent.futures as cf, json, os, re, shutil, subprocess, threading, time, uuid
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from social import DIR, _read, _write
from agency import claude, as_json, log, _pz_account, TZ, SMART
from skills import sk

router = APIRouter()
F = "dlugie.json"
WORK = DIR / "dlugie"
DEF = {"enabled": True, "days": [1, 4], "hour": 9, "img_quality": "low", "voice": "alloy", "auto_publish": False}
_busy = {}

STYLE = ("2D flat vector cartoon, the stick figure character Stick (black stick body, round white head, two dot eyes, small red scarf), "
         "thick clean black outlines, simple flat pastel background, bold readable shapes, correct relative proportions, "
         "horizontal 16:9, no text, no watermark")


def cfg():
    return {**DEF, **_read(F, {}).get("cfg", {})}


def _state():
    S = _read(F, {})
    S.setdefault("items", [])
    return S


def _save(S):
    S["items"] = S["items"][-30:]
    _write(F, S)


def _upd(vid, **kw):
    S = _state()
    for it in S["items"]:
        if it["id"] == vid:
            it.update(kw)
    _save(S)


def _key():
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k:
        raise RuntimeError("brak OPENAI_API_KEY")
    return {"Authorization": "Bearer " + k}


# ---------------------------------------------------------------- 1. scenariusz
def script(used):
    p = f"""Write ONE long-form YouTube episode (EN, 6-8 minutes) for the channel "Poza Skala", series "What If?" starring Stick
(a simple stick figure with a round white head and a small red scarf). Real science and real scale are the payoff; numbers conservative and correct.
Pick a fresh hypothetical scenario that a 13-year-old instantly understands and wants answered (e.g. What if you fell into a black hole,
What if the Moon disappeared, What if you lived on Jupiter for a day). Do NOT repeat these titles: {"; ".join(used[-40:]) or "none"}.

Structure: a cold-open hook (first 15 s pose the question and tease the craziest moment), then 5-6 chapters that escalate,
a mid-video re-hook, and a final twist that echoes the opening. Narration follows Stick through the scenario, second person allowed,
short spoken sentences (max 16 words), American English B1-B2, no filler, no "in this video".

Return ONLY JSON:
{{"title":"max 60 chars, starts with What If","description":"3-4 sentences + 3 hashtags incl. #WhatIf #Stickman",
"tags":["8-12 search phrases"],"thumbnail_prompt":"Stick with a shocked face in the most dramatic moment, bold composition",
"chapters":[{{"title":"short chapter title","narration":"150-230 words","scenes":["7-9 visual scene descriptions, each one focal idea, what Stick does + the scale object"]}}]}}
The sum of all narration must be 1050-1350 words."""
    r = as_json(claude(p, SMART, 6000, system=sk("reel", "hook")))
    if isinstance(r, list):
        r = r[0] if r else {}
    ch = [c for c in (r.get("chapters") or []) if c.get("narration") and c.get("scenes")]
    if not r.get("title") or len(ch) < 4:
        raise RuntimeError("scenariusz niekompletny")
    r["chapters"] = ch[:7]
    return r


# ---------------------------------------------------------------- 2. lektor i kadry
def tts(text, out, voice):
    r = httpx.post("https://api.openai.com/v1/audio/speech", headers=_key(), timeout=300,
                   json={"model": "tts-1", "voice": voice, "input": text[:4000], "response_format": "mp3"})
    if r.status_code >= 300:
        raise RuntimeError("TTS: " + r.text[:200])
    out.write_bytes(r.content)


def image(prompt, out, quality, size="1536x1024"):
    for a in range(3):
        try:
            r = httpx.post("https://api.openai.com/v1/images/generations", headers=_key(), timeout=300,
                           json={"model": os.environ.get("DLUGI_IMG_MODEL", "gpt-image-2.5-flare"), "prompt": prompt + ". " + STYLE,
                                 "size": size, "quality": quality, "n": 1})
            j = r.json()
            if r.status_code < 300 and j.get("data"):
                d = j["data"][0]
                out.write_bytes(base64.b64decode(d["b64_json"]) if d.get("b64_json") else httpx.get(d["url"], timeout=120).content)
                return
            err = str((j.get("error") or {}).get("message", j))[:200]
        except Exception as e:
            err = str(e)[:200]
        time.sleep(5 * (a + 1))
    raise RuntimeError("obraz: " + err)


def dur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                capture_output=True, text=True).stdout.strip() or 0)


# ---------------------------------------------------------------- 3. montaż
def render(d, chapters):
    fps, parts = 25, []
    for ci, ch in enumerate(chapters):
        a = d / f"a{ci}.mp3"
        n = len(ch["scenes"])
        per = max(dur(a) / n, 2.0) + 0.04
        clips = []
        for si in range(n):
            img, clip = d / f"i{ci}_{si}.png", d / f"c{ci}_{si}.mp4"
            fr = int(per * fps)
            z = f"z='min(zoom+{0.12 / fr:.6f},1.12)'" if (si % 2 == 0) else f"z='if(eq(on,0),1.12,max(zoom-{0.12 / fr:.6f},1.0))'"
            vf = (f"scale=2880:1620:force_original_aspect_ratio=increase,crop=2880:1620,"
                  f"zoompan={z}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s=1920x1080:fps={fps},format=yuv420p")
            subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-vf", vf, "-t", f"{per:.3f}", "-r", str(fps),
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(clip)], check=True, capture_output=True)
            clips.append(clip)
        lst = d / f"l{ci}.txt"
        lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
        part = d / f"p{ci}.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(a), "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", str(part)], check=True, capture_output=True, cwd=d)
        parts.append(part)
        for c in clips:
            c.unlink(missing_ok=True)
    lst = d / "all.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = d / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], check=True, capture_output=True, cwd=d)
    return out


def check(f):
    """Kontrola techniczna jak w Montażu shortów: długość, obraz, dźwięk, czarne klatki, cisza."""
    probs = []
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(f)],
                                   capture_output=True, text=True).stdout or "{}")
    d = float((pr.get("format") or {}).get("duration") or 0)
    st = pr.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in st):
        probs.append("brak obrazu")
    if not any(s.get("codec_type") == "audio" for s in st):
        probs.append("brak dźwięku")
    if d < 240 or d > 900:
        probs.append(f"długość {d / 60:.1f} min (powinno 4–15)")
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(f), "-vf", "blackdetect=d=1:pix_th=0.10",
                          "-af", "silencedetect=noise=-45dB:d=2.5", "-f", "null", "-"], capture_output=True, text=True).stderr
    if re.search(r"black_start:([\d.]+)", out):
        probs.append("czarny ekran")
    sil = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out) if float(x) < d - 3]
    if sil:
        probs.append("cisza od " + ", ".join(f"{s:.0f}s" for s in sil[:3]))
    return d, probs


# ---------------------------------------------------------------- całość
def produce(why="harmonogram"):
    if _busy.get("run"):
        return {"ok": False, "error": "już trwa"}
    _busy["run"] = True
    c = cfg()
    vid = datetime.now(TZ).strftime("%y%m%d") + "-" + uuid.uuid4().hex[:4]
    d = WORK / vid
    d.mkdir(parents=True, exist_ok=True)
    S = _state()
    used = [x.get("title", "") for x in S["items"]]
    S["items"].append({"id": vid, "status": "working", "step": "scenariusz", "at": time.time(), "why": why})
    _save(S)
    try:
        sc = script(used)
        (d / "script.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
        n_img = sum(len(ch["scenes"]) for ch in sc["chapters"])
        _upd(vid, title=sc["title"], step=f"lektor + {n_img} kadrów")
        jobs = []
        with cf.ThreadPoolExecutor(4) as ex:
            for ci, ch in enumerate(sc["chapters"]):
                jobs.append(ex.submit(tts, ch["narration"], d / f"a{ci}.mp3", c["voice"]))
                for si, s in enumerate(ch["scenes"]):
                    jobs.append(ex.submit(image, s, d / f"i{ci}_{si}.png", c["img_quality"]))
            jobs.append(ex.submit(image, sc.get("thumbnail_prompt") or sc["title"], d / "thumb.png", "medium"))
            for j in jobs:
                j.result()
        _upd(vid, step="montaż (FFmpeg)")
        cds = [dur(d / f"a{ci}.mp3") for ci in range(len(sc["chapters"]))]
        f = render(d, sc["chapters"])
        length, probs = check(f)
        if not probs:
            _upd(vid, step="zwiastuny (shorty)")
            try:
                trailers(d, sc, cds)
            except Exception as e:
                log("dlugi", f"Zwiastuny nie wyszły: {str(e)[:120]}", "warn")
        for p in d.glob("*.png"):
            if p.name != "thumb.png":
                p.unlink(missing_ok=True)
        for p in list(d.glob("a*.mp3")) + list(d.glob("p*.mp4")) + list(d.glob("*.txt")):
            p.unlink(missing_ok=True)
        st = "error" if probs else "review"
        _upd(vid, status=st, step="; ".join(probs) if probs else "gotowy do zatwierdzenia", length=round(length),
             chapters=[ch["title"] for ch in sc["chapters"]], images=n_img)
        log("dlugi", f"Długi film {'zatrzymany: ' + '; '.join(probs) if probs else 'gotowy'}: {sc['title']} ({length / 60:.1f} min, {n_img} kadrów)",
            "error" if probs else "ok")
        if not probs:
            try:
                from extra import push_all
                push_all("🎬 Długi film do zatwierdzenia", sc["title"], "/#pozaskala")
            except Exception:
                pass
            if c.get("auto_publish"):
                publish(vid)
        return {"ok": not probs, "id": vid}
    except Exception as e:
        _upd(vid, status="error", step=str(e)[:200])
        log("dlugi", f"Długi film nie wyszedł: {str(e)[:150]}", "error")
        return {"ok": False, "error": str(e)[:200]}
    finally:
        _busy.pop("run", None)


FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]


def _esc(t):
    return str(t).replace("\\", "").replace(":", "\\:").replace("'", "\u2019").replace("%", " percent")


def trailers(d, sc, cds):
    """2 pionowe shorty-zwiastuny z długiego filmu: początek (hook) i najmocniejszy moment (przedostatni rozdział).
    Kadr 16:9 na rozmytym tle 9:16 + tytuł u góry i „Full episode on the channel” na dole."""
    f = d / "final.mp4"
    starts = [(0.0, min(55.0, max(20.0, cds[0] if cds else 50)))]
    if len(cds) >= 3:
        k = len(cds) - 2
        st = sum(cds[:k])
        starts.append((st, min(55.0, max(20.0, cds[k]))))
    font = next((x for x in FONTS if os.path.exists(x)), None)
    made = []
    for i, (ss, ln) in enumerate(starts):
        out = d / f"trailer{i + 1}.mp4"
        vf = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3,eq=brightness=-0.08[bg];"
              "[b]scale=1080:-2[fg];[bg][fg]overlay=0:(H-h)/2")
        if font:
            lines, cur = [], ""
            for w_ in sc["title"].split():
                if len(cur) + len(w_) + 1 > 22 and cur:
                    lines.append(cur); cur = w_
                else:
                    cur = (cur + " " + w_).strip()
            lines = (lines + [cur])[:3]
            for li, tx in enumerate(lines):
                vf += f",drawtext=fontfile={font}:text='{_esc(tx)}':fontcolor=white:fontsize=66:x=(w-text_w)/2:y={250 + li * 92}:box=1:boxcolor=black@0.5:boxborderw=18"
            vf += f",drawtext=fontfile={font}:text='Full episode on the channel':fontcolor=white:fontsize=44:x=(w-text_w)/2:y=h-420:box=1:boxcolor=0x5FA987@0.9:boxborderw=20"
        subprocess.run(["ffmpeg", "-y", "-ss", f"{ss:.2f}", "-t", f"{ln:.2f}", "-i", str(f), "-filter_complex", vf, "-r", "30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
                       check=True, capture_output=True)
        made.append(out.name)
    return made


def _yt_upload(tok, f, meta):
    r = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=60,
                   headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(f.stat().st_size)}, json=meta)
    if r.status_code >= 300 or "location" not in r.headers:
        raise RuntimeError("YouTube: " + r.text[:200])
    with open(f, "rb") as fh:
        u = httpx.put(r.headers["location"], content=fh.read(), timeout=3600, headers={"Content-Type": "video/mp4"})
    if u.status_code >= 300:
        raise RuntimeError("YouTube (wysyłka): " + u.text[:200])
    return u.json().get("id")


def publish_trailer(vid, n):
    """Zwiastun n (1 = zaraz po długim filmie, 2 = dzień później) jako Short z linkiem do pełnego odcinka."""
    from social import _google_access
    S = _state()
    it = next((x for x in S["items"] if x["id"] == vid), None)
    f = WORK / vid / f"trailer{n}.mp4"
    if not it or not it.get("yt") or not f.exists():
        return None
    sc = json.loads((WORK / vid / "script.json").read_text())
    _, acc = _pz_account("youtube")
    tok = _google_access(acc)
    yid = _yt_upload(tok, f, {"snippet": {"title": (sc["title"][:80] + " #shorts"), "categoryId": "27", "defaultLanguage": "en",
                                          "description": f"Full episode 👉 https://youtu.be/{it['yt']}\n\n#WhatIf #Stickman #shorts",
                                          "tags": (sc.get("tags") or [])[:10]},
                              "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}})
    f.unlink(missing_ok=True)
    tr = it.get("trailers", {})
    tr[str(n)] = yid
    _upd(vid, trailers=tr)
    log("dlugi", f"Zwiastun {n} (Short) wrzucony: {sc['title']}", "ok")
    return yid


def publish(vid):
    from social import _google_access
    S = _state()
    it = next((x for x in S["items"] if x["id"] == vid), None)
    d = WORK / vid
    if not it or not (d / "final.mp4").exists():
        return {"ok": False, "error": "nie ma filmu"}
    sc = json.loads((d / "script.json").read_text())
    _, acc = _pz_account("youtube")
    if not acc:
        return {"ok": False, "error": "brak kanału YouTube PozaSkala"}
    tok = _google_access(acc)
    _upd(vid, status="uploading", step="wysyłam na YouTube")
    meta = {"snippet": {"title": sc["title"][:100], "description": sc.get("description", "")[:4900], "tags": (sc.get("tags") or [])[:15],
                        "categoryId": "27", "defaultLanguage": "en"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}}
    f = d / "final.mp4"
    try:
        r = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=60,
                       headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                                "X-Upload-Content-Length": str(f.stat().st_size)}, json=meta)
        if r.status_code >= 300 or "location" not in r.headers:
            raise RuntimeError("YouTube: " + r.text[:200])
        with open(f, "rb") as fh:
            u = httpx.put(r.headers["location"], content=fh.read(), timeout=3600, headers={"Content-Type": "video/mp4"})
        if u.status_code >= 300:
            raise RuntimeError("YouTube (wysyłka): " + u.text[:200])
        yid = u.json().get("id")
        if (d / "thumb.png").exists():   # okładka – działa tylko na zweryfikowanym kanale, błąd ignorujemy
            try:
                httpx.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={yid}", timeout=120,
                           headers={"Authorization": "Bearer " + tok, "Content-Type": "image/png"}, content=(d / "thumb.png").read_bytes())
            except Exception:
                pass
        _upd(vid, status="published", step="opublikowany", yt=yid, published=time.time())
        log("dlugi", f"Opublikował długi film: {sc['title']}", "ok")
        (d / "final.mp4").unlink(missing_ok=True)   # oszczędność dysku – film jest już na YouTube
        try:
            publish_trailer(vid, 1)
        except Exception as e:
            log("dlugi", f"Zwiastun 1 nie wyszedł: {str(e)[:120]}", "warn")
        return {"ok": True, "yt": yid}
    except Exception as e:
        _upd(vid, status="review", step="publikacja nie wyszła: " + str(e)[:150])
        return {"ok": False, "error": str(e)[:200]}


def _loop():
    def run():
        time.sleep(120)
        while True:
            try:
                c, now = cfg(), datetime.now(TZ)
                S = _state()
                today = now.strftime("%y%m%d")
                if c["enabled"] and now.weekday() in c["days"] and now.hour >= c["hour"] and not any(x["id"].startswith(today) for x in S["items"]):
                    produce()
                for it in S["items"]:
                    if it.get("status") == "published" and it.get("yt") and "2" not in (it.get("trailers") or {}) \
                            and time.time() - it.get("published", 0) > 86400 and (WORK / it["id"] / "trailer2.mp4").exists():
                        try:
                            publish_trailer(it["id"], 2)
                        except Exception as e:
                            print("dlugi trailer2", e, flush=True)
                # sprzątanie: odrzucone/stare katalogi > 10 dni
                for p in WORK.glob("*"):
                    if p.is_dir() and time.time() - p.stat().st_mtime > 10 * 86400:
                        shutil.rmtree(p, ignore_errors=True)
            except Exception as e:
                print("dlugi", e, flush=True)
            time.sleep(600)
    threading.Thread(target=run, daemon=True).start()


_loop()


# ---------------------------------------------------------------- API
@router.get("/api/dlugi")
def api_state():
    S = _state()
    return {"cfg": cfg(), "busy": bool(_busy.get("run")), "items": list(reversed(S["items"]))[:12]}


@router.post("/api/dlugi/run")
def api_run():
    if _busy.get("run"):
        return {"ok": False, "error": "już trwa"}
    threading.Thread(target=produce, args=("ręcznie",), daemon=True).start()
    return {"ok": True}


@router.post("/api/dlugi/{vid}/{action}")
def api_action(vid: str, action: str):
    if not re.fullmatch(r"[\w-]{4,20}", vid):
        return {"ok": False}
    if action == "publish":
        threading.Thread(target=publish, args=(vid,), daemon=True).start()
        return {"ok": True}
    if action == "reject":
        _upd(vid, status="rejected", step="odrzucony")
        shutil.rmtree(WORK / vid, ignore_errors=True)
        return {"ok": True}
    return {"ok": False}


@router.post("/api/dlugi-cfg")
async def api_cfg(req: Request):
    b = await req.json()
    S = _state()
    c = {**cfg(), **{k: b[k] for k in ("enabled", "days", "hour", "auto_publish") if k in b}}
    S["cfg"] = c
    _save(S)
    return {"ok": True, "cfg": c}


@router.get("/api/dlugi/{vid}/{name}")
def api_file(vid: str, name: str):
    if not re.fullmatch(r"[\w-]{4,20}", vid) or name not in ("final.mp4", "thumb.png"):
        return JSONResponse({"error": "nie"}, status_code=404)
    f = WORK / vid / name
    return FileResponse(f) if f.exists() else JSONResponse({"error": "nie ma"}, status_code=404)
