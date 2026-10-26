"""Zarządzanie ofertami Etsy z centrum: podmiana zdjęć i filmów, teksty / wersja 2027, wystawianie nowych ofert (szkic).
Materiały (grafiki, filmy, PDF-y, teksty) leżą w app/etsy_pack (manifest.json). Wymaga zakresu listings_w (ponowne „Połącz”)."""
import json, re, threading, time
from pathlib import Path
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from social import _read, _write
import etsy as E

router = APIRouter()
PACK = Path(__file__).parent / "etsy_pack"
MAP = "etsy_map.json"
DEFAULT_MAP = {"sg": 4603264871, "dp": 4603264189, "cb": 4603257831, "fit": 4603083533, "wp": 4603090330, "bp": 4603083764, "sb": 4603053353}
KW = {"sb": ["small business"], "bp": ["paycheck"], "wp": ["wedding"], "fit": ["calorie", "macro"], "dp": ["debt"], "cb": ["couple"],
      "sg": ["savings", "sinking"], "hb": ["christmas"], "sp": ["seller profit", "etsy fee", "etsy calculator"], "wo": ["workout", "progressive"], "mp": ["meal"]}
JOB = {"running": False, "log": [], "title": "", "at": 0}
_lk = threading.Lock()


def _man():
    try:
        return json.loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _map():
    m = {k: v for k, v in DEFAULT_MAP.items()}
    m.update({k: v for k, v in _read(MAP, {}).items() if v})
    listings = _read(E.DATA, {}).get("listings", [])
    used = set(m.values())
    for code, kws in KW.items():
        if code in m:
            continue
        hit = next((l for l in listings if any(k in l["title"].lower() for k in kws) and l["id"] not in used), None)
        if hit:
            m[code] = hit["id"]; used.add(hit["id"])
    return m


