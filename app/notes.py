"""Notatki („własny Obsidian”): zwykłe pliki Markdown w /root/social/notatki, linki [[nazwa]], wyszukiwarka,
szybka notatka z telefonu,
import rozmów z ChatGPT i serwer MCP dla aplikacji ChatGPT (bez kosztów API –
ChatGPT myśli na Twojej subskrypcji, centrum tylko podaje i zapisuje pliki).

MCP: https://<centrum>/mcp/<token>  (token w Notatki → ⚙️ ChatGPT)
"""
import io, json, os, re, secrets, shutil, time, zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from social import ROOT, _read, _write

router = APIRouter()
NOTES = ROOT / "notatki"
TRASH = NOTES / ".kosz"
FOLDERS = ["📥 Skrzynka", "📓 Dziennik", "💡 Pomysły na filmy", "🎥 Vlog", "📈 Trading", "🏋️ Trening", "🤖 ChatGPT", "🚀 Update"]
LINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def _init():
    NOTES.mkdir(parents=True, exist_ok=True)
    for f in FOLDERS:
        (NOTES / f).mkdir(exist_ok=True)
    w = NOTES / "📥 Skrzynka" / "Witaj w Notatkach.md"
    if not any(NOTES.rglob("*.md")):
        w.write_text("# Witaj w Notatkach\n\nTo zwykłe pliki Markdown na serwerze.\n\n- linkuj notatki tak: [[Pomysły]]\n"
                     "- szybkie notatki z telefonu lądują w 📥 Skrzynce\n- ChatGPT może czytać i zapisywać notatki (⚙️ ChatGPT)\n", encoding="utf-8")


def _safe(path):
    """Ścieżka względna wewnątrz NOTES (bez wychodzenia poza katalog)."""
    p = (NOTES / str(path or "").lstrip("/")).resolve()
    if NOTES.resolve() not in p.parents and p != NOTES.resolve():
        raise ValueError("zła ścieżka")
    return p


def _rel(p):
    return str(p.relative_to(NOTES))


def _clean_name(s):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', " ", str(s or "")).strip()[:90]
    return s or datetime.now().strftime("Notatka %Y-%m-%d %H%M")


def _all():
    return [p for p in NOTES.rglob("*.md") if ".kosz" not in p.parts]


def _meta(p):
    st = p.stat()
    txt = p.read_text(encoding="utf-8", errors="ignore")
    first = next((l.strip("# ").strip() for l in txt.splitlines() if l.strip()), "")
    return {"path": _rel(p), "name": p.stem, "folder": _rel(p.parent) if p.parent != NOTES else "", "mtime": st.st_mtime,
            "size": st.st_size, "preview": re.sub(r"\s+", " ", txt)[:140], "first": first[:80],
            "tags": sorted(set(re.findall(r"(?<!\w)#([\wąćęłńóśźż-]{2,30})", txt)))[:8]}


def _find(name):
    """[[nazwa]] → plik (po nazwie, bez względu na folder i wielkość liter)."""
    n = str(name).strip().lower()
    for p in _all():
        if p.stem.lower() == n or _rel(p).lower().removesuffix(".md") == n:
            return p
    return None


def search(q, limit=30):
    q = str(q or "").strip().lower()
    out = []
    for p in _all():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        low = txt.lower()
        if not q:
            score = 0
        else:
            words = [w for w in re.split(r"\s+", q) if w]
            if not all(w in low or w in p.stem.lower() for w in words):
                continue
            score = sum(low.count(w) for w in words) + sum(10 for w in words if w in p.stem.lower())
        i = low.find(q.split()[0]) if q else 0
        snip = re.sub(r"\s+", " ", txt[max(0, i - 60): i + 140]) if i >= 0 else txt[:200]
        out.append({**_meta(p), "score": score, "snippet": snip})
    out.sort(key=lambda x: (-x["score"], -x["mtime"]))
    return out[:limit]


