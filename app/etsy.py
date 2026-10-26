"""Etsy – sklep PlanPerfect Sheets: sprzedaż, produkty, kolejka personalizacji.

Dane: Etsy Open API v3 (OAuth 2.0 + PKCE, tylko odczyt: transactions_r listings_r shops_r) albo import CSV
z panelu sklepu (Shop Manager → Ustawienia → Opcje → Pobierz dane → zamówienia). Synchronizacja co 30 min.
"""
import base64, csv, hashlib, io, re, secrets, threading, time, urllib.parse
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from social import _read, _write

router = APIRouter()
CFG = "etsy_secret.json"      # klucze i tokeny – nazwa z „secret”, nie wychodzi przez diagnostykę
DATA = "etsy.json"
API = "https://api.etsy.com/v3/application"
SCOPES = "transactions_r listings_r listings_w shops_r"
DEF_FEES = {"transaction": 0.065, "processing_pct": 0.04, "processing_fix": 0.30, "listing": 0.20}
_lock = threading.Lock()


def _cfg():
    return _read(CFG, {})


def _redirect(req: Request):
    host = req.headers.get("x-forwarded-host") or req.headers.get("host") or ""
    return f"https://{host}/api/etsy/callback"


def _headers(c):
    return {"x-api-key": f"{c['keystring']}:{c['secret']}", "Authorization": "Bearer " + c["access"]}


def _token(c):
    if c.get("access") and time.time() < c.get("exp", 0) - 120:
        return c
    r = httpx.post("https://api.etsy.com/v3/public/oauth/token", timeout=30,
                   data={"grant_type": "refresh_token", "client_id": c["keystring"], "refresh_token": c["refresh"]})
    j = r.json()
    if r.status_code >= 300 or "access_token" not in j:
        raise RuntimeError("Etsy: odświeżenie tokenu nie wyszło – połącz sklep ponownie")
    c.update(access=j["access_token"], refresh=j["refresh_token"], exp=time.time() + int(j.get("expires_in", 3600)))
    _write(CFG, c)
    return c


def _get(c, path, **params):
    c = _token(c)
    r = httpx.get(API + path, headers=_headers(c), params=params, timeout=40)
    if r.status_code >= 300:
        raise RuntimeError(f"Etsy {r.status_code}: {r.text[:150]}")
    return r.json()


# ---------------------------------------------------------------- synchronizacja
def _money(m):
    try:
        return round(m["amount"] / (m.get("divisor") or 100), 2), m.get("currency_code", "")
    except Exception:
        return 0.0, ""


def sync():
    c = _cfg()
    if not c.get("refresh"):
        return {"ok": False, "error": "sklep nie jest połączony"}
    with _lock:
        D = _read(DATA, {})
        if not c.get("shop_id"):
            me = _get(c, "/users/me")
            c.update(shop_id=me.get("shop_id"), user_id=me.get("user_id"))
            _write(CFG, c)
        sid = c["shop_id"]
        shop = _get(c, f"/shops/{sid}")
        old = {o["id"]: o for o in D.get("orders", [])}
        new = 0
        for off in range(0, 1000, 100):
            j = _get(c, f"/shops/{sid}/receipts", limit=100, offset=off, was_paid="true")
            for rc in j.get("results", []):
                oid = str(rc["receipt_id"])
                items = []
                for t in rc.get("transactions", []):
                    pr, cur = _money(t.get("price", {}))
                    pers = "; ".join(f"{v.get('formatted_name')}: {v.get('formatted_value')}" for v in (t.get("variations") or [])
                                     if "person" in str(v.get("formatted_name", "")).lower())
                    items.append({"title": t.get("title", ""), "listing_id": t.get("listing_id"), "qty": t.get("quantity", 1),
                                  "price": pr, "currency": cur, "personalization": pers})
                tot, cur = _money(rc.get("grandtotal", {}))
                o = {"id": oid, "t": rc.get("create_timestamp") or rc.get("created_timestamp"), "total": tot, "currency": cur,
                     "buyer": rc.get("name", ""), "items": items, "src": "api",
                     "pers_done": (old.get(oid) or {}).get("pers_done", False)}
                if oid not in old:
                    new += 1
                old[oid] = o
            if len(j.get("results", [])) < 100:
                break
        listings = []
        for off in range(0, 500, 100):
            j = _get(c, f"/shops/{sid}/listings/active", limit=100, offset=off)
            for l in j.get("results", []):
                pr, cur = _money(l.get("price", {}))
                listings.append({"id": l["listing_id"], "title": l.get("title", ""), "price": pr, "currency": cur,
                                 "views": l.get("views", 0), "favorites": l.get("num_favorers", 0), "url": l.get("url", "")})
            if len(j.get("results", [])) < 100:
                break
        # dzienny zrzut wyświetleń i ulubionych (API daje tylko sumy narastające – różnica = ruch z danego dnia)
        H = D.get("views_hist", {})
        H[datetime.now().strftime("%Y-%m-%d")] = {"views": sum(l["views"] or 0 for l in listings), "fav": sum(l["favorites"] or 0 for l in listings),
                                                  "by": {str(l["id"]): l["views"] or 0 for l in listings}}
        D["views_hist"] = dict(sorted(H.items())[-120:])
        D.update(orders=sorted(old.values(), key=lambda o: o["t"] or 0), listings=listings, synced=time.time(), error=None,
                 shop={"name": shop.get("shop_name"), "url": shop.get("url"), "currency": shop.get("currency_code"),
                       "sales": shop.get("transaction_sold_count"), "favorites": shop.get("num_favorers")})
        _write(DATA, D)
    if new and D.get("notified_once"):
        try:
            from extra import push_all
            last = D["orders"][-1]
            push_all("🛒 Sprzedaż na Etsy!", f"{last['items'][0]['title'][:60] if last['items'] else 'Zamówienie'} – {last['total']} {last['currency']}", "/#etsy")
        except Exception:
            pass
    if not D.get("notified_once"):
        D["notified_once"] = True
        _write(DATA, D)
    try:
        auto_pers()
    except Exception as e:
        print("etsy pers", e, flush=True)
    return {"ok": True, "new": new}


