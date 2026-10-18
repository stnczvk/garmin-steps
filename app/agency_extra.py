"""Nowe osoby w Agencji:
  • Planer (nd 18:00) – rozpisuje tematy z kolejki na 7 dni w sloty generatora (8:00 SAFE → publikacja 0:00, 10:00 EXPERIMENT → 21:00)
  • Recykler (śr 18:00) – bierze najlepsze filmy PozaSkali i dokłada do kolejki 3 kontynuacje / nowe ujęcia tego, co zadziałało
"""
import threading, time, uuid
from datetime import datetime, timedelta

from fastapi import APIRouter

from agency import SMART, TZ, CHANNEL, _busy, _read, _write, ai_ready, as_json, claude, lessons_text, log, save_topics, topics

router = APIRouter()
DNI = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]


def planner(why="harmonogram"):
    if _busy.get("planner"):
        return
    _busy["planner"] = "układa plan tygodnia"
    try:
        q = [t for t in topics() if t["status"] == "queued"][:20]
        if len(q) < 4:
            from agency import research
            research(why="Planer – za mało tematów")
            q = [t for t in topics() if t["status"] == "queued"][:20]
        start = datetime.now(TZ).date() + timedelta(days=1)
        days = [(start + timedelta(days=i)) for i in range(7)]
        lst = "\n".join(f'{t["id"]}: {t["topic"]} [{t.get("pillar", "")}/{t.get("category", "")}] – {t.get("why", "")}' for t in q)
        txt = claude(f"""{CHANNEL}

{lessons_text()}

Kolejka tematów (id: temat [filar/kategoria] – uzasadnienie):
{lst}

Jesteś Planerem. Ułóż plan na 7 dni od {days[0].isoformat()}. Każdego dnia są 2 sloty:
- "SAFE" (generator 8:00, publikacja 0:00) – temat sprawdzony, w stylu tego, co działa najlepiej,
- "EXPERIMENT" (generator 10:00, publikacja 21:00) – coś odważniejszego, test nowego filaru/hooka.
Rotuj filary, nie dawaj dwóch podobnych tematów dzień po dniu. Użyj tylko id z listy (każde max raz).
Odpowiedz WYŁĄCZNIE JSON: [{{"day": "YYYY-MM-DD", "slot": "SAFE"|"EXPERIMENT", "id": "...", "why": "PL, 1 krótkie zdanie"}}]""", SMART, 1800)
        plan = [p for p in (as_json(txt) or []) if isinstance(p, dict) and any(t["id"] == p.get("id") for t in q)]
        if not plan:
            raise RuntimeError("model nie zwrócił planu")
        # kolejność w kolejce = kolejność w planie (generator bierze tematy po kolei)
        order = {p["id"]: i for i, p in enumerate(sorted(plan, key=lambda p: (p.get("day", ""), p.get("slot") != "SAFE")))}
        T = topics()
        T.sort(key=lambda t: (t["status"] != "queued", order.get(t["id"], 999)))
        for t in T:
            if t["id"] in order:
                p = next(x for x in plan if x["id"] == t["id"])
                t["plan"] = f'{p.get("day", "")} {p.get("slot", "")}'
        save_topics(T)
        names = {t["id"]: t["topic"] for t in q}
        lines = []
        for p in sorted(plan, key=lambda p: (p.get("day", ""), p.get("slot") != "SAFE")):
            try:
                d = datetime.fromisoformat(p["day"]).date()
                dn = f"{DNI[d.weekday()]} {d.day:02d}.{d.month:02d}"
            except Exception:
                dn = p.get("day", "?")
            lines.append(f'{dn} {"🛡" if p.get("slot") == "SAFE" else "🧪"} {names[p["id"]]}')
        _write("agency_plan.json", {"at": time.time(), "plan": plan, "text": lines})
        log("planner", f"Plan tygodnia: {len(plan)} filmów ({why})", "ok", extra={"plan": lines})
        from extra import push_all
        push_all("🗓 Plan PozaSkali na tydzień", " · ".join(lines[:4])[:170], "/#agencja", kind="agencja")
    except Exception as e:
        log("planner", f"Nie udało się ułożyć planu: {str(e)[:150]}", "error")
    finally:
        _busy.pop("planner", None)


def recycle(why="harmonogram"):
    if _busy.get("recycle"):
        return
    _busy["recycle"] = "szuka, co powtórzyć"
    try:
        from extra import insights
        ins = insights("pozaskala")
        if not ins.get("ok") or not ins.get("top"):
            log("recycle", "Za mało danych o wynikach, żeby coś powtórzyć", "info")
            return
        used = [t["topic"] for t in topics()][-120:]
        top = "\n".join(f"- {x['title']} – {x['rel']:.1f}× mediany" for x in ins["top"][:5])
        txt = claude(f"""{CHANNEL}

Najlepsze filmy kanału:
{top}

Tematy już użyte (nie powtarzaj 1:1):
{chr(10).join('- ' + u for u in used[-60:])}

Jesteś Recyklerem. Zaproponuj 3 NOWE tematy, które wykorzystują to, co zadziałało w najlepszych filmach
(ta sama mechanika/porównanie/emocja, ale inny obiekt albo „część 2” z nowym zaskoczeniem).
Odpowiedz WYŁĄCZNIE tablicą JSON: [{{"topic": "...(EN)", "pillar": "...", "category": "...", "hook": "max 12 słów EN",
"reference": "...", "payoff": "...", "why": "na którym filmie bazuje i dlaczego (PL, 1 zdanie)"}}]""", SMART, 1500)
        new = [{"id": uuid.uuid4().hex[:8], "status": "queued", "created": time.time(), "source": "recykler",
                **{k: str(x.get(k, ""))[:300] for k in ("topic", "pillar", "category", "hook", "reference", "payoff", "why")}}
               for x in (as_json(txt) or []) if isinstance(x, dict) and x.get("topic")][:3]
        if new:
            save_topics(topics() + new)
        log("recycle", f"Dodał {len(new)} kontynuacji najlepszych filmów ({why})", "ok" if new else "info")
    except Exception as e:
        log("recycle", f"Recykler nie zadziałał: {str(e)[:150]}", "error")
    finally:
        _busy.pop("recycle", None)


AGENTS = {"planner": planner, "recycle": recycle}


@router.post("/api/agency/agent/{name}")
def run_agent(name: str):
    if name not in AGENTS:
        return {"ok": False, "error": "nie ma"}
    threading.Thread(target=AGENTS[name], kwargs={"why": "na żądanie"}, daemon=True).start()
    return {"ok": True}


@router.get("/api/agency/plan")
def get_plan():
    return _read("agency_plan.json", {})


def _loop():
    time.sleep(120)
    done = _read("agency_extra_done.json", {})
    while True:
        try:
            now = datetime.now(TZ)
            today = now.date().isoformat()
            if ai_ready() and now.weekday() == 6 and now.hour == 18 and done.get("planner") != today:
                done["planner"] = today
                _write("agency_extra_done.json", done)
                planner()
            if ai_ready() and now.weekday() == 2 and now.hour == 18 and done.get("recycle") != today:
                done["recycle"] = today
                _write("agency_extra_done.json", done)
                recycle()
        except Exception as e:
            print("agency_extra", e, flush=True)
        time.sleep(300)


threading.Thread(target=_loop, daemon=True).start()
