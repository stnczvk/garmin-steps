"""Strażnicy – każdy pilnuje jednej rzeczy i co minutę sprawdza, czy działa i czy zrobiło się to, co miało się zrobić.
Zmiana na błąd (albo powrót do normy) = powiadomienie push. Historia ostatnich 48 sprawdzeń na strażnika.
"""
import json, os, re, shutil, threading, time
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
    """Wszystkie koszty: agenci centrum + n8n (OpenAI, Kling…) + saldo środków u dostawców."""
    from agency import costs
    from costs_full import summary, cfg, LBL
    c = costs()
    f = summary()
    tot = f["today"]["n8n"] + c["today"]
    B = f.get("balances") or {}
    lo = min(cfg()["levels"])
    left = " · ".join(f"{LBL[k]} {v['left']:.2f} $" for k, v in B.items())
    msg = f"dziś {tot:.2f} $ (n8n {f['today']['n8n']:.2f} + agenci {c['today']:.2f})" + (f" · zostało: {left}" if left else "")
    if any(v["left"] <= lo for v in B.values()):
        return FAIL, msg, None
    p = c["today"] / c["limit"] * 100 if c["limit"] else 0
    warn = p >= 80 or any(v["left"] <= max(cfg()["levels"]) for v in B.values())
    return (FAIL if p >= 100 else WARN if warn else OK), msg, None


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


def g_ssl():
    """Ile dni zostało do wygaśnięcia certyfikatu HTTPS centrum (DuckDNS + Let's Encrypt odnawia się sam – to tylko kontrola)."""
    import socket, ssl
    host = (os.environ.get("PUBLIC_URL") or "https://centrum-dowodzenia.duckdns.org").split("//")[-1].split("/")[0]
    ctx = ssl.create_default_context()
    with socket.create_connection((host, 443), timeout=10) as s, ctx.wrap_socket(s, server_hostname=host) as c:
        exp = ssl.cert_time_to_seconds(c.getpeercert()["notAfter"])
    days = (exp - time.time()) / 86400
    return (FAIL if days < 7 else WARN if days < 20 else OK), f"{host} · certyfikat ważny jeszcze {days:.0f} dni", None


def g_tokens():
    """Tokeny kont: Instagram (60 dni, centrum odświeża po 20) i Google (refresh token)."""
    accs = _read("cache.json", {}).get("accounts", {})
    raw = _read("tokens.json", {})
    old = []
    for k, a in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(a, dict) and a.get("token_at"):
            age = (time.time() - a["token_at"]) / 86400
            if age > 45:
                old.append(f"{a.get('name', k)}: token IG ma {age:.0f} dni")
    bad = [f"{a.get('name')}: {str(a.get('error'))[:60]}" for a in accs.values()
           if a.get("error") and re.search(r"token|auth|połącz|expired|invalid_grant", str(a.get("error")), re.I)]
    if bad:
        return FAIL, bad[0] + " – połącz konto ponownie", None
    if old:
        return WARN, old[0] + " (wygasa po 60) – otwórz Statystyki, odświeży się", None
    return OK, f"{len(accs)} kont · tokeny ważne", None


def g_mac():
    """Pomocnik montażu vloga na Macu: czy jest online, gdy czeka zlecenie."""
    if not os.environ.get("MONT_TOKEN"):
        return IDLE, "Mac nie połączony (brak MONT_TOKEN)", None
    v = _read("mont_vlog.json", {})
    seen = (v.get("helper") or {}).get("seen") or 0
    wait = [j for j in v.get("jobs", []) if j.get("state") == "queued"]
    on = time.time() - seen < 90
    if wait and not on and time.time() - min(j.get("at", time.time()) for j in wait) > 600:
        return WARN, f"vlog #{wait[0].get('ep')} czeka, a Mac jest offline od {_ago(seen) if seen else 'dawna'}", None
    return (OK if on else IDLE), ("Mac online – gotowy do montażu" if on else f"Mac offline (ostatnio {_ago(seen)})" if seen else "Mac jeszcze się nie zgłosił"), None


