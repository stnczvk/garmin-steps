"""Dodatki centrum: powiadomienia push (post się rozkręca), raport tygodnia i analiza „co działa” (PozaSkala)."""
import hashlib, json, os, re, statistics, threading, time, urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from social import _read, _write, DIR, GROUPS, ROOT

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
NAMES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}
DNI = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
DNI_KR = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]


def _ai_key():
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if k:
        return k, os.environ.get("ANTHROPIC_URL", "https://api.anthropic.com/v1/messages")
    env = Path(os.environ.get("DZ_HOME", "/root/dziennik")) / ".env"
    vals = {}
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                a, b = line.split("=", 1)
                vals[a.strip()] = b.strip()
    return vals.get("ANTHROPIC_API_KEY", ""), vals.get("ANTHROPIC_URL", "https://api.anthropic.com/v1/messages")


def post_time(p):
    """Czas publikacji posta w strefie Warszawy (albo None)."""
    t = p.get("ts") or ""
    if not t:
        return None
    try:
        t = t.replace("Z", "+00:00")
        if re.search(r"[+-]\d{4}$", t):
            t = t[:-2] + ":" + t[-2:]
        return datetime.fromisoformat(t).astimezone(TZ)
    except Exception:
        return None


def _median(v):
    v = [x for x in v if x]
    return statistics.median(v) if v else 0


# ---------------------------------------------------------------- push
def _vapid():
    pem = DIR / "vapid.pem"
    if not pem.exists():
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        k = ec.generate_private_key(ec.SECP256R1())
        pem.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        os.chmod(pem, 0o600)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    import base64
    k = load_pem_private_key(pem.read_bytes(), None)
    raw = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return str(pem), base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _kind(url):
    h = (url or "").split("#")[-1]
    return {"skaner": "skaner", "pozaskala": "pozaskala", "agencja": "agencja", "straznicy": "straznicy", "montazownia": "montaz"}.get(h, "social" if "post=" in (url or "") or h == "raport" else "inne")


def notify_store(title, body, url="/", kind=None, data=None):
    """Każde powiadomienie trafia też do centrum powiadomień w apce (nawet gdy push nie dojdzie)."""
    L = _read("notifs.json", [])
    nid = (L[-1]["id"] + 1) if L else 1
    L.append({"id": nid, "t": time.time(), "title": title[:120], "body": (body or "")[:600], "url": url,
              "kind": kind or _kind(url), "data": data or {}, "read": False})
    _write("notifs.json", L[-400:])
    return nid


def push_all(title, body, url="/", kind=None, data=None):
    try:
        nid = notify_store(title, body, url, kind, data)
        if url in ("/", "") or url.startswith("/#"):
            url = f"/#powiadomienia={nid}"
    except Exception:
        pass
    subs = _read("push.json", [])
    if not subs:
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return 0
    pem, _ = _vapid()
    # Apple (iPhone) odrzuca VAPID z adresem @localhost – kontaktem jest adres centrum
    dom = os.environ.get("SOCIAL_DOMAIN", "")
    claims = {"sub": f"https://{dom}" if dom else "mailto:admin@example.com"}
    keep, sent, results = [], 0, []
    for s in subs:
        host = urllib.parse.urlparse(s.get("endpoint", "")).netloc
        try:
            webpush(subscription_info=s, data=json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False),
                    vapid_private_key=pem, vapid_claims=dict(claims), ttl=86400, headers={"Urgency": "high"})
            keep.append(s); sent += 1
            results.append({"host": host, "ok": True, "code": 201})
        except WebPushException as e:
            code = getattr(getattr(e, "response", None), "status_code", 0)
            txt = (getattr(getattr(e, "response", None), "text", "") or str(e))[:160]
            results.append({"host": host, "ok": False, "code": code, "err": txt})
            if code not in (404, 410):
                keep.append(s)
        except Exception as e:
            results.append({"host": host, "ok": False, "code": 0, "err": str(e)[:160]})
            keep.append(s)
    _write("push.json", keep)
    log = _read("push_log.json", [])
    log.append({"t": time.time(), "title": title[:80], "sent": sent, "subs": len(subs), "results": results})
    _write("push_log.json", log[-60:])
    return sent


