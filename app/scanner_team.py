"""Nowe osoby w biurze skanera (wszystko tylko do odczytu, nic nie zleca):
  • Analityk sesji (codziennie 21:30) – podsumowanie dnia: sygnały, zamknięcia, PnL, najlepszy/najgorszy → push
  • Strażnik korelacji (co 5 min) – czy otwarte sygnały nie są w praktyce jednym zakładem (np. 3 longi na krypto)
  • Kalendarz makro – ważne dane z USA (CPI, FOMC, NFP…): wieczorem lista na jutro + przypomnienie 45 min przed
  • Trener (nd 20:30) – czy Twoje wejścia na OKX były w strefie i z SL; tygodniowa ocena
"""
import threading, time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter

from social import _read, _write

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
WIN = ("WIN", "PARTIAL_WIN")
NONCRYPTO = {"XAU", "XAG", "CL", "NG", "SPY", "QQQ", "NVDA", "MSTR", "TSLA", "AAPL", "AMZN", "MSFT", "GOOGL", "GOOG", "META", "AMD", "COIN",
             "HOOD", "PLTR", "RKLB", "NBIS", "MU", "SKHYNIX", "CRCL", "ORCL", "AVGO", "INTC", "NFLX", "BABA", "TSM", "SMCI", "ARM", "IWM", "DIA"}


def _push(title, body, url="/#skaner-sygnaly"):
    from extra import push_all
    push_all(title, body[:180], url, kind="skaner")


def _sig(where, args=()):
    from scanner import _q, DB
    import os
    if not os.path.exists(DB):
        return []
    return _q(f"SELECT * FROM trade_signals WHERE is_shadow=0 AND {where} ORDER BY id DESC", args)


# ---------- Analityk sesji ----------
def session(day=None, push=True):
    day = day or datetime.now(TZ).date()
    a = datetime.combine(day, datetime.min.time(), TZ).astimezone(timezone.utc).replace(tzinfo=None).isoformat(" ")
    b = (datetime.combine(day, datetime.min.time(), TZ) + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None).isoformat(" ")
    new = _sig("created_at >= ? AND created_at < ?", (a, b))
    closed = _sig("closed_at >= ? AND closed_at < ? AND result IS NOT NULL", (a, b))
    opn = _sig("result IS NULL AND UPPER(COALESCE(status,'')) NOT IN ('CLOSED','INVALIDATED','MISSED','CANCELLED','EXPIRED')")
    pnl = round(sum(r.get("pnl") or 0 for r in closed), 2)
    wins = sum(1 for r in closed if r["result"] in WIN)
    best = max(closed, key=lambda r: r.get("pnl") or 0, default=None)
    worst = min(closed, key=lambda r: r.get("pnl") or 0, default=None)
    nm = lambda r: f"{r['symbol'].split('/')[0]} {r['direction']} {r.get('pnl') or 0:+.2f}%"
    rep = {"day": day.isoformat(), "at": time.time(), "new": len(new), "closed": len(closed), "wins": wins, "pnl": pnl,
           "open": len(opn), "best": best and nm(best), "worst": worst and nm(worst),
           "list": [f"{r['symbol'].split('/')[0]} {r['direction']}: {r['result']} {r.get('pnl') or 0:+.2f}%" for r in closed][:8]}
    _write("scanner_session.json", rep)
    if push:
        body = (f"Nowe sygnały: {rep['new']} · zamknięte: {rep['closed']} ({wins} na plus) · PnL {pnl:+.2f}% · otwarte: {rep['open']}"
                + (f" · najlepszy {rep['best']}" if best else ""))
        _push("📊 Sesja skanera – podsumowanie dnia", body)
    return rep