def g_dziennik():
    import httpx
    from montaz import DZ_URL
    url = DZ_URL.rstrip("/")
    if not url:
        return IDLE, "brak adresu dziennika (DZIENNIK_URL)", None
    t = time.time()
    r = httpx.get(url + "/", timeout=10, follow_redirects=True)
    ms = (time.time() - t) * 1000
    if r.status_code >= 500:
        return FAIL, f"dziennik odpowiada błędem {r.status_code}", None
    return (WARN if ms > 4000 else OK), f"działa · odpowiedź {ms:.0f} ms", None


_gc = {}


def _cached(key, ttl, fn):
    """Strażnicy chodzą co minutę – droższe sprawdzenia (API, AI) robimy rzadziej."""
    c = _gc.get(key)
    if c and time.time() - c[0] < ttl:
        return c[1]
    try:
        r = fn()
    except Exception as e:
        r = (WARN, f"nie udało się sprawdzić: {str(e)[:100]}", None)
    _gc[key] = (time.time(), r)
    return r


def _api_costs():
    """Środki w API: OpenAI (najtańsze możliwe zapytanie), błędy „quota/billing” w n8n z ostatnich 6 h, limit Apify."""
    import httpx
    probs, notes = [], []
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=20, headers={"Authorization": "Bearer " + key},
                       json={"model": "gpt-4.1-nano", "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1})
        if r.status_code in (429, 402) and re.search(r"quota|billing|credit", r.text, re.I):
            probs.append("OpenAI: skończyły się środki – doładuj konto")
        elif r.status_code == 401:
            probs.append("OpenAI: klucz nieważny")
        elif r.status_code < 300:
            notes.append("OpenAI OK")
    try:
        s = _n8n()
        for e in s.get("executions", [])[:30]:
            if e["status"] in ("error", "crashed") and (_iso(e["startedAt"]) or _now()) > _now() - timedelta(hours=6):
                from n8n_control import execution
                d = execution(e["id"])
                txt = (d.get("message", "") + " " + d.get("description", "")).lower()
                if re.search(r"quota|insufficient|billing|credit|balance|payment required|402", txt):
                    probs.append(f"{e['workflow']}: {d.get('node')} – brak środków / limit ({d.get('message', '')[:60]})")
                    break
    except Exception:
        pass
    tok = os.environ.get("APIFY_TOKEN", "")
    if tok:
        j = httpx.get("https://api.apify.com/v2/users/me/limits", params={"token": tok}, timeout=15).json().get("data", {})
        used, lim = (j.get("current") or {}).get("monthlyUsageUsd"), (j.get("limits") or {}).get("maxMonthlyUsageUsd")
        if used is not None and lim:
            pct = used / lim * 100
            (probs if pct >= 95 else notes).append(f"Apify {pct:.0f}% limitu (${used:.2f}/${lim:.0f})")
    if probs:
        return FAIL, " · ".join(probs)[:150], None
    return OK, " · ".join(notes) or "brak sygnałów o końcu środków", None


def g_costs_api():
    return _cached("costs_api", 3600, _api_costs)


def _publication():
    """Czy filmy PozaSkali są naprawdę publiczne (YouTube + Instagram), a nie tylko „n8n przeszedł”."""
    import httpx
    from agency import _pz_youtube, _pz_account
    from social import _google_access, _ig_token
    out, bad = [], []
    k, acc = _pz_youtube()
    if acc:
        h = {"Authorization": "Bearer " + _google_access(acc)}
        api = "https://www.googleapis.com/youtube/v3"
        up = httpx.get(f"{api}/channels", params={"part": "contentDetails", "id": acc["channel_id"]}, headers=h, timeout=20).json()
        pl = up["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        it = httpx.get(f"{api}/playlistItems", params={"part": "contentDetails", "playlistId": pl, "maxResults": 8}, headers=h, timeout=20).json()
        ids = ",".join(x["contentDetails"]["videoId"] for x in it.get("items", []))
        vs = httpx.get(f"{api}/videos", params={"part": "status,snippet", "id": ids}, headers=h, timeout=20).json().get("items", [])
        pub = [v for v in vs if v["status"].get("privacyStatus") == "public"]
        stuck = [v for v in vs if v["status"].get("privacyStatus") != "public" and v["status"].get("publishAt")
                 and (_iso(v["status"]["publishAt"]) or _now()) < _now() - timedelta(minutes=30)]
        if stuck:
            bad.append(f"YT: „{stuck[0]['snippet']['title'][:40]}” miał wyjść {_iso(stuck[0]['status']['publishAt']).strftime('%d.%m %H:%M')}, a dalej nie jest publiczny")
        last = max((_iso(v["snippet"]["publishedAt"]) for v in pub), default=None)
        if last:
            age = (_now() - last).total_seconds() / 3600
            (bad if age > 26 else out).append(f"YT ostatni publiczny {age:.0f} h temu")
    k, acc = _pz_account("instagram")
    if acc:
        m = httpx.get("https://graph.instagram.com/me/media", params={"fields": "timestamp", "limit": 1, "access_token": _ig_token(k, acc)}, timeout=20).json()
        if m.get("data"):
            age = (_now() - _iso(m["data"][0]["timestamp"].replace("+0000", "+00:00"))).total_seconds() / 3600
            (bad if age > 26 else out).append(f"IG ostatni {age:.0f} h temu")
    if bad:
        return FAIL, " · ".join(bad)[:150], None
    return (OK if out else IDLE), " · ".join(out) or "brak połączonych kont PozaSkali", None


def g_publication():
    return _cached("publication", 1200, _publication)


def _tg_scanner():
    """Telegram skanera: czy bot działa i czy w logach skanera nie ma błędów wysyłki."""
    import httpx, subprocess
    env = {}
    for p in ("/root/MarketScanner_git/.env", "/root/MarketScanner/.env"):
        if os.path.exists(p):
            for line in open(p):
                if "=" in line and not line.strip().startswith("#"):
                    a, b = line.split("=", 1)
                    env[a.strip()] = b.strip().strip('"').strip("'")
            break
    tok = env.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_TOKEN") or env.get("BOT_TOKEN")
    if not tok:
        return IDLE, "nie widzę tokenu bota w .env skanera", None
    me = httpx.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15).json()
    if not me.get("ok"):
        return FAIL, f"bot Telegrama nie działa: {me.get('description', '')[:80]}", None
    from scanner import CONTAINER
    try:   # same końcówki logów – pełne 6 h potrafiło się mielić ponad 30 s
        logs = subprocess.run(["docker", "logs", "--since", "6h", "--tail", "4000", CONTAINER], capture_output=True, text=True, timeout=90)
        txt = (logs.stdout + logs.stderr)
    except subprocess.TimeoutExpired:
        return OK, f"@{me['result'].get('username')} działa (logów skanera nie zdążyłem przejrzeć – sprawdzę przy następnym razie)", None
    errs = [l for l in txt.splitlines() if re.search(r"telegram", l, re.I) and re.search(r"error|fail|błąd|blad|exception|timed out|429|403|400", l, re.I)]
    if errs:
        return WARN, "błędy wysyłki w logach: " + errs[-1][-110:], None
    sent = sum(1 for l in txt.splitlines() if re.search(r"telegram", l, re.I) and re.search(r"wys[łl]ano|sent|ok", l, re.I))
    return OK, f"@{me['result'].get('username')} działa" + (f" · wysłane w 6 h: {sent}" if sent else ""), None


def _video_log():
    """Czy Publisher zapisuje w logu filmu ID z YouTube i IG – bez nich Analytics Collector musi zgadywać po tytule."""
    from agency import n8n
    s = _n8n()
    wf = [w for w in s.get("workflows", []) if re.search(r"^publisher$", w["name"], re.I) and w["active"]]
    if not wf:
        return IDLE, "Publisher wyłączony", None
    r = n8n("GET", "/api/v1/executions", params={"workflowId": wf[0]["id"], "status": "success", "limit": 5, "includeData": "true"})
    for e in (r.json().get("data") or []) if r.status_code < 300 else []:
        run = (((e.get("data") or {}).get("resultData") or {}).get("runData") or {}).get("Create Video Log Entry")
        if not run:
            continue
        txt = (((run[-1].get("data") or {}).get("main") or [[{}]])[0] or [{}])[0].get("json", {}).get("text") or "{}"
        j = json.loads(txt)
        miss = [n for k, n in (("youtubeVideoId", "YouTube"), ("igMediaId", "Instagram")) if not j.get(k)]
        name = str(j.get("title") or j.get("movieName") or "")[:40]
        if miss:
            if "YouTube" in miss:
                g = next((w for w in s.get("workflows", []) if re.search(r"^generator$", w["name"], re.I)), None)
                code = ""
                if g:
                    gr = n8n("GET", f"/api/v1/workflows/{g['id']}")
                    node = next((n for n in (gr.json().get("nodes") or []) if n.get("name") == "Create Queue Entry"), {}) if gr.status_code < 300 else {}
                    code = json.dumps(node.get("parameters") or {})
                if g and "uploadId" not in code:
                    return WARN, f"log „{name}” bez ID z YouTube – generator jest w starej wersji: wgraj generator_poprawiony.json (Pipeline → Edytuj)", None
                upd = _iso(g.get("updatedAt")) if g else None
                st = _iso(e.get("startedAt"))
                if upd and st and st - upd < timedelta(hours=30):
                    return OK, f"log „{name}” bez ID z YouTube, ale film powstał przed poprawką generatora – następne będą miały ID", None
                # co faktycznie zwrócił node Upload to YouTube w ostatnim udanym generatorze
                seen = "brak danych"
                try:
                    gr = n8n("GET", "/api/v1/executions", params={"workflowId": g["id"], "status": "success", "limit": 3, "includeData": "true"})
                    for ge in (gr.json().get("data") or []):
                        rd = (((ge.get("data") or {}).get("resultData") or {}).get("runData") or {})
                        up = rd.get("Upload to YouTube")
                        if not up:
                            seen = "node Upload to YouTube nie uruchomił się w generatorze"
                            continue
                        it = (((up[-1].get("data") or {}).get("main") or [[{}]])[0] or [{}])[0].get("json") or {}
                        seen = "Upload to YouTube zwrócił: " + (json.dumps(it, ensure_ascii=False)[:160] if it else "pusto")
                        q = rd.get("Create Queue Entry")
                        if q:
                            qt = (((q[-1].get("data") or {}).get("main") or [[{}]])[0] or [{}])[0].get("json", {}).get("text") or "{}"
                            seen += f" · w kolejce youtubeVideoId={json.loads(qt).get('youtubeVideoId')}"
                        break
                except Exception as ex:
                    seen = f"nie udało się odczytać generatora ({ex.__class__.__name__})"
                return WARN, f"log „{name}” bez ID z YouTube – {seen}", None
            return WARN, f"log „{name}” bez ID z {' i '.join(miss)} – sprawdź w Publisherze node publikacji na Instagram", None
        return OK, f"ostatni log „{name}” ma ID z YouTube i IG", None
    return IDLE, "brak udanej publikacji z logiem", None


def g_video_log():
    return _cached("video_log", 3600, _video_log)


def g_tg_scanner():
    return _cached("tg_scanner", 900, _tg_scanner)


def g_mac_disk():
    d = _read("mac_disk.json", {})
    if not d.get("at"):
        return IDLE, "Mac jeszcze nie przysłał raportu miejsca", None
    f = d.get("free_gb") or 0
    old = [x for x in d.get("raw", []) if x.get("days", 0) > 30]
    gb = sum(x["gb"] for x in old)
    msg = f"wolne {f:.0f} GB z {d.get('total_gb', 0):.0f}" + (f" · stare surowe klipy (>30 dni): {gb:.1f} GB" if old else "")
    return (FAIL if f < 10 else WARN if f < 25 else OK), msg, None



# ---------------------------------------------------------------- Etsy
def _etsy():
    c = _read("etsy_secret.json", {}); D = _read("etsy.json", {})
    return c, D


def g_etsy_conn():
    c, D = _etsy()
    if not c.get("refresh"):
        return IDLE, "sklep niepołączony (zakładka Etsy → Połącz)", None
    if D.get("error"):
        return FAIL, str(D["error"])[:140], None
    if "listings_w" not in (c.get("scopes") or ""):
        return WARN, "brak uprawnienia do edycji ofert – Rozłącz → Połącz w zakładce Etsy", None
    ts = D.get("synced") or 0
    if not ts or time.time() - ts > 2 * 3600:
        return WARN, f"dane nieodświeżane od {_ago(ts) if ts else 'początku'}", None
    sh = D.get("shop") or {}
    return OK, f"{sh.get('name') or 'sklep'} · odświeżone {_ago(ts)}", None


def _etsy_listings():
    c, D = _etsy()
    if not c.get("refresh"):
        return IDLE, "sklep niepołączony", None
    import etsy_manage as EM
    m = EM._map(); M = EM._man()
    active = {l["id"] for l in D.get("listings", [])}
    missing = [M[k]["name"] for k, v in m.items() if k in M and v and v not in active]
    nolink = [M[k]["name"] for k in M if not m.get(k)]
    weak = []
    for k, lid in m.items():
        if not lid or lid not in active:
            continue
        imgs = len(EM._call("GET", f"/listings/{lid}/images").get("results", []))
        vids = [v for v in EM._call("GET", f"/listings/{lid}/videos").get("results", []) if v.get("video_state", "active") == "active"]
        if imgs < 5 or not vids:
            weak.append(f"{M.get(k, {}).get('name', lid)} ({imgs} zdj.{'' if vids else ', bez filmu'})")
    if missing:
        return FAIL, "nieaktywne / wygasłe: " + ", ".join(missing)[:140], None
    if weak or nolink:
        return WARN, ("słabe oferty: " + ", ".join(weak) if weak else "") + (" · niewystawione: " + ", ".join(nolink) if nolink else ""), None
    return OK, f"{len(active)} aktywnych ofert · każda ma 5+ zdjęć i film", None


def g_etsy_listings():
    return _cached("etsy_listings", 6 * 3600, _etsy_listings)


def g_etsy_pers():
    c, D = _etsy()
    if not D.get("orders"):
        return IDLE, "brak zamówień", None
    late = [o for o in D["orders"] if not o.get("pers_done") and any(i.get("personalization") for i in o["items"])]
    if not late:
        return OK, "wszystkie personalizacje wysłane", None
    oldest = max(time.time() - (o["t"] or time.time()) for o in late) / 3600
    st = FAIL if oldest > 36 else WARN
    return st, f"{len(late)} do wysłania · najstarsza {oldest:.0f} h (obiecujesz 48 h)", None


def g_etsy_traffic():
    c, D = _etsy()
    H = D.get("views_hist") or {}
    if len(H) < 2:
        return IDLE, "zbieram dane o wyświetleniach (pierwszy wynik po dobie)", None
    days = sorted(H)
    ref = days[-8] if len(days) >= 8 else days[0]
    views = H[days[-1]]["views"] - H[ref]["views"]
    span = len(days[days.index(ref):]) - 1
    week = time.time() - 7 * 86400
    sales = sum(1 for o in D.get("orders", []) if (o["t"] or 0) >= week)
    if span >= 7 and views <= 0:
        return WARN, "0 wyświetleń w 7 dni – popraw tytuły/zdjęcia albo włącz Etsy Ads", None
    return OK, f"{max(0, views)} wyświetleń / {span} dni · {sales} sprzedaży w 7 dni", None


def g_etsy_job():
    try:
        import etsy_manage as EM
    except Exception:
        return IDLE, "brak modułu", None
    J = EM.JOB
    if J.get("running"):
        return OK, f"trwa: {J.get('title')}", None
    if not J.get("log"):
        return IDLE, "jeszcze nic nie uruchamiano", None
    errs = [l for l in J["log"] if "⚠️" in l]
    if errs:
        return WARN, f"{J.get('title')}: {len(errs)} błędów – {errs[-1][9:120]}", None
    return OK, f"{J.get('title')}: zrobione {_ago(J.get('at') or time.time())}", None

# ---------------------------------------------------------------- lista strażników
G = [
    ("vps", "Serwer", "infra", "🖥️", "Dysk, pamięć i obciążenie VPS", g_vps),
    ("ssl", "Certyfikat HTTPS", "infra", "🔐", "Czy certyfikat centrum nie wygasa", g_ssl),
    ("dziennik", "Dziennik", "infra", "📓", "Czy aplikacja Dziennik odpowiada", g_dziennik),
    ("backup", "Kopie zapasowe", "infra", "💾", "Czy codzienna kopia danych centrum się zrobiła", g_backup),
    ("push", "Powiadomienia", "infra", "🔔", "Czy powiadomienia dochodzą na Twoje urządzenia", g_push),
    ("n8n", "n8n", "pozaskala", "⚙️", "Czy n8n działa i nie sypie błędami", g_n8n),
    ("gen", "Generator", "pozaskala", "🎬", "Czy filmy wygenerowały się o zaplanowanych godzinach", _scheduled(r"^generator$", "Generator")),
    ("montage", "Montaż", "pozaskala", "🎞️", "Czy montaż i kontrola techniczna przeszły", g_montage),
    ("pub", "Publikacja", "pozaskala", "🚀", "Czy filmy opublikowały się o zaplanowanych godzinach", _scheduled(r"^publisher$", "Publikacja")),
    ("live", "Filmy na żywo", "pozaskala", "📺", "Czy filmy są naprawdę publiczne na YouTube i IG (co 20 min)", g_publication),
    ("vlog", "Dane do analityki", "pozaskala", "🧷", "Czy log każdego filmu ma ID z YouTube i IG (dla Analytics Collectora)", g_video_log),
    ("queue", "Kolejka tematów", "agencja", "🔭", "Czy generator ma z czego brać tematy", g_queue),
    ("qa", "Kontrola jakości", "agencja", "🛡️", "Czy Kontroler ocenił ostatni scenariusz", g_quality),
    ("inbox", "Inbox", "agencja", "💬", "Czy komentarze są sprawdzane (co 30 min)", g_inbox),
    ("ret", "Retencja", "agencja", "📈", "Czy codzienna analiza retencji (7:00) się odbyła", _agent_ran("retention", "Retencja", 26, after_hour=8)),
    ("apicost", "Środki w API", "agencja", "🪙", "Czy w OpenAI / Apify nie kończą się środki (raz na godzinę)", g_costs_api),
    ("costs", "Koszty AI", "agencja", "💸", "Wszystkie wydatki (n8n: OpenAI, Kling; agenci) i ile zostało środków", g_costs),
    ("tokens", "Tokeny kont", "social", "🔑", "Czy logowania do YouTube/Instagrama nie wygasają", g_tokens),
    ("macdisk", "Miejsce na Macu", "social", "🗄️", "Archiwista: wolne miejsce na Macu i stare surowe klipy", g_mac_disk),
    ("mac", "Mac (vlog)", "social", "💻", "Czy Mac jest gotowy, gdy czeka montaż vloga", g_mac),
    ("stats", "Statystyki kont", "social", "📊", "Czy dane z YouTube/IG/TikTok się odświeżają", g_stats),
    ("scanner", "Skaner", "skaner", "📡", "Czy skaner działa i skanuje rynek", g_scanner),
    ("risk", "Ryzyko", "skaner", "⚖️", "Czy ekspozycja portfela mieści się w limicie", g_risk),
    ("tg", "Telegram skanera", "skaner", "✈️", "Czy bot działa i sygnały wychodzą na Telegram", g_tg_scanner),
    ("okx", "Pozycje OKX", "skaner", "🔒", "Czy SL na OKX zgadza się ze skanerem (tylko odczyt – przesuwasz sam)", g_okx),
    ("journal", "Dziennik", "skaner", "📝", "Czy transakcje dostają notatki po zamknięciu", lambda: (OK, "notatki po każdym zamknięciu", None)),
    ("saudit", "Audyt skanera", "skaner", "🧾", "Czy cotygodniowy audyt się odbył", g_audit),
    ("etsy", "Połączenie z Etsy", "etsy", "🛍️", "Czy sklep jest połączony i dane się odświeżają (co 30 min)", g_etsy_conn),
    ("etsyl", "Oferty Etsy", "etsy", "🖼️", "Czy oferty są aktywne i mają 5 zdjęć + film (co 6 h)", g_etsy_listings),
    ("etsyp", "Personalizacje", "etsy", "✍️", "Czy zamówienia z personalizacją są wysłane na czas (48 h)", g_etsy_pers),
    ("etsyt", "Ruch w sklepie", "etsy", "👀", "Wyświetlenia i sprzedaż z ostatnich 7 dni", g_etsy_traffic),
    ("etsyj", "Zmiany w ofertach", "etsy", "🔧", "Czy ostatnia podmiana / wystawianie z centrum przeszło bez błędów", g_etsy_job),
]
AREAS = {"infra": "Infrastruktura", "pozaskala": "PozaSkala", "agencja": "Agencja AI", "social": "Social media", "skaner": "Skaner", "etsy": "Etsy"}


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
