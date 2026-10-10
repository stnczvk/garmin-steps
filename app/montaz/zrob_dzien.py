#!/usr/bin/env python3
"""
ZRÓB DZIEŃ - cały daily film jednym poleceniem.

  1. Transkrypcja (co mówisz w klipach) - jeśli jeszcze jej nie ma dla tych klipów
  2. Tytuł z planera (plan.txt): numer dnia z daty nagrania, dzień treningowy/nie, kcal
  3. Kolejność klipów wg godziny nagrania
  4. Zrzuty dopasowane do posiłku z tego, co mówisz ("na obiad..." -> obiad-....png)
  5. Cięcia z transkrypcji: powtórki nagrania, przekleństwa, długa cisza na początku
  6. Zapis planu montażu do kolejnosc.txt + raport
  7. Montaż -> gotowe/dzien_<N>.mp4

Użycie:
  python3 zrob_dzien.py DailyContent/film              # wszystko
  python3 zrob_dzien.py DailyContent/film --tylko-plan # bez montażu (do sprawdzenia)
  python3 zrob_dzien.py DailyContent/film --od-nowa    # zignoruj istniejący kolejnosc.txt
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import shlex
import subprocess
import sys
import unicodedata
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo("Europe/Warsaw")
VIDEO_EXT = {".mov", ".mp4", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
DNI = {"pon": 0, "wt": 1, "śr": 2, "sr": 2, "czw": 3, "pt": 4, "sob": 5, "nd": 6, "niedz": 6}
DNI_PELNE = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
SWEAR = re.compile(r"kurw|chuj|pierdol|jeb|szmat")

# posiłek w mowie -> klucz ; kolejność ma znaczenie ("drugie śniadanie" przed "śniadanie")
# dopasowanie na tekście bez polskich znaków (Whisper czasem gubi ogonki albo zdrabnia: "śniadanko", "kolacyjka")
MEAL_SPEECH = [("drugie", r"(drug\w*|ii|2)\s+sniadan"), ("sniadanie", r"sniadan"), ("obiad", r"obiad"),
               ("podwieczorek", r"podwieczor"), ("przekaska", r"przekas|przegryz|deser"),
               ("kolacja", r"kolac|kolacyj")]
MEAL_ORDER = ["sniadanie", "drugie", "obiad", "podwieczorek", "przekaska", "kolacja"]
MEAL_HOUR = {"sniadanie": 8.0, "drugie": 11.0, "obiad": 14.5, "podwieczorek": 16.5, "przekaska": 17.0, "kolacja": 20.0}
MEAL_FILE = [("drugie", "drugie"), ("sniadanie", "sniadan"), ("obiad", "obiad"),
             ("podwieczorek", "podwieczor"), ("przekaska", "przekask"), ("kolacja", "kolac")]


def ascii_low(s):
    return unicodedata.normalize("NFKD", s.lower()).encode("ascii", "ignore").decode().replace("ł", "l")


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


# ---------------------------------------------------------------------------
# PLANER
# ---------------------------------------------------------------------------
def read_plan(path):
    plan = {"nazwa": "Redukcja", "start": None, "dni_treningowe": set(),
            "kcal_treningowy": None, "kcal_nietreningowy": None, "wyjatki": {}, "kcal_w_tytule": "cel"}
    if not path.exists():
        return plan
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if "=" not in line:
            continue
        k, v = [x.strip() for x in line.split("=", 1)]
        kl = k.lower()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", k):
            parts = v.lower().split()
            plan["wyjatki"][dt.date.fromisoformat(k)] = (
                parts[0].startswith("tren"), int(parts[1]) if len(parts) > 1 else None)
        elif kl == "start":
            plan["start"] = dt.date.fromisoformat(v)
        elif kl == "dni_treningowe":
            plan["dni_treningowe"] = {DNI[d.strip().lower()] for d in v.split(",") if d.strip()}
        elif kl in ("kcal_treningowy", "kcal_nietreningowy"):
            plan[kl] = int(v)
        elif kl == "nazwa":
            plan["nazwa"] = v
        elif kl == "kcal_w_tytule":
            plan["kcal_w_tytule"] = v.lower()
    return plan


def title_for(plan, day_date, rep=None):
    """Tytuł: numer dnia z planera; trening i kcal z dziennika (jeśli dostępny), inaczej z planera."""
    rep = rep if rep is not None else []
    if plan["start"] is None:
        return None, None
    n = (day_date - plan["start"]).days + 1
    trening = day_date.weekday() in plan["dni_treningowe"]
    zrodlo = "planer (stały grafik)"
    kcal = None
    info = None
    try:
        sys.path.insert(0, str(HERE))
        import dziennik
        info = dziennik.day_info(day_date)
    except Exception as e:                       # brak hasła / sieci -> sam planer
        rep.append(f"Dziennik: niedostępny ({e}) - tytuł z samego planera")
    if day_date in plan["wyjatki"]:
        trening, kc = plan["wyjatki"][day_date]
        kcal = kc
        zrodlo = "wyjątek w plan.txt"
    elif info and info["trening"]:
        trening, zrodlo = True, "dziennik: " + ", ".join(info["sesje"])
    elif info and trening and info["pierwszy_zapisany_trening"] \
            and day_date.isoformat() >= info["pierwszy_zapisany_trening"]:
        rep.append("  UWAGA: wg grafiku to dzień treningowy, ale w dzienniku nie ma zapisanego treningu "
                   "z tego dnia - zostawiam 'treningowy' (dopisz wyjątek w plan.txt, jeśli treningu nie było)")
    if info:
        eaten = info["zjedzone_kcal"]
        rep.append("Dziennik: " + ("trening: " + ", ".join(info["sesje"]) if info["trening"] else "brak treningu")
                   + (f"; zjedzone {eaten} kcal (" + ", ".join(f"{k} {v}" for k, v in info["posilki_kcal"].items()) + ")"
                      if eaten else "; brak posiłków")
                   + (f"; cardio: {', '.join(info['cardio'])}" if info["cardio"] else ""))
        if kcal is None:
            if plan.get("kcal_w_tytule") == "zjedzone" and eaten:
                kcal = int(round(eaten / 100.0) * 100)
            else:
                cel = info["cel_trening"] if trening else info["cel_odpoczynek"]
                if cel:
                    kcal = int(round(cel / 100.0) * 100)
    if kcal is None:
        kcal = plan["kcal_treningowy"] if trening else plan["kcal_nietreningowy"]
    if info and not info["trening"] and zrodlo.startswith("planer") and not trening:
        zrodlo = "grafik + brak treningu w dzienniku"
    rep.append(f"Dzień treningowy: {'TAK' if trening else 'NIE'} (źródło: {zrodlo})")
    lines = [f"{plan['nazwa']} dzień {n}"]
    if kcal:
        lines.append(f"{kcal} kcal")
    lines.append("dzień treningowy" if trening else "dzień nietreningowy")
    return n, lines


# ---------------------------------------------------------------------------
# KLIPY
# ---------------------------------------------------------------------------
def recorded_at(video):
    """Data i godzina NAGRANIA (z iPhone'a), a nie skopiowania pliku."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format_tags=com.apple.quicktime.creationdate,creation_time",
                        "-of", "json", str(video)], capture_output=True, text=True)
    tags = json.loads(r.stdout or "{}").get("format", {}).get("tags", {})
    for key in ("com.apple.quicktime.creationdate", "creation_time"):
        v = tags.get(key)
        if v:
            try:
                v2 = v.replace("Z", "+00:00")
                v2 = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v2)   # +0200 -> +02:00 (Python 3.10)
                t = dt.datetime.fromisoformat(v2)
                if key == "creation_time":
                    return t.astimezone(TZ), "przybliżona (czas zapisu pliku)"
                return t.astimezone(TZ), None
            except ValueError:
                pass
    return dt.datetime.fromtimestamp(video.stat().st_mtime, TZ), "przybliżona (data pliku)"


