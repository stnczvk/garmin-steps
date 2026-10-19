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
        from notes import auto_note
        auto_note("💡 Pomysły na filmy", f"Plan tygodnia od {days[0].isoformat()}", f"# Plan PozaSkali od {days[0].isoformat()}\n\n" + "\n".join(f"- {l}" for l in lines)
                  + "\n\n🛡 SAFE = publikacja 0:00 · 🧪 EXPERIMENT = 21:00\n\n#plan #pozaskala")
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


def ig_caption(script):
    """Copywriter: opis pod Reels – mocna pierwsza linia, 2–3 krótkie linijki, CTA na zapis/udostępnienie, 5–8 hashtagów IG."""
    if not ai_ready():
        return ""
    _busy["copy"] = "pisze opis pod Instagram"
    try:
        view = {k: script.get(k) for k in ("topic", "title", "hook", "description", "hashtags", "ig_caption")}
        txt = claude(f"""{CHANNEL}

Metadane filmu: {view}

Jesteś Copywriterem Instagrama kanału. Napisz opis pod Reels PO ANGIELSKU:
- 1. linia: hak, który zmusza do rozwinięcia opisu (max 70 znaków, bez clickbaitu),
- 2–3 krótkie linijki z najciekawszą liczbą/porównaniem z filmu,
- pytanie do widzów albo zachęta do zapisania/wysłania znajomemu (jedno, naturalne),
- pusta linia i 5–8 hashtagów pasujących do IG (mieszanka niszowych i szerszych, bez #fyp/#viral).
Bez emoji-spamu (max 2). Odpowiedz WYŁĄCZNIE gotowym tekstem opisu.""", FAST, 400).strip()
        if 40 <= len(txt) <= 2000:
            log("copy", f"Opis IG gotowy · {str(script.get('title') or script.get('topic'))[:60]}", "ok", {"caption": txt[:600]})
            return txt
        return ""
    finally:
        _busy.pop("copy", None)


def spokesman(why="harmonogram"):
    """Rzecznik: co widzowie najczęściej pytają/piszą w komentarzach → 2–3 tematy do kolejki."""
    if _busy.get("spokes"):
        return
    _busy["spokes"] = "czyta komentarze widzów"
    try:
        I = _read("agency_inbox.json", {"items": []})
        cut = time.time() - 21 * 86400
        com = [f'- ({x.get("platform")}) pod „{str(x.get("video", ""))[:50]}”: {str(x.get("text", ""))[:200]}' for x in I.get("items", []) if x.get("t", 0) > cut]
        if len(com) < 3:
            log("spokes", f"Za mało komentarzy z 3 tygodni ({len(com)}) – nic do podsumowania", "info")
            return
        used = [t["topic"] for t in topics()][-80:]
        txt = claude(f"""{CHANNEL}

Komentarze widzów z ostatnich 3 tygodni:
{chr(10).join(com[-80:])}

Tematy już użyte: {', '.join(used[-40:])}

Jesteś Rzecznikiem kanału. 1) Wypisz krótko, o co widzowie najczęściej pytają, co ich zaskoczyło, z czym się kłócą.
2) Zaproponuj 2–3 NOWE tematy filmów, które bezpośrednio odpowiadają na te pytania.
Odpowiedz WYŁĄCZNIE JSON: {{"summary": "3-4 zdania po polsku", "topics": [{{"topic": "...(EN)", "pillar": "...", "category": "...", "hook": "max 12 słów EN",
"reference": "...", "payoff": "...", "why": "na jaki komentarz odpowiada (PL)"}}]}}""", SMART, 1500)
        j = as_json(txt) or {}
        new = [{"id": uuid.uuid4().hex[:8], "status": "queued", "created": time.time(), "source": "widzowie",
                **{k: str(x.get(k, ""))[:300] for k in ("topic", "pillar", "category", "hook", "reference", "payoff", "why")}}
               for x in (j.get("topics") or []) if isinstance(x, dict) and x.get("topic")][:3]
        if new:
            save_topics(topics() + new)
        _write("agency_spokes.json", {"at": time.time(), "summary": str(j.get("summary", ""))[:1200], "n": len(com)})
        from notes import auto_note
        auto_note("💡 Pomysły na filmy", f"Co mówią widzowie {datetime.now(TZ):%Y-%m-%d}", f"# Co mówią widzowie\n\n{j.get('summary', '')}\n\n## Tematy dodane do kolejki\n"
                  + ("\n".join(f"- {t['topic']} – {t['why']}" for t in new) or "- brak") + "\n\n#widzowie #pozaskala")
        log("spokes", f"Podsumował {len(com)} komentarzy, dodał {len(new)} tematów od widzów ({why})", "ok", {"summary": str(j.get("summary", ""))[:600]})
    except Exception as e:
        log("spokes", f"Rzecznik nie zadziałał: {str(e)[:150]}", "error")
    finally:
        _busy.pop("spokes", None)


AGENTS = {"planner": planner, "recycle": recycle, "spokes": spokesman}


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
            if ai_ready() and now.weekday() == 3 and now.hour == 18 and done.get("spokes") != today:
                done["spokes"] = today
                _write("agency_extra_done.json", done)
                spokesman()
            if ai_ready() and now.weekday() == 2 and now.hour == 18 and done.get("recycle") != today:
                done["recycle"] = today
                _write("agency_extra_done.json", done)
                recycle()
        except Exception as e:
            print("agency_extra", e, flush=True)
        time.sleep(300)


threading.Thread(target=_loop, daemon=True).start()
