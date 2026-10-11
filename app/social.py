"""Centrum social mediów: statystyki YouTube, Instagrama i TikToka w jednym miejscu.

Konta podpina się na stronie głównej aplikacji (klucze wkleja się tam, logowanie przez Google / Instagram).
Dane trzymane w ROOT/social: config.json (klucze), tokens.json (połączone konta), cache.json (ostatnie statystyki).
"""
import json, os, secrets, threading, time, urllib.parse
from datetime import date, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

ROOT = Path(os.environ.get("SOCIAL_HOME", "/root/social"))
DIR = ROOT / "dane"
DIR.mkdir(parents=True, exist_ok=True)
router = APIRouter()
_lock = threading.Lock()
_states = {}          # state OAuth -> (dostawca, grupa, czas)

CFG_KEYS = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "IG_APP_ID", "IG_APP_SECRET", "APIFY_TOKEN", "TIKTOK_PROFILES"]
SECRET_KEYS = {"GOOGLE_CLIENT_SECRET", "IG_APP_SECRET", "APIFY_TOKEN"}
GROUPS = {"pozaskala": "PozaSkala", "prywatne": "Prywatne"}


def _read(name, default):
    p = DIR / name
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def _write(name, data):
    p = DIR / name
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def cfg(k):
    return _read("config.json", {}).get(k) or os.environ.get(k, "")


def base_url(req: Request):
    dom = os.environ.get("SOCIAL_DOMAIN")
    return f"https://{dom}" if dom else str(req.base_url).rstrip("/")


# ---------------------------------------------------------------- ustawienia
@router.get("/api/social/config")
def get_config():
    c = _read("config.json", {})
    out = {k: ("••••" + c[k][-4:] if k in SECRET_KEYS and c.get(k) else c.get(k, "")) for k in CFG_KEYS}
    out["set"] = {k: bool(c.get(k)) for k in CFG_KEYS}
    return out


@router.post("/api/social/config")
async def set_config(req: Request):
    body = await req.json()
    with _lock:
        c = _read("config.json", {})
        for k in CFG_KEYS:
            v = (body.get(k) or "").strip()
            if v and not v.startswith("••••"):
                c[k] = v
            elif k in body and body[k] == "":
                c.pop(k, None)
        _write("config.json", c)
    return {"ok": True}


@router.get("/api/social/redirects")
def redirects(req: Request):
    b = base_url(req)
    return {"google": f"{b}/api/social/google/callback", "instagram": f"{b}/api/social/instagram/callback"}


# ---------------------------------------------------------------- konta
def _accounts():
    return _read("tokens.json", {})


def _save_account(key, acc):
    with _lock:
        t = _read("tokens.json", {})
        t[key] = {**t.get(key, {}), **acc}
        _write("tokens.json", t)


@router.get("/api/social/accounts")
def accounts():
    return [{"key": k, "platform": a["platform"], "name": a.get("name", ""), "group": a.get("group", "prywatne")}
            for k, a in _accounts().items()]


@router.post("/api/social/accounts/{key}")
async def account_update(key: str, req: Request):
    body = await req.json()
    if key not in _accounts():
        return JSONResponse({"error": "brak konta"}, status_code=404)
    if body.get("remove"):
        with _lock:
            t = _read("tokens.json", {}); t.pop(key, None); _write("tokens.json", t)
            c = _read("cache.json", {}); c.get("accounts", {}).pop(key, None); _write("cache.json", c)
        return {"ok": True}
    if body.get("group") in GROUPS:
        _save_account(key, {"group": body["group"]})
    return {"ok": True}


def _new_state(provider, group):
    s = secrets.token_urlsafe(24)
    now = time.time()
    for k in [k for k, v in _states.items() if now - v[2] > 900]:
        _states.pop(k, None)
    _states[s] = (provider, group if group in GROUPS else "prywatne", now)
    return s


# ---- Google / YouTube
G_SCOPES = "https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly"


@router.get("/api/social/google/connect")
def google_connect(req: Request, group: str = "pozaskala"):
    if not cfg("GOOGLE_CLIENT_ID"):
        return RedirectResponse("/?blad=" + urllib.parse.quote("Najpierw wklej Google Client ID i Secret w ustawieniach."))
    q = {"client_id": cfg("GOOGLE_CLIENT_ID"), "redirect_uri": f"{base_url(req)}/api/social/google/callback",
         "response_type": "code", "scope": G_SCOPES, "access_type": "offline", "prompt": "consent select_account",
         "state": _new_state("google", group)}
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(q))


