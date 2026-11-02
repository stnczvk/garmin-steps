"""Menedżer sklepu Etsy: codzienny audyt wszystkich ofert i sklepu + lista konkretnych rzeczy do poprawy.
Sprawdza tytuły, tagi, opisy, zdjęcia, film, wygasanie, ruch i konwersję, personalizacje, piny i terminy sezonowe."""
import re, threading, time
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter
from social import _read, _write

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
OUT = "etsy_audit.json"
_lk = threading.Lock()


def _issue(L, lvl, what, msg, fix, lid=None):
    L.append({"lvl": lvl, "what": what, "msg": msg, "fix": fix, "lid": lid})


def audit():
    import etsy as E, etsy_manage as EM
    c, D = E._cfg(), _read(E.DATA, {})
    I, stats = [], {"listings": 0, "ok": 0}
    if not c.get("refresh"):
        _issue(I, "fail", "Sklep", "Sklep nie jest połączony z centrum.", "Zakładka Etsy → Połącz sklep.")
        return {"at": time.time(), "score": 0, "issues": I, "stats": stats}
    L = D.get("listings", [])
    stats["listings"] = len(L)
    if len(L) < 12:
        _issue(I, "info", "Sklep", f"Masz {len(L)} aktywnych ofert.", "Sklepy z 20+ ofertami sprzedają wyraźnie częściej – dodaj warianty istniejących szablonów (np. wersje 2027, pakiety 2 w 1).")
    week = time.time() - 7 * 86400
    O = D.get("orders", [])
    for l in L:
        lid, t = l["id"], l["title"]
        short = t[:46] + ("…" if len(t) > 46 else "")
        bad = 0
        try:
            j = EM._call("GET", f"/listings/{lid}", params={"includes": "Images,Videos"})
        except Exception as e:
            _issue(I, "warn", short, f"Nie udało się pobrać oferty ({str(e)[:60]}).", "Spróbuję przy następnym audycie.", lid); continue
        tags = j.get("tags") or []
        if len(tags) < 13:
            _issue(I, "warn", short, f"Tylko {len(tags)}/13 tagów.", "Uzupełnij do 13 – każdy pusty tag to stracone wyszukiwania (Zarządzanie ofertami → Teksty).", lid); bad += 1
        long = [x for x in tags if len(x) > 20]
        if long:
            _issue(I, "warn", short, f"Tagi dłuższe niż 20 znaków: {', '.join(long[:3])}.", "Etsy je ucina – skróć.", lid); bad += 1
        if len(t) < 70:
            _issue(I, "warn", short, f"Krótki tytuł ({len(t)} znaków).", "Wykorzystaj 100–140 znaków: najważniejsza fraza na początku, potem 2–3 warianty wyszukiwań.", lid); bad += 1
        if len(j.get("description") or "") < 600:
            _issue(I, "info", short, "Krótki opis.", "Dodaj sekcje: co dostajesz, jak działa, dla kogo, FAQ – dłuższy opis lepiej konwertuje.", lid)
        imgs = j.get("images") or []
        vids = [v for v in (j.get("videos") or []) if v.get("video_state", "active") == "active"]
        if len(imgs) < 7:
            _issue(I, "warn" if len(imgs) < 5 else "info", short, f"{len(imgs)}/10 zdjęć.", "Etsy pozwala na 10 – dodaj zbliżenia arkusza, widok na telefonie i „co dostajesz”.", lid); bad += len(imgs) < 5
        if not vids:
            _issue(I, "warn", short, "Brak filmu.", "Oferty z filmem dostają więcej kliknięć – Zarządzanie ofertami → Tylko filmy.", lid); bad += 1
        alt = [x for x in imgs if not x.get("alt_text")]
        if alt:
            _issue(I, "info", short, f"{len(alt)} zdjęć bez tekstu alternatywnego.", "Opis zdjęcia pomaga w wyszukiwarce Google – Podmień zdjęcia uzupełni go automatycznie.", lid)
        end = j.get("ending_timestamp") or 0
        if end and end - time.time() < 14 * 86400:
            _issue(I, "warn", short, f"Oferta wygasa {datetime.fromtimestamp(end, TZ):%d.%m}.", "Włącz automatyczne odnawianie albo odnów ręcznie.", lid); bad += 1
        v, f = l.get("views") or 0, l.get("favorites") or 0
        sold = sum(1 for o in O for i in o["items"] if i.get("listing_id") == lid)
        if v >= 150 and sold == 0:
            _issue(I, "warn", short, f"{v} wyświetleń, 0 sprzedaży.", "Ludzie patrzą, ale nie kupują: sprawdź cenę, pierwsze zdjęcie i pierwsze 2 zdania opisu.", lid); bad += 1
        elif v < 20:
            _issue(I, "info", short, f"Mało wyświetleń ({v}).", "Popraw tytuł i tagi pod frazy z eRank, dodaj piny na Pinterest.", lid)
        if v and f / max(v, 1) < 0.01 and v >= 100:
            _issue(I, "info", short, "Mało dodań do ulubionych.", "Pierwsze zdjęcie nie przyciąga – przetestuj inną miniaturę.", lid)
        if not bad:
            stats["ok"] += 1
    pend = [o for o in O if not o.get("pers_done") and any(i.get("personalization") for i in o["items"])]
    if pend:
        _issue(I, "fail", "Zamówienia", f"{len(pend)} personalizacji czeka na wysłanie.", "Zakładka Etsy → Personalizacje do zrobienia.")
    if not [o for o in O if (o["t"] or 0) >= week] and len(L):
        _issue(I, "info", "Sprzedaż", "Brak sprzedaży w 7 dni.", "Rozważ krótką promocję (−25%) albo Etsy Ads 1–2 € dziennie na najlepszą ofertę.")
    try:
        P = _read("pinterest.json", {}); pd = [d for d in P.get("drafts", []) if d["status"] == "draft"]
        if pd:
            _issue(I, "info", "Pinterest", f"{len(pd)} pinów czeka na zatwierdzenie.", "Ekran startowy → Biznes → Piny do zatwierdzenia.")
    except Exception:
        pass
    now = datetime.now(TZ)
    if now.month == 11 and now.day >= 20 or now.month == 12 and now.day <= 3:
        _issue(I, "info", "Sezon", "Zbliża się 1 grudnia – start wyszukiwań „2027”.", "Zarządzanie ofertami → Wersja 2027 (tytuły i pierwsze zdjęcia).")
    if now.month == 11 and now.day >= 22 or now.month == 12 and now.day <= 2:
        _issue(I, "info", "Sezon", "Black Friday / Cyber Monday.", "Ustaw wyprzedaż −35% na 27.11–30.11 (Marketing → Sales and discounts).")
    W = {"fail": 12, "warn": 4, "info": 1}
    score = max(0, 100 - sum(W[x["lvl"]] for x in I))
    return {"at": time.time(), "score": score, "issues": I, "stats": stats}


