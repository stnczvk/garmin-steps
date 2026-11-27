"""Widzowie: bank wyzwań z komentarzy (Instagram + YouTube prywatnych kont) i porównanie typów hooków po wynikach postów.
TikTok nie udostępnia treści komentarzy przez oficjalne API – stamtąd liczymy tylko statystyki."""
import json, re, threading, time, uuid

import httpx
from fastapi import APIRouter, Request

from social import _read, _write, _accounts, _ig_token, _google_access, IG

router = APIRouter()
_lock = threading.Lock()


# ---------------------------------------------------------------- bank wyzwań z komentarzy
def _komentarze_ig(key, acc, seen):
    tok = _ig_token(key, acc)
    posty = (_read("cache.json", {}).get("accounts", {}).get(key) or {}).get("posts") or []
    out = []
    for p in posty[:15]:
        try:
            r = httpx.get(f"{IG}/{p['id']}/comments", params={"fields": "id,text,like_count,username,timestamp", "access_token": tok},
                          timeout=30).json()
        except Exception:
            continue
        for c in r.get("data") or []:
            if c["id"] in seen or c.get("username") == acc.get("name", "").lstrip("@"):
                continue
            out.append({"id": c["id"], "tekst": c.get("text", ""), "lajki": c.get("like_count", 0), "autor": c.get("username"),
                        "platforma": "instagram", "post": p.get("url"), "post_tytul": p.get("title")})
    return out


def _komentarze_yt(acc, seen):
    tok = _google_access(acc)
    r = httpx.get("https://www.googleapis.com/youtube/v3/commentThreads", headers={"Authorization": f"Bearer {tok}"}, timeout=30,
                  params={"part": "snippet", "allThreadsRelatedToChannelId": acc["channel_id"], "maxResults": 100, "order": "time"}).json()
    out = []
    for t in r.get("items") or []:
        s = t["snippet"]["topLevelComment"]["snippet"]
        if t["id"] in seen or s.get("authorChannelId", {}).get("value") == acc["channel_id"]:
            continue
        out.append({"id": t["id"], "tekst": s.get("textOriginal", ""), "lajki": s.get("likeCount", 0), "autor": s.get("authorDisplayName"),
                    "platforma": "youtube", "post": f"https://youtu.be/{t['snippet'].get('videoId', '')}"})
    return out


def zbierz():
    """Nowe komentarze z prywatnych kont → AI wyciąga propozycje wyzwań → bank wyzwań (z liczbą głosów i lajków)."""
    seen = set(_read("widzowie_seen.json", []))
    nowe, bledy = [], []
    for key, acc in _accounts().items():
        if acc.get("group", "prywatne") != "prywatne":
            continue
        try:
            if acc.get("platform") == "instagram":
                nowe += _komentarze_ig(key, acc, seen)
            elif acc.get("platform") == "youtube" and acc.get("refresh_token") and acc.get("channel_id"):
                nowe += _komentarze_yt(acc, seen)
        except Exception as e:
            bledy.append(f"{acc.get('name')}: {e}")
    if nowe:
        _write("widzowie_seen.json", (list(seen) + [c["id"] for c in nowe])[-5000:])
    kand = [c for c in nowe if len(c["tekst"].strip()) >= 4]
    dodane = 0
    if kand:
        from agency import claude, as_json, SMART
        W = _read("wyzwania.json", [])
        lista = "\n".join(f"{i}. [{c['platforma']}] {c['tekst'][:200]}" for i, c in enumerate(kand))
        znane = "\n".join(f"- {w['id']}: {w['wyzwanie']}" for w in W[-60:])
        p = f"""To komentarze pod filmami Bartka (163 cm, robi filmy o jedzeniu na redukcji i wyzwania jedzeniowe od widzów).
Wybierz TYLKO te, w których widz proponuje wyzwanie albo coś do zjedzenia/sprawdzenia w filmie (np. „zjedz 6 drwali”, „spróbuj kebaba XXL”).
Pomiń pochwały, pytania, hejt i zwykłe rozmowy.
Komentarze:
{lista}

Wyzwania już zapisane (jeśli komentarz proponuje to samo, podaj jego id w "to_samo"):
{znane or "brak"}

Zwróć TYLKO JSON: {{"wyzwania":[{{"nr":numer komentarza,"wyzwanie":"krótko, np. 6 Drwali z McDonalda","to_samo":"id albo null"}}]}}"""
        try:
            r = as_json(claude(p, SMART, 2000)) or {}
        except Exception as e:
            r = {}
            bledy.append(f"AI: {e}")
        with _lock:
            W = _read("wyzwania.json", [])
            for x in r.get("wyzwania") or []:
                try:
                    c = kand[int(x["nr"])]
                except Exception:
                    continue
                gl = {"autor": c["autor"], "tekst": c["tekst"][:300], "lajki": c["lajki"], "platforma": c["platforma"], "post": c["post"], "at": time.time()}
                w = next((w for w in W if x.get("to_samo") and w["id"] == x["to_samo"]), None)
                if w:
                    w["glosy"].append(gl)
                else:
                    W.append({"id": uuid.uuid4().hex[:8], "wyzwanie": str(x.get("wyzwanie") or c["tekst"])[:120], "status": "nowe",
                              "glosy": [gl], "at": time.time()})
                dodane += 1
            _write("wyzwania.json", W[-300:])
    S = {"t": time.time(), "komentarzy": len(nowe), "wyzwan": dodane, "bledy": bledy}
    _write("widzowie_stan.json", S)
    return S


