"""Zakładka „Treści”: kalendarz miesięczny (pomysły + posty z planera + opublikowane), przeciąganie między dniami,
karty pomysłów z AI (hook, plan ujęć, CTA) i wyniki postów względem Twojej normy (mediana wyświetleń konta)."""
import secrets, statistics, threading, time
from datetime import datetime, timedelta

from fastapi import APIRouter, Request

from social import _read, _write
from planer import TZ, plan, save as plan_save, _lock as plan_lock

router = APIRouter()
F = "pomysly.json"
STAGES = ["pomysl", "nagrane", "zmontowane", "zaplanowane", "opublikowane"]
FIELDS = ("title", "day", "stage", "format", "length", "hook", "script", "cta", "caption", "notes", "pid")
_lk = threading.RLock()


def _ideas():
    return _read(F, [])


def _ts(p):
    t = (p.get("ts") or "").replace("Z", "+00:00")
    if len(t) > 5 and t[-5] in "+-" and t[-3] != ":":
        t = t[:-2] + ":" + t[-2:]
    try:
        return datetime.fromisoformat(t).timestamp()
    except Exception:
        return None


def results():
    """Opublikowane posty z kont prywatnych + ile razy lepiej/gorzej od normy konta."""
    out = []
    for k, a in (_read("cache.json", {}).get("accounts") or {}).items():
        if a.get("group") != "prywatne":
            continue
        posts = [p for p in (a.get("posts") or []) if _ts(p)]
        now = time.time()
        old = [p.get("views") or 0 for p in posts if now - _ts(p) > 2 * 86400 and p.get("views")]
        med = statistics.median(old) if len(old) >= 3 else 0
        for p in posts:
            t = _ts(p)
            v = p.get("views") or 0
            out.append({"id": p["id"], "platform": a.get("platform"), "acc": a.get("name"), "title": p.get("title") or "",
                        "t": t, "views": v, "likes": p.get("likes") or 0, "comments": p.get("comments") or 0,
                        "thumb": p.get("thumb") or "", "url": p.get("url") or "", "norm": int(med),
                        "rel": round(v / med, 2) if med else None, "fresh": now - t < 2 * 86400})
    return sorted(out, key=lambda x: -x["t"])


def _stage_from(it):
    return {"draft": "zmontowane", "approved": "zaplanowane", "publishing": "zaplanowane",
            "published": "opublikowane", "error": "zaplanowane"}.get(it.get("status"))


