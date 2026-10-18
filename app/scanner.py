"""Zakładka Market Scanner: podgląd skanera (MarketScanner w Dockerze) – tylko do odczytu.
Czyta bazę SQLite skanera (SCANNER_DB) w trybie read-only, kontekst rynku z jego API (SCANNER_URL)
i stan kontenera (docker). Niczego w skanerze nie zmienia.
"""
import json, os, re, sqlite3, subprocess, time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter

router = APIRouter()
DB = os.environ.get("SCANNER_DB") or next((p for p in ("/root/MarketScanner_git/market_scanner.db", "/root/MarketScanner/market_scanner.db",
                                                      "/root/market_scanner.db") if os.path.exists(p)), "/root/MarketScanner_git/market_scanner.db")
URL = os.environ.get("SCANNER_URL", "http://127.0.0.1:8000").rstrip("/")
CONTAINER = os.environ.get("SCANNER_CONTAINER", "marketscanner")
WIN = ("WIN", "PARTIAL_WIN")
_cache = {"t": 0, "data": None}


def _q(sql, args=()):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _docker():
    out = {"running": False, "started": None, "last_log": None, "symbols": [], "cycles": 0, "errors": []}
    try:
        st = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}|{{.State.StartedAt}}", CONTAINER],
                            capture_output=True, text=True, timeout=5).stdout.strip().split("|")
        out["running"], out["started"] = st[0] == "running", (st[1] if len(st) > 1 else None)
        lg = subprocess.run(["docker", "logs", "--since", "4m", "--tail", "400", "-t", CONTAINER],
                            capture_output=True, text=True, timeout=8)
        lines = (lg.stdout + lg.stderr).splitlines()
        if lines:
            out["last_log"] = lines[-1][:30]
        syms = re.findall(r"\b([A-Z0-9]{2,10})/USDT(?::USDT)?", "\n".join(lines))
        out["symbols"] = list(dict.fromkeys(reversed(syms)))[:15]
        out["cycles"] = sum("WORKFLOW RUN START" in l for l in lines)
        out["errors"] = [l[31:200] for l in lines if re.search(r"ERROR|Traceback|Exception", l)][-3:]
        txt = "\n".join(lines)
        out["hits"] = {k: len(re.findall(rx, txt, re.I)) for k, rx in KW.items()}
    except Exception as e:
        out["errors"] = [f"docker: {e}"[:150]]
    if not out["running"]:
        # brak dostępu do dockera (np. inne uprawnienia) – sprawdzamy, czy odpowiada API skanera
        try:
            out["running"] = httpx.get(URL + "/market-context", timeout=4).status_code < 500
            out["via"] = "api"
        except Exception:
            pass
    return out


# słowa kluczowe w logach skanera → który moduł właśnie pracował (ostatnie 4 min)
KW = {"scan": r"WORKFLOW RUN|scan_market|Skanuj|scanning", "watch": r"watchlist|priorytet", "elite": r"elite",
      "mods": r"Modifier", "calib": r"calibration|kalibrac", "macro": r"macro|VIX|DXY|SPY", "regime": r"regime|re[zż]im",
      "news": r"news|sentiment", "smart": r"smart.?money|order.?block|liquidity|premium|discount", "funding": r"funding|open.?interest|\bOI\b",
      "pattern": r"pattern|formac", "risk": r"portfolio|exposure|ekspozycj", "sizing": r"position.?size|sizing|wielko",
      "shadow": r"shadow", "audit": r"self.?audit|audyt", "report": r"report|raport|performance", "trade": r"\bTP\d|\bSL\b|trade|break.?even|INVALIDATED",
      "telegram": r"telegram|wys[łl]ano"}


def _rate(rows):
    closed = [r for r in rows if r.get("result")]
    w = sum(1 for r in closed if r["result"] in WIN)
    return {"n": len(rows), "closed": len(closed), "wins": w, "wr": round(w / len(closed) * 100) if closed else None,
            "pnl": round(sum(r.get("pnl") or 0 for r in closed), 2)}


@router.get("/api/scanner/signal/{sid}")
def signal(sid: int):
    """Pełny zapis jednego sygnału (do centrum powiadomień)."""
    if not os.path.exists(DB):
        return {"ok": False, "error": "brak bazy skanera"}
    try:
        r = _q("SELECT * FROM trade_signals WHERE id=?", (sid,))
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}
    return {"ok": bool(r), "signal": r[0] if r else None}