@router.get("/api/notifs")
def notifs(limit: int = 150, kind: str = ""):
    L = [n for n in _read("notifs.json", []) if not kind or n["kind"] == kind]
    return {"items": L[::-1][:limit], "unread": sum(1 for n in _read("notifs.json", []) if not n["read"])}


@router.post("/api/notifs/delete")
async def notifs_delete(req: Request):
    """Kasowanie: jedno (id), z jednej kategorii (kind), przeczytane (read) albo wszystkie (all)."""
    b = await req.json()
    L = _read("notifs.json", [])
    if b.get("all"):
        keep = [n for n in L if b.get("kind") and n["kind"] != b["kind"]]
    elif b.get("read"):
        keep = [n for n in L if not n["read"] or (b.get("kind") and n["kind"] != b["kind"])]
    else:
        ids = set(b.get("ids") or [b.get("id")])
        keep = [n for n in L if n["id"] not in ids]
    _write("notifs.json", keep)
    return {"ok": True, "deleted": len(L) - len(keep), "unread": sum(1 for n in keep if not n["read"])}


@router.post("/api/notifs/read")
async def notifs_read(req: Request):
    b = await req.json()
    L = _read("notifs.json", [])
    for n in L:
        if b.get("all") or n["id"] == b.get("id"):
            n["read"] = True
    _write("notifs.json", L)
    return {"ok": True, "unread": sum(1 for n in L if not n["read"])}


@router.get("/api/social/push/key")
def push_key():
    try:
        import pywebpush  # noqa: F401
        return {"key": _vapid()[1], "count": len(_read("push.json", []))}
    except Exception:
        return {"key": None, "count": 0}


@router.post("/api/social/push/subscribe")
async def push_subscribe(req: Request):
    sub = (await req.json()).get("sub") or {}
    if not str(sub.get("endpoint", "")).startswith("https://"):
        return JSONResponse({"error": "zła subskrypcja"}, status_code=400)
    subs = [s for s in _read("push.json", []) if s.get("endpoint") != sub["endpoint"]] + [sub]
    _write("push.json", subs[-10:])
    return {"ok": True, "count": len(subs)}


@router.post("/api/social/push/unsubscribe")
async def push_unsubscribe(req: Request):
    ep = (await req.json()).get("endpoint", "")
    _write("push.json", [s for s in _read("push.json", []) if s.get("endpoint") != ep])
    return {"ok": True}


@router.post("/api/social/push/test")
def push_test():
    n = push_all("Powiadomienia działają ✅", "Test z centrum – jeśli to widzisz, wszystko gra.", "/#straznicy")
    last = (_read("push_log.json", []) or [{}])[-1]
    return {"sent": n, "subs": last.get("subs", 0), "results": last.get("results", [])}


# ---------------------------------------------------------------- post się rozkręca
LEVELS = (2, 5, 10)


def detect_alerts(accounts):
    st = _read("alerts.json", None)
    first = st is None
    st = st or {"sent": {}, "list": []}
    now = time.time()
    for k, a in accounts.items():
        posts = a.get("posts") or []
        med = _median([p.get("views") or 0 for p in posts])
        if len(posts) < 5 or not med:
            continue
        for p in posts:
            t = post_time(p)
            if not t or now - t.timestamp() > 14 * 86400:
                continue
            v = p.get("views") or 0
            ratio = v / med
            lvl = max([l for l in LEVELS if ratio >= l] or [0])
            if not lvl or v < 300:
                continue
            key = f"{k}|{p['id']}"
            if lvl <= st["sent"].get(key, 0):
                continue
            st["sent"][key] = lvl
            if first:
                continue          # przy pierwszym uruchomieniu tylko zapamiętujemy – bez zalewu powiadomień
            hrs = max(1, round((now - t.timestamp()) / 3600))
            age = f"{hrs} h" if hrs < 48 else f"{round(hrs / 24)} dni"
            title = f"🔥 Post się rozkręca ({NAMES.get(a['platform'], '')})"
            body = f"„{(p.get('title') or 'Bez opisu')[:70]}” – {v:,} wyświetleń w {age}, {ratio:.0f}× więcej niż zwykle na {a.get('name', '')}".replace(",", " ")
            st["list"].insert(0, {"key": k, "id": p["id"], "title": p.get("title"), "platform": a["platform"], "acc": a.get("name"),
                                  "views": v, "ratio": round(ratio, 1), "level": lvl, "at": now})
            push_all(title, body, f"/?post={k}|{p['id']}")
    st["list"] = st["list"][:40]
    # stare wpisy (posty starsze niż 30 dni) – zapominamy
    _write("alerts.json", st)