def _loop():
    def run():
        time.sleep(90)
        while True:
            try:
                if _cfg().get("refresh"):
                    sync()
            except Exception as e:
                D = _read(DATA, {})
                D["error"] = str(e)[:200]
                _write(DATA, D)
            time.sleep(1800)
    threading.Thread(target=run, daemon=True).start()


_loop()


# ---------------------------------------------------------------- import CSV (plan B)
def _num(s):
    s = re.sub(r"[^\d,.\-]", "", str(s or ""))
    if s.count(",") and s.count("."):
        s = s.replace(",", "")
    return float(s.replace(",", ".") or 0)


def _date(s):
    for f in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(s).strip()[:10], f).timestamp()
        except Exception:
            pass
    return None


def import_csv(text):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {"ok": False, "error": "pusty plik"}
    k = {h.lower().strip(): h for h in rows[0].keys()}
    col = lambda *names: next((k[n] for n in names if n in k), None)
    cd, ci, cq, cp, ct, cc, co, cv, cb = (col("sale date", "date paid", "order date"), col("item name", "title"), col("quantity"),
                                          col("price", "item price"), col("item total", "order value", "order total"), col("currency"),
                                          col("order id", "receipt id"), col("variations"), col("buyer", "full name", "ship name"))
    if not cd or not (ci or ct):
        return {"ok": False, "error": "nie rozpoznaję kolumn – użyj raportu „Sold Order Items” albo „Sold Orders” z Etsy"}
    with _lock:
        D = _read(DATA, {})
        old = {o["id"]: o for o in D.get("orders", [])}
        n = 0
        for i, r in enumerate(rows):
            t = _date(r.get(cd))
            if not t:
                continue
            oid = str(r.get(co) or f"csv-{int(t)}-{i}")
            qty = int(_num(r.get(cq)) or 1) if cq else 1
            price = _num(r.get(cp)) if cp else 0
            total = _num(r.get(ct)) if ct else price * qty
            var = str(r.get(cv) or "")
            mm = re.search(r"personali[sz]ation[^:]*:\s*(.*)", var, re.I)
            pers = mm.group(1).strip() if mm else ""
            item = {"title": r.get(ci, "") if ci else "", "listing_id": None, "qty": qty, "price": price or total, "currency": r.get(cc, "") if cc else "",
                    "personalization": pers}
            if oid in old and old[oid].get("src") == "csv":
                if item not in old[oid]["items"]:
                    old[oid]["items"].append(item)
                    old[oid]["total"] = round(old[oid]["total"] + total, 2)
                continue
            if oid in old:
                continue
            old[oid] = {"id": oid, "t": t, "total": round(total, 2), "currency": item["currency"], "buyer": r.get(cb, "") if cb else "",
                        "items": [item], "src": "csv", "pers_done": False}
            n += 1
        D.update(orders=sorted(old.values(), key=lambda o: o["t"] or 0), csv_at=time.time())
        _write(DATA, D)
    threading.Thread(target=auto_pers, daemon=True).start()
    return {"ok": True, "added": n}


