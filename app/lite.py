"""Lekkie kopie filmów do oglądania w centrum (540x960, mały bitrate) – oryginał 1080p/wysoki bitrate lagował przy odtwarzaniu.
Pobieranie i publikacja zawsze biorą oryginał."""
import hashlib, os, subprocess, threading
from pathlib import Path

DIR = Path(os.environ.get("SOCIAL_HOME", "/root/social")) / "podglady"
_lock, _busy = threading.Lock(), set()


def _dst(src):
    st = os.stat(src)
    return DIR / (hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:16] + ".mp4")


def _build(src, dst):
    try:
        with _lock:   # jedna kopia naraz, niski priorytet – nie przeszkadza montażom
            if dst.exists():
                return
            DIR.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".part.mp4")
            r = subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-v", "error", "-i", str(src),
                                "-vf", "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)':flags=bicubic", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                                "-maxrate", "2500k", "-bufsize", "5000k", "-pix_fmt", "yuv420p",
                                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)],
                               capture_output=True, timeout=1800)
            if r.returncode == 0:
                tmp.replace(dst)
                for old in sorted(DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)[:-25]:   # trzymamy ostatnie 25
                    old.unlink(missing_ok=True)
            else:
                tmp.unlink(missing_ok=True)
    finally:
        _busy.discard(str(src))


def lite(src):
    """Ścieżka do lekkiej kopii, jeśli gotowa; inaczej oryginał (a kopia robi się w tle)."""
    try:
        if os.path.getsize(src) < 15 * 1024 * 1024:
            return src
        dst = _dst(src)
        if dst.exists():
            return str(dst)
        if str(src) not in _busy:
            _busy.add(str(src))
            threading.Thread(target=_build, args=(src, dst), daemon=True).start()
    except Exception:
        pass
    return src