def write(path, content, mode="replace"):
    p = _safe(path if str(path).endswith(".md") else f"{path}.md")
    p.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append" and p.exists():
        old = p.read_text(encoding="utf-8", errors="ignore")
        content = old.rstrip() + "\n\n" + str(content)
    p.write_text(str(content), encoding="utf-8")
    return _meta(p)


def auto_note(folder, title, content, mode="update"):
    """Notatki dopisywane przez centrum (sesja skanera, plan tygodnia, vlog…) – nigdy nie wywala reszty systemu.
    mode="update": gdy notatka już jest (i może mieć Twoje dopiski), nowa wersja jest dopisywana na końcu, nic nie znika."""
    try:
        _init()
        p = f"{_clean_name(folder)}/{_clean_name(title)}.md"
        if mode == "update" and _safe(p).exists():
            body = re.sub(r"^#[^\n]*\n+", "", str(content), count=1)
            return write(p, f"\n---\n*Aktualizacja {datetime.now():%d.%m %H:%M}*\n\n{body}", mode="append")
        return write(p, content)
    except Exception as e:
        print("auto_note", e, flush=True)


# ---------------------------------------------------------------- API centrum
@router.get("/api/notes/tree")
def tree():
    _init()
    folders = sorted({_rel(d) for d in NOTES.rglob("*") if d.is_dir() and ".kosz" not in d.parts and d != NOTES},
                     key=lambda f: (FOLDERS.index(f) if f in FOLDERS else 99, f))
    notes = sorted((_meta(p) for p in _all()), key=lambda x: -x["mtime"])
    return {"folders": folders, "notes": notes}


@router.get("/api/notes/graph")
def graph():
    """Mapa połączeń jak w Obsidianie: notatki = kropki, [[linki]] = kreski (też do jeszcze nieistniejących notatek)."""
    _init()
    files = _all()
    by = {p.stem.lower(): _rel(p) for p in files}
    nodes, edges, ghost = [], [], {}
    for p in files:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        r = _rel(p)
        nodes.append({"id": r, "name": p.stem, "folder": _rel(p.parent) if p.parent != NOTES else ""})
        for l in dict.fromkeys(LINK.findall(txt)):
            t = by.get(l.strip().lower())
            if not t:
                t = "?" + l.strip()
                ghost[t] = l.strip()
            if t != r:
                edges.append([r, t])
    nodes += [{"id": k, "name": v, "folder": "", "ghost": True} for k, v in ghost.items()]
    tags = {}
    for p in files:
        for t in set(re.findall(r"(?<!\w)#([\wąćęłńóśźż-]{2,30})", p.read_text(encoding="utf-8", errors="ignore"))):
            tags[t] = tags.get(t, 0) + 1
    return {"nodes": nodes, "edges": edges, "tags": sorted(tags.items(), key=lambda x: -x[1])[:60]}


@router.post("/api/notes/daily")
def daily():
    """Notatka dnia (📓 Dziennik/RRRR-MM-DD) – tworzy z szablonu, jeśli jeszcze nie ma."""
    _init()
    d = datetime.now(ZoneInfo("Europe/Warsaw"))
    path = f"📓 Dziennik/{d:%Y-%m-%d}.md"
    p = _safe(path)
    if not p.is_file():
        dni = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"][d.weekday()]
        write(path, f"# {d:%d.%m.%Y}, {dni}\n\n## Plan na dziś\n- [ ] \n\n## Notatki\n\n\n## Pomysły\n\n#dziennik")
    return {"ok": True, "path": path}


@router.get("/api/notes/file")
def get_file(path: str):
    p = _safe(path)
    if not p.is_file():
        return JSONResponse({"error": "nie ma"}, status_code=404)
    txt = p.read_text(encoding="utf-8", errors="ignore")
    me = p.stem.lower()
    back = [_rel(q) for q in _all() if q != p and any(l.strip().lower() == me for l in LINK.findall(q.read_text(encoding="utf-8", errors="ignore")))]
    links = [{"name": l, "path": _rel(f) if (f := _find(l)) else None} for l in dict.fromkeys(LINK.findall(txt))]
    return {**_meta(p), "content": txt, "backlinks": back, "links": links}