@router.get("/api/social/google/callback")
def google_callback(req: Request, code: str = "", state: str = "", error: str = ""):
    st = _states.pop(state, None)
    if error or not code or not st:
        return RedirectResponse("/?blad=" + urllib.parse.quote(error or "logowanie przerwane"))
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "code": code, "client_id": cfg("GOOGLE_CLIENT_ID"), "client_secret": cfg("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": f"{base_url(req)}/api/social/google/callback", "grant_type": "authorization_code"}, timeout=30)
    tok = r.json()
    if "refresh_token" not in tok:
        return RedirectResponse("/?blad=" + urllib.parse.quote("Google nie oddał tokenu: " + str(tok)[:150]))
    ch = httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                   headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=30).json()
    items = ch.get("items") or []
    if not items:
        return RedirectResponse("/?blad=" + urllib.parse.quote("To konto Google nie ma kanału YouTube – wybierz konto kanału."))
    c = items[0]
    _save_account("yt:" + c["id"], {"platform": "youtube", "name": c["snippet"]["title"], "group": st[1],
                                     "refresh_token": tok["refresh_token"], "channel_id": c["id"]})
    threading.Thread(target=refresh_all, daemon=True).start()
    return RedirectResponse("/?ok=youtube")


def _google_access(acc):
    r = httpx.post("https://oauth2.googleapis.com/token", data={
        "client_id": cfg("GOOGLE_CLIENT_ID"), "client_secret": cfg("GOOGLE_CLIENT_SECRET"),
        "refresh_token": acc["refresh_token"], "grant_type": "refresh_token"}, timeout=30).json()
    if "access_token" not in r:
        raise RuntimeError("Google: połącz kanał ponownie (" + r.get("error", "?") + ")")
    return r["access_token"]


