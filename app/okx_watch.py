"""Strażnik pozycji OKX – TYLKO DO ODCZYTU i powiadomienia.

Porównuje Twoje otwarte pozycje na OKX (klucz API tylko do odczytu) z tym, co skaner wie o sygnale:
  • skaner przesunął SL (po TP1 na entry, po TP2 na TP1 …), a na OKX SL stoi gdzie indziej → push „przesuń SL na X”
  • na OKX nie ma SL → push „ustaw SL na X”
  • skaner zamknął / unieważnił sygnał, a pozycja na OKX dalej otwarta → push „rozważ zamknięcie”
Moduł wysyła wyłącznie zapytania GET. Niczego nie zleca, nie zmienia ani nie zamyka – decyzja i klik są po Twojej stronie.

Klucz w /root/social/.env:  OKX_RO_KEY=…  OKX_RO_SECRET=…  OKX_RO_PASSPHRASE=…
(na OKX: API → Utwórz klucz → uprawnienia tylko „Odczyt”).
"""
import base64, hashlib, hmac, os, time
from datetime import datetime, timezone

import httpx

BASE = "https://www.okx.com"
CLOSED = ("CLOSED", "INVALIDATED", "MISSED", "CANCELLED", "EXPIRED")
_cache = {"t": 0, "data": None}


def configured():
    return all(os.environ.get(k) for k in ("OKX_RO_KEY", "OKX_RO_SECRET", "OKX_RO_PASSPHRASE"))


def _get(path):
    """Podpisane zapytanie GET (jedyny rodzaj zapytania w tym module)."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    sign = base64.b64encode(hmac.new(os.environ["OKX_RO_SECRET"].encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()).decode()
    h = {"OK-ACCESS-KEY": os.environ["OKX_RO_KEY"], "OK-ACCESS-SIGN": sign, "OK-ACCESS-TIMESTAMP": ts,
         "OK-ACCESS-PASSPHRASE": os.environ["OKX_RO_PASSPHRASE"]}
    for attempt in range(3):
        try:
            j = httpx.get(BASE + path, headers=h, timeout=12).json()
            break
        except httpx.TransportError:
            if attempt == 2:
                raise
            time.sleep(2)
    if j.get("code") not in ("0", 0):
        raise RuntimeError(f"OKX {j.get('code')}: {j.get('msg', '')[:120]}")
    return j.get("data", [])


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _near(a, b, tol=0.0015):
    return a is not None and b is not None and abs(a - b) <= abs(b) * tol


def _fmt(x):
    if x is None:
        return "–"
    return f"{x:.6g}" if abs(x) < 1000 else f"{x:,.2f}".replace(",", " ")


def positions():
    """Otwarte pozycje + SL/TP widoczne na OKX (dołączone do pozycji albo jako zlecenia warunkowe)."""
    pos = [p for p in _get("/api/v5/account/positions?instType=SWAP") if _f(p.get("pos"))]
    sl = {}
    for p in pos:
        for a in p.get("closeOrderAlgo") or []:
            if _f(a.get("slTriggerPx")):
                sl.setdefault(p["instId"], []).append(_f(a["slTriggerPx"]))
    for typ in ("conditional", "oco"):
        try:
            for a in _get(f"/api/v5/trade/orders-algo-pending?ordType={typ}&instType=SWAP"):
                if _f(a.get("slTriggerPx")):
                    sl.setdefault(a["instId"], []).append(_f(a["slTriggerPx"]))
        except Exception:
            pass
    out = []
    for p in pos:
        n = _f(p["pos"])
        side = p.get("posSide")
        d = "LONG" if side == "long" or (side == "net" and n > 0) else "SHORT"
        out.append({"inst": p["instId"], "base": p["instId"].split("-")[0], "dir": d, "size": abs(n), "avg": _f(p.get("avgPx")),
                    "mark": _f(p.get("markPx")), "upl": _f(p.get("upl")), "uplr": round((_f(p.get("uplRatio")) or 0) * 100, 2),
                    "lev": p.get("lever"), "sl": sorted(set(sl.get(p["instId"], [])))})
    return out


def perm():
    try:
        return (_get("/api/v5/account/config") or [{}])[0].get("perm", "")
    except Exception:
        return ""


def check(signals_for):
    """signals_for(base, dir) -> ostatni prawdziwy sygnał skanera dla symbolu/kierunku (dict) albo None.
    Zwraca {"ok", "positions": [...], "alerts": [...], "perm"}."""
    if not configured():
        return {"ok": False, "configured": False, "positions": [], "alerts": []}
    if _cache["data"] and time.time() - _cache["t"] < 45:
        return _cache["data"]
    out = {"ok": True, "configured": True, "positions": [], "alerts": [], "perm": perm()}
    try:
        P = positions()
    except Exception as e:
        out.update(ok=False, error=str(e)[:160])
        return out
    for p in P:
        s = signals_for(p["base"], p["dir"])
        p["signal"] = s["id"] if s else None
        if not s:
            p["note"] = "pozycja bez sygnału skanera – nie pilnuję"
            out["positions"].append(p)
            continue
        want = _f(s.get("stop_loss"))
        closed = s.get("result") or (s.get("status") or "").upper() in CLOSED
        a = None
        if closed:
            a = {"kind": "close", "title": f"⚠️ {p['base']} {p['dir']}: skaner zamknął sygnał",
                 "body": f"Sygnał #{s['id']} ma wynik {s.get('result') or s.get('status')}, a pozycja na OKX jest dalej otwarta "
                         f"({p['uplr']:+.2f}%). Rozważ zamknięcie ręczne."}
        elif not p["sl"]:
            a = {"kind": "nosl", "title": f"🛑 {p['base']} {p['dir']}: brak SL na OKX",
                 "body": f"Skaner trzyma SL na {_fmt(want)}. Ustaw SL ręcznie w aplikacji OKX."}
        elif want and not any(_near(x, want) for x in p["sl"]):
            why = "po TP2 (na TP1)" if s.get("tp2_hit") else "po TP1 (na entry)" if s.get("tp1_hit") or s.get("break_even_set") else ""
            a = {"kind": "movesl", "title": f"🔒 {p['base']} {p['dir']}: przesuń SL na {_fmt(want)}",
                 "body": f"Skaner przesunął SL {why}. Na OKX SL stoi na {', '.join(_fmt(x) for x in p['sl'])}. Zmień ręcznie w aplikacji OKX."}
        if a:
            a.update(key=f"{p['inst']}|{a['kind']}|{want}", inst=p["inst"], signal=s["id"], target=want)
            out["alerts"].append(a)
            p["alert"] = a["title"]
        else:
            p["note"] = "SL zgodny ze skanerem"
        out["positions"].append(p)
    _cache.update(t=time.time(), data=out)
    return out