@router.get("/api/social/alerts")
def alerts():
    return _read("alerts.json", {"list": []}).get("list", [])


# ---------------------------------------------------------------- pora publikacji
SLOTS = [(0, 6, "0–6"), (6, 9, "6–9"), (9, 12, "9–12"), (12, 15, "12–15"), (15, 18, "15–18"), (18, 21, "18–21"), (21, 24, "21–24")]


def best_slot(accounts):
    cells = {}
    for a in accounts.values():
        posts = a.get("posts") or []
        med = _median([p.get("views") or 0 for p in posts])
        if not med:
            continue
        for p in posts:
            t = post_time(p)
            if not t:
                continue
            s = next(i for i, (a0, a1, _) in enumerate(SLOTS) if a0 <= t.hour < a1)
            cells.setdefault((t.weekday(), s), []).append((p.get("views") or 0) / med)
    good = [(statistics.median(v), k, len(v)) for k, v in cells.items() if len(v) >= 3]
    if not good:
        return None
    sc, (wd, s), n = max(good)
    return {"day": DNI[wd], "slot": SLOTS[s][2], "score": round(sc, 2), "n": n}


# ---------------------------------------------------------------- raport tygodnia
def _at(hist_pts, d):
    """Wyświetlenia posta na koniec dnia d (z zapisanych odświeżeń)."""
    v = None
    for stamp, val in hist_pts:
        if stamp[:10] <= d:
            v = val
    return v


def _fol_at(hist, d):
    v = None
    for h in hist:
        if h["date"] <= d:
            v = h["followers"]
    return v


def build_report(ws):
    we = ws + timedelta(days=6)
    s0, s1, sp = ws.isoformat(), we.isoformat(), (ws - timedelta(days=1)).isoformat()
    accs = _read("cache.json", {}).get("accounts", {})
    rows, best = [], None
    for k, a in accs.items():
        f1, f0 = _fol_at(a.get("history", []), s1), _fol_at(a.get("history", []), sp)
        new = [p for p in a.get("posts", []) if s0 <= (p.get("date") or "") <= s1]
        gains = 0
        for p in a.get("posts", []):
            pts = a.get("post_hist", {}).get(p["id"], [])
            e, b = _at(pts, s1), _at(pts, sp)
            fresh = (p.get("date") or "") >= s0
            if e is None and fresh and (p.get("date") or "") <= s1:
                e = p.get("views") or 0
            if b is None and fresh:
                b = 0
            if e is None or b is None:
                continue
            gain = max(0, e - b)
            gains += gain
            if gain and (best is None or gain > best["gain"]):
                best = {"gain": gain, "title": p.get("title"), "acc": a.get("name"), "platform": a["platform"], "key": k, "id": p["id"],
                        "views": p.get("views"), "url": p.get("url"), "thumb": p.get("thumb")}
        if a["platform"] == "youtube" and a.get("daily"):
            views = sum(d["views"] for d in a["daily"] if s0 <= d["date"] <= s1)
        else:
            views = gains
        rows.append({"key": k, "name": a.get("name"), "platform": a["platform"], "group": a.get("group"),
                     "followers": f1, "followers_delta": (f1 - f0) if f1 is not None and f0 is not None else None,
                     "views": views, "posts": len(new)})
    prev_posts = sum(1 for a in accs.values() for p in a.get("posts", []) if (ws - timedelta(days=7)).isoformat() <= (p.get("date") or "") < s0)
    tot = {"followers_delta": sum(r["followers_delta"] or 0 for r in rows), "views": sum(r["views"] for r in rows),
           "posts": sum(r["posts"] for r in rows), "prev_posts": prev_posts}
    tips = []
    bs = best_slot(accs)
    if bs and bs["score"] >= 1.2:
        tips.append(f"Najlepiej chodzą posty wrzucane w {bs['day']} między {bs['slot']} – średnio "
                    f"{str(round(bs['score'], 1)).replace('.', ',')}× więcej wyświetleń niż zwykle.")
    if tot["posts"] == 0:
        tips.append("W tym tygodniu nie było nowych postów – algorytmy wolą regularność, nawet 2–3 posty w tygodniu robią różnicę.")
    elif tot["posts"] < prev_posts:
        tips.append(f"W tym tygodniu wrzuciłeś {tot['posts']} postów, tydzień wcześniej {prev_posts}. Regularność zwykle podbija zasięgi.")
    rep = {"week": s0, "end": s1, "accounts": rows, "total": tot, "best": best, "tips": tips, "made": time.time()}
    key, url = _ai_key()
    if key:
        try:
            txt = _ai(key, url, "Jesteś analitykiem social mediów. Na podstawie danych tygodnia napisz JEDNĄ krótką, konkretną wskazówkę "
                                "na następny tydzień (max 2 zdania, po polsku, bez wstępów, bez emoji). Dane:\n" +
                      json.dumps({k: rep[k] for k in ("accounts", "total", "best")}, ensure_ascii=False), 200)
            if txt:
                rep["tips"].append(txt.strip())
        except Exception:
            pass
    return rep


