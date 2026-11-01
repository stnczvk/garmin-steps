"""Menedżer sklepu Etsy: codzienny audyt wszystkich ofert i sklepu + lista konkretnych rzeczy do poprawy.
Sprawdza tytuły, tagi, opisy, zdjęcia, film, wygasanie, ruch i konwersję, personalizacje, piny i terminy sezonowe."""
import threading, time
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
    return _read(OUT, {}) or {"issues": [], "score": None}


@router.post("/api/etsy/audit")
def post_audit():
    return run()
