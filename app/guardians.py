"""Strażnicy – każdy pilnuje jednej rzeczy i co minutę sprawdza, czy działa i czy zrobiło się to, co miało się zrobić.
Zmiana na błąd (albo powrót do normy) = powiadomienie push. Historia ostatnich 48 sprawdzeń na strażnika.
"""
import os, re, shutil, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from social import _read, _write

router = APIRouter()
TZ = ZoneInfo("Europe/Warsaw")
OK, WARN, FAIL, IDLE = "ok", "warn", "fail", "idle"
_lock = threading.Lock()


def _now():
    return datetime.now(TZ)


def _ago(ts):
    s = time.time() - ts
    return "przed chwilą" if s < 90 else f"{int(s // 60)} min temu" if s < 3600 else f"{int(s // 3600)} h temu" if s < 86400 else f"{int(s // 86400)} dni temu"


def _iso(t):
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(TZ)
    except Exception:
        return None


# ---------------------------------------------------------------- infrastruktura
def g_vps():
    du = shutil.disk_usage("/")
    disk = du.used / du.total * 100
    mem = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            mem[k] = int(v.split()[0])
        ram = 100 - mem["MemAvailable"] / mem["MemTotal"] * 100
    except Exception:
        ram = 0
    load = os.getloadavg()[0] / (os.cpu_count() or 1) * 100
    st = FAIL if disk > 92 or ram > 95 else WARN if disk > 85 or ram > 88 or load > 150 else OK
    return st, f"dysk {disk:.0f}% · RAM {ram:.0f}% · CPU {load:.0f}%", {"disk": round(disk), "ram": round(ram)}


def g_backup():
    from agency import backups_list
    b = backups_list()
    if not b:
        return WARN, "jeszcze nie było kopii (robi się o 3:30)", None
    age = (time.time() - b[0]["at"]) / 3600
    return (OK if age < 26 else FAIL), f"ostatnia {_ago(b[0]['at'])} · {b[0]['kb']} KB", None


def g_push():
    subs = _read("push.json", [])
    log = _read("push_log.json", [])
    if not subs:
        return FAIL, "żadne urządzenie nie ma włączonych powiadomień (🔔 w centrum)", None
    if not log:
        return WARN, f"{len(subs)} urządzeń · jeszcze nic nie wysłano – kliknij „Test”", None
    last = log[-1]
    bad = [r for r in last["results"] if not r["ok"]]
    if bad and not last["sent"]:
        return FAIL, f"wysyłka nie dochodzi: {bad[0]['host']} {bad[0]['code']} {bad[0].get('err', '')[:80]}", None
    if bad:
        return WARN, f"{last['sent']}/{last['subs']} urządzeń dostało ostatnie powiadomienie", None
    return OK, f"{len(subs)} urządzeń · ostatnie „{last['title'][:40]}” {_ago(last['t'])}", None


# ---------------------------------------------------------------- n8n / PozaSkala
def _n8n():
    from pozaskala import status
    return status()


def g_n8n():
    s = _n8n()
    if not s.get("ok"):
        return FAIL, s.get("error", "n8n nie odpowiada")[:120], None
    act = [w for w in s["workflows"] if w["active"]]
    errs = [e for e in s["executions"] if e["status"] in ("error", "crashed") and (_iso(e["startedAt"]) or _now()) > _now() - timedelta(hours=24)]
    return (WARN if errs else OK), f"{len(act)} workflow aktywnych · błędy 24 h: {len(errs)}" + (f" (ostatni: {errs[0]['workflow']})" if errs else ""), None


def _scheduled(wf_re, label, grace=40):
    """Czy każde dzisiejsze zaplanowane uruchomienie workflow faktycznie się odbyło i skończyło sukcesem."""
    def check():
        s = _n8n()
        if not s.get("ok"):
            return IDLE, "brak danych z n8n", None
        wfs = [w for w in s["workflows"] if re.search(wf_re, w["name"], re.I) and w["active"]]
        if not wfs:
            return IDLE, "workflow wyłączony", None
        now = _now()
        times = sorted({x["time"] for w in wfs for x in w["schedules"] if x.get("time") and x.get("every") in ("days", None)})
        if not times:
            return IDLE, "brak harmonogramu", None
        ids = {w["id"] for w in wfs}
        ex = [(e, _iso(e["startedAt"])) for e in s["executions"] if e["workflowId"] in ids]
        done, missing, failed = [], [], []
        for t in times:
            h, m = map(int, t.split(":"))
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now < due + timedelta(minutes=grace):
                continue
            hit = [e for e, st in ex if st and due - timedelta(minutes=5) <= st <= due + timedelta(minutes=grace)]
            if not hit:
                missing.append(t)
            elif hit[0]["status"] in ("error", "crashed"):
                failed.append(t)
            else:
                done.append(t)
        nxt = next((t for t in times if now < now.replace(hour=int(t[:2]), minute=int(t[3:]), second=0)), times[0])
        if failed:
            return FAIL, f"{label} {', '.join(failed)} – zakończył się błędem", None
        if missing:
            return FAIL, f"{label} {', '.join(missing)} – nie wystartował", None
        return OK, (f"dziś OK: {', '.join(done)}" if done else "dziś jeszcze nic nie było zaplanowane") + f" · następny {nxt}", None
    return check