def meal_from_speech(text):
    """Posiłek, który PIERWSZY pada w wypowiedzi ("na obiad to samo co na kolację" -> obiad)."""
    t = ascii_low(text)
    best = None
    for key, pat in MEAL_SPEECH:
        m = re.search(pat, t)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), key)
    return best[1] if best else None


def meal_from_file(name):
    n = ascii_low(name)
    for key, pat in MEAL_FILE:
        if pat in n:
            return key
    return None


START_WORDS = {"no", "i", "a", "to", "wiec", "dobra", "dobrze", "okej", "ok", "noi"}
FILLER = {"yyy", "eee", "em", "eh", "hmm", "mmm", "yy", "ee"}


def _wn(w):
    return re.sub(r"[^a-z0-9]", "", ascii_low(w))


def _same(a, b):
    """To samo słowo – także urwane ("ob" ~ "obiad") albo z inną końcówką ("obiad" ~ "obiadu")."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 2 and long_.startswith(short) and (len(short) >= 3 or len(long_) <= 4) \
        or (len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4])


def word_restarts(words, max_gap=14):
    """Szuka miejsc, gdzie zaczynasz zdanie jeszcze raz: te same 2+ słowa (albo 1 słowo + urwane następne)
    pojawiają się znowu w ciągu kilkunastu słów. Zwraca [(start_cięcia, koniec_cięcia, początek_tekstu_który_zostaje)]."""
    ws = [(_wn(w["word"]), float(w["start"]), float(w["end"])) for w in words]
    ws = [x for x in ws if x[0] and x[0] not in FILLER]
    out, i = [], 0
    while i < len(ws) - 2:
        best = None
        for j in range(i + 1, min(len(ws) - 1, i + 1 + max_gap)):
            if not _same(ws[i][0], ws[j][0]):
                continue
            k = 0
            while j + k < len(ws) and i + k < j and _same(ws[i + k][0], ws[j + k][0]):
                k += 1
            # 2+ zgodne słowa, albo 1 słowo, po którym w pierwszym podejściu jest urwane słowo
            broken = k == 1 and i + 1 < j and len(ws[i + 1][0]) <= 3 and j + 1 < len(ws) and ws[j + 1][0].startswith(ws[i + 1][0])
            if (k >= 2 or broken) and ws[j][1] - ws[i][1] >= 0.4 and len(ws[i][0]) + len(ws[i + 1][0]) >= 3:
                best = j            # szukamy dalej – przy kilku podejściach zostaje ostatnie
        if best is not None:
            a, b = max(0.0, ws[i][1] - 0.1), ws[best][1] - 0.12
            if b - a >= 0.3:
                out.append((a, b, " ".join(x[0] for x in ws[best:best + 4])))
            i = best
        else:
            i += 1
    return out


def audio_silences(video):
    """Cisza w nagraniu wg głośności (ffmpeg). Próg względem najgłośniejszego miejsca klipu.
    Zwraca ([(start, koniec)], długość klipu)."""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af", "volumedetect",
                            "-f", "null", "-"], capture_output=True, text=True, timeout=300)
        mx = float(re.search(r"max_volume: (-?[\d.]+) dB", r.stderr).group(1))
        h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr).groups()
        dur = int(h) * 3600 + int(m) * 60 + float(sec)
        thr = min(-30.0, max(-55.0, mx - 28.0))
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                            f"silencedetect=noise={thr:.0f}dB:d=0.6", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=300)
        out, st = [], None
        for line in r.stderr.splitlines():
            m = re.search(r"silence_start: (-?[\d.]+)", line)
            if m:
                st = max(0.0, float(m.group(1)))
            m = re.search(r"silence_end: ([\d.]+)", line)
            if m and st is not None:
                out.append((st, float(m.group(1))))
                st = None
        if st is not None:
            out.append((st, dur))
        return out, dur
    except Exception:
        return [], None


def auto_cuts(info):
    """Cięcia z transkrypcji. Zwraca (opcje dla kolejnosc.txt, lista powodów)."""
    chunks = [c for c in info.get("chunks", []) if not c.get("szum")]
    if not chunks:
        return [], []
    words = lambda c: [w for w in re.sub(r"[^\w\s]", " ", c["text"].lower()).split() if w]
    cuts, reasons, removed = [], [], set()
    # 1) powtórki: od początku wcześniejszego podejścia do początku ostatniego podejścia
    for i, c1 in enumerate(chunks):
        if i in removed:
            continue
        w1, last_j = words(c1), None
        for j in range(i + 1, len(chunks)):
            w2 = words(chunks[j])
            n = 0
            while n < min(len(w1), len(w2)) and w1[n] == w2[n]:
                n += 1
            if n >= 2 or (w1 and len(w1) <= 3 and w2[:len(w1)] == w1):
                last_j = j
        if last_j is not None:
            a, b = max(0.0, c1["start"] - 0.15), chunks[last_j]["start"] - 0.2
            if b > a:
                cuts.append((a, b))
                removed.update(range(i, last_j))
                reasons.append(f"powtórka nagrania: wycinam {a:.2f}-{b:.2f} s (zostaje ostatnie podejście "
                               f"od \"{' '.join(chunks[last_j]['text'].split()[:5])}...\")")
    # 1b) powtórki na poziomie słów – także bez pauzy i z urwanym słowem ("na ob… na obiad", "no to no to jem")
    for a, b, txt in word_restarts(info.get("words", [])):
        if any(a < cb and b > ca for ca, cb in cuts):
            continue
        cuts.append((a, b))
        reasons.append(f"powtórka (słowa): wycinam {a:.2f}-{b:.2f} s (zostaje od \"{txt}...\")")
    # 1c) "rozciągnięte" słowo – rozpoznawanie mowy skleja powtórzone słowo z pauzą w jedno długie
    #     (np. "na" trwające 1,6 s = "na… na"). Zostawiamy tylko końcówkę, w której słowo faktycznie pada.
    for w in info.get("words", [])[:-1]:
        txt = _wn(w.get("word", ""))
        if not txt or len(txt) > 4 or re.search(r"\d", txt):
            continue
        st, en = float(w["start"]), float(w["end"])
        normal = 0.15 + 0.09 * len(txt)
        if en - st > max(1.0, normal * 2.5):
            a, b = max(0.0, st - 0.05), en - normal - 0.05
            # krótkie słowa startowe tuż przed ("no i", "a", "to") to zwykle pierwsze podejście – też wycinamy
            ws_all = info.get("words", [])
            k = next((n for n, x in enumerate(ws_all) if x is w), None)
            while k and _wn(ws_all[k - 1].get("word", "")) in START_WORDS and st - float(ws_all[k - 1]["start"]) < 3.0:
                k -= 1
                a = max(0.0, float(ws_all[k]["start"]) - 0.05)
            if b - a >= 0.4 and not any(a < cb and b > ca for ca, cb in cuts):
                cuts.append((a, b))
                reasons.append(f"rozciągnięte słowo \"{w['word']}\" ({en - st:.1f} s) – pewnie powtórka/zawieszenie: wycinam {a:.2f}-{b:.2f} s")
    # 2) krótkie fragmenty z przekleństwem (reakcja na pomyłkę)
    for i, c in enumerate(chunks):
        if i in removed or not SWEAR.search(c["text"].lower()):
            continue
        if c["end"] - c["start"] <= 3.5:
            a, b = max(0.0, c["start"] - 0.15), c["end"] + 0.15
            cuts.append((a, b))
            removed.add(i)
            reasons.append(f"przekleństwo: wycinam {a:.2f}-{b:.2f} s (\"{c['text']}\")")
        else:
            reasons.append(f"UWAGA - przekleństwo w dłuższej wypowiedzi, NIE wycinam automatycznie: "
                           f"{c['start']:.2f}-{c['end']:.2f} s \"{c['text']}\"")
    # 3) długa cisza na początku (zanim zaczniesz mówić)
    kept = [c for i, c in enumerate(chunks) if i not in removed]
    if kept and not any(a <= 0.2 for a, _ in cuts) and kept[0]["start"] > 0.9:
        b = kept[0]["start"] - 0.3
        cuts.append((0.0, b))
        reasons.append(f"cisza na początku: wycinam 0.00-{b:.2f} s")
    # 4) cisza w dźwięku (pauzy, wstęp, koniec) – zostawiamy ~0,25 s oddechu
    dur = info.get("dur")
    norm_words = [(float(x["start"]), float(x["end"])) for x in info.get("words", []) if _wn(x.get("word", ""))
                  and float(x["end"]) - float(x["start"]) <= max(1.0, (0.15 + 0.09 * len(_wn(x["word"]))) * 2.5)]
    for s0, e0 in info.get("silences", []):
        lead, tail = s0 <= 0.1, dur is not None and e0 >= dur - 0.1
        a, b = (0.0 if lead else s0 + 0.25), (dur if tail else e0 - 0.25)
        if b - a < (0.5 if lead or tail else 0.7):
            continue
        if any(ws >= a and we <= b for ws, we in norm_words):
            continue        # rozpoznane słowo w środku – to cicha mowa, nie cisza
        cuts.append((a, b))
        reasons.append(f"cisza: wycinam {a:.2f}-{b:.2f} s")
    # scal nakładające się cięcia
    cuts.sort()
    merged = []
    for a, b in cuts:
        if merged and a <= merged[-1][1] + 0.3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    # po cięciu nie zostawiaj ciszy: zacznij ~0,3 s przed następnym słowem
    wl = [(float(x["start"]), float(x["end"])) for x in info.get("words", []) if _wn(x.get("word", ""))]
    for n, (a, b) in enumerate(merged):
        if any(s0 < b < e0 for s0, e0 in wl):
            continue
        nxt = min((s0 for s0, _ in wl if s0 >= b), default=None)
        if nxt is not None and nxt - b > 0.6:
            merged[n] = (a, nxt - 0.3)
            reasons.append(f"cisza po cięciu: start od {nxt - 0.3:.2f} s")
    # między cięciami zostały tylko "no", "i" albo cisza → jedno cięcie
    m2 = []
    for a, b in merged:
        if m2 and all(_wn(x["word"]) in START_WORDS or not _wn(x["word"]) for x in info.get("words", [])
                      if m2[-1][1] <= float(x["start"]) < a):
            m2[-1] = (m2[-1][0], b)
        else:
            m2.append((a, b))
    merged = m2
    opts = []
    for a, b in merged:
        if dur is not None and b >= dur - 0.05:
            opts.append(f"do={a:.2f}")
        else:
            opts.append(f"od={b:.2f}" if a <= 0.2 else f"wytnij={a:.2f}-{b:.2f}")
    return opts, reasons


def files_hash(files):
    return hashlib.md5("|".join(f"{p.name}:{p.stat().st_size}" for p in files).encode()).hexdigest()[:10]


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--tylko-plan", action="store_true", help="bez montażu")
    ap.add_argument("--od-nowa", action="store_true", help="przygotuj kolejnosc.txt od nowa")
    ap.add_argument("--data", action="store_true", help="wypisz tylko datę nagrania klipów (RRRR-MM-DD)")
    args = ap.parse_args()
    day = Path(args.folder).resolve()
    if args.data:
        vids = [p for p in day.iterdir() if p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")]
        if not vids:
            sys.exit("Brak klipów w folderze.")
        print(min(recorded_at(v)[0] for v in vids).date().isoformat())
        return

    videos = [p for p in day.iterdir() if p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")]
    images = sorted([p for p in day.iterdir() if p.suffix.lower() in IMAGE_EXT and not p.name.startswith(".")],
                    key=natural_key)
    if not videos:
        sys.exit("Brak klipów w folderze.")
    times = {v: recorded_at(v) for v in videos}
    videos.sort(key=lambda v: (times[v][0], natural_key(v)))
    rep = []

    # --- 1. transkrypcja (jeśli brak albo dotyczy innych klipów) ---
    tj = day / "transkrypcja.json"
    trans = json.loads(tj.read_text(encoding="utf-8")) if tj.exists() else {}
    if not all(v.name in trans and "chunks" in trans[v.name] for v in videos):
        print("Transkrypcja...", flush=True)
        r = subprocess.run([sys.executable, str(HERE / "transkrybuj.py"), str(day)], text=True,
                           capture_output=True)
        if r.returncode != 0:
            sys.exit(r.stdout + r.stderr)
        trans = json.loads(tj.read_text(encoding="utf-8"))

    # --- 2. tytuł ---
    day_date = min(t for t, _ in times.values()).date()
    approx = [n for v, (_, n) in times.items() if n]
    n, title = title_for(read_plan(day.parent / "plan.txt"), day_date, rep)
    opis = day / "opis.txt"
    newest_clip = max(v.stat().st_mtime for v in videos)
    if opis.exists() and opis.stat().st_mtime >= newest_clip:
        title = [l.strip() for l in opis.read_text(encoding="utf-8").splitlines() if l.strip()]
        rep.append(f"Tytuł: z opis.txt (Twój ręczny): {' / '.join(title)}")
        if n is not None and not any(re.search(rf"\b{n}\b", l) for l in title):
            rep.append(f"  UWAGA: wg planera to dzień {n} - sprawdź numer w opis.txt")
    elif title:
        rep.append(f"Tytuł: z planera: {' / '.join(title)}")
        if opis.exists():
            rep.append("  (stary opis.txt pominięty - jest starszy niż klipy)")
    else:
        sys.exit("Brak planera (plan.txt) i opis.txt - nie wiem jaki tytuł wstawić.")
    rep.append(f"Data nagrania: {day_date.strftime('%d.%m.%Y')} ({DNI_PELNE[day_date.weekday()]})"
               + (f"  UWAGA: data {approx[0]}" if approx else ""))

    # --- 3-5. plan montażu ---
    ko = day / "kolejnosc.txt"
    fh = files_hash(videos + images)
    use_existing = False
    if ko.exists() and args.od_nowa and not ko.read_text(encoding="utf-8").startswith("# AUTO"):
        ko.rename(day / "kolejnosc_reczna.txt")
        rep.append("Ręczny kolejnosc.txt zachowany jako kolejnosc_reczna.txt")
    if ko.exists() and not args.od_nowa:
        txt = ko.read_text(encoding="utf-8")
        referenced = [t for line in txt.splitlines() for t in shlex.split(line, comments=True) if "=" not in t]
        stale = any(not (day / t).exists() for t in referenced)
        auto = txt.startswith("# AUTO")
        if not stale and (not auto or f"[{fh}]" in txt):
            use_existing = True
            rep.append("Plan montażu: istniejący kolejnosc.txt (" + ("automatyczny" if auto else "ręczny") + ")")
        elif not auto:
            ko.rename(day / "kolejnosc_stare.txt")
            rep.append("Stary kolejnosc.txt (od innych klipów) przeniesiony do kolejnosc_stare.txt")

    if not use_existing:
        free = {meal_from_file(i.name): i for i in images if meal_from_file(i.name)}
        unnamed = [i for i in images if not meal_from_file(i.name)]
        lines = [f"# AUTO [{fh}] - plan przygotowany automatycznie; możesz go poprawić ręcznie", ""]
        meals, shots, how = {}, {}, {}
        for v in videos:
            meals[v] = meal_from_speech(trans.get(v.name, {}).get("text", ""))
            shot = free.pop(meals[v], None) if meals[v] else None
            if shot is None and meals[v] and meals[v] != "drugie" and unnamed:
                shot = unnamed.pop(0)       # zrzuty bez nazwy posiłku -> po kolei
            shots[v] = shot
            if shot:
                how[v] = "z mowy"
        # karty ustawione ręcznie w aplikacji (karty.json) mają pierwszeństwo
        kj = day / "karty.json"
        manual = json.loads(kj.read_text()) if kj.exists() else {}
        byname = {i.name: i for i in images}
        for v in videos:
            if v.name in manual:
                c = byname.get(manual[v.name] or "")
                if shots[v] and shots[v] != c:
                    free[meal_from_file(shots[v].name) or shots[v].name] = shots[v]
                shots[v], how[v] = c, "ręcznie"
                if c:
                    meals[v] = meal_from_file(c.name) or meals[v]
        taken = {s for s in shots.values() if s}
        free = {k: i for k, i in free.items() if i not in taken}
        unnamed = [i for i in unnamed if i not in taken]
        # karty, których nie udało się dopasować z mowy -> do klipów bez karty, wg godziny nagrania
        left_cards = sorted(free.items(), key=lambda kv: MEAL_ORDER.index(kv[0]) if kv[0] in MEAL_ORDER else 99)
        last_t = None
        for meal_key, img in left_cards:
            cand = [v for v in videos if shots[v] is None and v.name not in manual and (last_t is None or times[v][0] >= last_t)]
            if not cand:
                break
            want = MEAL_HOUR.get(meal_key, 14.0)
            v = min(cand, key=lambda x: abs(times[x][0].hour + times[x][0].minute / 60 - want))
            shots[v], meals[v], how[v] = img, meals[v] or meal_key, "wg godziny nagrania – sprawdź"
            free.pop(meal_key, None)
            last_t = times[v][0]
        for v in videos:
            info = dict(trans.get(v.name, {}))
            info["silences"], info["dur"] = audio_silences(v)
            text = info.get("text", "")
            meal, shot = meals[v], shots[v]
            opts, reasons = auto_cuts(info)
            parts = [shlex.quote(v.name)] + ([shlex.quote(shot.name)] if shot else []) + opts
            lines.append(" ".join(parts) + f"   # {times[v][0].strftime('%H:%M')} {meal or '-'}")
            desc = f"{times[v][0].strftime('%H:%M')}  {v.name}  ->  " + \
                   (f"{meal or '?'} + zrzut {shot.name} ({how.get(v, '')})" if shot else f"{meal or 'bez posiłku'}, bez zrzutu")
            rep.append(desc)
            rep += [f"      {r}" for r in reasons]
            rep += [f"      transkrypcja: {w}" for w in info.get("warnings", [])
                    if not w.startswith(("POWTÓRKA", "PRZEKLEŃSTWO", "SZUM"))]
            if not text.strip():
                rep.append("      UWAGA: brak mowy w tym klipie")
        left = list(free.values()) + unnamed
        if left:
            rep.append("UWAGA: zrzuty bez dopasowanego klipu: " + ", ".join(i.name for i in left))
        ko.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        rep.append(ko.read_text(encoding="utf-8").strip())

    nazwa = f"dzien_{n}" if (n is not None and title and str(n) in title[0]) else day.name
    rep.append(f"Plik wynikowy: gotowe/{nazwa}.mp4")
    print("\n".join(rep), flush=True)
    (day / "raport.txt").write_text("\n".join(rep) + "\n", encoding="utf-8")

    if args.tylko_plan:
        return
    print("\nMontaż...", flush=True)
    r = subprocess.run([sys.executable, str(HERE / "montuj.py"), str(day),
                        "--tytul", "|".join(title), "--nazwa", nazwa])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