def fetch_youtube(acc):
    h = {"Authorization": f"Bearer {_google_access(acc)}"}
    api = "https://www.googleapis.com/youtube/v3"
    ch = httpx.get(f"{api}/channels", params={"part": "snippet,statistics,contentDetails", "id": acc["channel_id"]},
                   headers=h, timeout=30).json()["items"][0]
    stats = ch["statistics"]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = httpx.get(f"{api}/playlistItems", params={"part": "contentDetails", "playlistId": uploads, "maxResults": 25},
                   headers=h, timeout=30).json()
    ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
    posts = []
    if ids:
        vids = httpx.get(f"{api}/videos", params={"part": "snippet,statistics,contentDetails", "id": ",".join(ids)},
                         headers=h, timeout=30).json().get("items", [])
        for v in vids:
            s = v.get("statistics", {})
            posts.append({"id": v["id"], "title": v["snippet"]["title"], "date": v["snippet"]["publishedAt"][:10],
                          "url": f"https://youtu.be/{v['id']}",
                          "thumb": (v["snippet"].get("thumbnails", {}).get("medium") or {}).get("url", ""),
                          "views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                          "comments": int(s.get("commentCount", 0))})
    end = date.today()
    start = end - timedelta(days=27)
    daily = []
    try:
        rep = httpx.get("https://youtubeanalytics.googleapis.com/v2/reports", params={
            "ids": "channel==MINE", "startDate": start.isoformat(), "endDate": end.isoformat(),
            "metrics": "views,estimatedMinutesWatched,subscribersGained,subscribersLost", "dimensions": "day", "sort": "day"},
            headers=h, timeout=30).json()
        daily = [{"date": r[0], "views": r[1], "minutes": r[2], "subs": r[3] - r[4]} for r in rep.get("rows", [])]
    except Exception:
        pass
    return {"followers": int(stats.get("subscriberCount", 0)), "followers_label": "subskrypcje",
            "total_views": int(stats.get("viewCount", 0)), "media_count": int(stats.get("videoCount", 0)),
            "views_28d": sum(d["views"] for d in daily) if daily else None,
            "minutes_28d": sum(d["minutes"] for d in daily) if daily else None,
            "followers_28d": sum(d["subs"] for d in daily) if daily else None,
            "daily": daily, "posts": posts}


# ---- Instagram (Instagram API z logowaniem przez Instagram – konta firmowe i twórcy)
IG_SCOPES = "instagram_business_basic,instagram_business_manage_insights"
IG = "https://graph.instagram.com"


@router.get("/api/social/instagram/connect")
def ig_connect(req: Request, group: str = "prywatne"):
    if not cfg("IG_APP_ID"):
        return RedirectResponse("/?blad=" + urllib.parse.quote("Najpierw wklej Instagram App ID i Secret w ustawieniach."))
    q = {"client_id": cfg("IG_APP_ID"), "redirect_uri": f"{base_url(req)}/api/social/instagram/callback",
         "response_type": "code", "scope": IG_SCOPES, "state": _new_state("instagram", group), "force_reauth": "true"}
    return RedirectResponse("https://www.instagram.com/oauth/authorize?" + urllib.parse.urlencode(q))


@router.get("/api/social/instagram/callback")
def ig_callback(req: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    st = _states.pop(state, None)
    if error or not code or not st:
        return RedirectResponse("/?blad=" + urllib.parse.quote(error_description or error or "logowanie przerwane"))
    code = code.split("#")[0]
    short = httpx.post("https://api.instagram.com/oauth/access_token", data={
        "client_id": cfg("IG_APP_ID"), "client_secret": cfg("IG_APP_SECRET"), "grant_type": "authorization_code",
        "redirect_uri": f"{base_url(req)}/api/social/instagram/callback", "code": code}, timeout=30).json()
    if "access_token" not in short:
        return RedirectResponse("/?blad=" + urllib.parse.quote("Instagram: " + str(short)[:200]))
    long = httpx.get(f"{IG}/access_token", params={"grant_type": "ig_exchange_token", "client_secret": cfg("IG_APP_SECRET"),
                                                  "access_token": short["access_token"]}, timeout=30).json()
    tok = long.get("access_token", short["access_token"])
    me = httpx.get(f"{IG}/me", params={"fields": "user_id,username", "access_token": tok}, timeout=30).json()
    if "username" not in me:
        return RedirectResponse("/?blad=" + urllib.parse.quote("Instagram: " + str(me)[:200]))
    _save_account("ig:" + str(me.get("user_id") or me.get("id")), {
        "platform": "instagram", "name": "@" + me["username"], "group": st[1], "token": tok,
        "token_at": time.time()})
    threading.Thread(target=refresh_all, daemon=True).start()
    return RedirectResponse("/?ok=instagram")


def _ig_token(key, acc):
    # token ważny 60 dni – odświeżamy, gdy ma ponad 20 dni
    if time.time() - acc.get("token_at", 0) > 20 * 86400:
        r = httpx.get(f"{IG}/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": acc["token"]},
                      timeout=30).json()
        if "access_token" in r:
            acc["token"], acc["token_at"] = r["access_token"], time.time()
            _save_account(key, {"token": acc["token"], "token_at": acc["token_at"]})
    return acc["token"]


def _ig_insights(mid, tok):
    for metrics in ("views,reach,saved,shares", "reach,saved,shares", "reach"):
        r = httpx.get(f"{IG}/{mid}/insights", params={"metric": metrics, "access_token": tok}, timeout=30).json()
        if "data" in r:
            return {m["name"]: (m.get("values") or [{}])[0].get("value", 0) for m in r["data"]}
    return {}


def fetch_instagram(key, acc):
    tok = _ig_token(key, acc)
    me = httpx.get(f"{IG}/me", params={"fields": "username,followers_count,media_count", "access_token": tok},
                   timeout=30).json()
    if "error" in me:
        raise RuntimeError("Instagram: " + me["error"].get("message", "błąd") + " – połącz konto ponownie")
    media = httpx.get(f"{IG}/me/media", params={
        "fields": "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count,thumbnail_url,media_url",
        "limit": 25, "access_token": tok}, timeout=30).json().get("data", [])
    posts = []
    for m in media:
        ins = _ig_insights(m["id"], tok)
        posts.append({"id": m["id"], "title": (m.get("caption") or "").split("\n")[0][:90],
                      "date": m.get("timestamp", "")[:10], "url": m.get("permalink", ""),
                      "thumb": m.get("thumbnail_url") or (m.get("media_url") if m.get("media_type") == "IMAGE" else ""),
                      "type": m.get("media_product_type") or m.get("media_type"),
                      "views": ins.get("views") or ins.get("reach") or 0, "reach": ins.get("reach"),
                      "likes": m.get("like_count", 0), "comments": m.get("comments_count", 0),
                      "saved": ins.get("saved"), "shares": ins.get("shares")})
    cut = (date.today() - timedelta(days=28)).isoformat()
    return {"followers": me.get("followers_count", 0), "followers_label": "obserwujący",
            "media_count": me.get("media_count", 0),
            "views_28d": sum(p["views"] for p in posts if p["date"] >= cut), "daily": [], "posts": posts}


# ---- TikTok (Apify – publiczne liczby z profilu)
def fetch_tiktok(handle):
    tok = cfg("APIFY_TOKEN")
    r = httpx.post("https://api.apify.com/v2/acts/clockworks~tiktok-scraper/run-sync-get-dataset-items",
                   params={"token": tok, "timeout": 240},
                   json={"profiles": [handle], "resultsPerPage": 25, "shouldDownloadVideos": False,
                         "shouldDownloadCovers": False, "shouldDownloadSubtitles": False}, timeout=300)
    items = r.json() if r.status_code < 300 else []
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"TikTok/Apify: brak danych ({r.status_code})")
    author = items[0].get("authorMeta", {})
    posts = [{"id": str(i.get("id")), "title": (i.get("text") or "")[:90], "date": (i.get("createTimeISO") or "")[:10],
              "url": i.get("webVideoUrl", ""), "thumb": (i.get("videoMeta") or {}).get("coverUrl", ""),
              "views": i.get("playCount", 0), "likes": i.get("diggCount", 0), "comments": i.get("commentCount", 0),
              "shares": i.get("shareCount", 0), "saved": i.get("collectCount")} for i in items]
    cut = (date.today() - timedelta(days=28)).isoformat()
    return {"followers": author.get("fans", 0), "followers_label": "obserwujący", "media_count": author.get("video", 0),
            "views_28d": sum(p["views"] for p in posts if p["date"] >= cut), "daily": [], "posts": posts}


# ---------------------------------------------------------------- odświeżanie
_running = threading.Event()


def refresh_all(tiktok=True):
    if _running.is_set():
        return
    _running.set()
    try:
        cache = _read("cache.json", {})
        out = cache.get("accounts", {})
        todo = [(k, a) for k, a in _accounts().items()]
        if tiktok and cfg("APIFY_TOKEN"):
            for h in [x.strip().lstrip("@") for x in cfg("TIKTOK_PROFILES").split(",") if x.strip()]:
                todo.append(("tt:" + h, {"platform": "tiktok", "name": "@" + h, "group": "prywatne", "handle": h}))
        for k, a in todo:
            try:
                if a["platform"] == "youtube":
                    d = fetch_youtube(a)
                elif a["platform"] == "instagram":
                    d = fetch_instagram(k, a)
                else:
                    d = fetch_tiktok(a["handle"])
                prev = out.get(k, {})
                hist = prev.get("history", [])
                today = date.today().isoformat()
                hist = [h for h in hist if h["date"] != today] + [{"date": today, "followers": d.get("followers", 0)}]
                out[k] = {**d, "platform": a["platform"], "name": a.get("name"), "group": a.get("group", "prywatne"),
                          "updated": time.time(), "error": None, "history": hist[-120:]}
            except Exception as e:
                out.setdefault(k, {"platform": a["platform"], "name": a.get("name"), "group": a.get("group", "prywatne"),
                                   "posts": [], "daily": []})
                out[k]["error"] = str(e)[:300]
        live = set(_accounts()) | {k for k, _ in todo}
        out = {k: v for k, v in out.items() if k in live}
        _write("cache.json", {"accounts": out, "updated": time.time()})
    finally:
        _running.clear()


@router.get("/api/social/summary")
def summary():
    c = _read("cache.json", {})
    c["refreshing"] = _running.is_set()
    c["groups"] = GROUPS
    return c


@router.post("/api/social/refresh")
def refresh():
    if not _running.is_set():
        threading.Thread(target=refresh_all, daemon=True).start()
    return {"ok": True}


def _scheduler():
    # co 6 godzin; TikTok (płatny Apify) raz dziennie
    time.sleep(120)
    n = 0
    while True:
        try:
            refresh_all(tiktok=(n % 4 == 0))
        except Exception as e:
            print("social refresh error", e, flush=True)
        n += 1
        time.sleep(6 * 3600)


threading.Thread(target=_scheduler, daemon=True).start()