# ---------------------------------------------------------------- API centrum
@router.get("/api/etsy")
def state():
    c, D = _cfg(), _read(DATA, {})
    fees = {**DEF_FEES, **(D.get("fees") or {})}
    O = D.get("orders", [])
    now = time.time()
    day0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    def net(o):
        n_items = sum(i.get("qty", 1) for i in o["items"]) or 1
        return o["total"] * (1 - fees["transaction"] - fees["processing_pct"]) - fees["processing_fix"] - fees["listing"] * n_items

    def agg(since):
        L = [o for o in O if (o["t"] or 0) >= since]
        return {"orders": len(L), "revenue": round(sum(o["total"] for o in L), 2), "net": round(sum(net(o) for o in L), 2)}
    days = []
    for k in range(29, -1, -1):
        d0 = day0 - k * 86400
        L = [o for o in O if d0 <= (o["t"] or 0) < d0 + 86400]
        days.append({"d": datetime.fromtimestamp(d0).strftime("%d.%m"), "rev": round(sum(o["total"] for o in L), 2), "n": len(L)})
    prod = {}
    for o in O:
        for i in o["items"]:
            key = i.get("listing_id") or i["title"][:40]
            p = prod.setdefault(key, {"title": i["title"], "sales": 0, "revenue": 0.0})
            p["sales"] += i.get("qty", 1)
            p["revenue"] = round(p["revenue"] + i["price"] * i.get("qty", 1), 2)
    for l in D.get("listings", []):
        p = prod.setdefault(l["id"], {"title": l["title"], "sales": 0, "revenue": 0.0})
        p.update(views=l["views"], favorites=l["favorites"], price=l["price"], url=l["url"])
        p["conv"] = round(p["sales"] / l["views"], 4) if l["views"] else None
    pers = [{"id": o["id"], "t": o["t"], "buyer": o["buyer"], "title": i["title"], "text": i["personalization"], "done": o.get("pers_done"),
             "url": o.get("pers_url"), "msg": o.get("pers_msg"), "parsed": o.get("pers_parsed")}
            for o in reversed(O) for i in o["items"] if i.get("personalization")][:30]
    return {"connected": bool(c.get("refresh")), "has_keys": bool(c.get("keystring")), "shop": D.get("shop"), "synced": D.get("synced"),
            "error": D.get("error"), "currency": (D.get("shop") or {}).get("currency") or (O[-1]["currency"] if O else "EUR"),
            "today": agg(day0), "d7": agg(now - 7 * 86400), "d30": agg(now - 30 * 86400), "all": agg(0), "days": days,
            "traffic": _traffic(D, O), "products": sorted(prod.values(), key=lambda p: -p["revenue"]), "pers": pers,
            "recent": [{"id": o["id"], "t": o["t"], "total": o["total"], "currency": o["currency"], "items": ", ".join(i["title"][:40] for i in o["items"])}
                       for o in reversed(O[-12:])], "fees": fees}


