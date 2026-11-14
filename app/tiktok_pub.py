"""Publikacja na TikToka z planera (TikTok Content Posting API).
Dwa tryby:
  • „szkic” (domyślny) – film trafia do skrzynki w aplikacji TikTok, Ty jednym kliknięciem publikujesz (działa bez audytu TikToka),
  • „bezpośrednio” – publikuje sam; dopóki TikTok nie zatwierdzi aplikacji (audyt), wychodzi tylko jako prywatny.
Klucze aplikacji (Client key / secret) wpisuje się w ustawieniach kont – nigdy w czacie."""
import math, os, secrets, time, urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

from social import _read, _write, cfg, base_url

router = APIRouter()
F = "tiktok_pub.json"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,user.info.profile,user.info.stats,video.list,video.upload,video.publish"
_states = {}


def _acc():
    return _read(F, {})


def connected():
    return bool(_acc().get("refresh_token"))


def mode():
    return _acc().get("mode") or "szkic"


@router.get("/api/tiktok/status")
def status(req: Request):
    a = _acc()
    return {"ready": bool(cfg("TIKTOK_CLIENT_KEY") and cfg("TIKTOK_CLIENT_SECRET")), "connected": connected(),
            "name": a.get("name", ""), "mode": mode(), "redirect": f"{base_url(req)}/api/tiktok/callback",
            "refresh_days": round((a.get("refresh_exp", 0) - time.time()) / 86400) if a.get("refresh_exp") else None}


@router.post("/api/tiktok/mode")
async def set_mode(req: Request):
    b = await req.json()
    a = _acc()
    if b.get("mode") in ("szkic", "bezposrednio"):
        a["mode"] = b["mode"]
        _write(F, a)
    if b.get("disconnect"):
        _write(F, {"mode": a.get("mode", "szkic")})
    return {"ok": True}


