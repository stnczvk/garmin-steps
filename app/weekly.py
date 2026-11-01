"""Raport tygodnia (karta na ekranie głównym + wiadomość na Telegramie w niedzielę o 20:00) i kalendarz treści na 7 dni."""
import json, os, sqlite3, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter

from social import _read, _write

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
DZ_DB = os.path.join(os.environ.get("DZ_HOME", "/root/dziennik"), "dziennik.db")
GN = {"prywatne": "Konta prywatne", "pozaskala": "PozaSkala"}


def _pts(p):
    try:
        if p.get("ts"):
            return float(p["ts"])
        return datetime.fromisoformat(str(p.get("date")).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _social(now):
    acc = (_read("cache.json", {}).get("accounts") or {})
    acc = list(acc.values()) if isinstance(acc, dict) else acc
    w1, w2 = now - 7 * 86400, now - 14 * 86400
    d7 = (datetime.fromtimestamp(now, TZ) - timedelta(days=7)).date().isoformat()
    G, best = {}, None
    for a in acc:
        g = G.setdefault(a.get("group") or "inne", {"views": 0, "views_prev": 0, "posts": 0, "fol": 0, "fol_d": 0})
        g["fol"] += a.get("followers") or 0
        h = [x for x in a.get("history") or [] if x.get("date", "") <= d7]
        if h:
            g["fol_d"] += (a.get("followers") or 0) - (h[-1].get("followers") or 0)
        for p in a.get("posts") or []:
            t = _pts(p)
            if t >= w1:
                g["views"] += p.get("views") or 0; g["posts"] += 1
                if not best or (p.get("views") or 0) > best["views"]:
                    best = {"title": (p.get("title") or "bez opisu")[:90], "views": p.get("views") or 0, "acc": a.get("name"), "url": p.get("url")}
            elif t >= w2:
                g["views_prev"] += p.get("views") or 0
    return G, best


def _dziennik(now):
    try:
        con = sqlite3.connect(f"file:{DZ_DB}?mode=ro", uri=True, timeout=5)
        days = [(datetime.fromtimestamp(now, TZ) - timedelta(days=i)).date().isoformat() for i in range(1, 8)]
        rows = con.execute(f"SELECT data FROM docs WHERE path IN ({','.join('?' * 7)})", ["vlog/" + d for d in days]).fetchall()
        con.close()
    except Exception:
        return None
    D = [json.loads(r[0]) for r in rows if r[0]]
    if not D:
        return None
    kc = [d["total"]["kcal"] for d in D if (d.get("total") or {}).get("kcal")]
    ok = sum(1 for d in D if (d.get("goal") or {}).get("kcal") and d["total"]["kcal"] <= d["goal"]["kcal"] * 1.05)
    return {"days": len(D), "kcal": round(sum(kc) / len(kc)) if kc else None, "in_goal": ok, "train": sum(1 for d in D if d.get("workout"))}


def report():
    now = time.time()
    G, best = _social(now)
    out = {"t": now, "social": G, "best": best}
    try:
        from planer import plan
        out["published"] = sum(1 for x in plan() if x["status"] == "published" and (x.get("done_at") or x["when"]) >= now - 7 * 86400)
    except Exception:
        out["published"] = None
    try:
        from etsy import state as etsy_state
        e = etsy_state()
        if e.get("connected"):
            out["etsy"] = {"orders": e["d7"]["orders"], "views": (e.get("traffic") or {}).get("d7", {}).get("views")}
    except Exception:
        pass
    try:
        from scanner import equity
        q = equity(15)
        out["scanner"] = {"pnl": round(q[-1]["real"] - q[-8]["real"], 2), "trades": sum(x["n"] for x in q[-7:])}
    except Exception:
        pass
    out["dziennik"] = _dziennik(now)
    return out


def _pct(a, b):
    return "" if not b else f" ({'+' if a >= b else ''}{round((a - b) / b * 100)}%)"


def text(R):
    L = [f"📊 Raport tygodnia · {datetime.fromtimestamp(R['t'], TZ):%d.%m}"]
    for k, g in R["social"].items():
        L.append(f"\n{GN.get(k, k)}: {g['views']:,} wyśw. nowych postów{_pct(g['views'], g['views_prev'])}, "
                 f"{g['posts']} postów, obserwujący {g['fol']} ({'+' if g['fol_d'] >= 0 else ''}{g['fol_d']})".replace(",", " "))
    if R.get("best"):
        b = R["best"]; L.append(f"🏆 Najlepszy: „{b['title'][:60]}” – {b['views']} wyśw. ({b['acc']})")
    if R.get("published") is not None:
        L.append(f"📅 Opublikowane z planera: {R['published']}")
    if R.get("etsy"):
        L.append(f"🛍 Etsy: {R['etsy']['orders']} zamówień, {R['etsy'].get('views') or 0} wyświetleń ofert")
    if R.get("scanner"):
        s = R["scanner"]; L.append(f"📈 Skaner: {s['trades']} zamkniętych, wynik {'+' if s['pnl'] >= 0 else ''}{s['pnl']}")
    if R.get("dziennik"):
        d = R["dziennik"]; L.append(f"🍽 Dieta: śr. {d['kcal']} kcal, w celu {d['in_goal']}/{d['days']} dni, treningi: {d['train']}")
    return "\n".join(L)


def _tg():
    env = {}
    for p in ("/root/MarketScanner_git/.env", "/root/MarketScanner/.env"):
        if os.path.exists(p):
            for line in open(p):
                if "=" in line and not line.strip().startswith("#"):
                    a, b = line.split("=", 1)
                    env[a.strip()] = b.strip().strip('"').strip("'")
            break
    tok = env.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_TOKEN") or env.get("BOT_TOKEN")
    chat = env.get("TELEGRAM_CHAT_ID") or env.get("CHAT_ID") or env.get("TG_CHAT_ID")
    return tok, chat


def send(R=None):
    tok, chat = _tg()
    if not tok or not chat:
        return {"ok": False, "error": "brak tokenu albo chat_id Telegrama w .env skanera"}
    r = httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text(R or report())}, timeout=20)
    return {"ok": r.status_code == 200} if r.status_code == 200 else {"ok": False, "error": f"Telegram {r.status_code}"}