@router.get("/api/scanner/state")
def state(force: int = 0):
    if _cache["data"] and not force and time.time() - _cache["t"] < 20:
        return _cache["data"]
    out = {"ok": True, "docker": _docker(), "context": None, "fetched": time.time()}
    try:
        out["context"] = httpx.get(URL + "/market-context", timeout=6).json()
    except Exception as e:
        out["context_error"] = str(e)[:120]
    if not os.path.exists(DB):
        out.update(ok=False, error=f"Nie widzę bazy skanera: {DB} (ustaw SCANNER_DB w /root/social/.env)")
        _cache.update(t=time.time(), data=out)
        return out
    since = (datetime.utcnow() - timedelta(days=30)).isoformat(" ")
    cols = "id,created_at,closed_at,symbol,direction,status,entry,stop_loss,tp1,tp2,exit_price,result,exit_reason,pnl," \
           "position_size_percent,is_shadow,elite_score,rejection_reason,market_regime,risk_reward,elite_breakdown_json"
    rows = _q(f"SELECT {cols} FROM trade_signals WHERE created_at >= ? ORDER BY created_at DESC", (since,))
    real = [r for r in rows if not r["is_shadow"]]
    shadow = [r for r in rows if r["is_shadow"]]
    out["real"], out["shadow"] = _rate(real), _rate(shadow)
    today = datetime.utcnow().date().isoformat()
    out["today"] = sum(1 for r in real if str(r["created_at"]).startswith(today))
    # otwarte pozycje (prawdziwe) i ekspozycja
    opn = [r for r in real if not r["result"] and (r["status"] or "").upper() not in ("CLOSED", "INVALIDATED", "MISSED", "CANCELLED")]
    out["open"] = [{k: r[k] for k in ("symbol", "direction", "status", "entry", "stop_loss", "tp1", "elite_score", "position_size_percent", "created_at")} for r in opn][:20]
    out["exposure"] = round(sum(r["position_size_percent"] or 0 for r in opn), 2)
    out["shadow_open"] = sum(1 for r in shadow if not r["result"])
    # ostatnie zdarzenia
    out["recent"] = [{k: r[k] for k in ("id", "created_at", "closed_at", "symbol", "direction", "status", "result", "pnl", "elite_score", "is_shadow", "rejection_reason")}
                     for r in rows[:30]]
    # kalibracja: win rate wg przedziału elite_score
    buckets = defaultdict(list)
    for r in rows:
        s = r["elite_score"] or 0
        b = "95–100" if s >= 95 else "90–94" if s >= 90 else "80–89" if s >= 80 else "70–79" if s >= 70 else "<70"
        buckets[b].append(r)
    out["calibration"] = [{"bucket": b, **_rate(buckets[b])} for b in ("95–100", "90–94", "80–89", "70–79", "<70") if buckets[b]]
    # powody odrzucenia (shadow)
    rej = defaultdict(list)
    for r in shadow:
        rej[(r["rejection_reason"] or "brak powodu").split(":")[0][:60]].append(r)
    out["rejections"] = sorted([{"reason": k, **_rate(v)} for k, v in rej.items()], key=lambda x: -x["n"])[:8]
    # reżimy
    reg = defaultdict(list)
    for r in rows:
        reg[r["market_regime"] or "?"].append(r)
    out["regimes"] = sorted([{"regime": k, **_rate(v)} for k, v in reg.items()], key=lambda x: -x["n"])[:6]
    # wpływ modyfikatorów Elite Engine: jak często działa i win rate gdy dodatni / ujemny
    mods = defaultdict(lambda: {"vals": [], "pos": [], "neg": []})
    for r in rows:
        try:
            bd = json.loads(r["elite_breakdown_json"] or "{}")
        except Exception:
            continue
        for k, v in bd.items():
            if not isinstance(v, (int, float)):
                continue
            m = mods[k]
            m["vals"].append(v)
            if r["result"]:
                (m["pos"] if v > 0 else m["neg"] if v < 0 else []).append(r["result"] in WIN)
    out["modifiers"] = sorted([{"name": k, "n": len(m["vals"]), "avg": round(sum(m["vals"]) / len(m["vals"]), 2),
                                "active": round(sum(1 for v in m["vals"] if v) / len(m["vals"]) * 100),
                                "wr_pos": round(sum(m["pos"]) / len(m["pos"]) * 100) if m["pos"] else None, "n_pos": len(m["pos"]),
                                "wr_neg": round(sum(m["neg"]) / len(m["neg"]) * 100) if m["neg"] else None, "n_neg": len(m["neg"])}
                               for k, m in mods.items() if m["vals"]], key=lambda x: x["name"])
    out["total_db"] = _q("SELECT COUNT(*) AS n FROM trade_signals")[0]["n"]
    _cache.update(t=time.time(), data=out)
    return out


