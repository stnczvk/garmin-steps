"""Kanały faceless (11.2026): „Teoria w 30 sekund” (quiz z prawa jazdy) i „Rekrutacja bez Stresu”.
Codziennie: Gemini (Vertex, kredyty Google Cloud) pisze skrypt -> drugi przebieg sprawdza poprawność -> Google TTS -> render (PIL+ffmpeg)
-> wpis do planera (YouTube Shorts, + Reels jeśli kanał ma Instagram)."""
import base64, json, os, random, secrets, subprocess, sys, threading, time, traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from social import _read, _write, ROOT

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
F = "faceless.json"
WORK = ROOT / "faceless"
WORK.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_busy = set()

CH = {
    "teoria": {"name": "Teoria w 30 sekund", "voice": "pl-PL-Chirp3-HD-Fenrir", "hour": 6, "pub": 17, "cat": "27",
               "tags": ["prawo jazdy", "egzamin na prawo jazdy", "teoria prawo jazdy", "pytania egzaminacyjne", "kategoria B", "przepisy drogowe"],
               "cta": "Trafiłeś? Napisz w komentarzu. Jutro kolejne pytanie."},
    "rekrutacja": {"name": "Rekrutacja bez Stresu", "voice": "pl-PL-Chirp3-HD-Puck", "voice2": "pl-PL-Chirp3-HD-Leda", "hour": 6, "pub": 19, "cat": "27",
                   "tags": ["rozmowa o pracę", "rekrutacja", "pytania rekrutacyjne", "jak odpowiedzieć", "praca", "CV"],
                   "cta": "Zapisz, zanim pójdziesz na rozmowę. Jutro kolejne pytanie."},
}
DEF = {"enabled": False, "auto": True}   # włączamy po rebrandingu kanałów

TEORIA_TEMATY = [
    "pierwszeństwo na skrzyżowaniu równorzędnym (prawa ręka)", "skręt w lewo a pojazd z naprzeciwka", "znak ustąp pierwszeństwa na skrzyżowaniu",
    "znak STOP", "droga z pierwszeństwem i skręt", "tramwaj na skrzyżowaniu równorzędnym", "rowerzysta jadący prosto a skręt w prawo",
    "skrzyżowanie typu T bez znaków", "trzy pojazdy na skrzyżowaniu równorzędnym", "zawracanie na skrzyżowaniu",
    "dopuszczalna prędkość w terenie zabudowanym", "prędkość na autostradzie i drodze ekspresowej", "prędkość poza terenem zabudowanym",
    "odległość postoju od przejścia dla pieszych", "zatrzymanie przed przejazdem kolejowym", "światła mijania w dzień i w nocy",
    "korzystanie z telefonu podczas jazdy", "dopuszczalne stężenie alkoholu", "pas ruchu dla autobusów", "jazda na suwak",
    "korytarz życia", "wyprzedzanie na przejściu dla pieszych", "pierwszeństwo pieszego wchodzącego na przejście", "przewożenie dzieci w foteliku",
    "parkowanie na chodniku", "trójkąt ostrzegawczy na autostradzie", "pierwsza pomoc na miejscu wypadku", "światła przeciwmgłowe",
    "rondo i kierunkowskaz", "strzałka warunkowa", "sygnał żółty migający", "opony i minimalna głębokość bieżnika", "pasy bezpieczeństwa",
    "holowanie pojazdu", "punkty karne dla młodego kierowcy", "okres próbny dla nowego kierowcy", "hulajnoga elektryczna na drodze",
    "odległość między pojazdami na autostradzie", "cofanie na drodze jednokierunkowej", "zatrzymanie a postój",
]
REK_TEMATY = [
    "Jaka jest Twoja największa wada?", "Dlaczego odchodzisz z obecnej pracy?", "Gdzie widzisz się za 5 lat?", "Opowiedz coś o sobie",
    "Dlaczego mamy wybrać właśnie Ciebie?", "Jakie masz oczekiwania finansowe?", "Dlaczego chcesz u nas pracować?", "Jaka jest Twoja największa zaleta?",
    "Opowiedz o konflikcie w pracy", "Opowiedz o swojej porażce", "Dlaczego masz przerwę w CV?", "Czy masz do nas jakieś pytania?",
    "Jak radzisz sobie ze stresem?", "Co wiesz o naszej firmie?", "Dlaczego tak często zmieniałeś pracę?", "Jak pracujesz w zespole?",
    "Opowiedz o swoim największym sukcesie", "Co zrobisz, jeśli nie zdążysz z terminem?", "Jak motywujesz się do pracy?",
    "Dlaczego zmieniasz branżę?", "Czy jesteś gotów na nadgodziny?", "Jak przyjmujesz krytykę?", "Kiedy możesz zacząć?",
    "Co robisz w wolnym czasie?", "Jak sobie radzisz z trudnym klientem?", "Czego nie lubiłeś w poprzedniej pracy?",
    "Dlaczego nie masz doświadczenia?", "Jak organizujesz swój dzień pracy?", "Co byś zmienił w swoim poprzednim miejscu pracy?",
    "Jaki był Twój najgorszy szef?", "Czy aplikujesz też gdzie indziej?", "Jak wyobrażasz sobie idealne miejsce pracy?",
]