@router.put("/api/notes/file")
async def put_file(req: Request):
    b = await req.json()
    old = b.get("old")
    path = b.get("path") or f"📥 Skrzynka/{_clean_name(b.get('name'))}.md"
    if old and old != path:  # zmiana nazwy / folderu
        po, pn = _safe(old), _safe(path)
        if pn.exists():
            return JSONResponse({"error": "taka notatka już jest"}, status_code=409)
        pn.parent.mkdir(parents=True, exist_ok=True)
        if po.exists():
            po.rename(pn)
    return write(path, b.get("content", ""))


@router.delete("/api/notes/file")
def del_file(path: str):
    p = _safe(path)
    if p.is_file():
        TRASH.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(TRASH / f"{int(time.time())}_{p.name}"))
    return {"ok": True}


@router.post("/api/notes/folder")
async def new_folder(req: Request):
    b = await req.json()
    _safe(_clean_name(b.get("name"))).mkdir(parents=True, exist_ok=True)
    return tree()


@router.get("/api/notes/search")
def api_search(q: str = ""):
    return {"results": search(q)}


@router.post("/api/notes/quick")
async def quick(req: Request):
    b = await req.json()
    txt = str(b.get("text", "")).strip()
    if not txt:
        return {"ok": False}
    title = _clean_name(b.get("title") or txt.splitlines()[0][:60])
    path = f"📥 Skrzynka/{title}.md"
    if _safe(path).exists():
        path = f"📥 Skrzynka/{title} {datetime.now():%H%M%S}.md"
    return write(path, txt)


UPD = "🚀 Update"


def add_update(project, text):
    """Wpis do dziennika zmian projektu (🚀 Update/<projekt>.md) – najnowszy na górze, z datą."""
    _init()
    project = _clean_name(project or "Ogólne")
    text = str(text or "").strip()
    if not text:
        raise ValueError("pusty update")
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    body = "\n".join(l if l.lstrip().startswith(("-", "*", "#", ">")) else f"- {l.strip()}" for l in lines)
    entry = f"## {datetime.now(ZoneInfo('Europe/Warsaw')):%d.%m.%Y %H:%M}\n{body}\n"
    path = f"{UPD}/{project}.md"
    p = _safe(path)
    if p.exists():
        old = p.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"^## ", old, re.M)
        new = old[:m.start()] + entry + "\n" + old[m.start():] if m else old.rstrip() + "\n\n" + entry
    else:
        new = f"# {project}\n\nDziennik zmian projektu #update\n\n" + entry
    return write(path, new)


@router.get("/api/notes/projects")
def projects():
    d = NOTES / UPD
    fs = sorted(d.glob("*.md"), key=lambda f: -f.stat().st_mtime) if d.is_dir() else []
    return {"projects": [f.stem for f in fs]}