# ---------- wykres kapitału: skumulowany PnL (real i shadow) ----------
def equity(days=60):
    since = (datetime.utcnow() - timedelta(days=days)).isoformat(" ")
    rows = _q("SELECT closed_at, pnl, is_shadow FROM trade_signals WHERE result IS NOT NULL AND closed_at >= ? ORDER BY closed_at", (since,))
    d0 = datetime.utcnow().date() - timedelta(days=days - 1)
    day = {(d0 + timedelta(days=i)).isoformat(): [0.0, 0.0, 0] for i in range(days)}
    for r in rows:
        k = str(r["closed_at"])[:10]
        if k in day:
            day[k][1 if r["is_shadow"] else 0] += r["pnl"] or 0
            day[k][2] += 0 if r["is_shadow"] else 1
    out, a, b = [], 0.0, 0.0
    for k, (pr, ps, n) in day.items():
        a, b = a + pr, b + ps
        out.append({"d": k, "real": round(a, 2), "shadow": round(b, 2), "n": n})
    return out


@router.get("/api/scanner/equity")
def equity_api(days: int = 60):
    try:
        return {"ok": True, "series": equity(max(7, min(days, 180)))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:150]}


# ---------- sentyment krypto (do podglądu w centrum) ----------
_fg = {"t": 0, "data": None}


@router.get("/api/scanner/sentiment")
def sentiment():
    if _fg["data"] and time.time() - _fg["t"] < 1800:
        return _fg["data"]
    d = {"fear_greed": None, "fg_label": None, "btc_dominance": None}
    try:
        v = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=8).json()["data"][0]
        d.update(fear_greed=int(v["value"]), fg_label=v.get("value_classification"))
    except Exception:
        pass
    try:
        d["btc_dominance"] = round(httpx.get("https://api.coingecko.com/api/v3/global", timeout=8).json()["data"]["market_cap_percentage"]["btc"], 1)
    except Exception:
        pass
    _fg.update(t=time.time(), data=d)
    return d


# ---------- Audytor AI (raz w tygodniu): diagnoza i propozycje, bez zmian w skanerze ----------
def _brief(r):
    try:
        bd = {k: v for k, v in json.loads(r.get("elite_breakdown_json") or "{}").items() if v not in (0, None)}
    except Exception:
        bd = {}
    return (f"{r['symbol'].split('/')[0]} {r['direction']} elite={r['elite_score']} reżim={r['market_regime']} "
            f"wynik={r['result']} pnl={r['pnl']} powód_odrzucenia={(r['rejection_reason'] or '-')[:60]} składniki={json.dumps(bd)[:300]}")


def audit():
    from agency import claude, SMART, ai_ready, log
    from extra import push_all
    from social import _read, _write
    if not ai_ready():
        return {"ok": False, "error": "brak klucza AI"}
    st = state(force=1)
    if not st.get("ok"):
        return {"ok": False, "error": st.get("error")}
    since = (datetime.utcnow() - timedelta(days=30)).isoformat(" ")
    cols = "symbol,direction,elite_score,market_regime,result,pnl,rejection_reason,elite_breakdown_json"
    losers = _q(f"SELECT {cols} FROM trade_signals WHERE is_shadow=0 AND result='LOSS' AND created_at>=? ORDER BY created_at DESC LIMIT 15", (since,))
    missed = _q(f"SELECT {cols} FROM trade_signals WHERE is_shadow=1 AND result IN ('WIN','PARTIAL_WIN') AND created_at>=? ORDER BY elite_score DESC LIMIT 15", (since,))
    summary = {k: st[k] for k in ("real", "shadow", "calibration", "rejections", "regimes", "modifiers", "exposure")}
    txt = claude(f"""Jesteś Audytorem skanera krypto/akcji (OKX, swap USDT). Skaner wysyła max 4 sygnały dziennie z elite_score ≥ 90,
odrzucone sygnały (≥70) śledzi jako shadow. Właściciel kalibruje wagi wyłącznie na danych i ostrożnie (małe próbki = obserwacja).

STATYSTYKI 30 DNI (JSON): {json.dumps(summary, ensure_ascii=False)[:6000]}

PRZEGRANE PRAWDZIWE SYGNAŁY:
{chr(10).join(_brief(r) for r in losers) or 'brak'}

DZIENNIK OSTATNICH TRANSAKCJI (notatki AI po zamknięciu):
{chr(10).join(f"- {j['symbol']} {j['direction']} {j['result']} {j['pnl']:+.2f}%: {j['text']}" for j in _read("scanner_journal.json", [])[-15:]) or 'brak'}

WYGRANE, KTÓRE ODRZUCIŁ (shadow):
{chr(10).join(_brief(r) for r in missed) or 'brak'}

Napisz po polsku krótki raport audytu:
1. Diagnoza (2–3 zdania): co działa, co nie.
2. Wzorce w przegranych i w odrzuconych wygranych (konkretne modyfikatory, reżimy, przedziały score).
3. Max 4 propozycje zmian (próg, waga modyfikatora, filtr) – przy każdej: na jakich danych, wielkość próbki, ryzyko,
   i czy próbka jest wystarczająca (jeśli N < 30, napisz wprost „obserwować”, nie zmieniać).
Bez ogólników i bez porad inwestycyjnych. Nie wymyślaj liczb spoza danych.""", SMART, 1800)
    rep = {"at": time.time(), "text": txt.strip(), "real": st["real"], "shadow": st["shadow"]}
    hist = _read("scanner_audit.json", [])
    hist.append(rep)
    _write("scanner_audit.json", hist[-8:])
    log("op", "Audytor skanera: nowy raport", "ok")
    push_all("🧾 Audyt skanera gotowy", f"Real {st['real'].get('wr')}% WR, shadow {st['shadow'].get('wr')}% – propozycje w zakładce Skaner.", "/#skaner")
    return {"ok": True}