def _loop():
    time.sleep(90)
    while True:
        try:
            now = datetime.now(TZ)
            wk = f"{now.isocalendar()[0]}-{now.isocalendar()[1]}"
            S = _read("weekly.json", {})
            if now.weekday() == 6 and now.hour >= 20 and S.get("sent") != wk:
                R = report()
                S.update(sent=wk, last=R); _write("weekly.json", S)
                send(R)
        except Exception as e:
            print("weekly", e, flush=True)
        time.sleep(600)


threading.Thread(target=_loop, daemon=True).start()


@router.get("/api/weekly")
def api_weekly():
    return report()


@router.post("/api/weekly/send")
def api_send():
    return send()


# ---------- kalendarz treści: 7 dni od dziś ----------
def _n8n_slots(day):
    out = []
    try:
        from pozaskala import status
        S = status()
        for w in S.get("workflows") or []:
            if not w.get("active"):
                continue
            nm = w["name"]
            if not any(k in nm.lower() for k in ("publish", "publik")):
                continue
            for s in w.get("schedules") or []:
                if not s.get("time"):
                    continue
                if s.get("every") == "weeks" and s.get("days") and (day.isoweekday() % 7) not in s["days"]:
                    continue
                out.append({"time": s["time"], "src": "pozaskala", "label": "PozaSkala: " + nm, "status": "auto"})
    except Exception:
        pass
    return out


@router.get("/api/kalendarz")
def kalendarz():
    from planer import plan
    d0 = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    P = [x for x in plan() if x["status"] != "skipped"]
    days = []
    for i in range(7):
        a = d0 + timedelta(days=i); b = a + timedelta(days=1)
        it = [{"id": x.get("id"), "time": datetime.fromtimestamp(x["when"], TZ).strftime("%H:%M"), "src": "prywatne", "label": x.get("label", ""),
               "kind": x.get("kind"), "status": x["status"], "platforms": x.get("platforms") or []}
              for x in P if a.timestamp() <= x["when"] < b.timestamp()]
        it += _n8n_slots(a)
        days.append({"d": a.date().isoformat(), "items": sorted(it, key=lambda x: x["time"])})
    return {"days": days}