@router.post("/api/notes/update")
async def api_update(req: Request):
    b = await req.json()
    try:
        return {"ok": True, **add_update(b.get("project"), b.get("text"))}
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@router.put("/api/notes/import-chatgpt")
async def import_chatgpt(req: Request):
    """Eksport z ChatGPT (zip albo conversations.json, wysłany jako surowe body) → każda rozmowa = notatka w 🤖 ChatGPT."""
    raw = await req.body()
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
        if not name:
            return {"ok": False, "error": "w archiwum nie ma conversations.json"}
        raw = z.read(name)
    try:
        convs = json.loads(raw)
    except Exception:
        return {"ok": False, "error": "to nie jest eksport ChatGPT (conversations.json)"}
    n = 0
    for c in convs if isinstance(convs, list) else []:
        mapping = c.get("mapping") or {}
        msgs = []
        for m in mapping.values():
            msg = (m or {}).get("message") or {}
            role = (msg.get("author") or {}).get("role")
            parts = ((msg.get("content") or {}).get("parts") or [])
            text = "\n".join(p if isinstance(p, str) else str(p.get("text") or "") if isinstance(p, dict) else "" for p in parts).strip()
            if role in ("user", "assistant") and text:
                msgs.append((msg.get("create_time") or 0, role, text))
        if not msgs:
            continue
        msgs.sort()
        t = c.get("create_time") or msgs[0][0] or time.time()
        title = _clean_name(c.get("title") or "Rozmowa")
        body = f"# {title}\n\n*Rozmowa z ChatGPT · {datetime.fromtimestamp(t):%Y-%m-%d}* #chatgpt\n\n" + "\n\n".join(
            ("**Ty:** " if r == "user" else "**ChatGPT:** ") + x for _, r, x in msgs)
        path = f"🤖 ChatGPT/{datetime.fromtimestamp(t):%Y-%m-%d} {title}.md"
        write(path, body)
        os.utime(_safe(path), (t, t))
        n += 1
    return {"ok": True, "imported": n}


@router.post("/api/notes/to-queue")
async def to_queue(req: Request):
    """Pomysł z notatki → kolejka tematów Agencji (generator weźmie go jak każdy inny temat)."""
    import uuid
    from agency import topics, save_topics, log
    b = await req.json()
    p = _safe(b.get("path", ""))
    if not p.is_file():
        return {"ok": False, "error": "nie ma notatki"}
    txt = p.read_text(encoding="utf-8", errors="ignore")
    body = re.sub(r"^#.*\n?", "", txt).strip()
    body = re.sub(r"\n?—\n✅ W kolejce Agencji.*$", "", body, flags=re.S).strip()
    topic = str(b.get("topic") or p.stem)[:200]
    T = topics()
    if any(t.get("note") == _rel(p) and t.get("status") == "queued" for t in T):
        return {"ok": False, "error": "ten pomysł już czeka w kolejce"}
    T.append({"id": uuid.uuid4().hex[:8], "status": "queued", "created": time.time(), "source": "notatki", "prio": 1 if b.get("prio") else 0,
              "topic": topic, "pillar": "", "category": "", "hook": "", "reference": "", "payoff": "",
              "why": ("z notatki: " + re.sub(r"\s+", " ", body))[:300], "note": _rel(p)})
    save_topics(T)
    log("scout", f"Pomysł z Notatek trafił do kolejki: {topic}", "ok")
    write(_rel(p), f"\n—\n✅ W kolejce Agencji od {datetime.now():%d.%m %H:%M}", mode="append")
    return {"ok": True}


@router.get("/api/notes/mcp")
def mcp_info(req: Request):
    c = _read("notes_cfg.json", {})
    if not c.get("token"):
        c["token"] = secrets.token_urlsafe(24)
        _write("notes_cfg.json", c)
    host = req.headers.get("x-forwarded-host") or req.headers.get("host", "")
    return {"url": f"https://{host}/mcp/{c['token']}", "used": c.get("used")}


@router.post("/api/notes/mcp/reset")
def mcp_reset(req: Request):
    c = _read("notes_cfg.json", {})
    c["token"] = secrets.token_urlsafe(24)
    _write("notes_cfg.json", c)
    return mcp_info(req)


