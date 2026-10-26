"""Uruchamia montaż jednego dnia na serwerze i zapisuje postęp do status.json.

Użycie: python3 montaz_job.py <folder zadania>
Folder zadania: <id>/dzien_N/ (klipy, karty posiłków, opis.txt), wynik w <id>/gotowe/.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "montaz"


def main():
    job = Path(sys.argv[1]).resolve()
    status = job / "status.json"
    st = json.loads(status.read_text()) if status.exists() else {}

    def save(**kw):
        st.update(kw, at=time.time())
        status.write_text(json.dumps(st, ensure_ascii=False))

    day = next((p for p in job.iterdir() if p.is_dir() and p.name.startswith("dzien_")), None)
    if day is None:
        save(state="error", error="Brak folderu z klipami.")
        return 1
    save(state="working", step="Transkrypcja mowy…", started=time.time())
    log = (job / "log.txt").open("w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-u", str(TOOLS / "zrob_dzien.py"), str(day)],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    tail = []
    for line in p.stdout:
        log.write(line)
        log.flush()
        line = line.rstrip()
        tail = (tail + [line])[-40:]
        if line.startswith("Montaż"):
            save(step="Montaż…")
        m = re.match(r"\[(\d+)/(\d+)\]", line)
        if m:
            save(step=f"Montaż: klip {m.group(1)} z {m.group(2)}")
        if line.startswith("OK:") and "fragm" in line:
            save(step="Transkrypcja: " + line[4:].split(" (")[0])
    rc = p.wait()
    log.close()
    films = sorted((job / "gotowe").glob("*.mp4")) if (job / "gotowe").is_dir() else []
    rap = (day / "raport.txt").read_text(encoding="utf-8") if (day / "raport.txt").exists() else ""
    if rc == 0 and films:
        f = films[-1]
        save(state="done", step="Gotowe", film=f.name, size=f.stat().st_size, raport=rap, finished=time.time())
        # karuzela na Instagram z tego samego dnia (najlepsze stop-klatki + karty) – po filmie, nie blokuje go
        try:   # numpy potrzebny do zdjęć z iPhone'a (HDR) – doinstaluj, jeśli go brak
            subprocess.run([sys.executable, "-c", "import numpy"], check=True, capture_output=True)
        except Exception:
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", "numpy"], capture_output=True, timeout=300)
        try:
            r = subprocess.run([sys.executable, str(TOOLS / "karuzela.py"), str(day), str(job / "gotowe" / "karuzela")],
                               capture_output=True, text=True, timeout=600)
            with (job / "log.txt").open("a", encoding="utf-8") as lf:
                lf.write(r.stdout + r.stderr[-1500:])
        except Exception as e:
            with (job / "log.txt").open("a", encoding="utf-8") as lf:
                lf.write(f"karuzela: {e}\n")
        return 0
    err = next((l for l in reversed(tail) if l.strip()), "nieznany błąd")
    save(state="error", error=err[:300], raport=rap, log="\n".join(tail[-15:]))
    return 1


if __name__ == "__main__":
    sys.exit(main())