def _traffic(D, O):
    """Wyświetlenia ofert: dzienne przyrosty z zapisanych zrzutów + konwersja (zamówienia / wyświetlenia)."""
    H = D.get("views_hist", {})
    keys = sorted(H)
    daily = {}
    for a, b in zip(keys, keys[1:]):
        daily[b] = {"views": max(0, H[b]["views"] - H[a]["views"]), "fav": max(0, H[b]["fav"] - H[a]["fav"])}
    today = datetime.now().date()
    def win(n):
        ds = {(today - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(n)}
        v = sum(x["views"] for d, x in daily.items() if d in ds)
        f = sum(x["fav"] for d, x in daily.items() if d in ds)
        since = datetime.combine(today - timedelta(days=n - 1), datetime.min.time()).timestamp()
        o = sum(1 for x in O if (x["t"] or 0) >= since)
        return {"views": v, "fav": f, "orders": o, "conv": round(o / v, 4) if v else None}
    days = [{"d": (today - timedelta(days=k)).strftime("%d.%m"), "v": daily.get((today - timedelta(days=k)).strftime("%Y-%m-%d"), {}).get("views", 0)}
            for k in range(29, -1, -1)]
    last = H[keys[-1]] if keys else {}
    return {"today": win(1), "d7": win(7), "d30": win(30), "total_views": last.get("views", 0), "total_fav": last.get("fav", 0),
            "days": days, "since": keys[0] if keys else None}


@router.post("/api/etsy/keys")
async def keys(req: Request):
    b = await req.json()
    ks, sec = str(b.get("keystring", "")).strip(), str(b.get("secret", "")).strip()
    if not ks or not sec:
        return {"ok": False, "error": "podaj keystring i shared secret"}
    c = _cfg()
    c.update(keystring=ks, secret=sec)
    _write(CFG, c)
    # test klucza: openapi-ping odpowiada 200 tylko dla aktywnej (zatwierdzonej) aplikacji z poprawnym kluczem
    try:
        r = httpx.get(API + "/openapi-ping", headers={"x-api-key": f"{ks}:{sec}"}, timeout=20)
        if r.status_code == 200:
            return {"ok": True, "ping": True}
        return {"ok": True, "ping": False, "error": f"Etsy nie przyjmuje klucza ({r.status_code}): {r.text[:160]} – sprawdź, czy aplikacja ma status zatwierdzonej i czy keystring/secret są wklejone bez spacji."}
    except Exception as e:
        return {"ok": True, "ping": None, "error": "Nie udało się sprawdzić klucza: " + str(e)[:120]}


@router.get("/api/etsy/connect")
def connect(req: Request):
    c = _cfg()
    if not c.get("keystring"):
        return JSONResponse({"error": "najpierw zapisz klucze aplikacji"}, status_code=400)
    ver = secrets.token_urlsafe(48)[:64]
    st = secrets.token_urlsafe(16)
    chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).decode().rstrip("=")
    c.update(verifier=ver, state=st)
    _write(CFG, c)
    q = {"response_type": "code", "client_id": c["keystring"], "redirect_uri": _redirect(req), "scope": SCOPES, "state": st,
         "code_challenge": chal, "code_challenge_method": "S256"}
    return RedirectResponse("https://www.etsy.com/oauth/connect?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote))


@router.get("/api/etsy/callback")
def callback(req: Request, code: str = "", state: str = "", error: str = ""):
    c = _cfg()
    if error or not code or state != c.get("state"):
        _seterr("Połączenie z Etsy przerwane: " + (error or "zły stan, spróbuj jeszcze raz"))
        return RedirectResponse("/#etsy")
    r = httpx.post("https://api.etsy.com/v3/public/oauth/token", timeout=30, data={
        "grant_type": "authorization_code", "client_id": c["keystring"], "redirect_uri": _redirect(req),
        "code": code, "code_verifier": c.get("verifier", "")})
    j = r.json()
    if r.status_code >= 300 or "access_token" not in j:
        _seterr("Etsy nie wydało tokenu: " + str(j)[:150])
        return RedirectResponse("/#etsy")
    c.update(access=j["access_token"], refresh=j["refresh_token"], exp=time.time() + int(j.get("expires_in", 3600)), scopes=SCOPES)
    c.pop("verifier", None); c.pop("state", None)
    _write(CFG, c)
    _seterr(None)
    threading.Thread(target=sync, daemon=True).start()
    return RedirectResponse("/#etsy")


def _seterr(m):
    D = _read(DATA, {})
    D["error"] = m
    _write(DATA, D)


@router.post("/api/etsy/sync")
def api_sync():
    try:
        return sync()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.post("/api/etsy/csv")
async def api_csv(req: Request):
    raw = await req.body()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return import_csv(text)


@router.post("/api/etsy/pers/{oid}")
def pers_done(oid: str):
    with _lock:
        D = _read(DATA, {})
        for o in D.get("orders", []):
            if o["id"] == oid:
                o["pers_done"] = not o.get("pers_done")
        _write(DATA, D)
    return {"ok": True}


@router.post("/api/etsy/disconnect")
def disconnect():
    c = _cfg()
    for k in ("access", "refresh", "exp", "shop_id", "user_id"):
        c.pop(k, None)
    _write(CFG, c)
    return {"ok": True}


# ---------------------------------------------------------------- personalizacja zamówień (Small Business Tracker)
from fastapi.responses import FileResponse  # noqa: E402
from social import DIR  # noqa: E402

PERS = DIR / "etsy_pers"


def _public_base():
    import os
    d = os.environ.get("SOCIAL_DOMAIN", "").strip()
    return f"https://{d}" if d else "https://centrum-dowodzenia.duckdns.org"


def parse_pers(text, currency_hint="USD"):
    """Tekst personalizacji od klienta → nazwa firmy, waluta, kategorie (AI, z bezpiecznym fallbackiem)."""
    out = {"name": "", "currency": currency_hint, "inc": [], "exp": []}
    try:
        from agency import claude, as_json, FAST
        j = as_json(claude(f"""A customer bought a personalized small-business income & expense tracker and wrote:
"{text[:900]}"
Extract JSON only: {{"name":"business name or empty","currency":"3-letter ISO code (guess from symbols/country, default {currency_hint})",
"inc":["income categories they listed, max 20, short Title Case"],"exp":["expense categories they listed, max 20, short Title Case"]}}
If they listed categories without saying income/expense, put sales/services/products-type items in inc and costs in exp.""", FAST, 500)) or {}
        if isinstance(j, list):
            j = j[0] if j else {}
        out.update({k: j[k] for k in ("name", "currency", "inc", "exp") if j.get(k)})
    except Exception:
        out["name"] = text.split(",")[0].strip()[:60]
    out["currency"] = re.sub(r"[^A-Z]", "", str(out["currency"]).upper())[:3] or currency_hint
    out["inc"] = [str(x)[:40] for x in out["inc"]][:20]
    out["exp"] = [str(x)[:40] for x in out["exp"]][:20]
    return out


def make_pers(oid, override=None):
    import etsy_tpl
    with _lock:
        D = _read(DATA, {})
        o = next((x for x in D.get("orders", []) if x["id"] == oid), None)
    if not o:
        return {"ok": False, "error": "nie ma zamówienia"}
    text = "; ".join(i["personalization"] for i in o["items"] if i.get("personalization"))
    p = (override or None) or o.get("pers_parsed") or parse_pers(text, o.get("currency") or "USD")
    tok = o.get("pers_tok") or secrets.token_urlsafe(12)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", p.get("name") or "Business").strip("_")[:40] or "Business"
    fn = f"PlanPerfect_Profit_Tracker_{safe}.xlsx"
    d = PERS / tok
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("*.xlsx"):
        old.unlink()
    etsy_tpl.build(str(d / fn), NAME=p.get("name") or "Your Business Name", CURRENCY=p.get("currency") or "USD",
                   INC=p.get("inc") or None, EXP=p.get("exp") or None, EXAMPLE=False)
    url = f"{_public_base()}/pub/etsy/{tok}/{fn}"
    first = (o.get("buyer") or "").split(" ")[0] or "there"
    msg = (f"Hi {first}! Thank you so much for your order 💚\n\n"
           f"Your personalized Small Business Profit & Expense Tracker{(' for ' + p['name']) if p.get('name') else ''} is ready:\n{url}\n\n"
           "How to open it in Google Sheets: upload the file to your Google Drive, right-click it → Open with → Google Sheets "
           "(then File → Save as Google Sheets). It also opens directly in Excel.\n\n"
           "Your business name, currency and categories are already set up – just start adding income and expenses. "
           "If you'd like any changes, reply here and I'll update it for you.\n\nEnjoy! – PlanPerfect Sheets")
    with _lock:
        D = _read(DATA, {})
        for x in D.get("orders", []):
            if x["id"] == oid:
                x.update(pers_tok=tok, pers_file=fn, pers_parsed=p, pers_msg=msg, pers_url=url, pers_at=time.time())
        _write(DATA, D)
    return {"ok": True, "url": url, "msg": msg, "parsed": p}


def auto_pers():
    """Nowe zamówienia z personalizacją → plik robi się sam, push z gotową wiadomością."""
    D = _read(DATA, {})
    for o in D.get("orders", []):
        if any(i.get("personalization") for i in o["items"]) and not o.get("pers_tok") and not o.get("pers_done"):
            r = make_pers(o["id"])
            if r.get("ok"):
                try:
                    from extra import push_all
                    push_all("✨ Personalizacja gotowa", f"Zamówienie {o['buyer'] or o['id']} – skopiuj wiadomość w zakładce Etsy i wyślij klientowi.", "/#etsy")
                except Exception:
                    pass


@router.post("/api/etsy/pers/{oid}/make")
async def api_make(oid: str, req: Request):
    b = {}
    try:
        b = await req.json()
    except Exception:
        pass
    ov = None
    if b.get("name") is not None:
        split = lambda v: [x.strip() for x in (v if isinstance(v, list) else str(v).split(",")) if x.strip()]
        ov = {"name": str(b.get("name", ""))[:60], "currency": str(b.get("currency", "USD"))[:3].upper(),
              "inc": split(b.get("inc", "")), "exp": split(b.get("exp", ""))}
    return make_pers(oid, ov)


@router.get("/pub/etsy/{tok}/{name}")
def pub_file(tok: str, name: str):
    if not re.fullmatch(r"[\w-]{8,40}", tok) or "/" in name or ".." in name:
        return JSONResponse({"error": "nie"}, status_code=404)
    f = PERS / tok / name
    return FileResponse(f, filename=name) if f.is_file() else JSONResponse({"error": "nie ma"}, status_code=404)
