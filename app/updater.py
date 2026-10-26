"""Aktualizacja aplikacji z telefonu: Centrum (social-update) i Dziennik (dz-update).
Polecenie idzie przez systemd-run jako osobna jednostka – restart usługi centrum go nie przerywa."""
import os, shutil, subprocess, time
from pathlib import Path
from fastapi import APIRouter

router = APIRouter()
LOGS = Path(os.environ.get("SOCIAL_HOME", "/root/social"))
APPS = {
    "centrum": {"name": "Centrum", "cmd": "/usr/local/bin/social-update", "src": "/root/social/src", "branch": "social-app"},
    "dziennik": {"name": "Dziennik", "cmd": "/usr/local/bin/dz-update", "src": "/root/dziennik/src", "branch": "dziennik-app"},
}
_remote = {}


def _git(src, *a, timeout=20):
    try:
        r = subprocess.run(["git", "-C", src, *a], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _unit(k):
    return f"upd-{k}"


def _running(k):
    try:
        r = subprocess.run(["systemctl", "is-active", _unit(k)], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() in ("active", "activating")
    except Exception:
        return False


def _info(k, fresh=False):
    a = APPS[k]
    ok = os.path.exists(a["cmd"]) and os.path.isdir(a["src"])
    local = _git(a["src"], "rev-parse", "HEAD") if ok else ""
    c = _remote.get(k)
    if ok and (fresh or not c or time.time() - c[0] > 300):
        out = _git(a["src"], "ls-remote", "origin", a["branch"])
        c = _remote[k] = (time.time(), out.split()[0] if out else "")
    remote = c[1] if c else ""
    msg = _git(a["src"], "log", "-1", "--format=%s", local) if local else ""
    when = _git(a["src"], "log", "-1", "--format=%ct", local) if local else ""
    log = LOGS / f"update_{k}.log"
    tail = log.read_text(errors="ignore").splitlines()[-12:] if log.exists() else []
    return {"id": k, "name": a["name"], "installed": ok, "local": local[:7], "remote": remote[:7],
            "update": bool(ok and local and remote and local != remote), "msg": msg[:120],
            "at": int(when) if when.isdigit() else None, "running": _running(k), "log": tail,
            "finished": log.stat().st_mtime if log.exists() else None}


@router.get("/api/update")
def status(fresh: int = 0):
    return {"apps": [_info(k, bool(fresh)) for k in APPS], "systemd": bool(shutil.which("systemd-run"))}


@router.post("/api/update/{k}")
def run(k: str):
    if k not in APPS:
        return {"error": "nie ma takiej aplikacji"}
    a = APPS[k]
    if not os.path.exists(a["cmd"]):
        return {"error": f"brak {a['cmd']} na serwerze"}
    if _running(k):
        return {"ok": True, "already": True}
    log = LOGS / f"update_{k}.log"
    sh = f'echo "start $(date "+%F %T")" > {log}; {a["cmd"]} >> {log} 2>&1; echo "KONIEC kod=$? $(date "+%F %T")" >> {log}'
    subprocess.run(["systemctl", "reset-failed", _unit(k)], capture_output=True)
    r = subprocess.run(["systemd-run", f"--unit={_unit(k)}", "--collect", "--quiet", "/bin/bash", "-c", sh],
                       capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return {"error": (r.stderr or "systemd-run nie zadziałał")[:200]}
    _remote.pop(k, None)
    return {"ok": True}
