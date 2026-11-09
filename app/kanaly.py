"""Kanały YT – wspólny kalendarz: długie filmy PozaSkali i Zdrowia 50+ (zaplanowane na YouTube, do zatwierdzenia, zwiastuny,
następne filmy z harmonogramu) + codzienny short PozaSkali z n8n."""
import time
from datetime import datetime, timedelta

from fastapi import APIRouter

router = APIRouter()


def _ev(ch, kind, t, title, status, extra=None):
    return {"ch": ch, "kind": kind, "t": t, "title": title, "status": status, **(extra or {})}


@router.get("/api/kanaly/kalendarz")
def api_kal(days: int = 35):
    import sys
    import dlugi
    mods = [("pozaskala", dlugi)]
    z = sys.modules.get("dlugi_zdrowie")
    if z:
        mods.append(("zdrowie", z))
    now, end = time.time(), time.time() + days * 86400
    E = []
    for ch, m in mods:
        c = m.cfg()
        S = m._state()
        for it in S["items"]:
            st = it.get("status")
            if st == "published" and it.get("publish_at"):
                pa = it["publish_at"]
                E.append(_ev(ch, "film", pa, it.get("title", ""), "zaplanowany" if pa > now else "opublikowany", {"yt": it.get("yt")}))
                for n in range(1, int(c.get("shorts", 2)) + 1):
                    tt = pa + (1800 if n == 1 else (n - 1) * 86400)
                    E.append(_ev(ch, "zwiastun", tt, f"Short {n}: " + it.get("title", ""), "zaplanowany" if tt > now else "opublikowany"))
            elif st == "review":
                E.append(_ev(ch, "film", it.get("at", now), it.get("title", ""), "do zatwierdzenia"))
            elif st in ("working", "uploading"):
                E.append(_ev(ch, "film", it.get("at", now), it.get("title") or "(powstaje)", "w trakcie"))
        if c.get("enabled"):   # kolejne filmy z harmonogramu (powstają rano, publikacja według godziny dla USA)
            d = datetime.now(m.TZ).replace(hour=int(c.get("hour", 9)), minute=0, second=0, microsecond=0)
            made = {x["id"][:6] for x in S["items"]}
            for k in range(days):
                dd = d + timedelta(days=k)
                if dd.weekday() in c["days"] and dd.timestamp() > now - 3600 and dd.strftime("%y%m%d") not in made:
                    pub = m.us_slot(after=dd.timestamp() + 3 * 3600, min_lead=0).timestamp()
                    E.append(_ev(ch, "film", pub, "Nowy film z harmonogramu", "plan"))
    # codzienny short PozaSkali z n8n (publikacja o północy)
    d0 = datetime.now(dlugi.TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for k in range(1, days + 1):
        E.append(_ev("pozaskala", "short", (d0 + timedelta(days=k)).timestamp(), "Short PozaSkala (n8n)", "plan"))
    E = [e for e in E if now - 7 * 86400 <= e["t"] <= end]
    return {"events": sorted(E, key=lambda e: e["t"])}