def run():
    with _lk:
        try:
            R = audit()
        except Exception as e:
            R = {"at": time.time(), "score": None, "issues": [], "error": str(e)[:200], "stats": {}}
        try:
            if R.get("issues") and autofix(R):
                R = audit()            # po poprawkach od razu nowy audyt – poprawione rzeczy znikają z listy
        except Exception as e:
            print("etsy autofix", e, flush=True)
        R["done"] = _recent_done()
        _write(OUT, R)
        return R


def _loop():
    time.sleep(240)
    while True:
        R = _read(OUT, {})
        if time.time() - (R.get("at") or 0) > 6 * 3600:
            run()
        time.sleep(900)


threading.Thread(target=_loop, daemon=True).start()


@router.get("/api/etsy/audit")
def get_audit():
    R = _read(OUT, {}) or {"issues": [], "score": None}
    R["done"] = _recent_done()
    return R


@router.post("/api/etsy/audit")
def post_audit():
    return run()


# ---------------------------------------------------------------- automatyczne poprawki
# Bot sam poprawia w ofertach to, co da się bezpiecznie zmienić przez API: tagi (13, max 20 znaków), za krótki tytuł,
# za krótki opis, automatyczne odnawianie. Każda zmiana ma kopię „przed”, więc można ją cofnąć jednym kliknięciem.
# Zdjęć, filmów i cen nie rusza (to zostaje na liście do poprawy). Tytuł zmienia najwyżej raz na 21 dni (częste zmiany szkodzą pozycji).
FIX = "etsy_fixes.json"
FDEF = {"auto": True, "max_per_run": 3}