# ---------------------------------------------------------------- stan
def _S():
    S = _read(F, {})
    S.setdefault("cfg", {})
    S.setdefault("items", [])
    S.setdefault("used", {})
    return S


def cfg(ch):
    return {**DEF, **_S()["cfg"].get(ch, {})}


def _save(S):
    S["items"] = S["items"][-200:]
    _write(F, S)


def _upd(iid, **kw):
    with _lock:
        S = _S()
        for it in S["items"]:
            if it["id"] == iid:
                it.update(kw)
        _save(S)


def _push(t, b):
    try:
        from extra import push_all
        push_all(t, b, "/#faceless")
    except Exception:
        pass


def _ensure_pil():
    try:
        import PIL  # noqa
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow"], check=False, timeout=600)


def _migr():
    """Jednorazowo: stare kanały YT (Historia -> Teoria w 30 sekund, Zdrowie -> Rekrutacja bez Stresu)."""
    S = _S()
    if S.get("migr"):
        return
    try:
        from social import _lock as sl
        with sl:
            t = _read("tokens.json", {})
            for k, a in t.items():
                if a.get("platform") == "youtube" and a.get("group") == "historia":
                    a["group"] = "teoria"
                elif a.get("platform") == "youtube" and a.get("group") == "zdrowie":
                    a["group"] = "rekrutacja"
            _write("tokens.json", t)
        S["migr"] = time.time()
        _save(S)
        print("faceless: konta YT przepięte (historia->teoria, zdrowie->rekrutacja)", flush=True)
    except Exception as e:
        print("faceless migr:", e, flush=True)