_audit_busy = {"on": False}


@router.get("/api/scanner/audit")
def audit_get():
    from social import _read
    return {"busy": _audit_busy["on"], "reports": _read("scanner_audit.json", [])[::-1]}


@router.post("/api/scanner/audit")
def audit_run():
    import threading

    def go():
        _audit_busy["on"] = True
        try:
            audit()
        except Exception as e:
            print("scanner audit", e, flush=True)
        finally:
            _audit_busy["on"] = False
    if not _audit_busy["on"]:
        threading.Thread(target=go, daemon=True).start()
    return {"ok": True}


# ---------- push: nowe prawdziwe sygnały i ich wyniki + audyt w niedzielę 20:00 ----------
def _watch():
    import threading  # noqa: F401
    from zoneinfo import ZoneInfo
    from extra import push_all
    from social import _read, _write
    tz = ZoneInfo("Europe/Warsaw")
    time.sleep(60)
    while True:
        try:
            if os.path.exists(DB):
                S = _read("scanner_seen.json", {})
                first = not S
                rows = _q("SELECT id, symbol, direction, entry, stop_loss, tp1, elite_score, position_size_percent, result, pnl "
                          "FROM trade_signals WHERE is_shadow=0 ORDER BY id DESC LIMIT 60")
                for r in reversed(rows):
                    k, prev = str(r["id"]), S.get(str(r["id"]))
                    sym = r["symbol"].split("/")[0]
                    if prev is None and not first:
                        push_all(f"📈 Sygnał: {sym} {r['direction']}",
                                 f"elite {r['elite_score']:.0f} · entry {r['entry']} · SL {r['stop_loss']} · TP1 {r['tp1']} · {r['position_size_percent'] or '?'}%",
                                 "/#skaner", kind="skaner", data={"signal": r["id"]})
                    if r["result"] and prev != r["result"] and not first:
                        icon = "✅" if r["result"] in WIN else "➖" if r["result"] == "BREAK_EVEN" else "❌"
                        push_all(f"{icon} {sym} {r['direction']}: {r['result']}", f"PnL {r['pnl']:+.2f}%", "/#skaner", kind="skaner", data={"signal": r["id"]})
                        _th.Thread(target=journal_entry, args=(r["id"],), daemon=True).start()
                    S[k] = r["result"] or ""
                _write("scanner_seen.json", dict(sorted(S.items(), key=lambda kv: -int(kv[0]))[:200]))
            now = datetime.now(tz)
            if now.weekday() == 6 and now.hour == 20:
                hist = _read("scanner_audit.json", [])
                if not hist or time.time() - hist[-1]["at"] > 20 * 3600:
                    audit_run()
        except Exception as e:
            print("scanner watch", e, flush=True)
        time.sleep(60)


import threading as _th  # noqa: E402
_th.Thread(target=_watch, daemon=True).start()


# ---------- notowania OKX do wykresu świecowego i paska kursów (publiczne API, bez klucza) ----------
_okx = {}