def _ai_fix(j, need):
    from agency import ai_ready, claude, as_json
    if not ai_ready():
        return {}
    p = (f"Jesteś ekspertem SEO Etsy. Popraw ofertę (to może być cyfrowy szablon Google Sheets albo produkt z nadrukiem – trzymaj się typu produktu z tytułu i opisu), w języku obecnej oferty.\n"
         f"TYTUŁ: {j.get('title')}\nTAGI: {', '.join(j.get('tags') or [])}\nOPIS:\n{(j.get('description') or '')[:2500]}\n\n"
         f"Do poprawy: {', '.join(need)}.\nZasady: tags = dokładnie 13 tagów, każdy max 20 znaków, frazy których kupujący szukają, bez powtórzeń "
         f"(zachowaj dobre istniejące). title = 110-140 znaków, najważniejsza fraza na początku, potem 2-3 warianty wyszukiwań, przecinki, bez emoji, "
         f"bez CAPS LOCK, nie zmieniaj produktu. description = zachowaj cały obecny tekst i dopisz brakujące sekcje (WHAT'S INSIDE, HOW IT WORKS, "
         f"WHO IT'S FOR, FAQ) – razem 900-1500 znaków, zwykły tekst, punkty z •. Nie wymyślaj funkcji, których nie ma w opisie.\n"
         f"Zwróć TYLKO JSON z polami, które poprawiasz: {{\"tags\": [...], \"title\": \"...\", \"description\": \"...\"}}")
    try:
        return as_json(claude(p, max_tokens=1800)) or {}
    except Exception:
        return {}


def autofix(R):
    import etsy_manage as EM
    F = _read(FIX, {})
    cfg = {**FDEF, **(F.get("cfg") or {})}
    if not cfg["auto"]:
        return
    log = F.setdefault("log", [])
    last = F.setdefault("last_title", {})
    by = {}
    for x in R.get("issues", []):
        if x.get("lid"):
            by.setdefault(x["lid"], []).append(x["msg"])
    done = 0
    changed = False
    for lid, msgs in by.items():
        if done >= cfg["max_per_run"]:
            break
        need = []
        if any("tagów" in m or "Tagi dłuższe" in m for m in msgs): need.append("tags")
        if any("Krótki tytuł" in m for m in msgs) and time.time() - last.get(str(lid), 0) > 21 * 86400: need.append("title")
        if any("Krótki opis" in m for m in msgs): need.append("description")
        renew = any("wygasa" in m for m in msgs)
        if not need and not renew:
            continue
        try:
            j = EM._call("GET", f"/listings/{lid}")
            data, before = {}, {"title": j.get("title"), "tags": j.get("tags") or [], "description": j.get("description") or "", "should_auto_renew": j.get("should_auto_renew")}
            if need:
                a = _ai_fix(j, need)
                if "tags" in need and isinstance(a.get("tags"), list):
                    t = [str(x).strip().lower()[:20] for x in a["tags"] if str(x).strip()]
                    t = list(dict.fromkeys(t))[:13]
                    if len(t) >= len(before["tags"]): data["tags"] = ",".join(t)
                if "title" in need and isinstance(a.get("title"), str) and 70 <= len(a["title"].strip()) <= 140:
                    data["title"] = a["title"].strip()
                if "description" in need and isinstance(a.get("description"), str) and len(a["description"]) > len(before["description"]):
                    data["description"] = a["description"].strip()
            if renew and not j.get("should_auto_renew"):
                data["should_auto_renew"] = "true"
            if not data:
                continue
            EM._call("PATCH", f"/shops/{EM._sid()}/listings/{lid}", data=data)
            if "title" in data: last[str(lid)] = time.time()
            what = [{"tags": "tagi", "title": "tytuł", "description": "opis", "should_auto_renew": "auto-odnawianie"}[k] for k in data]
            log.insert(0, {"t": time.time(), "lid": lid, "name": (j.get("title") or "")[:60], "what": what, "before": before,
                           "after": {k: v for k, v in data.items()}, "undone": False})
            done += 1; changed = True
        except Exception as e:
            log.insert(0, {"t": time.time(), "lid": lid, "name": str(lid), "what": [], "error": str(e)[:160]})
    F["log"] = log[:80]; F["cfg"] = cfg
    _write(FIX, F)
    return changed


def _recent_done(hours=48):
    """Poprawki zrobione przez bota w ostatnich 48 h – pokazywane jako „zrobione”, potem same znikają."""
    out = []
    for x in (_read(FIX, {}).get("log") or []):
        if x.get("error") or x.get("undone") or time.time() - x.get("t", 0) > hours * 3600:
            continue
        out.append({"t": x["t"], "what": x.get("name"), "msg": "Bot poprawił: " + ", ".join(x.get("what") or [])})
    return out


@router.get("/api/etsy/fixes")
def get_fixes():
    F = _read(FIX, {})
    return {"cfg": {**FDEF, **(F.get("cfg") or {})}, "log": [{k: v for k, v in x.items() if k != "before"} for x in (F.get("log") or [])[:30]]}