@router.get("/api/tiktok/connect")
def connect(req: Request):
    if not cfg("TIKTOK_CLIENT_KEY"):
        return RedirectResponse("/?blad=" + urllib.parse.quote("Najpierw wklej TikTok Client key i secret w ustawieniach kont."))
    s = secrets.token_urlsafe(20)
    _states[s] = time.time()
    q = {"client_key": cfg("TIKTOK_CLIENT_KEY"), "scope": SCOPES, "response_type": "code",
         "redirect_uri": f"{base_url(req)}/api/tiktok/callback", "state": s}
    return RedirectResponse("https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(q))


def _save_tok(t, extra=None):
    a = _acc()
    a.update(access_token=t["access_token"], refresh_token=t.get("refresh_token", a.get("refresh_token")),
             open_id=t.get("open_id", a.get("open_id")), exp=time.time() + int(t.get("expires_in", 86400)) - 120,
             refresh_exp=time.time() + int(t.get("refresh_expires_in", 365 * 86400)), **(extra or {}))
    _write(F, a)
    return a


@router.get("/api/tiktok/callback")
def callback(req: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error or not code or _states.pop(state, None) is None:
        return RedirectResponse("/?blad=" + urllib.parse.quote("TikTok: " + (error_description or error or "logowanie przerwane")))
    t = httpx.post(f"{API}/oauth/token/", timeout=30, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key": cfg("TIKTOK_CLIENT_KEY"), "client_secret": cfg("TIKTOK_CLIENT_SECRET"), "code": code,
        "grant_type": "authorization_code", "redirect_uri": f"{base_url(req)}/api/tiktok/callback"}).json()
    if "access_token" not in t:
        return RedirectResponse("/?blad=" + urllib.parse.quote("TikTok nie oddał tokenu: " + str(t)[:180]))
    name = ""
    try:
        u = httpx.get(f"{API}/user/info/", params={"fields": "open_id,display_name,username"}, timeout=20,
                      headers={"Authorization": "Bearer " + t["access_token"]}).json()
        uu = (u.get("data") or {}).get("user") or {}
        name = uu.get("display_name", "")
        if uu.get("username"):
            _write(F, {**_acc(), "username": uu["username"]})
    except Exception:
        pass
    _save_tok(t, {"name": name})
    return RedirectResponse("/?ok=tiktok")


def token():
    a = _acc()
    if not a.get("refresh_token"):
        raise RuntimeError("TikTok nie jest połączony (Statystyki → Konta → Połącz TikToka)")
    if time.time() < a.get("exp", 0):
        return a["access_token"]
    t = httpx.post(f"{API}/oauth/token/", timeout=30, headers={"Content-Type": "application/x-www-form-urlencoded"}, data={
        "client_key": cfg("TIKTOK_CLIENT_KEY"), "client_secret": cfg("TIKTOK_CLIENT_SECRET"),
        "grant_type": "refresh_token", "refresh_token": a["refresh_token"]}).json()
    if "access_token" not in t:
        raise RuntimeError("TikTok: połącz konto ponownie (" + str(t.get("error_description") or t.get("error") or t)[:120] + ")")
    return _save_tok(t)["access_token"]


def _err(r):
    try:
        e = r.json().get("error") or {}
        return f"{e.get('code')}: {e.get('message')}"[:200]
    except Exception:
        return r.text[:200]


def _chunks(n):
    """Zasady TikToka: do 64 MB jednym kawałkiem; większe po 10 MB (ostatni bierze resztę)."""
    if n <= 64 * 1024 * 1024:
        return n, 1
    cs = 10 * 1024 * 1024
    return cs, n // cs


@router.get("/api/tiktok/creator")
def creator():
    """Dane do ekranu publikacji (wymóg TikToka): konto, dozwolone poziomy prywatności, co twórca ma wyłączone, max długość."""
    try:
        h = {"Authorization": "Bearer " + token(), "Content-Type": "application/json; charset=UTF-8"}
        r = httpx.post(f"{API}/post/publish/creator_info/query/", headers=h, timeout=30)
        if r.status_code >= 300:
            return {"ok": False, "error": _err(r)}
        d = r.json().get("data") or {}
        return {"ok": True, "name": d.get("creator_nickname") or d.get("creator_username"), "user": d.get("creator_username"),
                "avatar": d.get("creator_avatar_url"), "privacy": d.get("privacy_level_options") or [],
                "comment_off": d.get("comment_disabled"), "duet_off": d.get("duet_disabled"), "stitch_off": d.get("stitch_disabled"),
                "max_sec": d.get("max_video_post_duration_sec"), "mode": mode()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def publish(path, caption="", direct=None, opts=None):
    """Wysyła film. Zwraca (publish_id, opis_wyniku). opts = ustawienia z ekranu publikacji (prywatność, komentarze…)."""
    opts = opts or {}
    direct = (mode() == "bezposrednio") if direct is None else direct
    tok = token()
    h = {"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8"}
    for _ in range(0 if os.path.basename(path).startswith("tt_") else 30):   # plik jeszcze się zapisuje → czekamy, aż przestanie rosnąć
        if time.time() - os.path.getmtime(path) > 90:
            break
        time.sleep(10)
    try:
        import subprocess
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                   capture_output=True, text=True, timeout=30).stdout.strip() or 0)
    except Exception:
        dur = 0
    print(f"tiktok publish {path} {os.path.getsize(path)} B {dur:.1f} s", flush=True)
    # Każdy film przed wysyłką przerabiamy na „bezpieczny” format: H.264 8 bit SDR, stałe 30 kl/s, AAC, faststart, < 60 MB, jeden kawałek.
    # Pliki z iPhone'a/CapCuta (HEVC 10 bit HDR, 60 kl/s, zmienna liczba klatek) TikTok potrafił zamrozić po paru sekundach,
    # a gdy dźwięk jest dłuższy niż obraz – ucinał końcówkę (wtedy dociągamy obraz ostatnią klatką).
    if not os.path.basename(path).startswith("tt_"):
        import subprocess, tempfile, json as _j
        try:
            pr = _j.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,pix_fmt,duration,color_transfer:stream_disposition=attached_pic:format=duration",
                                          "-of", "json", path], capture_output=True, text=True, timeout=30).stdout or "{}")
        except Exception:
            pr = {}
        sts = [x for x in (pr.get("streams") or []) if not (x.get("disposition") or {}).get("attached_pic")]   # okładka w pliku to nie film
        fd = float((pr.get("format") or {}).get("duration") or dur or 0)
        def _d(t):
            for x in sts:
                if x.get("codec_type") == t:
                    try:
                        return float(x.get("duration") or fd)
                    except ValueError:
                        return fd
            return 0.0
        vd, ad = _d("video"), _d("audio")
        hdr = any(x.get("color_transfer") in ("arib-std-b67", "smpte2084") for x in sts)
        pad = (ad - vd + 0.1) if (vd and ad and ad - vd > 0.25) else 0.0
        tot = max(vd, ad, fd, 1.0)
        vs = next((x for x in sts if x.get("codec_type") == "video"), {})
        # zawsze przerabiamy: filmy prosto z ffmpeg (montaż, klipy z vloga) mają w MP4 listę edycji i przesunięcia klatek B –
        # TikTok przy wysyłce przez API potrafi wtedy „zagrać” tylko pierwszą sekundę (ręcznie z telefonu działa, bo aplikacja koduje po swojemu)
        trzeba = True
        if not trzeba:   # zwykły plik H.264 (jak montaże z centrum) – wysyłamy oryginał, tak jak kiedyś, kiedy wszystko działało
            print("tiktok: plik w porządku – wysyłam bez przeróbki", flush=True)
        else:
            vb = max(1500, min(8000, int(55 * 8 * 1024 / tot) - 160))   # kb/s, żeby zmieścić się w ~55 MB
            out = os.path.join(tempfile.gettempdir(), f"tt_{secrets.token_hex(4)}.mp4")
            tail = f",tpad=stop_mode=clone:stop_duration={pad:.2f}" if pad else ""
            base = "scale='min(1080,iw)':-2"   # bez fps=30: przy nierównych znacznikach czasu fps dublował klatki = stop-klatka
            vfs = ([f"zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,{base}{tail}"] if hdr else []) + \
                  [f"{base},format=yuv420p{tail}"]
            print(f"tiktok: przerabiam (hdr={hdr}, obraz {vd:.1f} s, dźwięk {ad:.1f} s, dociągnięcie {pad:.2f} s, {vb} kb/s)", flush=True)
            ok = False
            for vf in vfs:
                cmd = ["nice", "-n", "10", "ffmpeg", "-y", "-v", "error", "-i", path, "-map", "0:V:0", "-map", "0:a:0?", "-vf", vf,
                       "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level:v", "4.1", "-bf", "0", "-g", "60",
                       "-b:v", f"{vb}k", "-maxrate", f"{vb * 2}k", "-bufsize", f"{vb * 2}k",
                       "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                       "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-af", "aresample=async=1:first_pts=0",
                       "-map_metadata", "-1", "-map_chapters", "-1", "-avoid_negative_ts", "make_zero", "-use_editlist", "0",
                       "-movflags", "+faststart", out]
                if subprocess.run(cmd, timeout=3600).returncode == 0 and os.path.getsize(out) > 1000:
                    ok = True
                    break
            if ok:
                try:
                    return publish(out, caption, direct, opts)
                finally:
                    try:
                        os.unlink(out)
                    except Exception:
                        pass
            print("tiktok: przeróbka nie wyszła – wysyłam oryginał", flush=True)
    size = os.path.getsize(path)
    cs, cnt = _chunks(size)
    src = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": cs, "total_chunk_count": cnt}
    if direct:
        ci = httpx.post(f"{API}/post/publish/creator_info/query/", headers=h, timeout=30)
        if ci.status_code >= 300:
            raise RuntimeError("TikTok (konto): " + _err(ci))
        allowed = (ci.json().get("data") or {}).get("privacy_level_options") or ["SELF_ONLY"]
        priv = opts.get("privacy")
        if priv not in allowed:
            raise RuntimeError("TikTok: wybierz w planerze, kto ma widzieć film (prywatność)")
        body = {"post_info": {"title": caption[:2200], "privacy_level": priv,
                              "disable_comment": not opts.get("comment", False), "disable_duet": not opts.get("duet", False),
                              "disable_stitch": not opts.get("stitch", False), "video_cover_timestamp_ms": 1000,
                              "brand_content_toggle": bool(opts.get("branded")), "brand_organic_toggle": bool(opts.get("own_brand"))},
                "source_info": src}
        r = httpx.post(f"{API}/post/publish/video/init/", headers=h, json=body, timeout=60)
    else:
        priv = None
        r = httpx.post(f"{API}/post/publish/inbox/video/init/", headers=h, json={"source_info": src}, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError("TikTok (start wysyłki): " + _err(r))
    d = r.json().get("data") or {}
    pid, url = d.get("publish_id"), d.get("upload_url")
    with open(path, "rb") as f:
        for i in range(cnt):
            start = i * cs
            ln = (size - start) if i == cnt - 1 else cs
            chunk = f.read(ln)
            u = httpx.put(url, content=chunk, timeout=600, headers={
                "Content-Type": "video/mp4", "Content-Length": str(ln), "Content-Range": f"bytes {start}-{start + ln - 1}/{size}"})
            if u.status_code >= 300:
                raise RuntimeError(f"TikTok (wysyłka pliku): {u.status_code} {u.text[:150]}")
    t0 = time.time()
    while time.time() - t0 < 600:
        s = httpx.post(f"{API}/post/publish/status/fetch/", headers=h, json={"publish_id": pid}, timeout=30)
        st = (s.json().get("data") or {}) if s.status_code < 300 else {}
        v = st.get("status")
        if v == "SEND_TO_USER_INBOX":
            return pid, f"w skrzynce TikToka ({dur:.0f} s) – otwórz aplikację i opublikuj"
        if v == "PUBLISH_COMPLETE":
            return pid, "opublikowany" + (" (prywatnie – TikTok jeszcze nie zatwierdził aplikacji)" if priv == "SELF_ONLY" else "")
        if v == "FAILED":
            raise RuntimeError("TikTok odrzucił film: " + str(st.get("fail_reason") or "?"))
        time.sleep(8)
    return pid, "TikTok jeszcze przetwarza film"


def stats(handle):
    """Oficjalne statystyki konta (user.info.stats + video.list). None, gdy to nie jest połączone konto albo brak uprawnień."""
    a = _acc()
    if not a.get("refresh_token"):
        return None
    if a.get("username") and a["username"].lower() != handle.lower():
        return None
    from datetime import date, datetime, timedelta, timezone
    h = {"Authorization": "Bearer " + token()}
    u = httpx.get(f"{API}/user/info/", params={"fields": "display_name,username,follower_count,likes_count,video_count"}, headers=h, timeout=30)
    if u.status_code >= 300:
        return None                                   # brak user.info.stats – zostaje stara metoda
    us = (u.json().get("data") or {}).get("user") or {}
    if us.get("username") and us["username"].lower() != handle.lower():
        return None
    if not us.get("username") and len([x for x in cfg("TIKTOK_PROFILES").split(",") if x.strip()]) > 1:
        return None                                   # nie wiemy, który profil jest połączony
    posts, cursor = [], None
    for _ in range(2):                                # do 40 ostatnich filmów
        body = {"max_count": 20, **({"cursor": cursor} if cursor else {})}
        r = httpx.post(f"{API}/video/list/", params={"fields": "id,title,video_description,create_time,cover_image_url,share_url,view_count,like_count,comment_count,share_count,duration"},
                       headers={**h, "Content-Type": "application/json"}, json=body, timeout=30)
        if r.status_code >= 300:
            return None
        d = r.json().get("data") or {}
        for v in d.get("videos") or []:
            ts = datetime.fromtimestamp(v["create_time"], timezone.utc).isoformat() if v.get("create_time") else ""
            posts.append({"id": str(v["id"]), "title": (v.get("title") or v.get("video_description") or "")[:90], "date": ts[:10], "ts": ts,
                          "dur": v.get("duration"), "url": v.get("share_url") or f"https://www.tiktok.com/@{handle}/video/{v['id']}",
                          "thumb": v.get("cover_image_url") or "", "views": v.get("view_count") or 0, "likes": v.get("like_count") or 0,
                          "comments": v.get("comment_count") or 0, "shares": v.get("share_count") or 0, "saved": None})
        if not d.get("has_more"):
            break
        cursor = d.get("cursor")
    cut = (date.today() - timedelta(days=28)).isoformat()
    return {"followers": int(us.get("follower_count") or 0), "followers_label": "obserwujący",
            "media_count": int(us.get("video_count") or len(posts)), "likes_total": us.get("likes_count"),
            "views_28d": sum(p["views"] for p in posts if p["date"] >= cut), "daily": [], "posts": posts[:30], "source": "api"}


@router.get("/api/mont-hook/tt-diag")
def tt_diag(req: Request, n: int = 1):
    """Diagnostyka „film na TikToku staje po sekundzie”: co jest w ostatnich plikach wysyłanych na TikToka i co wychodzi z przeróbki."""
    import subprocess, tempfile, json as _j
    from montaz import _tok
    import planer
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=401)
    P = sorted([x for x in planer.plan() if "tiktok" in (x.get("platforms") or []) and x.get("file") and os.path.isfile(x["file"])],
               key=lambda x: x.get("when", 0), reverse=True)[:max(1, min(3, n))]
    pr = lambda f: _j.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                             "stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,nb_frames,duration,color_transfer:stream_disposition=attached_pic:format=duration,size",
                                             "-of", "json", f], capture_output=True, text=True, timeout=60).stdout or "{}")
    out = []
    for x in P:
        o = {"id": x["id"], "label": x.get("label"), "status": x.get("status"), "file": os.path.basename(x["file"]), "zrodlo": pr(x["file"])}
        try:   # ta sama przeróbka co przy wysyłce, tylko pierwsze 8 s
            t = os.path.join(tempfile.gettempdir(), "ttdiag.mp4")
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", "8", "-i", x["file"], "-map", "0:V:0", "-map", "0:a:0?", "-vf",
                                "scale='min(1080,iw)':-2,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-b:v", "4000k",
                                "-c:a", "aac", "-movflags", "+faststart", t], capture_output=True, text=True, timeout=300)
            o["przerobka"] = pr(t) if r.returncode == 0 else {"blad": r.stderr[-300:]}
            vf = subprocess.run(["ffmpeg", "-v", "error", "-i", t, "-vf", "mpdecimate", "-f", "null", "-", "-loglevel", "debug"],
                                capture_output=True, text=True, timeout=120).stderr
            o["unikalne_klatki_8s"] = vf.count(" keep ")
            os.unlink(t)
        except Exception as e:
            o["przerobka"] = {"blad": str(e)[:200]}
        out.append(o)
    return {"pliki": out}