def _ai(key, url, prompt, max_tokens=400):
    r = httpx.post(url, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=60,
                   json={"model": "claude-haiku-4-5-20251001", "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
    j = r.json()
    return "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")


def weekly_job():
    now = datetime.now(TZ)
    ws = (now.date() - timedelta(days=now.weekday() + 7))         # poniedziałek poprzedniego tygodnia
    reps = _read("reports.json", {})
    if ws.isoformat() in reps or (now.weekday() == 0 and now.hour < 8):
        return
    rep = build_report(ws)
    reps[ws.isoformat()] = rep
    _write("reports.json", dict(sorted(reps.items())[-12:]))
    t = rep["total"]
    d = t["followers_delta"]
    body = (f"{'+' if d >= 0 else '−'}{abs(d)} obserwujących, {t['views']:,} wyświetleń".replace(",", " ") +
            (f". Najlepszy: „{(rep['best']['title'] or '')[:50]}”" if rep.get("best") else ""))
    push_all("📊 Raport tygodnia gotowy", body, "/#raport")


@router.get("/api/social/report")
def report(week: str = ""):
    reps = _read("reports.json", {})
    if not reps:
        weekly_job()
        reps = _read("reports.json", {})
    weeks = sorted(reps)
    if not weeks:
        return {"report": None, "weeks": []}
    w = week if week in reps else weeks[-1]
    return {"report": reps[w], "weeks": weeks}


# ---------------------------------------------------------------- co działa (PozaSkala)
def fmt_kind(a, p):
    if a["platform"] == "youtube":
        return "Shorty" if (p.get("dur") or 0) and p["dur"] <= 180 else "Długie filmy"
    if a["platform"] == "instagram":
        t = (p.get("type") or "").upper()
        return "Rolki" if "REEL" in t or t == "VIDEO" else "Karuzele" if "CAROUSEL" in t else "Zdjęcia"
    return "TikToki"


LEN_B = [(0, 15, "do 15 s"), (15, 30, "15–30 s"), (30, 60, "30–60 s"), (60, 180, "1–3 min"), (180, 1e9, "ponad 3 min")]
_ai_lock = threading.Lock()


@router.get("/api/social/insights")
def insights(group: str = "pozaskala", force: int = 0):
    accs = {k: a for k, a in _read("cache.json", {}).get("accounts", {}).items() if a.get("group") == group}
    items = []
    for k, a in accs.items():
        posts = a.get("posts") or []
        med = _median([p.get("views") or 0 for p in posts])
        if not med:
            continue
        for p in posts:
            v = p.get("views") or 0
            eng = ((p.get("likes") or 0) + (p.get("comments") or 0) + (p.get("shares") or 0) + (p.get("saved") or 0)) / v if v else 0
            items.append({"key": k, "id": p["id"], "title": p.get("title"), "acc": a.get("name"), "platform": a["platform"],
                          "views": v, "rel": v / med, "eng": eng, "kind": fmt_kind(a, p), "dur": p.get("dur"),
                          "date": p.get("date"), "thumb": p.get("thumb"), "url": p.get("url")})
    if len(items) < 6:
        return {"ok": False, "group": GROUPS.get(group, group), "msg": "Za mało postów do porównania – potrzeba co najmniej 6."}
    kinds = {}
    for it in items:
        kinds.setdefault((it["platform"], it["kind"]), []).append(it)
    formats = sorted([{"platform": pl, "kind": kd, "n": len(v), "rel": round(statistics.median(x["rel"] for x in v), 2),
                       "views": int(statistics.median(x["views"] for x in v)),
                       "eng": round(statistics.mean(x["eng"] for x in v) * 100, 1)} for (pl, kd), v in kinds.items()],
                     key=lambda x: -x["rel"])
    lens = []
    for a0, a1, lab in LEN_B:
        v = [x for x in items if x["dur"] and a0 <= x["dur"] < a1]
        if v:
            lens.append({"label": lab, "n": len(v), "rel": round(statistics.median(x["rel"] for x in v), 2)})
    srt = sorted(items, key=lambda x: -x["rel"])
    top, bottom = srt[:5], srt[-5:][::-1]
    out = {"ok": True, "group": GROUPS.get(group, group), "n": len(items), "formats": formats, "lengths": lens,
           "top": top, "bottom": bottom, "slot": best_slot(accs), "ai": None}
    # wniosek AI – raz na tydzień albo na żądanie (najwyżej raz na godzinę)
    cache = _read("insights_ai.json", {})
    sig = hashlib.md5(json.dumps([x["id"] for x in top + bottom]).encode()).hexdigest()
    c = cache.get(group)
    fresh = c and (c.get("sig") == sig or time.time() - c.get("at", 0) < 7 * 86400)
    if c and (fresh and not force or (force and time.time() - c.get("at", 0) < 3600)):
        out["ai"] = c.get("text")
    else:
        key, url = _ai_key()
        if key and _ai_lock.acquire(blocking=False):
            try:
                desc = lambda xs: "\n".join(f"- [{x['platform']}/{x['kind']}{', ' + str(int(x['dur'])) + ' s' if x['dur'] else ''}] "
                                            f"„{x['title'] or 'bez opisu'}” – {x['rel']:.1f}× mediany konta, zaang. {x['eng'] * 100:.1f}%" for x in xs)
                txt = _ai(key, url, "Analizujesz posty marki PozaSkala (edukacyjne filmy/posty tworzone z pomocą AI). Porównaj 5 najlepszych "
                          "i 5 najsłabszych postów (wynik względem mediany konta) i napisz 3 krótkie, konkretne wnioski po polsku: "
                          "co mają wspólnego najlepsze (temat, haczyk w tytule, format, długość), czego unikać i co zrobić w kolejnych postach. "
                          "Każdy wniosek w osobnej linii zaczynającej się od „• ”. Bez wstępu i bez emoji.\n\nNAJLEPSZE:\n" + desc(top) +
                          "\n\nNAJSŁABSZE:\n" + desc(bottom) + "\n\nFormaty (mediana względem konta): " +
                          ", ".join(f"{f['kind']} ({NAMES[f['platform']]}) {f['rel']}×" for f in formats), 500)
                if txt.strip():
                    cache[group] = {"sig": sig, "at": time.time(), "text": txt.strip()}
                    _write("insights_ai.json", cache)
                    out["ai"] = txt.strip()
            except Exception:
                out["ai"] = c.get("text") if c else None
            finally:
                _ai_lock.release()
        elif c:
            out["ai"] = c.get("text")
    out["ai_available"] = bool(_ai_key()[0])
    return out


def after_refresh(accounts):
    try:
        detect_alerts(accounts)
    except Exception as e:
        print("alerts error", e, flush=True)
    try:
        weekly_job()
    except Exception as e:
        print("report error", e, flush=True)
