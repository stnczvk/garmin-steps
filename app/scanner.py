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
