"""Pobieranie danych o śnie z Garmin Connect (dla raportu snu w dzienniku).

Logowanie: własne tokeny w /root/dziennik/garmin_tokens; za pierwszym razem
logowanie danymi z /root/garmin-sync/.env i zapis tokenów. Hasło nigdy nie jest logowane.
"""
import os
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("DZ_HOME", "/root/dziennik"))
OWN_TOKENS = ROOT / "garmin_tokens"
ENV_FILE = Path(os.environ.get("GARMIN_ENV", "/root/garmin-sync/.env"))
# Tylko własne tokeny: nie ruszamy tokenów skryptu kroków (odświeżenie mogłoby je unieważnić).
CANDIDATES = [OWN_TOKENS]

_client = None


def _creds():
    email = pw = None
    try:
        for line in ENV_FILE.read_text().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().upper(), v.strip().strip('"').strip("'")
            if ("EMAIL" in k or "USER" in k or "LOGIN" in k) and not email:
                email = v
            elif "PASS" in k and not pw:
                pw = v
    except Exception:
        pass
    return email, pw


def _dump(g, path):
    path.mkdir(mode=0o700, exist_ok=True)
    c = getattr(g, "client", None) or getattr(g, "garth", None)
    if c is not None and hasattr(c, "dump"):
        c.dump(str(path))


def client():
    global _client
    if _client is not None:
        return _client
    from garminconnect import Garmin
    # 1) zapisane tokeny – bez ponownego logowania
    for d in CANDIDATES:
        try:
            if d.is_dir() and any(d.iterdir()):
                g = Garmin()
                g.login(str(d))
                _client = g
                return g
        except Exception:
            continue
    # 2) logowanie danymi z .env skryptu Garmina; tokeny zapisują się do OWN_TOKENS
    email, pw = _creds()
    if not email or not pw:
        raise RuntimeError("brak danych logowania do Garmina")
    g = Garmin(email, pw)
    g.login(str(OWN_TOKENS))
    _dump(g, OWN_TOKENS)
    _client = g
    return g


def _hm(ms):
    if not ms:
        return None
    return datetime.utcfromtimestamp(ms / 1000).strftime("%H:%M")  # znaczniki *Local są już w czasie lokalnym


def _m(sec):
    return round((sec or 0) / 60)


def sleep_for(day: str):
    """Sen zakończony rano danego dnia (RRRR-MM-DD). None, gdy brak zapisu."""
    global _client
    try:
        raw = client().get_sleep_data(day) or {}
    except Exception:
        _client = None  # przy błędzie spróbuj następnym razem zalogować się od nowa
        raise
    d = raw.get("dailySleepDTO") or {}
    total = d.get("sleepTimeSeconds")
    if not total:
        return None
    sc = (d.get("sleepScores") or {}).get("overall") or {}
    out = {
        "total": _m(total), "deep": _m(d.get("deepSleepSeconds")), "light": _m(d.get("lightSleepSeconds")),
        "rem": _m(d.get("remSleepSeconds")), "awake": _m(d.get("awakeSleepSeconds")),
        "start": _hm(d.get("sleepStartTimestampLocal")), "end": _hm(d.get("sleepEndTimestampLocal")),
        "score": sc.get("value"), "quality": sc.get("qualifierKey"),
        "rhr": raw.get("restingHeartRate"), "hrv": raw.get("avgOvernightHrv"),
        "resp": d.get("averageRespirationValue"), "stress": d.get("avgSleepStress"),
        "src": "garmin",
    }
    return {k: v for k, v in out.items() if v is not None}