def g_montage():
    s = _n8n()
    if not s.get("ok"):
        return IDLE, "brak danych z n8n", None
    gen = next((e for e in s["executions"] if re.search(r"^generator$", e["workflow"], re.I) and e["status"] == "success"), None)
    mon = next((e for e in s["executions"] if re.search(r"monta", e["workflow"], re.I)), None)
    if not gen:
        return IDLE, "brak ostatniego generatora", None
    if not mon:
        return WARN, "nie widzę uruchomienia Montażu", None
    if mon["status"] in ("error", "crashed"):
        return FAIL, f"ostatni montaż: błąd ({_iso(mon['startedAt']).strftime('%d.%m %H:%M')})", None
    from agency import _read as ar
    tech = [e.get("extra") for e in ar("agency_log.json", []) if e.get("agent") == "tech" and e.get("extra")]
    if tech and not tech[-1].get("ok"):
        return FAIL, "kontrola techniczna zatrzymała film: " + "; ".join(tech[-1].get("problems") or [])[:100], None
    return OK, f"ostatni montaż OK {_iso(mon['startedAt']).strftime('%d.%m %H:%M')}" + (f" · film {tech[-1].get('duration')} s" if tech else ""), None


def g_queue():
    from agency import topics, settings
    q = [t for t in topics() if t["status"] == "queued"]
    if not settings().get("autopilot"):
        return IDLE, "autopilot wyłączony", None
    return (OK if len(q) >= 2 else WARN if q else FAIL), f"{len(q)} tematów w kolejce" + (f" · następny: {q[0]['topic'][:50]}" if q else ""), None


def g_quality():
    from agency import _read as ar
    L = [e for e in ar("agency_log.json", []) if e.get("agent") == "qa"]
    if not L:
        return IDLE, "jeszcze nie oceniał", None
    last = L[-1]
    if last.get("kind") == "error":
        return WARN, last["text"][:120], None
    return OK, last["text"][:120], None


# ---------------------------------------------------------------- agencja
def g_costs():
    from agency import costs
    c = costs()
    p = c["today"] / c["limit"] * 100 if c["limit"] else 0
    return (FAIL if p >= 100 else WARN if p >= 80 else OK), f"{c['today']:.2f} $ z {c['limit']} $ dziś ({p:.0f}%)", None


def _agent_ran(agent, label, max_h, after_hour=None):
    def check():
        from agency import _read as ar, ai_ready
        if not ai_ready():
            return IDLE, "brak klucza AI", None
        L = [e for e in ar("agency_log.json", []) if e.get("agent") == agent]
        if not L:
            return IDLE, f"{label}: jeszcze nie działał", None
        last = L[-1]
        age = (time.time() - last["t"]) / 3600
        if last.get("kind") == "error":
            return FAIL, last["text"][:120], None
        if last.get("kind") == "warn" and age < max_h:
            return WARN, f"{_ago(last['t'])} · {last['text'][:110]}", None
        if age > max_h and (after_hour is None or _now().hour >= after_hour):
            return WARN, f"{label}: ostatnio {_ago(last['t'])}", None
        return OK, f"{_ago(last['t'])} · {last['text'][:90]}", None
    return check


def g_inbox():
    """Stan z ostatniego sprawdzenia komentarzy (nie z ostatniego wpisu w logu – stary błąd nie wisi, gdy już działa)."""
    from agency import _read as ar, ai_ready
    if not ai_ready():
        return IDLE, "brak klucza AI", None
    I = ar("agency_inbox.json", {})
    t = I.get("checked")
    if not t:
        return _agent_ran("inbox", "Inbox", 1.5)()
    age = (time.time() - t) / 3600
    errs = I.get("errs") or []
    if age > 1.5:
        return WARN, f"Inbox: ostatnio sprawdzał {_ago(t)}", None
    if errs:
        from agency import _transient
        return (WARN if all(_transient(e) for e in errs) else FAIL), f"{_ago(t)} · {errs[0][:110]}", None
    drafts = sum(1 for x in I.get("items", []) if x.get("status") == "draft")
    return OK, f"sprawdzono {_ago(t)}" + (f" · {drafts} szkiców czeka na Ciebie" if drafts else " · bez błędów"), None