@router.post("/api/etsy/fixes/cfg")
async def set_fixes(req: __import__("fastapi").Request):
    b = await req.json()
    F = _read(FIX, {}); c = {**FDEF, **(F.get("cfg") or {})}
    if "auto" in b: c["auto"] = bool(b["auto"])
    F["cfg"] = c; _write(FIX, F)
    return {"ok": True, "cfg": c}


@router.post("/api/etsy/fixes/undo/{i}")
def undo_fix(i: int):
    import etsy_manage as EM
    F = _read(FIX, {}); L = F.get("log") or []
    if i >= len(L) or L[i].get("undone") or not L[i].get("before"):
        return {"ok": False, "error": "nie da się cofnąć"}
    x, b = L[i], L[i]["before"]
    data = {}
    for k in x.get("after", {}):
        if k == "tags": data["tags"] = ",".join(b["tags"])
        elif k == "should_auto_renew": data[k] = "true" if b.get(k) else "false"
        else: data[k] = b[k]
    try:
        EM._call("PATCH", f"/shops/{EM._sid()}/listings/{x['lid']}", data=data)
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}
    x["undone"] = True; _write(FIX, F)
    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


# ---------------------------------------------------------------- jednorazowy przegląd ofert z nadrukiem (POD)
# v1 tłumaczył angielskie oferty na polski i pisał małymi literami – cofamy go (pod_undo) i robimy v2 ze sztywnymi regułami:
# język i rodzaj produktu ustala kod (nie AI), wynik jest sprawdzany przed zapisem (język, słowo produktu, wielka litera, długość, brak duplikatów).
POD_PASS = "v2"
PLW = re.compile(r"[ąćęłńóśźż]|\b(koszulka|kubek|bluza|prezent|święta|mikołajki|śmieszn)", re.I)
KIND = {"mug": ("Mug", "Kubek"), "tee": ("T-Shirt", "Koszulka"), "sweat": ("Sweatshirt", "Bluza")}
SMALL = {"a", "an", "and", "at", "for", "in", "of", "on", "or", "the", "to", "with"}


def _kind(price):
    return "mug" if abs((price or 0) - 26.99) < .01 or abs((price or 0) - 21) < .01 else "sweat" if (price or 0) >= 45 else "tee"


def _tcase(t):
    w = t.split(" ")
    return " ".join(x if (i and x.lower() in SMALL) or not x else x[0].upper() + x[1:] for i, x in enumerate(w))


def _pod_ai(j, others, lang, word):
    from agency import ai_ready, claude, as_json
    if not ai_ready():
        return {}
    L = "ENGLISH" if lang == "en" else "POLISH"
    p = (f"You are an Etsy SEO expert. This is a print-on-demand {word} listing. Write EVERYTHING in {L} ONLY "
         f"({'do NOT use any Polish words' if lang == 'en' else 'pisz wyłącznie po polsku'}).\n"
         f"CURRENT TITLE: {j.get('title')}\nCURRENT TAGS: {', '.join(j.get('tags') or [])}\nCURRENT DESCRIPTION:\n{(j.get('description') or '')[:2500]}\n\n"
         f"OTHER LISTINGS IN THE SHOP (your title must clearly differ from them):\n- " + "\n- ".join(others[:30]) + "\n\n"
         f"Rules:\n• title: 100-140 characters, START with the printed slogan/motif in its original wording, then the product word \"{word}\" "
         f"(this exact product – never mention any other product type), then 2-3 search phrases (e.g. funny Christmas gift, Secret Santa"
         f"{', Mikołajki' if lang == 'pl' else ''}), separated by commas. {'Title Case.' if lang == 'en' else 'Pierwsza litera wielka, potem normalna polska pisownia.'} "
         f"No emoji, no ALL CAPS, no double spaces. If several listings share the same slogan, add a distinguishing angle (recipient or occasion).\n"
         f"• tags: exactly 13, max 20 characters each, lowercase, buyer search phrases in {L}, no duplicates.\n"
         f"• description: 900-1500 characters, plain text, keep every concrete fact from the current description; sections "
         f"{'THE DESIGN, PRODUCT, PERFECT FOR, CARE, GOOD TO KNOW' if lang == 'en' else 'O WZORZE, PRODUKT, DLA KOGO, PIELĘGNACJA, WAŻNE'}, bullets with •. "
         f"Do NOT invent materials, weights, capacity, shipping times or dishwasher safety unless already stated; say it is printed to order and colours may vary slightly on screens."
         f"{' Care for garments: wash inside out on low temperature, do not iron the print.' if word != 'Mug' and word != 'Kubek' else ''}\n"
         'Return ONLY JSON: {"title": "...", "tags": [...], "description": "..."}')
    try:
        return as_json(claude(p, max_tokens=2200)) or {}
    except Exception:
        return {}