# ---------------------------------------------------------------- MCP dla ChatGPT (JSON-RPC przez HTTP)
TOOLS = [
    {"name": "search", "description": "Szukaj w notatkach użytkownika (centrum). Zwraca listę pasujących notatek z id (ścieżką).",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "fetch", "description": "Pobierz pełną treść notatki po id (ścieżce z wyniku search albo list_notes).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
    {"name": "list_notes", "description": "Lista folderów i notatek (najnowsze pierwsze). Opcjonalnie tylko z jednego folderu.",
     "inputSchema": {"type": "object", "properties": {"folder": {"type": "string"}}}},
    {"name": "create_note", "description": "Utwórz albo nadpisz notatkę Markdown. folder np. '💡 Pomysły na filmy', '📈 Trading', '🏋️ Trening', '🎥 Vlog', '📥 Skrzynka'. "
     "Linki do innych notatek: [[nazwa]].",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "content": {"type": "string"}, "folder": {"type": "string"}},
                     "required": ["title", "content"]}},
    {"name": "add_video_idea", "description": "Dodaj pomysł na film do kolejki Agencji PozaSkala (generator filmów). Zapisuje też notatkę w '💡 Pomysły na filmy'.",
     "inputSchema": {"type": "object", "properties": {"topic": {"type": "string", "description": "temat filmu, najlepiej po angielsku"}, "notes": {"type": "string"}},
                     "required": ["topic"]}},
    {"name": "add_update", "description": "Zapisz update (postęp, zmianę, decyzję) do dziennika projektu w '🚀 Update'. project = nazwa projektu "
     "(np. 'Centrum', 'Dziennik', 'Montaż vlogów', 'PozaSkala', 'Skaner'); najnowszy wpis ląduje na górze z datą.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}, "text": {"type": "string"}}, "required": ["project", "text"]}},
    {"name": "append_note", "description": "Dopisz tekst na końcu istniejącej notatki (id = ścieżka).",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}, "content": {"type": "string"}}, "required": ["id", "content"]}},
    {"name": "centrum_data", "description": "Diagnostyka centrum (tylko odczyt, bez tokenów/haseł): bez 'file' → lista plików danych + ostatnie logi; "
     "z 'file' (np. agency_log.json, agency_inbox.json) → zawartość pliku (ostatnie 'tail' elementów listy).",
     "inputSchema": {"type": "object", "properties": {"file": {"type": "string"}, "tail": {"type": "integer"}}}},
]

_SECRET = re.compile(r"token|secret|refresh|passw|api_?key|cookie|session|auth|credential", re.I)


def _redact(x, d=0):
    if d > 12:
        return "…"
    if isinstance(x, dict):
        return {k: ("***" if _SECRET.search(str(k)) else _redact(v, d + 1)) for k, v in x.items()}
    if isinstance(x, list):
        return [_redact(v, d + 1) for v in x]
    if isinstance(x, str) and len(x) > 40 and re.fullmatch(r"[A-Za-z0-9_\-\.=/+]{40,}", x):
        return "***"
    return x