# ---------------------------------------------------------------- social
def g_stats():
    c = _read("cache.json", {})
    accs = c.get("accounts", {})
    if not accs:
        return IDLE, "brak połączonych kont", None
    ts = c.get("updated") or c.get("ts") or 0
    bad = [a for a in accs.values() if a.get("error")]
    age = (time.time() - ts) / 3600 if ts else None
    if bad:
        b = bad[0]
        hint = "" if b.get("platform") == "tiktok" else " – połącz konto ponownie w Statystykach"
        return FAIL, f"{b.get('name')}: {str(b.get('error'))[:110]}{hint}" + (f" (+{len(bad) - 1})" if len(bad) > 1 else ""), None
    if age is not None and age > 3:
        return WARN, f"statystyki nieodświeżane od {age:.0f} h", None
    return OK, f"{len(accs)} kont · odświeżone {_ago(ts) if ts else 'niedawno'}", None


# ---------------------------------------------------------------- skaner
def g_scanner():
    from scanner import state
    s = state()
    d = s.get("docker", {})
    if not d.get("running"):
        return FAIL, "kontener skanera nie działa", None
    if d.get("errors"):
        return WARN, "błędy w logach: " + d["errors"][-1][:100], None
    if d.get("cycles") == 0 and d.get("via") != "api":
        return WARN, "brak cyklu skanowania w ostatnich 4 min", None
    return OK, f"działa · {d.get('cycles', '?')} cykli / 4 min · sygnały dziś {s.get('today', 0)}/4", None


def g_risk():
    from scanner import state
    s = state()
    if not s.get("ok"):
        return IDLE, s.get("error", "brak bazy")[:100], None
    e = s.get("exposure", 0)
    return (FAIL if e > 12 else WARN if e > 10 else OK), f"ekspozycja {e}% z 12% · otwartych {len(s.get('open', []))}", None


def g_audit():
    from social import _read as sr
    h = sr("scanner_audit.json", [])
    if not h:
        return IDLE, "audyt jeszcze się nie odbył (nd 20:00)", None
    age = (time.time() - h[-1]["at"]) / 86400
    return (OK if age < 8 else WARN), f"ostatni audyt {_ago(h[-1]['at'])}", None


def g_okx():
    from scanner import okx_guard_tick
    st, msg = okx_guard_tick()
    return st, msg, None


# ---------------------------------------------------------------- lista strażników
G = [
    ("vps", "Serwer", "infra", "🖥️", "Dysk, pamięć i obciążenie VPS", g_vps),
    ("backup", "Kopie zapasowe", "infra", "💾", "Czy codzienna kopia danych centrum się zrobiła", g_backup),
    ("push", "Powiadomienia", "infra", "🔔", "Czy powiadomienia dochodzą na Twoje urządzenia", g_push),
    ("n8n", "n8n", "pozaskala", "⚙️", "Czy n8n działa i nie sypie błędami", g_n8n),
    ("gen", "Generator", "pozaskala", "🎬", "Czy filmy wygenerowały się o zaplanowanych godzinach", _scheduled(r"^generator$", "Generator")),
    ("montage", "Montaż", "pozaskala", "🎞️", "Czy montaż i kontrola techniczna przeszły", g_montage),
    ("pub", "Publikacja", "pozaskala", "🚀", "Czy filmy opublikowały się o zaplanowanych godzinach", _scheduled(r"^publisher$", "Publikacja")),
    ("queue", "Kolejka tematów", "agencja", "🔭", "Czy generator ma z czego brać tematy", g_queue),
    ("qa", "Kontrola jakości", "agencja", "🛡️", "Czy Kontroler ocenił ostatni scenariusz", g_quality),
    ("inbox", "Inbox", "agencja", "💬", "Czy komentarze są sprawdzane (co 30 min)", g_inbox),
    ("ret", "Retencja", "agencja", "📈", "Czy codzienna analiza retencji (7:00) się odbyła", _agent_ran("retention", "Retencja", 26, after_hour=8)),
    ("costs", "Koszty AI", "agencja", "💸", "Czy agenci nie przekraczają dziennego limitu", g_costs),
    ("stats", "Statystyki kont", "social", "📊", "Czy dane z YouTube/IG/TikTok się odświeżają", g_stats),
    ("scanner", "Skaner", "skaner", "📡", "Czy skaner działa i skanuje rynek", g_scanner),
    ("risk", "Ryzyko", "skaner", "⚖️", "Czy ekspozycja portfela mieści się w limicie", g_risk),
    ("okx", "Pozycje OKX", "skaner", "🔒", "Czy SL na OKX zgadza się ze skanerem (tylko odczyt – przesuwasz sam)", g_okx),
    ("journal", "Dziennik", "skaner", "📝", "Czy transakcje dostają notatki po zamknięciu", lambda: (OK, "notatki po każdym zamknięciu", None)),
    ("saudit", "Audyt skanera", "skaner", "🧾", "Czy cotygodniowy audyt się odbył", g_audit),
]
AREAS = {"infra": "Infrastruktura", "pozaskala": "PozaSkala", "agencja": "Agencja AI", "social": "Social media", "skaner": "Skaner"}