def _inst(sym):
    """'BTC/USDT:USDT' albo 'BTC' -> 'BTC-USDT-SWAP'."""
    base = sym.split("/")[0].split("-")[0].upper()
    return f"{base}-USDT-SWAP"


@router.get("/api/scanner/candles")
def candles(symbol: str = "BTC", bar: str = "1H", limit: int = 120):
    if bar not in ("15m", "1H", "4H", "1D"):
        bar = "1H"
    inst, key = _inst(symbol), f"{symbol}|{bar}"
    c = _okx.get(key)
    if c and time.time() - c[0] < 4:
        return c[1]
    try:
        r = httpx.get("https://www.okx.com/api/v5/market/candles", params={"instId": inst, "bar": bar, "limit": max(20, min(limit, 300))},
                      timeout=10).json()
        rows = [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r.get("data", [])][::-1]
        out = {"ok": bool(rows), "symbol": inst.split("-")[0], "inst": inst, "bar": bar, "candles": rows, "error": r.get("msg") if not rows else None}
    except Exception as e:
        out = {"ok": False, "symbol": inst.split("-")[0], "error": str(e)[:120], "candles": []}
    # poziomy z otwartej (prawdziwej) pozycji na tym symbolu
    try:
        if os.path.exists(DB):
            p = _q("SELECT direction, entry, stop_loss, tp1, tp2, elite_score, status FROM trade_signals WHERE is_shadow=0 AND result IS NULL "
                   "AND symbol LIKE ? ORDER BY id DESC LIMIT 1", (inst.split("-")[0] + "/%",))
            out["position"] = p[0] if p else None
    except Exception:
        out["position"] = None
    _okx[key] = (time.time(), out)
    return out


@router.get("/api/scanner/tickers")
def tickers():
    c = _okx.get("tickers")
    if c and time.time() - c[0] < 30:
        return c[1]
    want = ["BTC", "ETH", "SOL", "XRP", "BNB", "TAO", "HYPE", "AVAX", "LINK", "SUI", "XAU", "NVDA", "MSTR", "SPY", "QQQ"]
    try:
        if os.path.exists(DB):
            want = list(dict.fromkeys([r["s"].split("/")[0] for r in _q("SELECT DISTINCT symbol AS s FROM trade_signals ORDER BY id DESC LIMIT 40")] + want))[:24]
    except Exception:
        pass
    try:
        data = httpx.get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SWAP"}, timeout=10).json().get("data", [])
        m = {d["instId"].split("-")[0]: d for d in data if d["instId"].endswith("-USDT-SWAP")}
        out = {"ok": True, "items": [{"s": s, "last": float(m[s]["last"]), "chg": round((float(m[s]["last"]) / float(m[s]["open24h"]) - 1) * 100, 2)}
                                     for s in want if s in m and float(m[s]["open24h"] or 0)]}
    except Exception as e:
        out = {"ok": False, "items": [], "error": str(e)[:120]}
    _okx["tickers"] = (time.time(), out)
    return out