@router.get("/api/tresci")
def api_get(month: str = ""):
    try:
        m0 = datetime.strptime(month, "%Y-%m").replace(tzinfo=TZ)
    except ValueError:
        m0 = datetime.now(TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    a = (m0 - timedelta(days=7)).timestamp()
    b = (m0 + timedelta(days=45)).timestamp()
    P = {x["id"]: x for x in plan()}
    R = results()
    rid = {r["id"]: r for r in R}
    posts, used = [], set()
    for x in P.values():
        if x["status"] == "skipped" or not (a <= x["when"] <= b):
            continue
        ids = [v.get("id") for v in (x.get("results") or {}).values() if v.get("ok") and v.get("id")]
        used.update(ids)
        st = [rid[i] for i in ids if i in rid]
        posts.append({"id": x["id"], "src": "plan", "t": x.get("done_at") if x["status"] == "published" else x["when"],
                      "label": x.get("label"), "status": x["status"], "kind": x.get("kind"), "platforms": x.get("platforms"),
                      "thumb": f"/api/planer/{x['id']}/preview" if x.get("kind") == "karuzela" else "",
                      "video": f"/api/planer/{x['id']}/preview" if x.get("kind") == "film" else "",
                      "rel": max((s["rel"] for s in st if s["rel"]), default=None), "views": sum(s["views"] for s in st) or None,
                      "movable": x["status"] in ("draft", "approved", "error")})
    for r in R:
        if r["id"] in used or not (a <= r["t"] <= b):
            continue
        posts.append({"id": r["id"], "src": "pub", "t": r["t"], "label": r["title"] or "(bez opisu)", "status": "published",
                      "platforms": [r["platform"]], "thumb": r["thumb"], "url": r["url"], "rel": r["rel"],
                      "views": r["views"], "fresh": r["fresh"], "movable": False})
    I = _ideas()
    for i in I:
        if i.get("pid") in P:
            i["stage"] = _stage_from(P[i["pid"]]) or i.get("stage")
            i["pwhen"] = P[i["pid"]]["when"]
    free = [{"id": x["id"], "label": x.get("label"), "when": x["when"]} for x in P.values()
            if x["status"] in ("draft", "approved") and not any(i.get("pid") == x["id"] for i in I)]
    return {"month": m0.strftime("%Y-%m"), "ideas": I, "posts": posts, "free": free, "results": R[:40]}


@router.post("/api/tresci/idea")
async def api_idea(req: Request):
    b = await req.json()
    with _lk:
        I = _ideas()
        it = next((i for i in I if i["id"] == b.get("id")), None)
        if not it:
            if not str(b.get("title", "")).strip():
                return {"ok": False, "error": "Wpisz tytuł."}
            it = {"id": secrets.token_hex(4), "stage": "pomysl", "created": time.time()}
            I.append(it)
        for k in FIELDS:
            if k in b:
                v = b[k]
                it[k] = (str(v)[:4000] if v is not None else None)
        if it.get("stage") not in STAGES:
            it["stage"] = "pomysl"
        _write(F, I[-300:])
    return {"ok": True, "idea": it}


@router.post("/api/tresci/idea/{iid}/delete")
def api_del(iid: str):
    with _lk:
        _write(F, [i for i in _ideas() if i["id"] != iid])
    return {"ok": True}


@router.post("/api/tresci/move")
async def api_move(req: Request):
    b = await req.json()
    day = b.get("day")  # "YYYY-MM-DD" albo null (bank pomysłów)
    if b.get("kind") == "idea":
        with _lk:
            I = _ideas()
            for i in I:
                if i["id"] == b.get("id"):
                    i["day"] = day
            _write(F, I)
        return {"ok": True}
    if not day:
        return {"ok": False, "error": "Post musi mieć dzień."}
    with plan_lock:
        P = plan()
        it = next((x for x in P if x["id"] == b.get("id")), None)
        if not it or it["status"] not in ("draft", "approved", "error"):
            return {"ok": False, "error": "Tego posta nie da się przesunąć."}
        old = datetime.fromtimestamp(it["when"], TZ)
        y, mo, d = map(int, day.split("-"))
        new = old.replace(year=y, month=mo, day=d)
        if new.timestamp() < time.time() + 300:
            return {"ok": False, "error": "To już minęło – wybierz późniejszy dzień."}
        it["when"] = new.timestamp()
        if it["status"] == "error":
            it["status"] = "approved"
        plan_save(P)
    return {"ok": True}


@router.post("/api/tresci/idea/{iid}/ai")
def api_ai(iid: str):
    from agency import ai_ready, claude, as_json
    if not ai_ready():
        return {"ok": False, "error": "AI niedostępne"}
    it = next((i for i in _ideas() if i["id"] == iid), None)
    if not it:
        return {"ok": False, "error": "nie ma"}
    R = [r for r in results() if r["rel"] and not r["fresh"]]
    top = sorted(R, key=lambda r: -r["rel"])[:6]
    low = sorted(R, key=lambda r: r["rel"])[:3]
    ctx = "\n".join(f"- {r['rel']}× normy ({r['platform']}): {r['title'][:90]}" for r in top) or "- brak danych"
    ctx_low = "\n".join(f"- {r['rel']}× normy ({r['platform']}): {r['title'][:90]}" for r in low) or "- brak danych"
    p = ("Jesteś strategiem krótkich filmów (Reels/Shorts/TikTok) dla Bartłomieja – polski twórca lifestyle: siłownia, "
         "jedzenie/redukcja, codzienne vlogi (seria Daily WNBF), od listopada tygodniowy vlog na YT + klipy promujące.\n"
         f"POMYSŁ: {it.get('title')}\nNOTATKI: {it.get('notes') or '-'}\nFORMAT (jeśli podany): {it.get('format') or '-'}\n\n"
         f"Jego posty, które poszły NAJLEPIEJ względem normy:\n{ctx}\nNajsłabsze:\n{ctx_low}\n\n"
         "Wyciągnij wnioski z tych danych (jakie tematy/hooki działają) i rozpisz pomysł. Zasady hooka: pierwsze 1-2 s, "
         "konkret, ciekawość albo wynik, bez „hej, dzisiaj…”. Wszystko po polsku, naturalnym językiem, bez emotek w hooku.\n"
         "Zwróć TYLKO JSON: {\"hook\": \"co mówi/pokazuje w pierwszych 2 s + napis na ekranie\", "
         "\"hooks_alt\": [\"2 inne warianty hooka\"], \"script\": \"plan ujęć w punktach (5-8 linijek, każda od '- ')\", "
         "\"cta\": \"jedno zdanie CTA na koniec\", \"format\": \"np. vlog z napisami / talking head / przepis POV\", "
         "\"length\": \"sugerowana długość w s\", \"caption\": \"opis posta 2-3 zdania + 3-5 hashtagów\", "
         "\"why\": \"1 zdanie: dlaczego to powinno zadziałać, odwołaj się do jego danych\"}")
    try:
        r = as_json(claude(p, max_tokens=1400)) or {}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    if not r.get("hook"):
        return {"ok": False, "error": "AI nie zwróciło wyniku – spróbuj jeszcze raz"}
    alt = r.get("hooks_alt") or []
    with _lk:
        I = _ideas()
        it = next((i for i in I if i["id"] == iid), None)
        it.update(hook=r["hook"], script=r.get("script", ""), cta=r.get("cta", ""), caption=r.get("caption", ""),
                  format=it.get("format") or r.get("format", ""), length=it.get("length") or str(r.get("length", "")),
                  hooks_alt=alt[:3], why=r.get("why", ""))
        _write(F, I)
    return {"ok": True, "idea": it}
