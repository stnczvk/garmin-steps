"""Czat z Hermesem (Hermes Agent na tym samym VPS) – przez jego API server (OpenAI-compatible, port 8642).
Klucz: HERMES_API_KEY w /root/social/.env albo API_SERVER_KEY z ~/.hermes/.env (centrum działa jako root)."""
import json, os, time
from pathlib import Path
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from social import _read, _write

router = APIRouter()
F = "hermes_chat.json"


def _henv():
    out = {}
    for p in (Path.home() / ".hermes" / ".env", Path("/root/.hermes/.env")):
        try:
            for line in p.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1); out[k.strip()] = v.strip().strip('"').strip("'")
            break
        except Exception:
            pass
    return out


def _cfg():
    h = _henv()
    key = os.environ.get("HERMES_API_KEY") or h.get("API_SERVER_KEY", "")
    port = h.get("API_SERVER_PORT", "8642")
    url = os.environ.get("HERMES_API_URL") or f"http://127.0.0.1:{port}"
    on = os.environ.get("HERMES_API_KEY") or h.get("API_SERVER_ENABLED", "").lower() in ("1", "true", "yes")
    return url.rstrip("/"), key, bool(on and key)


@router.get("/api/hermes")
def status():
    url, key, on = _cfg()
    ok = False
    if on:
        try:
            ok = httpx.get(url + "/health", timeout=3).status_code < 500
        except Exception:
            ok = False
    return {"configured": on, "ok": ok, "history": _read(F, {}).get("msgs", [])[-60:]}


@router.post("/api/hermes/clear")
def clear():
    _write(F, {"msgs": [], "sid": f"centrum-{int(time.time())}"})
    return {"ok": True}


@router.post("/api/hermes/chat")
async def chat(req: Request):
    b = await req.json()
    text = str(b.get("text", "")).strip()[:8000]
    url, key, on = _cfg()
    D = _read(F, {})
    msgs = D.get("msgs", [])
    sid = D.get("sid") or "centrum"
    if not text:
        return {"error": "pusta wiadomość"}
    if not on:
        async def nope():
            yield "⚠️ Hermes nie ma włączonego API. Na serwerze dopisz do ~/.hermes/.env:\nAPI_SERVER_ENABLED=true\nAPI_SERVER_KEY=<dowolny długi klucz>\ni zrestartuj gateway Hermesa."
        return StreamingResponse(nope(), media_type="text/plain; charset=utf-8")
    msgs.append({"role": "user", "content": text, "at": time.time()})
    hist = [{"role": m["role"], "content": m["content"]} for m in msgs[-30:]]

    async def gen():
        out = []
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as c:
                async with c.stream("POST", url + "/v1/chat/completions",
                                    headers={"Authorization": f"Bearer {key}", "X-Hermes-Session-Id": sid},
                                    json={"model": "hermes-agent", "messages": hist, "stream": True}) as r:
                    if r.status_code >= 400:
                        err = (await r.aread()).decode(errors="ignore")[:300]
                        out.append(f"⚠️ Hermes odpowiedział błędem {r.status_code}: {err}"); yield out[-1]; return
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        d = line[5:].strip()
                        if d == "[DONE]":
                            break
                        try:
                            delta = json.loads(d)["choices"][0].get("delta", {}).get("content") or ""
                        except Exception:
                            delta = ""
                        if delta:
                            out.append(delta); yield delta
        except Exception as e:
            out.append(f"⚠️ Brak połączenia z Hermesem ({e.__class__.__name__}). Czy gateway działa?"); yield out[-1]
        finally:
            ans = "".join(out).strip()
            if ans:
                D2 = _read(F, {}); m2 = D2.get("msgs", [])
                if not m2 or m2[-1].get("content") != text:
                    m2.append({"role": "user", "content": text, "at": time.time()})
                m2.append({"role": "assistant", "content": ans, "at": time.time()})
                D2["msgs"] = m2[-200:]; D2.setdefault("sid", sid); _write(F, D2)

    D["msgs"] = msgs[-200:]; D["sid"] = sid; _write(F, D)
    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
