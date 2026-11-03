"""Publikacja na TikToka z planera (TikTok Content Posting API).
Dwa tryby:
  • „szkic” (domyślny) – film trafia do skrzynki w aplikacji TikTok, Ty jednym kliknięciem publikujesz (działa bez audytu TikToka),
  • „bezpośrednio” – publikuje sam; dopóki TikTok nie zatwierdzi aplikacji (audyt), wychodzi tylko jako prywatny.
Klucze aplikacji (Client key / secret) wpisuje się w ustawieniach kont – nigdy w czacie."""
import math, os, secrets, time, urllib.parse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

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
            return pid, "w skrzynce TikToka – otwórz aplikację i opublikuj"
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