def _ok_lang(text, lang):
    return bool(PLW.search(text)) if lang == "pl" else not PLW.search(text)


def pod_undo():
    """Cofa wszystkie zmiany v1 (oznaczone pod=True)."""
    import etsy_manage as EM
    F = _read(FIX, {})
    if F.get("pod_undo") == "v1":
        return 0
    n = 0
    for x in F.get("log") or []:
        if not x.get("pod") or x.get("undone") or x.get("error") or not x.get("before"):
            continue
        b = x["before"]
        data = {k: (",".join(b[k]) if k == "tags" else b[k]) for k in (x.get("after") or {}) if k in b and b[k] is not None}
        if not data:
            continue
        try:
            EM._call("PATCH", f"/shops/{EM._sid()}/listings/{x['lid']}", data=data)
            x["undone"] = True; n += 1; time.sleep(1)
        except Exception as e:
            x["undo_err"] = str(e)[:160]
    F["pod_undo"] = "v1"
    _write(FIX, F)
    print(f"etsy pod_undo: cofnięto {n}", flush=True)
    return n


def pod_pass(force=False):
    import etsy_manage as EM
    F = _read(FIX, {})
    if F.get("pod_pass") == POD_PASS and not force:
        return 0
    L = (_read("etsy.json", {}) or {}).get("listings", [])
    titles, n = {str(l["id"]): l.get("title", "") for l in L}, 0
    log, last = F.setdefault("log", []), F.setdefault("last_title", {})
    for l in L:
        lid = str(l["id"])
        try:
            j = EM._call("GET", f"/listings/{lid}")
            if j.get("listing_type") == "download" or j.get("is_digital"):
                continue
            lang = "pl" if PLW.search(j.get("title") or "") else "en"
            word = KIND[_kind(l.get("price"))][0 if lang == "en" else 1]
            a = _pod_ai(j, [t for k, t in titles.items() if k != lid], lang, word)
            before = {"title": j.get("title"), "tags": j.get("tags") or [], "description": j.get("description") or ""}
            data = {}
            t = re.sub(r"\s+", " ", str(a.get("title") or "")).strip().strip(",")
            t = _tcase(t) if lang == "en" else (t[:1].upper() + t[1:])
            k = _kind(l.get("price"))
            has = {"mug": r"\b(mug|kubek|kubk)", "tee": r"\b(t-?shirt|tee|koszul)|(?<!sweat)shirt", "sweat": r"\b(sweatshirt|jumper|bluz)"}
            if (70 <= len(t) <= 140 and t not in titles.values() and _ok_lang(t, lang) and re.search(has[k], t, re.I)
                    and not any(re.search(has[o], t, re.I) for o in has if o != k)):
                data["title"] = t; titles[lid] = t
            if isinstance(a.get("tags"), list):
                tg = list(dict.fromkeys(str(x).strip().lower()[:20] for x in a["tags"] if str(x).strip()))[:13]
                if len(tg) == 13 and (lang == "pl" or not any(PLW.search(x) for x in tg)): data["tags"] = ",".join(tg)
            d = str(a.get("description") or "").strip()
            if len(d) >= max(600, int(len(before["description"]) * .8)) and _ok_lang(d[:400], lang): data["description"] = d
            if not data:
                continue
            EM._call("PATCH", f"/shops/{EM._sid()}/listings/{lid}", data=data)
            if "title" in data: last[lid] = time.time()
            log.insert(0, {"t": time.time(), "lid": int(lid), "name": (j.get("title") or "")[:60],
                           "what": [{"tags": "tagi", "title": "tytuł", "description": "opis"}[k] for k in data], "before": before,
                           "after": dict(data), "undone": False, "pod": POD_PASS})
            n += 1
            time.sleep(1.5)
        except Exception as e:
            log.insert(0, {"t": time.time(), "lid": int(lid), "name": lid, "what": [], "error": str(e)[:160]})
    F = {**_read(FIX, {}), "log": log[:150], "last_title": last, "pod_pass": POD_PASS}
    _write(FIX, F)
    print(f"etsy pod_pass {POD_PASS}: poprawiono {n} ofert", flush=True)
    return n


def _pod_boot():
    time.sleep(300)
    try:
        with _lk:
            pod_undo()
            pod_pass()
            R = audit(); R["done"] = _recent_done(); _write(OUT, R)
    except Exception as e:
        print("etsy pod_pass", e, flush=True)


threading.Thread(target=_pod_boot, daemon=True).start()