# ---------------------------------------------------------------- Google: Gemini + TTS
MODELS = [m for m in [os.environ.get("FACELESS_MODEL", "")] if m] + [
    "gemini-3-flash-preview", "gemini-3.1-flash-preview", "gemini-3-flash", "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
_OK = {}


def _gurl(loc, m):
    from montaz import VERTEX_JSON
    p = json.loads(VERTEX_JSON.read_text())["project_id"]
    host = "aiplatform.googleapis.com" if loc == "global" else f"{loc}-aiplatform.googleapis.com"
    return f"https://{host}/v1/projects/{p}/locations/{loc}/publishers/google/models/{m}:generateContent"


def gemini(prompt, temp=0.9):
    from montaz import _vertex_token
    combos = ([_OK["c"]] if _OK.get("c") else []) + [(l, m) for m in MODELS for l in ("us-central1", "global", "europe-west4")]
    err = []
    for loc, m in combos:
        for proba in range(4):
            r = httpx.post(_gurl(loc, m), timeout=180, headers={"Authorization": "Bearer " + _vertex_token()},
                           json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                 "generationConfig": {"responseMimeType": "application/json", "temperature": temp}})
            if r.status_code in (429, 500, 503) and proba < 3:
                time.sleep(20 * (proba + 1))
                continue
            break
        if r.status_code == 200:
            j = r.json()
            txt = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought"))
            _OK["c"] = (loc, m)
            return json.loads(txt)
        if r.status_code in (400, 403, 404):
            err.append(f"{m}@{loc}: {r.status_code}")
            continue
        err.insert(0, f"{m}@{loc}: {r.status_code} {r.text[:200]}")
        break
    k = _aistudio_key()
    if k:
        for m in ("gemini-flash-latest", "gemini-2.5-flash", "gemini-3-flash-preview"):
            for proba in range(4):
                r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent", timeout=180,
                               headers={"x-goog-api-key": k},
                               json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                     "generationConfig": {"responseMimeType": "application/json", "temperature": temp}})
                if r.status_code in (429, 500, 503) and proba < 3:
                    time.sleep(20 * (proba + 1))
                    continue
                break
            if r.status_code == 200:
                j = r.json()
                return json.loads("".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"] if not p.get("thought")))
            err.insert(0, f"AI Studio {m}: {r.status_code} {r.text[:150]}")
    raise RuntimeError("Gemini: " + " | ".join(err))


KEYF = ROOT / "gemini_key"


def _aistudio_key():
    try:
        return KEYF.read_text().strip()
    except Exception:
        return ""


def tts(text, out, voice, rate=1.08):
    from montaz import _vertex_token, VERTEX_JSON
    p = json.loads(VERTEX_JSON.read_text())["project_id"]
    r = httpx.post("https://texttospeech.googleapis.com/v1/text:synthesize", timeout=120,
                   headers={"Authorization": "Bearer " + _vertex_token(), "x-goog-user-project": p},
                   json={"input": {"text": text}, "voice": {"languageCode": "pl-PL", "name": voice},
                         "audioConfig": {"audioEncoding": "MP3", "speakingRate": rate}})
    if r.status_code >= 300:
        raise RuntimeError("TTS: " + r.text[:200])
    Path(out).write_bytes(base64.b64decode(r.json()["audioContent"]))
    from faceless_render import dur
    return (str(out), dur(out))


# ---------------------------------------------------------------- skrypty
OPIS_ZASADY = ("Opis: 2 krótkie zdania naturalnym językiem, jak człowiek, bez myślników jako przerwy w zdaniu, bez wykrzykników na siłę, "
               "na końcu 3–4 hashtagi. Tytuł: max 70 znaków, ciekawy, bez clickbaitowych kłamstw.")


def script_teoria(used):
    temat = random.choice([t for t in TEORIA_TEMATY if t not in used[-15:]] or TEORIA_TEMATY)
    for proba in range(4):
        sc = gemini(f"""Jesteś doświadczonym instruktorem nauki jazdy w Polsce. Przygotuj JEDNO pytanie quizowe do 30-sekundowego filmu (YouTube Shorts)
dla kandydatów na kierowców kat. B. Musi być w 100% zgodne z aktualnym polskim Prawem o ruchu drogowym i rozporządzeniem o znakach. Bez wymyślania przepisów.
Temat: {temat}. Nie powtarzaj tych pytań: {json.dumps(used[-30:], ensure_ascii=False)}.
Pytanie ma być podchwytliwe (takie, na którym ludzie często się mylą), ale jednoznaczne.

typ = "skrzyzowanie" gdy pytanie dotyczy pierwszeństwa/kolejności przejazdu na skrzyżowaniu (scena rysowana automatycznie), w innym wypadku "karta".
Scena skrzyżowania (tylko dla typu skrzyzowanie):
- wloty: lista z "N","S","E","W" (4 wloty albo 3 dla skrzyżowania typu T); brak sygnalizacji świetlnej i brak policjanta.
- pojazdy (max 3): {{"kolor": "czerwony"|"niebieski"|"zielony"|"zolty", "typ": "auto"|"tramwaj"|"rower", "skad": "N"|"S"|"E"|"W", "manewr": "prosto"|"lewo"|"prawo", "ty": true/false}}
  Pojazd "ty" jest zawsze czerwonym autem i przyjeżdża z "S". Tramwaj tylko z "N" lub "S", jedzie prosto, wtedy "tory": true. Kierunki: N = z góry, E = z prawej (od strony TY), W = z lewej.
- znaki: {{"N": "ustap"|"stop"|"pierwszenstwo"|null, "S": ..., "E": ..., "W": ...}} (znak stoi przy wlocie i dotyczy pojazdów z tego wlotu)
- kolejnosc: lista kolorów w poprawnej kolejności przejazdu (musi zgadzać się z poprawną odpowiedzią).
Dla typu "karta": "karta": {{"duzy_tekst": "max 3 słowa, sam TEMAT, np. TEREN ZABUDOWANY albo ALKOHOL", "podpis": "max 8 słów"}}. Karta widać przez cały film, więc NIE może zdradzać odpowiedzi.

Zwróć JSON:
{{"temat": "{temat}", "typ": ..., "scena": ..., "karta": ...,
 "hook_voice": "1 zdanie, zaczepka, np. 'Zdałbyś to pytanie z egzaminu?'", "hook_ekran": "max 4 słowa, pasujące do pytania, np. Zdałbyś to? albo Wiesz to?",
 "pytanie_voice": "pytanie czytane przez lektora, max 30 słów", "pytanie_ekran": "pytanie na ekranie, max 90 znaków",
 "odpowiedzi": ["2 albo 3 krótkie odpowiedzi, max 32 znaki każda"], "poprawna": indeks od 0,
 "werdykt_ekran": "max 4 słowa, np. 'Nie możesz!'", "wyjasnienie_voice": "zaczyna się od poprawnej odpowiedzi, 1–2 zdania, prosto, z podaniem zasady",
 "wyjasnienie_ekran": "zasada w max 60 znakach", "przepis": "np. art. 25 ust. 1 PoRD",
 "tytul": "...", "opis": "...", "tagi": ["5 tagów"]}}
{OPIS_ZASADY} Pisz poprawną polszczyzną.""")
        v = gemini(f"""Jesteś egzaminatorem WORD. Sprawdź BARDZO surowo to pytanie quizowe pod kątem polskich przepisów (PoRD, znaki, stan na 2026):
{json.dumps(sc, ensure_ascii=False)}
Sprawdź: 1) czy oznaczona poprawna odpowiedź jest na pewno prawidłowa, 2) czy pozostałe są na pewno błędne, 3) czy wyjaśnienie i przepis są prawdziwe,
4) dla typu skrzyzowanie: czy opis sceny (wloty, pojazdy, manewry, znaki) jednoznacznie prowadzi do podanej kolejności i zgadza się z pytaniem,
5) czy pytanie jest jednoznaczne. Zwróć JSON {{"ok": true/false, "powod": "krótko"}}. Przy jakiejkolwiek wątpliwości: ok=false.""", temp=0.1)
        if v.get("ok") and _valid_teoria(sc):
            return sc
        print("faceless teoria odrzucone:", v.get("powod"), flush=True)
    raise RuntimeError("Gemini nie dał poprawnego pytania w 4 próbach")


def _valid_teoria(sc):
    try:
        assert sc["typ"] in ("skrzyzowanie", "karta") and 2 <= len(sc["odpowiedzi"]) <= 3 and 0 <= int(sc["poprawna"]) < len(sc["odpowiedzi"])
        if sc["typ"] == "skrzyzowanie":
            P = sc["scena"]["pojazdy"]
            assert 1 <= len(P) <= 3 and sum(1 for p in P if p.get("ty")) == 1
            assert all(p["skad"] in "NSEW" and p["manewr"] in ("prosto", "lewo", "prawo") for p in P)
            assert len({p["kolor"] for p in P}) == len(P)
        return True
    except Exception:
        return False


def script_rekrutacja(used):
    temat = random.choice([t for t in REK_TEMATY if t not in used[-20:]] or REK_TEMATY)
    return gemini(f"""Jesteś doświadczoną rekruterką w Polsce. Przygotuj skrypt 30-sekundowego filmu (YouTube Shorts) o pytaniu z rozmowy o pracę.
Pytanie: "{temat}" (możesz lekko przeformułować). Odbiorcy: zwykli ludzie szukający pracy (sklep, biuro, magazyn, obsługa klienta, junior).
Zła odpowiedź ma być typowa i realna (to, co ludzie naprawdę mówią). Lepsza odpowiedź: konkretna, naturalna, w 1. osobie, do powiedzenia na głos, max 35 słów, bez korpo-mowy.
Nie powtarzaj: {json.dumps(used[-20:], ensure_ascii=False)}.
Zwróć JSON:
{{"temat": "{temat}", "pytanie": "pytanie rekrutera (na ekranie i czytane), max 60 znaków",
 "hook_voice": "1 zdanie zaczepki, np. 'Nigdy nie odpowiadaj tak na pytanie o wady.'", "hook_ekran": "max 5 słów",
 "zla": "zła odpowiedź, max 6 słów", "zla_voice": "Zła odpowiedź: <zla>. + 1 zdanie dlaczego",
 "dobra": "lepsza odpowiedź", "zasada_ekran": "zasada w max 8 słowach", "zasada_voice": "1 krótkie zdanie z zasadą",
 "tytul": "...", "opis": "...", "tagi": ["5 tagów"]}}
{OPIS_ZASADY} Pisz poprawną, naturalną polszczyzną.""")


# ---------------------------------------------------------------- produkcja
def _slot(ch):
    now = datetime.now(TZ)
    t = now.replace(hour=CH[ch]["pub"], minute=0, second=0, microsecond=0)
    if t.timestamp() < time.time() + 1800:
        t += timedelta(days=1)
    return t.timestamp()


def produce(ch, why="harmonogram", to_planer=True):
    if ch in _busy:
        return None
    _busy.add(ch)
    iid = datetime.now(TZ).strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
    d = WORK / ch / iid
    d.mkdir(parents=True, exist_ok=True)
    with _lock:
        S = _S()
        S["items"].append({"id": iid, "ch": ch, "date": datetime.now(TZ).strftime("%Y-%m-%d"), "status": "robi się", "at": time.time(), "why": why})
        _save(S)
    try:
        _ensure_pil()
        import faceless_render as R
        used = _S()["used"].get(ch, [])
        c = CH[ch]
        if ch == "teoria":
            sc = script_teoria(used)
            aud = {"hook": tts(sc["hook_voice"], d / "hook.mp3", c["voice"]), "q": tts(sc["pytanie_voice"], d / "q.mp3", c["voice"]),
                   "a": tts(sc["wyjasnienie_voice"], d / "a.mp3", c["voice"]), "cta": tts(c["cta"], d / "cta.mp3", c["voice"])}
            R.render_teoria(sc, aud, d / "film.mp4", d)
            key = sc.get("pytanie_ekran") or sc["temat"]
        else:
            sc = script_rekrutacja(used)
            aud = {"hook": tts(sc["hook_voice"], d / "hook.mp3", c["voice"]), "q": tts(sc["pytanie"], d / "q.mp3", c["voice2"], 1.0),
                   "zla": tts(sc["zla_voice"], d / "zla.mp3", c["voice"]), "dobra": tts("Lepiej tak: " + sc["dobra"], d / "dobra.mp3", c["voice"]),
                   "zasada": tts(sc["zasada_voice"], d / "zasada.mp3", c["voice"]), "cta": tts(c["cta"], d / "cta.mp3", c["voice"])}
            R.render_rekrutacja(sc, aud, d / "film.mp4", d)
            key = sc["temat"]
        (d / "script.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
        for p in d.glob("*.wav"):
            p.unlink(missing_ok=True)
        with _lock:
            S = _S()
            S["used"].setdefault(ch, []).append(key if ch == "rekrutacja" else sc["temat"] + ": " + key)
            S["used"][ch] = S["used"][ch][-120:]
            _save(S)
        pid = _do_planera(ch, iid, sc, d / "film.mp4") if to_planer else None
        _upd(iid, status="gotowe", title=sc.get("tytul", ""), pid=pid)
        return iid
    except Exception as e:
        traceback.print_exc()
        _upd(iid, status="błąd", err=str(e)[:300])
        _push(f"⚠️ {CH[ch]['name']}", "Film się nie zrobił: " + str(e)[:120])
        return iid
    finally:
        _busy.discard(ch)


def _do_planera(ch, iid, sc, f):
    import planer
    plats = ["youtube"] + (["instagram"] if planer._acc("instagram", ch)[0] else [])
    if not planer._acc("youtube", ch)[0]:
        plats = plats[1:]
    if not plats:
        return None
    opis = sc.get("opis", "")
    it = planer._add({"src": f"fl:{ch}:{iid}", "kind": "film", "series": ch, "hkind": ch, "group": ch, "label": sc.get("tytul") or CH[ch]["name"],
                      "file": str(f), "when": _slot(ch), "platforms": plats, "caption": opis, "yt_title": (sc.get("tytul") or "")[:90],
                      "yt_desc": opis, "yt_cat": CH[ch]["cat"], "yt_tags": (sc.get("tagi") or [])[:6] + CH[ch]["tags"]})
    if it and cfg(ch).get("auto"):
        with planer._lock:
            P = planer.plan()
            x = next((y for y in P if y["id"] == it["id"]), None)
            if x:
                x["status"] = "approved"
                planer.save(P)
    return it and it["id"]


def _stale():
    """Po restarcie serwera: filmy przerwane w trakcie dostają status błędu (liczą się jako próba)."""
    with _lock:
        S = _S()
        for x in S["items"]:
            if x["status"] == "robi się":
                x["status"] = "błąd"
                x["err"] = "przerwane przez restart serwera"
        _save(S)


def _due(ch, S, now):
    """Czy harmonogram ma dziś zrobić film: brak gotowego/trwającego, max 3 próby, min. 30 min od ostatniej."""
    today = now.strftime("%Y-%m-%d")
    mine = [x for x in S["items"] if x["ch"] == ch and x["date"] == today and x.get("why") != "test"]
    if any(x["status"] in ("robi się", "gotowe") for x in mine) or ch in _busy:
        return False
    proby = [x for x in mine if x.get("why") == "harmonogram"]
    if len(proby) >= 3:
        return False
    return not proby or time.time() - max(x.get("at", 0) for x in proby) > 1800


def _loop():
    _stale()
    time.sleep(90)
    _migr()
    while True:
        try:
            now = datetime.now(TZ)
            S = _S()
            for ch in CH:
                if cfg(ch).get("enabled") and now.hour >= CH[ch]["hour"] and _due(ch, S, now):
                    threading.Thread(target=produce, args=(ch,), daemon=True).start()
        except Exception as e:
            print("faceless loop:", e, flush=True)
        time.sleep(600)


threading.Thread(target=_loop, daemon=True).start()


# ---------------------------------------------------------------- API
def _pub(S):
    out = []
    for x in S["items"][-40:][::-1]:
        y = dict(x)
        y["name"] = CH.get(x["ch"], {}).get("name", x["ch"])
        out.append(y)
    return out


@router.get("/api/faceless")
def api_state():
    S = _S()
    return {"kanaly": {k: {"name": v["name"], **cfg(k), "pub": v["pub"], "busy": k in _busy} for k, v in CH.items()}, "items": _pub(S)}


@router.post("/api/faceless/{ch}/run")
def api_run(ch: str):
    if ch not in CH:
        return JSONResponse({"error": "nie ma"}, status_code=404)
    threading.Thread(target=produce, args=(ch, "ręcznie"), daemon=True).start()
    return {"ok": True}


@router.post("/api/faceless/{ch}/cfg")
async def api_cfg(ch: str, req: Request):
    b = await req.json()
    with _lock:
        S = _S()
        S["cfg"][ch] = {**S["cfg"].get(ch, {}), **{k: bool(v) for k, v in b.items() if k in ("enabled", "auto")}}
        _save(S)
    return {"ok": True}


@router.get("/api/faceless/plik/{iid}")
def api_plik(iid: str):
    it = next((x for x in _S()["items"] if x["id"] == iid), None)
    f = WORK / (it or {}).get("ch", "x") / iid / "film.mp4"
    if not it or not f.exists():
        return JSONResponse({"error": "nie ma"}, status_code=404)
    return FileResponse(f, media_type="video/mp4")


def _tok(req):
    from montaz import _tok as t
    return t(req)


@router.post("/api/mont-hook/faceless-test")
async def hook_test(req: Request):
    """Claude/Mac: zrób film testowy (bez planera), czeka na wynik."""
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=403)
    b = await req.json()
    ch = b.get("ch")
    if ch not in CH:
        return JSONResponse({"error": "kanał"}, status_code=400)
    import asyncio
    iid = await asyncio.to_thread(produce, ch, "test", False)
    it = next((x for x in _S()["items"] if x["id"] == iid), None)
    sc = WORK / ch / str(iid) / "script.json"
    return {"item": it, "script": json.loads(sc.read_text()) if sc.exists() else None}


@router.get("/api/mont-hook/faceless-plik/{iid}")
def hook_plik(iid: str, req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=403)
    return api_plik(iid)


@router.get("/api/mont-hook/faceless-stan")
def hook_stan(req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=403)
    return api_state()


@router.post("/api/faceless/klucz")
async def api_klucz(req: Request):
    """Klucz Gemini z Google AI Studio (wpisywany w centrum, nie w czacie)."""
    k = str((await req.json()).get("key") or "").strip()
    if not k.startswith("AIza") or len(k) < 30:
        return JSONResponse({"error": "To nie wygląda na klucz z AI Studio (zaczyna się od AIza…)."}, status_code=400)
    r = httpx.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent", timeout=60,
                   headers={"x-goog-api-key": k}, json={"contents": [{"role": "user", "parts": [{"text": "Odpowiedz jednym słowem: ok"}]}]})
    if r.status_code != 200:
        return JSONResponse({"error": f"Google odrzucił klucz: {r.status_code} {r.text[:150]}"}, status_code=400)
    KEYF.write_text(k)
    os.chmod(KEYF, 0o600)
    return {"ok": True}


@router.get("/api/faceless/klucz")
def api_klucz_stan():
    return {"jest": bool(_aistudio_key())}


@router.get("/api/mont-hook/gemini-diag")
def hook_diag(req: Request):
    if not _tok(req):
        return JSONResponse({"error": "token"}, status_code=403)
    from montaz import _vertex_token
    out = []
    for loc, m in [("global", "gemini-flash-latest"), ("us-central1", "gemini-2.5-flash"), ("us-central1", "gemini-3-flash-preview")]:
        r = httpx.post(_gurl(loc, m), timeout=60, headers={"Authorization": "Bearer " + _vertex_token()},
                       json={"contents": [{"role": "user", "parts": [{"text": "ok"}]}]})
        out.append({"m": m, "loc": loc, "st": r.status_code, "txt": r.text[:400]})
    return out
