"""Portfele: OKX i Kraken przez API (klucze TYLKO DO ODCZYTU).
Wartości w PLN (kurs USD z NBP), codzienny zapis historii do wykresu i zmiany 7 dni."""
import base64, hashlib, hmac, json, time, urllib.parse
from datetime import datetime, timezone, date

import httpx
from fastapi import APIRouter, Request

from social import _read, _write

router = APIRouter()
CFG = "wallets_secret.json"      # nazwa z „secret” – nie wychodzi przez MCP/diagnostykę
HIST = "wallets_hist.json"
_cache = {"t": 0, "v": None}


def _cfg():
    return _read(CFG, {})


def usd_pln():
    c = _read("wallets_fx.json", {})
    if c.get("d") == date.today().isoformat():
        return c["v"]
    try:
        r = httpx.get("https://api.nbp.pl/api/exchangerates/rates/a/usd/?format=json", timeout=15).json()
        v = float(r["rates"][0]["mid"])
        _write("wallets_fx.json", {"d": date.today().isoformat(), "v": v})
        return v
    except Exception:
        return c.get("v") or 4.0


OKX_HOSTS = ["https://www.okx.com", "https://eea.okx.com", "https://my.okx.com", "https://us.okx.com"]


def okx_usd(c):
    """Konta z Polski są zwykle w OKX EEA (eea.okx.com) – klucz z EEA nie istnieje na www.okx.com („API key doesn't exist”).
    Próbujemy kolejnych domen i zapamiętujemy tę, która działa."""
    path = "/api/v5/asset/asset-valuation?ccy=USD"
    hosts = ([c["host"]] if c.get("host") else []) + [h for h in OKX_HOSTS if h != c.get("host")]
    last = None
    for host in hosts:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        sign = base64.b64encode(hmac.new(c["secret"].encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()).decode()
        try:
            r = httpx.get(host + path, timeout=20, headers={"OK-ACCESS-KEY": c["key"], "OK-ACCESS-SIGN": sign,
                                                           "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": c["pass"]}).json()
        except Exception as e:
            last = str(e)[:80]
            continue
        if str(r.get("code")) == "0":
            if c.get("host") != host:
                C = _cfg()
                if C.get("okx"):
                    C["okx"]["host"] = host
                    _write(CFG, C)
            return float(r["data"][0]["totalBal"])
        last = r.get("msg") or r.get("code")
        if str(r.get("code")) not in ("50119", "50101", "50105"):   # zły klucz/środowisko – próbuj następnej domeny; inne błędy od razu
            break
    raise RuntimeError(f"OKX: {last}")


def kraken_usd(c):
    path = "/0/private/TradeBalance"
    nonce = str(int(time.time() * 1000))
    body = urllib.parse.urlencode({"nonce": nonce, "asset": "ZUSD"})
    sha = hashlib.sha256((nonce + body).encode()).digest()
    sig = base64.b64encode(hmac.new(base64.b64decode(c["secret"]), path.encode() + sha, hashlib.sha512).digest()).decode()
    r = httpx.post("https://api.kraken.com" + path, content=body, timeout=20,
                   headers={"API-Key": c["key"], "API-Sign": sig, "Content-Type": "application/x-www-form-urlencoded"}).json()
    if r.get("error"):
        raise RuntimeError("Kraken: " + ", ".join(r["error"]))
    return float(r["result"]["eb"])


def compute(force=False):
    if not force and _cache["v"] and time.time() - _cache["t"] < 600:
        return _cache["v"]
    c, fx, acc, errs = _cfg(), usd_pln(), [], []
    for name, fn, k in (("OKX", okx_usd, "okx"), ("Kraken", kraken_usd, "kraken")):
        if (c.get(k) or {}).get("key"):
            try:
                acc.append({"name": name, "value": round(fn(c[k]) * fx)})
            except Exception as e:
                errs.append(str(e)[:120])
    if not acc:
        out = {"ok": False, "configured": False, "errors": errs}
        _cache.update(t=time.time(), v=out)
        return out
    total = sum(a["value"] for a in acc)
    H = _read(HIST, {})
    today = date.today().isoformat()
    H[today] = {"total": total, **{a["name"]: a["value"] for a in acc}}
    H = dict(sorted(H.items())[-400:])
    _write(HIST, H)
    days = sorted(H)
    def ago(n):
        ref = date.fromordinal(date.today().toordinal() - n).isoformat()
        prev = [d for d in days if d <= ref]
        return H[prev[-1]] if prev else None
    w = ago(7)
    for a in acc:
        if w and w.get(a["name"]):
            a["change"] = round((a["value"] / w[a["name"]] - 1) * 100, 1)
    out = {"ok": True, "total": total, "change_7d": round((total / w["total"] - 1) * 100, 1) if w and w.get("total") else None,
           "accounts": acc, "history": [{"d": d, "v": H[d]["total"]} for d in days[-30:]], "errors": errs, "usd_pln": fx}
    _cache.update(t=time.time(), v=out)
    return out


@router.get("/api/wallets")
def wallets():
    return compute()


@router.get("/api/wallets/cfg")
def wallets_cfg():
    c = _cfg()
    return {"okx": bool((c.get("okx") or {}).get("key")), "kraken": bool((c.get("kraken") or {}).get("key"))}


@router.post("/api/wallets/cfg")
async def wallets_save(req: Request):
    b = await req.json()
    c = _cfg()
    o = b.get("okx") or {}
    if o.get("key") and o.get("secret") and o.get("pass"):
        c["okx"] = {"key": o["key"].strip(), "secret": o["secret"].strip(), "pass": o["pass"].strip()}
    k = b.get("kraken") or {}
    if k.get("key") and k.get("secret"):
        c["kraken"] = {"key": k["key"].strip(), "secret": k["secret"].strip()}
    c.pop("xtb", None)
    for x in b.get("remove") or []:
        c.pop(x, None)
    _write(CFG, c)
    try:
        import os
        from social import DIR
        os.chmod(DIR / CFG, 0o600)
    except Exception:
        pass
    r = compute(force=True)
    return {"ok": True, "wallets": r}