def centrum_data(file=None, tail=80):
    from social import DIR
    tail = max(1, min(int(tail or 80), 400))
    if not file:
        fs = sorted(DIR.glob("*.json"), key=lambda f: -f.stat().st_mtime)
        return {"files": [{"name": f.name, "kb": f.stat().st_size // 1024, "mtime": int(f.stat().st_mtime)} for f in fs if not _SECRET.search(f.stem)],
                "log": _redact(_read("agency_log.json", [])[-tail:])}
    name = Path(str(file)).name
    if not name.endswith(".json") or _SECRET.search(Path(name).stem) or name in ("push.json",):
        raise ValueError("tego pliku nie udostępniam")
    d = _read(name, None)
    if d is None:
        raise ValueError("nie ma takiego pliku")
    if isinstance(d, list):
        d = d[-tail:]
    elif isinstance(d, dict):
        d = {k: (v[-tail:] if isinstance(v, list) else v) for k, v in d.items()}
    out = json.dumps(_redact(d), ensure_ascii=False)
    return {"file": name, "data": json.loads(out) if len(out) < 400_000 else out[:400_000]}


def _tool(name, a):
    if name == "search":
        r = search(a.get("query", ""), 15)
        return {"results": [{"id": x["path"], "title": x["name"], "text": x["snippet"], "url": ""} for x in r]}
    if name == "fetch":
        p = _safe(a.get("id", ""))
        if not p.is_file():
            p = _find(a.get("id", "")) or p
        if not p.is_file():
            raise ValueError("nie ma takiej notatki")
        return {"id": _rel(p), "title": p.stem, "text": p.read_text(encoding="utf-8", errors="ignore"), "url": "",
                "metadata": {"folder": _rel(p.parent) if p.parent != NOTES else ""}}
    if name == "list_notes":
        t = tree()
        f = a.get("folder")
        return {"folders": t["folders"], "notes": [{"id": n["path"], "title": n["name"], "folder": n["folder"]}
                                                   for n in t["notes"] if not f or n["folder"] == f][:100]}
    if name == "create_note":
        folder = a.get("folder") or "📥 Skrzynka"
        m = write(f"{_clean_name(folder)}/{_clean_name(a.get('title'))}.md", a.get("content", ""))
        return {"ok": True, "id": m["path"]}
    if name == "add_video_idea":
        import uuid
        from agency import topics, save_topics, log
        m = write(f"💡 Pomysły na filmy/{_clean_name(a.get('topic'))}.md", f"# {a.get('topic')}\n\n{a.get('notes', '')}\n\n—\n✅ W kolejce Agencji od {datetime.now():%d.%m %H:%M} (z ChatGPT)")
        T = topics()
        T.append({"id": uuid.uuid4().hex[:8], "status": "queued", "created": time.time(), "source": "notatki", "topic": str(a.get("topic"))[:200],
                  "pillar": "", "category": "", "hook": "", "reference": "", "payoff": "", "why": ("z ChatGPT: " + str(a.get("notes", "")))[:300], "note": m["path"]})
        save_topics(T)
        log("scout", f"Pomysł z ChatGPT trafił do kolejki: {a.get('topic')}", "ok")
        return {"ok": True, "note": m["path"]}
    if name == "add_update":
        m = add_update(a.get("project"), a.get("text"))
        return {"ok": True, "id": m["path"]}
    if name == "append_note":
        p = _safe(a.get("id", ""))
        if not p.is_file():
            p = _find(a.get("id", "")) or p
        m = write(_rel(p), a.get("content", ""), mode="append")
        return {"ok": True, "id": m["path"]}
    if name == "centrum_data":
        return centrum_data(a.get("file"), a.get("tail") or 80)
    raise ValueError("nieznane narzędzie")


@router.api_route("/mcp/{token}", methods=["GET", "POST", "DELETE"])
async def mcp(token: str, req: Request):
    c = _read("notes_cfg.json", {})
    if not c.get("token") or not secrets.compare_digest(token, c["token"]):
        return JSONResponse({"error": "zły token"}, status_code=401)
    if req.method != "POST":
        return Response(status_code=405)
    _init()
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    batch = body if isinstance(body, list) else [body]
    out = []
    for m in batch:
        mid, meth, prm = m.get("id"), m.get("method"), m.get("params") or {}
        if mid is None:          # powiadomienia (np. notifications/initialized) – bez odpowiedzi
            continue
        try:
            if meth == "initialize":
                res = {"protocolVersion": prm.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {"listChanged": False}},
                       "serverInfo": {"name": "centrum-notatki", "version": "1.0"},
                       "instructions": "Notatki użytkownika z jego centrum (Markdown). Używaj search/fetch do czytania, create_note/append_note do zapisu."}
            elif meth == "tools/list":
                res = {"tools": TOOLS}
            elif meth == "tools/call":
                r = _tool(prm.get("name"), prm.get("arguments") or {})
                res = {"content": [{"type": "text", "text": json.dumps(r, ensure_ascii=False)}], "structuredContent": r, "isError": False}
                c["used"] = time.time()
                _write("notes_cfg.json", c)
            elif meth == "ping":
                res = {}
            else:
                out.append({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"brak metody {meth}"}})
                continue
            out.append({"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            out.append({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"Błąd: {e}"}], "isError": True}})
    if not out:
        return Response(status_code=202)
    return JSONResponse(out if isinstance(body, list) else out[0])