# ---------- Strażnik korelacji ----------
def correlation():
    opn = _sig("result IS NULL AND UPPER(COALESCE(status,'')) NOT IN ('CLOSED','INVALIDATED','MISSED','CANCELLED','EXPIRED')")
    groups = {}
    for r in opn:
        base = r["symbol"].split("/")[0].upper()
        cls = "akcje/surowce" if base in NONCRYPTO else "krypto"
        groups.setdefault((cls, (r["direction"] or "").upper()), []).append(r)
    warn = []
    for (cls, d), rs in groups.items():
        if len(rs) >= 3 or (cls == "krypto" and len(rs) >= 2 and sum(r.get("position_size_percent") or 0 for r in rs) >= 6):
            size = sum(r.get("position_size_percent") or 0 for r in rs)
            warn.append({"key": f"{cls}|{d}|{','.join(sorted(str(r['id']) for r in rs))}", "cls": cls, "dir": d, "n": len(rs), "size": round(size, 1),
                         "symbols": [r["symbol"].split("/")[0] for r in rs]})
    st = _read("scanner_corr.json", {})
    for w in warn:
        if w["key"] not in (st.get("pushed") or []):
            _push(f"🔗 {w['n']}× {w['dir']} na {w['cls']} naraz",
                  f"{', '.join(w['symbols'])} – w praktyce to jeden zakład (łącznie {w['size']}% kapitału). Rozważ mniejsze pozycje albo tylko najlepszy sygnał.")
    _write("scanner_corr.json", {"at": time.time(), "warn": warn, "open": len(opn),
                                 "pushed": list({*(st.get("pushed") or [])[-50:], *[w["key"] for w in warn]})})
    return warn


# ---------- Kalendarz makro ----------
FF = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
PL = {"CPI": "inflacja CPI", "Core CPI": "inflacja bazowa CPI", "Non-Farm": "rynek pracy NFP", "FOMC": "Fed (FOMC)", "Federal Funds": "stopy Fed",
      "Unemployment": "bezrobocie", "PPI": "inflacja PPI", "GDP": "PKB", "Retail Sales": "sprzedaż detaliczna", "PCE": "inflacja PCE",
      "Powell": "wystąpienie Powella", "JOLTS": "oferty pracy JOLTS", "ISM": "ISM", "Jobless Claims": "wnioski o zasiłek"}


def macro_events():
    c = _read("scanner_macro.json", {})
    if time.time() - c.get("fetched", 0) > 6 * 3600 or not c.get("events"):
        try:
            data = httpx.get(FF, timeout=20, headers={"User-Agent": "Mozilla/5.0 centrum"}).json()
            ev = []
            for e in data:
                if e.get("country") != "USD" or e.get("impact") != "High":
                    continue
                t = datetime.fromisoformat(e["date"]).astimezone(TZ)
                pl = next((v for k, v in PL.items() if k.lower() in e.get("title", "").lower()), "")
                ev.append({"t": t.timestamp(), "title": e.get("title"), "pl": pl, "forecast": e.get("forecast"), "previous": e.get("previous")})
            c = {"fetched": time.time(), "events": sorted(ev, key=lambda x: x["t"]), "pushed": c.get("pushed", [])}
            _write("scanner_macro.json", c)
        except Exception as e:
            c["error"] = str(e)[:120]
    return c


def macro_tick():
    c = macro_events()
    now = datetime.now(TZ)
    pushed = set(c.get("pushed") or [])
    ev = c.get("events") or []
    # wieczorem (20:00) lista na jutro
    tomorrow = (now + timedelta(days=1)).date()
    k = f"day|{tomorrow}"
    if now.hour == 20 and k not in pushed:
        tm = [e for e in ev if datetime.fromtimestamp(e["t"], TZ).date() == tomorrow]
        if tm:
            _push("🗓 Jutro ważne dane z USA", " · ".join(f"{datetime.fromtimestamp(e['t'], TZ):%H:%M} {e['pl'] or e['title']}" for e in tm)
                  + " – skaner może dawać fałszywe sygnały wokół tych godzin.")
        pushed.add(k)
    # 45 min przed
    for e in ev:
        mins = (e["t"] - time.time()) / 60
        k = f"soon|{e['t']}|{e['title']}"
        if 0 < mins <= 45 and k not in pushed:
            _push(f"⏰ Za {mins:.0f} min: {e['pl'] or e['title']}",
                  f"{e['title']} o {datetime.fromtimestamp(e['t'], TZ):%H:%M} (prognoza {e.get('forecast') or '–'}, poprzednio {e.get('previous') or '–'}). "
                  "Duża zmienność – nie wchodź w nowe pozycje tuż przed danymi.")
            pushed.add(k)
    c["pushed"] = list(pushed)[-80:]
    _write("scanner_macro.json", c)