def run_all(push=True):
    from extra import push_all
    with _lock:
        S = _read("guardians.json", {})
        changes = []
        for gid, name, area, icon, task, fn in G:
            try:
                st, msg, extra = fn()
            except Exception as e:
                st, msg, extra = WARN, f"strażnik nie mógł sprawdzić: {str(e)[:100]}", None
            g = S.setdefault(gid, {"hist": [], "since": time.time()})
            prev = g.get("status")
            if prev != st:
                g["since"] = time.time()
                if prev and push and st in (FAIL, OK) and (prev == FAIL or st == FAIL):
                    changes.append((name, icon, st, msg))
            g.update(status=st, msg=msg, at=time.time(), extra=extra)
            g["hist"] = (g["hist"] + [st])[-48:]
        _write("guardians.json", S)
    for name, icon, st, msg in changes:
        try:
            push_all(f"{'🔴' if st == FAIL else '🟢'} {name}: {'problem' if st == FAIL else 'znowu OK'}", msg[:150], "/#straznicy")
        except Exception:
            pass
    return S


@router.get("/api/guardians")
def guardians():
    S = _read("guardians.json", {}) or run_all(push=False)
    out = [{"id": gid, "name": name, "area": area, "areaName": AREAS[area], "icon": icon, "task": task,
            **{k: S.get(gid, {}).get(k) for k in ("status", "msg", "at", "since", "hist")}} for gid, name, area, icon, task, _ in G]
    cnt = {s: sum(1 for g in out if g["status"] == s) for s in (OK, WARN, FAIL, IDLE)}
    return {"guardians": out, "count": cnt, "push_log": _read("push_log.json", [])[-8:][::-1]}


@router.post("/api/guardians/run")
def guardians_run():
    run_all(push=False)
    return guardians()


@router.get("/api/guardians/diag")
def diag():
    """Diagnoza połączeń, których używa Inbox: DNS (IPv4/IPv6), zapytanie przez httpx i przez curl."""
    import socket, subprocess
    import httpx
    hosts = ["graph.instagram.com", "www.googleapis.com", "oauth2.googleapis.com", "api.openai.com", "api.anthropic.com"]
    out = []
    for h in hosts:
        r = {"host": h}
        try:
            ai = socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)
            r["v4"] = sorted({a[4][0] for a in ai if a[0] == socket.AF_INET})[:2]
            r["v6"] = sorted({a[4][0] for a in ai if a[0] == socket.AF_INET6})[:2]
        except Exception as e:
            r["dns"] = str(e)[:80]
        t = time.time()
        try:
            r["httpx"] = f"HTTP {httpx.get(f'https://{h}/', timeout=10).status_code} · {int((time.time() - t) * 1000)} ms"
        except Exception as e:
            r["httpx"] = f"BŁĄD {type(e).__name__}: {str(e)[:90]}"
        try:
            c = subprocess.run(["curl", "-sS", "-4", "-o", "/dev/null", "-m", "10", "-w", "HTTP %{http_code} · %{time_total}s", f"https://{h}/"],
                               capture_output=True, text=True, timeout=15)
            r["curl"] = c.stdout if not c.returncode else "BŁĄD " + c.stderr.strip()[:90]
        except Exception as e:
            r["curl"] = f"BŁĄD {str(e)[:90]}"
        out.append(r)
    from agency import _read as ar, inbox
    threading.Thread(target=inbox, daemon=True).start()  # od razu sprawdź komentarze ponownie
    log = [e for e in ar("agency_log.json", []) if e.get("agent") == "inbox"][-8:]
    return {"hosts": out, "log": [{"t": e["t"], "kind": e.get("kind"), "text": e["text"]} for e in log][::-1]}


def _loop():
    time.sleep(45)
    while True:
        try:
            run_all()
        except Exception as e:
            print("guardians", e, flush=True)
        time.sleep(60)


threading.Thread(target=_loop, daemon=True).start()