# ---------- statystyki skanera (zakładka „Statystyki skanera”) ----------
@router.get("/api/scanner/stats")
def stats(days: int = 30, shadow: int = 0):
    if not os.path.exists(DB):
        return {"ok": False, "error": f"Nie widzę bazy skanera: {DB}"}
    days = max(1, min(days, 3650))
    since = (datetime.utcnow() - timedelta(days=days)).isoformat(" ")
    rows = _q("SELECT created_at, closed_at, symbol, direction, result, exit_reason, pnl, elite_score, market_regime, is_shadow, "
              "position_size_percent FROM trade_signals WHERE created_at >= ? AND is_shadow = ? ORDER BY created_at", (since, 1 if shadow else 0))
    closed = [r for r in rows if r["result"]]
    wins = [r for r in closed if r["result"] in WIN]
    losses = [r for r in closed if r["result"] == "LOSS"]
    gp = sum(r["pnl"] or 0 for r in closed if (r["pnl"] or 0) > 0)
    gl = -sum(r["pnl"] or 0 for r in closed if (r["pnl"] or 0) < 0)

    def hours(r):
        try:
            return (datetime.fromisoformat(str(r["closed_at"])) - datetime.fromisoformat(str(r["created_at"]))).total_seconds() / 3600
        except Exception:
            return None
    hold = [h for h in (hours(r) for r in closed) if h is not None]
    # seria strat/wygranych i obsunięcie kapitału
    eq, peak, dd, streak, best_s, worst_s = 0.0, 0.0, 0.0, 0, 0, 0
    curve = []
    for r in sorted(closed, key=lambda r: str(r["closed_at"])):
        eq += r["pnl"] or 0
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        streak = (streak + 1 if streak >= 0 else 1) if r["result"] in WIN else (streak - 1 if streak <= 0 else -1)
        best_s, worst_s = max(best_s, streak), min(worst_s, streak)
        curve.append({"t": str(r["closed_at"])[:16], "eq": round(eq, 2), "sym": r["symbol"].split("/")[0], "pnl": r["pnl"]})

    def group(key):
        g = defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        return sorted([{"k": k, **_rate(v)} for k, v in g.items()], key=lambda x: -(x["pnl"] or 0))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Warsaw")

    def local(ts):
        try:
            return datetime.fromisoformat(str(ts)).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        except Exception:
            return None
    heat = defaultdict(lambda: {"n": 0, "w": 0, "pnl": 0.0})
    for r in closed:
        d = local(r["created_at"])
        if d:
            c = heat[f"{d.weekday()}-{d.hour // 4}"]
            c["n"] += 1
            c["w"] += r["result"] in WIN
            c["pnl"] += r["pnl"] or 0
    return {"ok": True, "days": days, "shadow": bool(shadow),
            "kpi": {**_rate(rows), "avg_win": round(sum(r["pnl"] or 0 for r in wins) / len(wins), 2) if wins else None,
                    "avg_loss": round(sum(r["pnl"] or 0 for r in losses) / len(losses), 2) if losses else None,
                    "pf": round(gp / gl, 2) if gl else None, "max_dd": round(dd, 2), "best_streak": best_s, "worst_streak": -worst_s,
                    "hold_h": round(sum(hold) / len(hold), 1) if hold else None,
                    "avg_size": round(sum(r["position_size_percent"] or 0 for r in rows) / len(rows), 2) if rows else None,
                    "open": sum(1 for r in rows if not r["result"])},
            "curve": curve,
            "by_symbol": [{**x, "k": x["k"].split("/")[0]} for x in group(lambda r: r["symbol"])],
            "by_direction": group(lambda r: r["direction"] or "?"),
            "by_regime": group(lambda r: r["market_regime"] or "?"),
            "by_exit": group(lambda r: r["exit_reason"] or ("otwarta" if not r["result"] else r["result"])),
            "heat": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in heat.items()},
            "recent": [{k: r[k] for k in ("created_at", "closed_at", "symbol", "direction", "result", "exit_reason", "pnl", "elite_score")}
                       for r in rows[::-1][:40]]}


# ---------- Dziennik transakcji: po każdym zamknięciu AI zapisuje, dlaczego wygrało / przegrało ----------
_BARS = {"15m": 900, "1H": 3600, "4H": 14400}