# ---------- Trener ----------
def coach_record(positions, signals_for):
    """Wołane przy każdym sprawdzeniu OKX: nowe pozycje trafiają do dziennika trenera (czy w strefie, czy z SL)."""
    J = _read("okx_journal.json", [])
    known = {x["key"] for x in J}
    for p in positions:
        key = f"{p['inst']}|{p['dir']}|{p.get('avg')}"
        if key in known:
            continue
        s = signals_for(p["base"], p["dir"]) or {}
        lo, hi = s.get("entry_min"), s.get("entry_max")
        zone = None
        if lo and hi and p.get("avg"):
            a, b = min(lo, hi), max(lo, hi)
            zone = a * 0.999 <= p["avg"] <= b * 1.001
        J.append({"key": key, "t": time.time(), "inst": p["inst"], "dir": p["dir"], "avg": p.get("avg"), "signal": s.get("id"),
                  "in_zone": zone, "has_sl": bool(p.get("sl")), "lev": p.get("lev")})
    _write("okx_journal.json", J[-300:])


def coach_week(push=True):
    J = [x for x in _read("okx_journal.json", []) if time.time() - x["t"] < 7 * 86400]
    n = len(J)
    sig = sum(1 for x in J if x.get("signal"))
    z = [x for x in J if x.get("in_zone") is not None]
    inz = sum(1 for x in z if x["in_zone"])
    sl = sum(1 for x in J if x.get("has_sl"))
    rep = {"at": time.time(), "n": n, "with_signal": sig, "zone_checked": len(z), "in_zone": inz, "with_sl": sl}
    tips = []
    if n and sl < n:
        tips.append(f"{n - sl} z {n} pozycji bez SL w momencie otwarcia – ustawiaj SL od razu przy wejściu")
    if z and inz < len(z):
        tips.append(f"{len(z) - inz} z {len(z)} wejść poza strefą skanera – czekaj na limit w strefie")
    if n and sig < n:
        tips.append(f"{n - sig} pozycji bez sygnału skanera")
    rep["tips"] = tips
    _write("scanner_coach.json", rep)
    if push and n:
        _push("🏋️ Trener tradingu – tydzień", f"{n} pozycji · w strefie {inz}/{len(z) or '–'} · z SL {sl}/{n}. " + ("; ".join(tips) or "Bez uwag – trzymasz plan."))
    return rep


@router.get("/api/scanner/team")
def team():
    return {"session": _read("scanner_session.json", {}), "corr": _read("scanner_corr.json", {}),
            "macro": [e for e in (macro_events().get("events") or []) if e["t"] > time.time() - 3600][:8],
            "coach": _read("scanner_coach.json", {})}


@router.post("/api/scanner/team/{what}")
def team_run(what: str):
    if what == "session":
        return session(push=False)
    if what == "coach":
        return coach_week(push=False)
    if what == "corr":
        return {"warn": correlation()}
    return {"ok": False}


def _loop():
    time.sleep(150)
    done = _read("scanner_team_done.json", {})
    last_corr = 0
    while True:
        try:
            now = datetime.now(TZ)
            today = now.date().isoformat()
            if time.time() - last_corr > 300:
                last_corr = time.time()
                correlation()
            macro_tick()
            if now.hour == 21 and now.minute >= 30 and done.get("session") != today:
                done["session"] = today
                _write("scanner_team_done.json", done)
                session()
            if now.weekday() == 6 and now.hour == 20 and now.minute >= 30 and done.get("coach") != today:
                done["coach"] = today
                _write("scanner_team_done.json", done)
                coach_week()
        except Exception as e:
            print("scanner_team", e, flush=True)
        time.sleep(60)


threading.Thread(target=_loop, daemon=True).start()
