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
    except Exception as e:
        out["errors"] = [f"docker: {e}"[:150]]
    return out


def _rate(rows):
    closed = [r for r in rows if r.get("result")]
    w = sum(1 for r in closed if r["result"] in WIN)
    return {"n": len(rows), "closed": len(closed), "wins": w, "wr": round(w / len(closed) * 100) if closed else None,
            "pnl": round(sum(r.get("pnl") or 0 for r in closed), 2)}


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
                                 "/#skaner")
                    if r["result"] and prev != r["result"] and not first:
                        icon = "✅" if r["result"] in WIN else "➖" if r["result"] == "BREAK_EVEN" else "❌"
                        push_all(f"{icon} {sym} {r['direction']}: {r['result']}", f"PnL {r['pnl']:+.2f}%", "/#skaner")
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