def _ranking(W):
    for w in W:
        w["ile"] = len(w["glosy"])
        w["lajki"] = sum(g.get("lajki") or 0 for g in w["glosy"])
        w["punkty"] = w["ile"] * 3 + w["lajki"]
    return sorted(W, key=lambda w: (w["status"] != "nowe", -w["punkty"], -w["at"]))


@router.get("/api/widzowie/wyzwania")
def api_wyzwania():
    return {"wyzwania": _ranking(_read("wyzwania.json", [])), "stan": _read("widzowie_stan.json", {})}


@router.post("/api/widzowie/wyzwania/{wid}")
async def api_wyzwanie_status(wid: str, req: Request):
    b = await req.json()
    with _lock:
        W = _read("wyzwania.json", [])
        W = [w for w in W if not (w["id"] == wid and b.get("usun"))]
        for w in W:
            if w["id"] == wid and b.get("status") in ("nowe", "zrobione", "odrzucone"):
                w["status"] = b["status"]
        _write("wyzwania.json", W)
    return {"ok": True}


@router.post("/api/widzowie/zbierz")
def api_zbierz():
    return zbierz()


# ---------------------------------------------------------------- porównanie hooków
TYPY = ["dzień N / seria", "odpowiedź na komentarz", "pytanie do widza", "żart z redukcji / kontrast", "liczba / wynik",
        "historia od środka", "wzrost 163 cm", "inne"]


def hooki():
    """Posty prywatnych kont starsze niż 3 dni (max 45 dni): typ hooka (AI, raz na post) → średnie wyniki na typ."""
    acc = _read("cache.json", {}).get("accounts") or {}
    teraz = time.time()
    posty = []
    for k, a in acc.items():
        if a.get("group", "prywatne") != "prywatne":
            continue
        for p in a.get("posts") or []:
            try:
                t = time.mktime(time.strptime(p.get("date", "")[:10], "%Y-%m-%d"))
            except Exception:
                continue
            if 3 * 86400 <= teraz - t <= 45 * 86400 and p.get("title") and p.get("views") is not None:
                posty.append({**p, "platforma": a.get("platform"), "konto": a.get("name")})
    typy = _read("hooki_typy.json", {})
    brak = [p for p in posty if (p.get("url") or p["title"]) not in typy]
    if brak:
        from agency import claude, as_json, SMART
        lista = "\n".join(f"{i}. {p['title'][:160]}" for i, p in enumerate(brak[:80]))
        try:
            r = as_json(claude(f"""Przypisz każdemu początkowi opisu/hookowi filmu JEDEN typ z listy: {", ".join(TYPY)}.
{lista}
Zwróć TYLKO JSON: {{"typy":[{{"nr":0,"typ":"..."}}]}}""", SMART, 3000)) or {}
            for x in r.get("typy") or []:
                try:
                    p = brak[int(x["nr"])]
                    typy[p.get("url") or p["title"]] = x["typ"] if x.get("typ") in TYPY else "inne"
                except Exception:
                    pass
            _write("hooki_typy.json", typy)
        except Exception as e:
            print("hooki typy", e, flush=True)
    T = {}
    for p in posty:
        ty = typy.get(p.get("url") or p["title"])
        if not ty:
            continue
        g = T.setdefault(ty, {"typ": ty, "n": 0, "views": 0, "likes": 0, "comments": 0, "shares": 0, "najlepszy": None})
        v = p.get("views") or 0
        g["n"] += 1; g["views"] += v; g["likes"] += p.get("likes") or 0
        g["comments"] += p.get("comments") or 0; g["shares"] += p.get("shares") or 0
        if not g["najlepszy"] or v > g["najlepszy"]["views"]:
            g["najlepszy"] = {"title": p["title"][:80], "views": v, "url": p.get("url"), "platforma": p["platforma"]}
    out = []
    for g in T.values():
        out.append({"typ": g["typ"], "postow": g["n"], "sr_wyswietlen": round(g["views"] / g["n"]),
                    "zaangazowanie_proc": round((g["likes"] + g["comments"] * 2 + g["shares"] * 3) / max(1, g["views"]) * 100, 2),
                    "najlepszy": g["najlepszy"]})
    return sorted(out, key=lambda x: -x["sr_wyswietlen"])


@router.get("/api/widzowie/hooki")
def api_hooki():
    return {"typy": hooki()}


# ---------------------------------------------------------------- co 6 godzin
def _loop():
    time.sleep(240)
    while True:
        try:
            zbierz()
        except Exception as e:
            print("widzowie", e, flush=True)
        time.sleep(6 * 3600)


threading.Thread(target=_loop, daemon=True).start()