def _call(method, path, data=None, files=None, params=None):
    c = E._token(E._cfg())
    r = httpx.request(method, E.API + path, headers=E._headers(c), data=data, files=files, params=params, timeout=300)
    if r.status_code >= 300:
        raise RuntimeError(f"Etsy {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


def _sid():
    c = E._cfg()
    if not c.get("shop_id"):
        me = _call("GET", "/users/me"); c = E._cfg(); c.update(shop_id=me.get("shop_id")); _write(E.CFG, c)
    return c["shop_id"]


def log(m):
    JOB["log"].append(f"{time.strftime('%H:%M:%S')} {m}"); JOB["log"] = JOB["log"][-80:]


# ---------------------------------------------------------------- operacje
def do_images(code, lid, p):
    sid = _sid()
    old = [i["listing_image_id"] for i in _call("GET", f"/listings/{lid}/images").get("results", [])]
    new = []
    for rank, name in enumerate(p["images"], 1):
        j = _call("POST", f"/shops/{sid}/listings/{lid}/images", data={"rank": rank, "overwrite": "true", "alt_text": f"{p['name']} – Google Sheets template"[:250]},
                  files={"image": (name, (PACK / name).read_bytes(), "image/jpeg")})
        new.append(j.get("listing_image_id"))
    for iid in old:
        if iid not in new:
            try:
                _call("DELETE", f"/shops/{sid}/listings/{lid}/images/{iid}")
            except Exception:
                pass
    log(f"{p['name']}: zdjęcia podmienione ({len(new)})")


def do_video(code, lid, p):
    sid = _sid()
    for v in _call("GET", f"/listings/{lid}/videos").get("results", []):
        try:
            _call("DELETE", f"/shops/{sid}/listings/{lid}/videos/{v['video_id']}")
        except Exception:
            pass
    _call("POST", f"/shops/{sid}/listings/{lid}/videos", data={"name": p["video"]}, files={"video": (p["video"], (PACK / p["video"]).read_bytes(), "video/mp4")})
    log(f"{p['name']}: film podmieniony")


def do_texts(code, lid, p, year=False):
    sid = _sid()
    title, tags = p["title"], list(p["tags"])
    if year:
        title = title if title.startswith("2027") else ("2027 " + title)[:140]
        if "2027 planner" not in tags:
            tags = tags[:12] + ["2027 planner"]
    _call("PATCH", f"/shops/{sid}/listings/{lid}", data={"title": title, "description": p["description"], "tags": ",".join(tags)})
    log(f"{p['name']}: {'tytuł i tagi 2027' if year else 'tytuł, opis i tagi'} zaktualizowane")


def do_create(code, p, m):
    sid = _sid()
    ref = next((v for k, v in m.items() if v and k != code), None)
    tax = _call("GET", f"/listings/{ref}").get("taxonomy_id") if ref else None
    if not tax:
        raise RuntimeError("nie znam kategorii – wystaw najpierw jedną ofertę ręcznie")
    j = _call("POST", f"/shops/{sid}/listings", data={
        "quantity": 999, "title": p["title"], "description": p["description"], "price": p["price"], "who_made": "i_did",
        "when_made": "made_to_order", "taxonomy_id": tax, "type": "download", "is_supply": "false", "tags": ",".join(p["tags"])})
    lid = j["listing_id"]
    M = _read(MAP, {}); M[code] = lid; _write(MAP, M)
    log(f"{p['name']}: szkic oferty utworzony ({lid})")
    do_images(code, lid, p)
    _call("POST", f"/shops/{sid}/listings/{lid}/files", data={"name": p["pdf"], "rank": 1}, files={"file": (p["pdf"], (PACK / p["pdf"]).read_bytes(), "application/pdf")})
    log(f"{p['name']}: PDF dla kupującego dodany")
    do_video(code, lid, p)
    log(f"{p['name']}: gotowe – szkic czeka w Etsy (Oferty → Szkice), aktywujesz przyciskiem „Aktywuj” albo w Etsy")
    return lid


def _run(title, tasks):
    with _lk:
        if JOB["running"]:
            return False
        JOB.update(running=True, log=[], title=title, at=time.time())

    def go():
        err = 0
        for fn in tasks:
            try:
                fn()
            except Exception as e:
                err += 1; log(f"⚠️ {e}")
        log("Koniec" + (f" – błędów: {err}" if err else " ✓"))
        JOB["running"] = False
        try:
            from extra import push_all
            push_all("🛍 Etsy – " + title, "zrobione" if not err else f"błędy: {err}", "/#etsy")
        except Exception:
            pass
        threading.Thread(target=E.sync, daemon=True).start()
    threading.Thread(target=go, daemon=True).start()
    return True


# ---------------------------------------------------------------- API
@router.get("/api/etsy/manage")
def manage_state():
    c = E._cfg(); M = _man(); m = _map()
    L = {l["id"]: l for l in _read(E.DATA, {}).get("listings", [])}
    prods = [{"code": k, "name": v["name"], "title": v["title"], "price": v["price"], "listing_id": m.get(k),
              "live": bool(m.get(k) and m.get(k) in L), "listing_title": (L.get(m.get(k)) or {}).get("title", "")} for k, v in M.items()]
    return {"connected": bool(c.get("refresh")), "can_write": "listings_w" in (c.get("scopes") or ""), "products": prods,
            "listings": [{"id": l["id"], "title": l["title"]} for l in L.values()], "job": JOB}


@router.post("/api/etsy/map")
async def set_map(req: Request):
    b = await req.json(); M = _read(MAP, {})
    M[str(b.get("code"))] = int(b["listing_id"]) if b.get("listing_id") else None
    _write(MAP, M)
    return {"ok": True}


@router.post("/api/etsy/do")
async def do(req: Request):
    b = await req.json()
    act, codes = b.get("action"), b.get("codes") or []
    c = E._cfg()
    if not c.get("refresh"):
        return JSONResponse({"error": "Sklep nie jest połączony"}, 400)
    if "listings_w" not in (c.get("scopes") or ""):
        return JSONResponse({"error": "Połącz sklep jeszcze raz (Rozłącz → Połącz) – potrzebne jest uprawnienie do edycji ofert"}, 400)
    P, m = _man(), _map()
    tasks = []
    for code in codes:
        p = P.get(code); lid = m.get(code)
        if not p:
            continue
        if act == "create":
            if lid:
                log(f"{p['name']}: oferta już istnieje – pomijam"); continue
            tasks.append(lambda code=code, p=p: do_create(code, p, m))
            continue
        if not lid:
            log(f"{p['name']}: brak powiązanej oferty – pomijam"); continue
        if act in ("images", "all"):
            tasks.append(lambda code=code, lid=lid, p=p: do_images(code, lid, p))
        if act in ("video", "all"):
            tasks.append(lambda code=code, lid=lid, p=p: do_video(code, lid, p))
        if act == "texts":
            tasks.append(lambda code=code, lid=lid, p=p: do_texts(code, lid, p))
        if act == "year":
            tasks.append(lambda code=code, lid=lid, p=p: do_texts(code, lid, p, True))
        if act == "activate":
            tasks.append(lambda lid=lid, p=p: (_call("PATCH", f"/shops/{_sid()}/listings/{lid}", data={"state": "active"}), log(f"{p['name']}: oferta aktywna")))
    names = {"images": "zdjęcia", "video": "filmy", "all": "zdjęcia i filmy", "texts": "teksty", "year": "wersja 2027", "create": "nowe oferty", "activate": "aktywacja"}
    if not tasks:
        return {"ok": False, "error": "nic do zrobienia"}
    if not _run(names.get(act, act), tasks):
        return JSONResponse({"error": "Poprzednie zadanie jeszcze trwa"}, 409)
    return {"ok": True}


@router.get("/api/etsy/pack/{name}")
def pack_file(name: str):
    f = PACK / Path(name).name
    if not f.is_file():
        return JSONResponse({"error": "brak"}, 404)
    return FileResponse(f)
