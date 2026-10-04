#!/usr/bin/env python3
"""
TRANSKRYBUJ - zamienia to, co mówisz w klipach, na tekst z czasem każdego słowa.

Wysyła do Groq (Whisper) SAM DŹWIĘK (mały plik mp3), wideo zostaje na komputerze.
Dźwięk jest dzielony na fragmenty wypowiedzi (po przerwach w mówieniu) i każdy
fragment rozpoznawany jest OSOBNO - dzięki temu Whisper nie "wygładza" powtórek
(np. "no i na drugie... no i na drugie śniadanie") w jedno zdanie.
Skrypt sam oznacza podejrzane miejsca (UWAGA: ...): powtórzony początek zdania,
powtórzone słowa, słowo rozciągnięte na kilka sekund, długa przerwa w środku klipu.

Wynik zapisuje w folderze dnia:
    transkrypcja.txt   - czytelny zapis: [sekunda] tekst + UWAGI, klip po klipie
    transkrypcja.json  - pełne dane (czas każdego słowa) do precyzyjnych cięć

Użycie:
    python3 transkrybuj.py DailyContent/dzien_21

Klucz API: plik groq_klucz.txt obok tego skryptu (albo zmienna GROQ_API_KEY).
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
API_URL = os.environ.get("GROQ_API_URL", "https://api.groq.com/openai/v1/audio/transcriptions")
MODEL = "whisper-large-v3"       # najdokładniejszy model Groq, dobrze radzi sobie z polskim
VIDEO_EXT = {".mov", ".mp4", ".m4v"}


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def api_key():
    k = os.environ.get("GROQ_API_KEY", "").strip()
    f = HERE / "groq_klucz.txt"
    if not k and f.exists():
        k = f.read_text(encoding="utf-8").strip()
    if not k:
        sys.exit("Brak klucza Groq: zapisz go w _narzedzie/groq_klucz.txt")
    return k


def extract_audio(video, out):
    # mono, 16 kHz, mp3 64k - wystarczy do rozpoznawania mowy, plik jest malutki
    r = subprocess.run(["ffmpeg", "-y", "-i", str(video), "-map", "0:a:0", "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k",
                        str(out)], capture_output=True, text=True)
    return r.returncode == 0 and out.exists() and out.stat().st_size > 0


def transcribe(audio_path, key):
    boundary = uuid.uuid4().hex
    fields = {
        "model": MODEL,
        "language": "pl",
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    for g in ("word", "segment"):
        body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                 f"name=\"timestamp_granularities[]\"\r\n\r\n{g}\r\n").encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{audio_path.name}\"\r\nContent-Type: audio/mpeg\r\n\r\n").encode()
    body += audio_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "DailyContent-montaz/1.0",   # domyślny podpis Pythona blokuje Cloudflare (błąd 1010)
        "Accept": "application/json",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 500, 502, 503) and attempt < 5:   # limit zapytań / chwilowy błąd
                wait = float(e.headers.get("retry-after") or 10)
                time.sleep(min(max(wait, 2), 60))
                continue
            sys.exit(f"Groq zwrócił błąd {e.code}: {msg}")
        except urllib.error.URLError as e:
            sys.exit(f"Brak połączenia z Groq ({e.reason}). Czy api.groq.com jest odblokowane?")


SILENCE_DB = -38          # poniżej tej głośności = cisza
SILENCE_MIN = 0.35        # przerwa w mówieniu musi trwać co najmniej tyle sekund
HALLUCINATIONS = ("dziękuję", "dzięki za obejrzenie", "napisy", "subskryb", "zapraszam na kanał",
                  "do zobaczenia", "amara.org")


def speech_chunks(audio, duration):
    """Fragmenty z mową na podstawie ciszy (ffmpeg silencedetect)."""
    r = subprocess.run(["ffmpeg", "-i", str(audio), "-af",
                        f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    silences = list(zip(starts, ends + [duration] * (len(starts) - len(ends))))
    chunks, cur = [], 0.0
    for a, b in silences:
        if a - cur > 0.15:
            chunks.append((cur, a))
        cur = b
    if duration - cur > 0.15:
        chunks.append((cur, duration))
    return chunks


def cut_audio(src, a, b, out):
    subprocess.run(["ffmpeg", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src),
                    "-c:a", "libmp3lame", "-b:a", "64k", str(out)], capture_output=True)
    return out.exists() and out.stat().st_size > 0


def norm_words(text):
    return [w for w in re.sub(r"[^\w\s]", " ", text.lower()).split() if w]


def find_warnings(chunks, words, duration):
    warn = []
    # a) powtórzony początek zdania w którymś z późniejszych fragmentów (powtórka nagrania)
    for i, c1 in enumerate(chunks):
        w1 = norm_words(c1["text"])
        for c2 in chunks[i + 1:]:
            w2 = norm_words(c2["text"])
            n = 0
            while n < min(len(w1), len(w2)) and w1[n] == w2[n]:
                n += 1
            if n >= 2 or (w1 and len(w1) <= 3 and w2[:len(w1)] == w1):
                warn.append(f"POWTÓRKA? fragment [{c1['start']:.2f}-{c1['end']:.2f}] \"{c1['text']}\" "
                            f"zaczyna się tak samo jak fragment od {c2['start']:.2f} s - "
                            f"pewnie wcześniejsze podejście do wycięcia")
                break
    # a2) przekleństwa - zwykle znak, że nagranie się posypało
    for c in chunks:
        if re.search(r"kurw|chuj|pierdol|jeb|szmat", c["text"].lower()):
            warn.append(f"PRZEKLEŃSTWO [{c['start']:.2f}-{c['end']:.2f}] \"{c['text']}\" - raczej do wycięcia")
    # b) powtórzone słowa pod rząd ("no i no i", "na na")
    ws = [(w["word"].lower().strip(".,!?"), w["start"]) for w in words]
    for i in range(len(ws) - 1):
        for n in (1, 2, 3):
            if i + 2 * n <= len(ws) and [x[0] for x in ws[i:i + n]] == [x[0] for x in ws[i + n:i + 2 * n]] \
                    and len("".join(x[0] for x in ws[i:i + n])) >= 2:
                warn.append(f"POWTÓRZONE SŁOWA? \"{' '.join(x[0] for x in ws[i:i + n])}\" x2 od {ws[i][1]:.2f} s")
                break
    # c) słowo rozciągnięte nienaturalnie (Whisper tak "przykrywa" powtórki i pauzy)
    for w in words:
        d = w["end"] - w["start"]
        limit = 2.5 if re.search(r"\d", w["word"]) else max(1.3, 0.3 + 0.12 * len(w["word"]))  # liczby mówi się dłużej
        if d > limit:
            warn.append(f"DŁUGIE SŁOWO? \"{w['word']}\" trwa {d:.1f} s ({w['start']:.2f}-{w['end']:.2f}) "
                        f"- sprawdź, czy tu nie ma powtórki albo przerwy")
    # d) długa przerwa w środku klipu
    for c1, c2 in zip(chunks, chunks[1:]):
        if c2["start"] - c1["end"] > 1.5:
            warn.append(f"DŁUGA PRZERWA {c2['start'] - c1['end']:.1f} s ({c1['end']:.2f}-{c2['start']:.2f})")
    # e) fragmenty, które są pewnie szumem, a nie mową
    for c in chunks:
        if c.get("szum"):
            warn.append(f"SZUM? [{c['start']:.2f}-{c['end']:.2f}] \"{c['text']}\" - raczej nie mowa")
    return list(dict.fromkeys(warn))


def main():
    if len(sys.argv) < 2:
        sys.exit("Użycie: python3 transkrybuj.py <folder dnia>")
    day = Path(sys.argv[1]).resolve()
    videos = sorted([p for p in day.iterdir() if p.suffix.lower() in VIDEO_EXT], key=natural_key)
    if not videos:
        sys.exit("Brak klipów w folderze.")
    key = api_key()

    wynik, lines = {}, []
    with tempfile.TemporaryDirectory() as tmp:
        for v in videos:
            a = Path(tmp) / (v.stem + ".mp3")
            if not extract_audio(v, a):
                lines.append(f"\n=== {v.name} === (brak dźwięku)")
                continue
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                        "-of", "csv=p=0", str(a)], capture_output=True, text=True).stdout or 0)
            chunks, words = [], []
            for j, (s0, e0) in enumerate(speech_chunks(a, dur)):
                ps, pe = max(0.0, s0 - 0.1), min(dur, e0 + 0.1)
                part = Path(tmp) / f"{v.stem}_{j}.mp3"
                if not cut_audio(a, ps, pe, part):
                    continue
                data = transcribe(part, key)
                text = data.get("text", "").strip()
                cw = [{"start": round(w["start"] + ps, 3), "end": round(w["end"] + ps, 3),
                       "word": w["word"].strip()} for w in data.get("words", [])]
                szum = (not text) or (any(h in text.lower() for h in HALLUCINATIONS) and len(norm_words(text)) <= 4)
                chunks.append({"start": round(s0, 2), "end": round(e0, 2), "text": text, "szum": szum})
                if not szum:
                    words += cw
            speech = [c for c in chunks if not c["szum"]]
            warnings = find_warnings(speech, words, dur) + \
                [w for w in find_warnings(chunks, [], dur) if w.startswith("SZUM")]
            wynik[v.name] = {"duration": dur, "text": " ".join(c["text"] for c in speech),
                             "chunks": chunks, "words": words, "warnings": warnings,
                             "segments": [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in speech]}
            lines.append(f"\n=== {v.name} ({dur:.1f} s) ===")
            if not chunks:
                lines.append("(brak mowy w klipie)")
            for c in chunks:
                lines.append(f"[{c['start']:6.2f} - {c['end']:6.2f}] {c['text']}" + ("   <- szum?" if c["szum"] else ""))
            for w in warnings:
                lines.append(f"  UWAGA: {w}")
            print(f"OK: {v.name} ({len(chunks)} fragm., uwag: {len(warnings)})", flush=True)

    (day / "transkrypcja.json").write_text(json.dumps(wynik, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    (day / "transkrypcja.txt").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(f"\nZapisano: {day / 'transkrypcja.txt'}")


if __name__ == "__main__":
    main()