def journal_entry(sid, force=False):
    from agency import claude, FAST, ai_ready
    from social import _read, _write
    J = _read("scanner_journal.json", [])
    if not force and any(j["id"] == sid for j in J):
        return None
    if not ai_ready():
        return None
    r = _q("SELECT id, created_at, closed_at, symbol, direction, entry, stop_loss, tp1, tp2, exit_price, result, exit_reason, pnl, elite_score, "
           "market_regime, risk_reward, max_profit_percent, max_drawdown_percent, elite_breakdown_json FROM trade_signals WHERE id=?", (sid,))
    if not r or not r[0]["result"]:
        return None
    r = r[0]
    try:
        bd = {k: v for k, v in json.loads(r["elite_breakdown_json"] or "{}").items() if v not in (0, None)}
    except Exception:
        bd = {}
    # przebieg ceny w czasie trwania transakcji (świece 1H z OKX)
    path = ""
    try:
        t0 = int(datetime.fromisoformat(str(r["created_at"])).replace(tzinfo=None).timestamp() * 1000)
        t1 = int(datetime.fromisoformat(str(r["closed_at"])).replace(tzinfo=None).timestamp() * 1000) if r["closed_at"] else t0 + 86400000
        js = httpx.get("https://www.okx.com/api/v5/market/history-candles", timeout=10,
                       params={"instId": _inst(r["symbol"]), "bar": "1H", "after": t1 + 3600000, "limit": 100}).json().get("data", [])
        cs = [(int(x[0]), float(x[2]), float(x[3]), float(x[4])) for x in js if t0 - 3600000 <= int(x[0]) <= t1][::-1]
        if cs:
            hi, lo = max(c[1] for c in cs), min(c[2] for c in cs)
            path = f"w trakcie: max {hi}, min {lo}, {len(cs)} świec 1H, zamknięcia: " + ", ".join(f"{c[3]}" for c in cs[:: max(1, len(cs) // 8)])
    except Exception:
        pass
    txt = claude(f"""Transakcja skanera (OKX swap): {r['symbol']} {r['direction']}, entry {r['entry']}, SL {r['stop_loss']}, TP1 {r['tp1']}, TP2 {r['tp2']},
wyjście {r['exit_price']} ({r['exit_reason']}), wynik {r['result']} {r['pnl']:+.2f}%, elite_score {r['elite_score']}, reżim {r['market_regime']},
RR {r['risk_reward']}, maks. zysk w trakcie {r['max_profit_percent']}%, maks. obsunięcie {r['max_drawdown_percent']}%.
Składniki elite_score: {json.dumps(bd)[:600]}
{path}

Napisz po polsku DOKŁADNIE 2 krótkie zdania do dziennika: 1) dlaczego transakcja wygrała/przegrała (konkretnie: ruch ceny względem poziomów,
który składnik scoringu trafił albo zawiódł), 2) jedna lekcja na przyszłość. Bez wstępu, bez porad inwestycyjnych.""", FAST, 220).strip()
    e = {"id": sid, "at": time.time(), "symbol": r["symbol"].split("/")[0], "direction": r["direction"], "result": r["result"],
         "pnl": r["pnl"] or 0, "text": txt[:500]}
    J = [j for j in J if j["id"] != sid] + [e]
    _write("scanner_journal.json", J[-150:])
    return e


@router.get("/api/scanner/journal")
def journal_get():
    from social import _read
    return _read("scanner_journal.json", [])[::-1][:60]


@router.post("/api/scanner/journal/backfill")
def journal_backfill():
    """Uzupełnia dziennik dla ostatnich 8 zamkniętych prawdziwych transakcji bez notatki."""
    from social import _read
    have = {j["id"] for j in _read("scanner_journal.json", [])}
    ids = [r["id"] for r in _q("SELECT id FROM trade_signals WHERE is_shadow=0 AND result IS NOT NULL ORDER BY closed_at DESC LIMIT 20")
           if r["id"] not in have][:8]

    def go():
        for i in ids:
            try:
                journal_entry(i)
            except Exception as ex:
                print("journal", ex, flush=True)
    _th.Thread(target=go, daemon=True).start()
    return {"ok": True, "n": len(ids)}


# ---------- zakładka „Sygnały”: aktualne sygnały z entry / SL / TP i co z nimi zrobić ----------
SIG_COLS = "id,created_at,closed_at,symbol,timeframe,direction,status,entry,entry_min,entry_max,stop_loss,original_stop_loss,tp1,tp2,tp3,tp4,exit_price,result," \
           "exit_reason,pnl,tp1_hit,tp2_hit,tp3_hit,break_even_set,position_size_percent,elite_score,risk_reward,market_regime,rank_label,stars," \
           "max_profit_percent,max_drawdown_percent"


def _sig_rows(where, args, limit):
    try:
        return _q(f"SELECT {SIG_COLS} FROM trade_signals WHERE is_shadow=0 AND {where} ORDER BY id DESC LIMIT {int(limit)}", args)
    except sqlite3.OperationalError:  # starsza baza bez części kolumn
        return _q(f"SELECT * FROM trade_signals WHERE is_shadow=0 AND {where} ORDER BY id DESC LIMIT {int(limit)}", args)


def _is_open(r):
    return not r.get("result") and (r.get("status") or "").upper() not in ("CLOSED", "INVALIDATED", "MISSED", "CANCELLED", "EXPIRED")


def _advice(r, price):
    """Co zrobić z sygnałem – tekst dla człowieka (centrum nic nie zleca)."""
    st = (r.get("status") or "").upper()
    sl, e = r.get("stop_loss"), r.get("entry")
    if not e or not sl:
        return "Skaner nie zapisał poziomów dla tego sygnału."
    if r.get("tp2_hit"):
        return f"TP2 osiągnięty – SL na TP1 ({sl}). Reszta pozycji jedzie do dalszych TP."
    if r.get("tp1_hit") or r.get("break_even_set"):
        return f"TP1 osiągnięty – SL na entry ({sl}). Pozycja nie może już zamknąć się stratą."
    lo, hi = r.get("entry_min"), r.get("entry_max")
    if st in ("PENDING", "WAIT_PULLBACK", "WAITING", "OPEN") and price and e:
        if lo and hi:
            a, b = min(lo, hi), max(lo, hi)
            if a <= price <= b:
                return f"Cena jest w strefie wejścia {a}–{b} – limit w strefie powinien się realizować."
            far = min(abs(price / a - 1), abs(price / b - 1)) * 100
            return f"Czeka na wejście: limit w strefie {a}–{b} (cena {far:.2f}% od strefy)."
        far = abs(price / e - 1) * 100
        if far > 0.15:
            return f"Czeka na wejście: zlecenie limit na {e} (cena {far:.2f}% od entry)."
    return f"W grze: SL {sl}, cel TP1 {r.get('tp1')}."


def _signals_for(base, d):
    r = _sig_rows("symbol LIKE ? AND UPPER(direction)=? AND created_at >= ?", (base + "/%", d.upper(),
                  (datetime.utcnow() - timedelta(days=21)).isoformat(" ")), 1)
    return r[0] if r else None


@router.get("/api/scanner/signals")
def signals():
    if not os.path.exists(DB):
        return {"ok": False, "error": f"Nie widzę bazy skanera: {DB}"}
    since = (datetime.utcnow() - timedelta(days=21)).isoformat(" ")
    rows = _sig_rows("created_at >= ?", (since,), 120)
    px = {t["s"]: t["last"] for t in tickers().get("items", [])}
    act, closed = [], []
    for r in rows:
        base = r["symbol"].split("/")[0]
        p = px.get(base)
        r["base"], r["price"] = base, p
        if p and r.get("entry"):
            sgn = 1 if (r["direction"] or "").upper() == "LONG" else -1
            r["move"] = round((p / r["entry"] - 1) * 100 * sgn, 2)
            risk = abs(r["entry"] - (r.get("original_stop_loss") or r["stop_loss"] or r["entry"]))
            r["r_now"] = round((p - r["entry"]) * sgn / risk, 2) if risk else None
        if _is_open(r):
            r["advice"] = _advice(r, p)
            act.append(r)
        elif len(closed) < 20:
            closed.append(r)
    import okx_watch
    okx = {"configured": okx_watch.configured()}
    if okx["configured"]:
        try:
            okx = okx_watch.check(_signals_for)
        except Exception as e:
            okx = {"configured": True, "ok": False, "error": str(e)[:160], "positions": [], "alerts": []}
    try:
        from social import _read
        okx["last"] = _read("okx_last.json", {})
        okx["site"] = (okx_watch._site.get("ok") or [None])[0]
    except Exception:
        pass
    return {"ok": True, "active": act, "closed": closed, "okx": okx, "at": time.time()}


def okx_guard_tick():
    """Wołane co minutę przez strażnika „Pozycje OKX”: nowe rozbieżności = push (każda raz). Zwraca (status, opis)."""
    import okx_watch
    from extra import push_all
    from social import _read, _write
    if not okx_watch.configured():
        return "idle", "dodaj klucz OKX tylko do odczytu (OKX_RO_KEY, OKX_RO_SECRET, OKX_RO_PASSPHRASE w .env)"
    r = okx_watch.check(_signals_for)
    _write("okx_last.json", {"at": time.time(), "ok": r.get("ok"), "error": r.get("error"), "n": len(r.get("positions") or []),
                             "site": (okx_watch._site.get("ok") or [None])[0]})
    if r.get("ok"):
        try:
            from scanner_team import coach_record
            coach_record(r.get("positions") or [], _signals_for)
        except Exception as e:
            print("trener", e, flush=True)
    if not r.get("ok"):
        return "warn", "OKX: " + r.get("error", "brak odpowiedzi")[:120]
    seen = _read("okx_alerts.json", {})
    now = {a["key"]: time.time() for a in r["alerts"]}
    for a in r["alerts"]:
        if a["key"] not in seen:
            push_all(a["title"], a["body"][:180], "/#skaner-sygnaly", kind="skaner", data={"signal": a["signal"]})
    _write("okx_alerts.json", {k: seen.get(k, v) for k, v in now.items()})
    if "trade" in (r.get("perm") or "") or "withdraw" in (r.get("perm") or ""):
        return "warn", f"klucz OKX ma uprawnienia „{r['perm']}” – utwórz klucz tylko do odczytu"
    n = len(r["positions"])
    if r["alerts"]:
        return "warn", f"{len(r['alerts'])} do zrobienia ręcznie: " + r["alerts"][0]["title"][2:80]
    return "ok", f"{n} {'pozycja' if n == 1 else 'pozycji'} na OKX · SL zgodne ze skanerem" if n else "brak otwartych pozycji na OKX"
