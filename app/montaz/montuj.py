#!/usr/bin/env python3
"""
MONTUJ DZIEŃ - automatyczny montaż daily filmu jedzeniowego (styl "Redukcja dzień X").

Co robi:
  1. Bierze wszystkie klipy i zrzuty ekranu (Fitatu) z folderu dnia
  2. Układa je po nazwie pliku (IMG_1234, IMG_1235... = kolejność nagrywania z iPhone'a)
  3. Każdy zrzut ekranu "przykleja" do klipu nagranego tuż przed nim i pokazuje go
     na środku ekranu pod koniec tego klipu
  4. Na pierwszym klipie wstawia tytuł na górze (z pliku opis.txt)
  5. Na końcu dokłada naklejki FOLLOW i LIKE
  6. Podkłada muzykę w tle i zapisuje gotowy film 1080x1920 do folderu "gotowe"

Użycie:
  python3 montuj.py DailyContent/dzien_20

Folder dnia:
  dzien_20/
    IMG_4001.MOV      <- śniadanie (klip)
    IMG_4002.PNG      <- zrzut z Fitatu do śniadania
    IMG_4003.MOV      <- kawa (klip bez zrzutu = zwykły przerywnik)
    IMG_4004.MOV      <- obiad
    IMG_4005.PNG      <- zrzut do obiadu
    opis.txt          <- tytuł, np. 3 linijki:
                           Redukcja dzień 20
                           2100 kcal
                           dzień treningowy
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------------------
# USTAWIENIA STYLU - tu możesz śmiało zmieniać wartości
# ---------------------------------------------------------------------------
W, H, FPS = 1080, 1920, 30

TITLE_SECONDS = 3.5        # jak długo tytuł jest na ekranie na początku
TITLE_FONT_SIZE = 78
TITLE_TOP = 150            # odległość tytułu od górnej krawędzi (px)

SHOT_SECONDS = 3.0         # jak długo zrzut z Fitatu jest widoczny
SHOT_WIDTH = 720           # szerokość zrzutu na ekranie (px, cały ekran = 1080)
SHOT_CORNER = 30           # zaokrąglenie rogów zrzutu

FOLLOW_SECONDS = 1.8       # naklejka FOLLOW
LIKE_SECONDS = 1.8         # naklejka LIKE (na samym końcu)

MUSIC_VOLUME = 0.07        # głośność muzyki (0-1) - cicho, żeby był słychać Twój głos
MUSIC_DUCKING = True       # muzyka dodatkowo przycicha, kiedy mówisz
CLIP_SOUND_VOLUME = 1.0    # Twój głos / dźwięk z klipów (1.0 = pełna głośność)

SPEEDUP = 1.2              # przyspieszenie dłuższych klipów
SPEEDUP_ABOVE = 15.0       # ...ale tylko tych dłuższych niż tyle sekund (0 = wszystkie)

SHARPEN = 0.6              # wyostrzenie obrazu (0 = brak, 1 = mocne)
FINAL_CRF = 16             # jakość końcowa (mniej = lepiej i większy plik; 16 = bardzo dobra)

VIDEO_EXT = {".mov", ".mp4", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".webp"}

HERE = Path(__file__).resolve().parent
# na serwerze: font i muzyka leżą w /root/dziennik/montaz (ścieżki podaje serwer przez zmienne)
FONT_PATH = Path(os.environ.get("MONTAZ_FONT") or HERE / "Montserrat.ttf")
if not FONT_PATH.exists():
    FONT_PATH = HERE / "fonts" / "BarlowCondensed-Bold.ttf"
DEFAULT_MUSIC = Path(os.environ.get("MONTAZ_MUSIC") or HERE / "muzyka_tlo.mp3")


# ---------------------------------------------------------------------------
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("\nBŁĄD ffmpeg:\n", " ".join(str(c) for c in cmd), "\n", r.stderr[-2500:])
        sys.exit(1)
    return r


def font(size, weight=700):
    f = ImageFont.truetype(str(FONT_PATH), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def probe(path):
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,color_transfer",
             "-of", "json", str(path)])
    data = json.loads(r.stdout)
    dur = float(data["format"]["duration"])
    streams = data.get("streams", [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    transfer = next((s.get("color_transfer", "") for s in streams
                     if s.get("codec_type") == "video"), "")
    return dur, has_audio, transfer


# ---------------------------------------------------------------------------
# GRAFIKI
# ---------------------------------------------------------------------------
def draw_title_png(lines, out):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    f = font(TITLE_FONT_SIZE, 650)
    d = ImageDraw.Draw(img)
    y = TITLE_TOP
    positions = []
    for line in lines:
        bb = d.textbbox((0, 0), line, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        positions.append(((W - tw) / 2 - bb[0], y - bb[1]))
        y += th + 22
    # miękki cień pod tekstem (jak w TikToku)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    for (x, yy), line in zip(positions, lines):
        ds.text((x, yy), line, font=f, fill=(0, 0, 0, 200),
                stroke_width=6, stroke_fill=(0, 0, 0, 200))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)
    for (x, yy), line in zip(positions, lines):
        d.text((x, yy), line, font=f, fill="white",
               stroke_width=3, stroke_fill=(20, 20, 20))
    img.save(out)


def prepare_screenshot(src, out):
    """Zrzut ekranu: skalowanie, zaokrąglone rogi, cień. Zwraca (x, y) pozycji."""
    im = Image.open(src).convert("RGBA")
    scale = SHOT_WIDTH / im.width
    new_w, new_h = SHOT_WIDTH, int(im.height * scale)
    max_h = H - 260
    if new_h > max_h:                      # bardzo długi zrzut - dopasuj do wysokości
        scale = max_h / im.height
        new_w, new_h = int(im.width * scale), max_h
    im = im.resize((new_w, new_h), Image.LANCZOS)

    mask = Image.new("L", (new_w, new_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, new_w - 1, new_h - 1],
                                           SHOT_CORNER, fill=255)
    im.putalpha(mask)

    pad = 40
    canvas = Image.new("RGBA", (new_w + 2 * pad, new_h + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 10, pad + new_w, pad + new_h + 10],
                                         SHOT_CORNER, fill=(0, 0, 0, 140))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    canvas = Image.alpha_composite(canvas, sh)
    canvas.alpha_composite(im, (pad, pad))
    canvas.save(out)
    x = (W - canvas.width) // 2
    y = (H - canvas.height) // 2
    return x, y


def draw_pill(text, icon, out):
    """Naklejka w stylu TikToka: czarna pigułka z białym napisem i ikoną."""
    f = font(66, 800)
    tmp = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bb = tmp.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    icon_w, gap, px, ph = 60, 22, 48, 118
    w = px + icon_w + gap + tw + px
    img = Image.new("RGBA", (w, ph), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, ph - 1], ph // 2, fill=(0, 0, 0, 235))
    cx, cy = px, ph // 2
    if icon == "check":
        d.line([(cx + 4, cy + 2), (cx + 22, cy + 20), (cx + 56, cy - 18)],
               fill="white", width=11, joint="curve")
    else:  # serce
        r = 15
        d.ellipse([cx + 2, cy - 20, cx + 2 + 2 * r, cy - 20 + 2 * r], fill="white")
        d.ellipse([cx + 28, cy - 20, cx + 28 + 2 * r, cy - 20 + 2 * r], fill="white")
        d.polygon([(cx + 1, cy - 4), (cx + 59, cy - 4), (cx + 30, cy + 26)], fill="white")
    d.text((px + icon_w + gap - bb[0], (ph - th) / 2 - bb[1]), text, font=f, fill="white")
    img.save(out)
    return img.size


# ---------------------------------------------------------------------------
# WIDEO
# ---------------------------------------------------------------------------
def base_filter(transfer):
    """Skalowanie do 1080x1920 (wypełnienie + przycięcie). HDR z iPhone'a -> SDR."""
    hdr = ""
    if transfer in ("arib-std-b67", "smpte2084"):
        hdr = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")
    return (f"{hdr}scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={W}:{H},setsar=1,fps={FPS},unsharp=5:5:{SHARPEN}:5:5:0")


def render_segment(video, shots, title_png, workdir, idx, keep=None):
    dur, has_audio, transfer = probe(video)
    pre = []                                   # filtry wycinające fragmenty (jeśli są)
    vsrc, asrc = "0:v", "0:a"
    if keep:
        keep = [(max(0.0, a), min(dur, b)) for a, b in keep if min(dur, b) - max(0.0, a) > 0.05]
        if not keep:
            sys.exit(f"{video.name}: po wycięciu nic nie zostało")
        k = len(keep)
        use_audio = has_audio and CLIP_SOUND_VOLUME > 0
        pre.append(f"[0:v]split={k}" + "".join(f"[vs{i}]" for i in range(k)))
        if use_audio:
            pre.append(f"[0:a]asplit={k}" + "".join(f"[as{i}]" for i in range(k)))
        for i, (a, b) in enumerate(keep):
            pre.append(f"[vs{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vt{i}]")
            if use_audio:
                fo = max(0.0, b - a - 0.03)    # krótkie wyciszenie na cięciu = brak trzasków
                pre.append(f"[as{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                           f"afade=t=in:d=0.03,afade=t=out:st={fo:.3f}:d=0.03[at{i}]")
        pairs = "".join(f"[vt{i}]" + (f"[at{i}]" if use_audio else "") for i in range(k))
        pre.append(f"{pairs}concat=n={k}:v=1:a={1 if use_audio else 0}[vc]" + ("[ac]" if use_audio else ""))
        vsrc, asrc = "vc", "ac"
        dur = sum(b - a for a, b in keep)
    speed = SPEEDUP if dur > SPEEDUP_ABOVE else 1.0   # dłuższe klipy -> przyspieszenie
    dur = dur / speed
    need = len(shots) * SHOT_SECONDS
    extra = max(0.0, need + 0.8 - dur)         # za krótki klip -> zamrożenie ostatniej klatki
    total = dur + extra

    inputs = ["-i", str(video)]
    fc = pre + [f"[{vsrc}]" + (f"setpts=PTS/{speed}," if speed != 1.0 else "") + base_filter(transfer)
          + (f",tpad=stop_mode=clone:stop_duration={extra:.3f}" if extra > 0 else "")
          + "[v0]"]
    last, n = "v0", 1

    if title_png:
        inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(title_png)]
        fc.append(f"[{last}][{n}:v]overlay=0:0:enable='lte(t,{TITLE_SECONDS})'[v{n}]")
        last, n = f"v{n}", n + 1

    start = total - need
    for png, (x, y) in shots:
        s, e = start, start + SHOT_SECONDS
        inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(png)]
        fc.append(f"[{n}:v]format=rgba,fade=t=in:st={s:.3f}:d=0.2:alpha=1[s{n}]")
        fc.append(f"[{last}][s{n}]overlay={x}:{y}:enable='between(t,{s:.3f},{e:.3f})'[v{n}]")
        last, n = f"v{n}", n + 1
        start = e

    # audio: dźwięk klipu (albo cisza), zawsze stereo 44.1k - żeby dało się skleić
    if has_audio and CLIP_SOUND_VOLUME > 0:
        tempo = f"atempo={speed}," if speed != 1.0 else ""   # szybciej, ale bez zmiany wysokości głosu
        fc.append(f"[{asrc}]{tempo}volume={CLIP_SOUND_VOLUME},aformat=sample_rates=44100:"
                  f"channel_layouts=stereo,apad[a]")
        amap = "[a]"
    else:
        inputs += ["-f", "lavfi", "-t", f"{total:.3f}",
                   "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        amap = f"{n}:a"

    out = workdir / f"seg_{idx:03d}.mp4"
    run(["ffmpeg", "-y", "-threads", "0", *inputs,
         "-filter_complex", ";".join(fc),
         "-map", f"[{last}]", "-map", amap, "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2", str(out)])
    return out, total, speed, keep


def finalize(segments, total, workdir, music, out_path):
    lst = workdir / "lista.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segments))
    joined = workdir / "polaczone.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])

    fw, _ = draw_pill("FOLLOW", "check", workdir / "follow.png")
    lw, _ = draw_pill("LIKE", "heart", workdir / "like.png")
    f_start = max(0.0, total - FOLLOW_SECONDS - LIKE_SECONDS)
    l_start = total - LIKE_SECONDS

    inputs = ["-i", str(joined),
              "-loop", "1", "-t", f"{total:.3f}", "-i", str(workdir / "follow.png"),
              "-loop", "1", "-t", f"{total:.3f}", "-i", str(workdir / "like.png")]
    fc = [f"[0:v][1:v]overlay={W - fw - 50}:230:enable='between(t,{f_start:.3f},{l_start:.3f})'[v1]",
          f"[v1][2:v]overlay={W - lw - 50}:230:enable='gte(t,{l_start:.3f})'[v]"]
    if music and Path(music).exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        fc.append(f"[3:a]volume={MUSIC_VOLUME},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                  f"afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[m0]")
        if MUSIC_DUCKING:
            # głos steruje przyciszaniem muzyki (sidechain) - gdy mówisz, muzyka schodzi w dół
            fc.append("[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                      "asplit=2[voice][key]")
            fc.append("[m0][key]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=400[m]")
            voice = "[voice]"
        else:
            fc.append("[m0]anull[m]")
            voice = "[0:a]"
        # czysta suma głos + muzyka (bez przyciszania głosu) - działa tak samo w każdej wersji ffmpeg
        fc.append(f"{voice}[m]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c3[a]")
        amap = "[a]"
    else:
        amap = "0:a"

    run(["ffmpeg", "-y", "-threads", "0", *inputs, "-filter_complex", ";".join(fc),
         "-map", "[v]", "-map", amap, "-t", f"{total:.3f}",
         "-c:v", "libx264", "-preset", "medium", "-crf", str(FINAL_CRF), "-profile:v", "high",
         "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
         "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(out_path)])




# ---------------------------------------------------------------------------
def parse_cuts(opts, line):
    """od=3.2  do=20  wytnij=8.1-10.4  (wytnij można podać kilka razy albo po przecinku).
    Zwraca listę fragmentów DO ZOSTAWIENIA [(start, koniec), ...] albo None."""
    if not opts:
        return None
    start, end, cuts = 0.0, 1e9, []
    try:
        for o in opts:
            k, v = o.split("=", 1)
            k = k.lower()
            if k == "od":
                start = float(v.replace(",", "."))
            elif k == "do":
                end = float(v.replace(",", "."))
            elif k == "wytnij":
                for part in v.split(";"):
                    for rng in part.split(","):
                        a, b = rng.split("-")
                        cuts.append((float(a), float(b)))
            else:
                raise ValueError(k)
    except ValueError:
        sys.exit(f"kolejnosc.txt: nie rozumiem linijki: {line.strip()}")
    keep, cur = [], start
    for a, b in sorted(cuts):
        if b <= cur or a >= end:
            continue
        if a > cur:
            keep.append((cur, a))
        cur = max(cur, b)
    if cur < end:
        keep.append((cur, end))
    return keep


def main():
    ap = argparse.ArgumentParser(description="Montuje daily film jedzeniowy z folderu dnia.")
    ap.add_argument("folder", help="folder dnia, np. DailyContent/dzien_20")
    ap.add_argument("--muzyka", default=str(DEFAULT_MUSIC), help="plik muzyki (mp3/wav)")
    ap.add_argument("--bez-muzyki", action="store_true", help="bez muzyki (dodasz dźwięk w TikToku)")
    ap.add_argument("--tytul", default=None, help="tytuł, linijki rozdzielone znakiem |  (zamiast opis.txt)")
    ap.add_argument("--nazwa", default=None, help="nazwa pliku wynikowego bez .mp4 (domyślnie nazwa folderu)")
    args = ap.parse_args()

    day = Path(args.folder).resolve()
    if not day.is_dir():
        sys.exit(f"Nie ma folderu: {day}")

    files = sorted([p for p in day.iterdir() if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT
                    and not p.name.startswith(".")], key=natural_key)
    if not any(p.suffix.lower() in VIDEO_EXT for p in files):
        sys.exit("W folderze nie ma żadnych klipów wideo.")

    kolejnosc = day / "kolejnosc.txt"
    if kolejnosc.exists():
        # ręczna kolejność: każda linijka = klip, a po nim (opcjonalnie) jego zrzut(y), np.
        #   IMG_5168.MOV IMG_5189.PNG
        #   IMG_5177.MOV
        plan = []
        for line in kolejnosc.read_text(encoding="utf-8").splitlines():
            try:
                tokens = shlex.split(line, comments=True)   # nazwy ze spacjami w cudzysłowie
            except ValueError:
                sys.exit(f"kolejnosc.txt: zła linijka: {line.strip()}")
            if not tokens:
                continue
            names = [t for t in tokens if "=" not in t]
            opts = [t for t in tokens if "=" in t]
            paths = [day / n for n in names]
            missing = [p.name for p in paths if not p.exists()]
            if missing:
                sys.exit(f"kolejnosc.txt: nie ma pliku {', '.join(missing)}")
            plan.append([paths[0], paths[1:], parse_cuts(opts, line)])
        files = []
    else:
        plan = []

    # zrzut ekranu -> doklejony do ostatniego klipu przed nim
    pending = []
    for p in files:
        if p.suffix.lower() in VIDEO_EXT:
            plan.append([p, pending, None])
            pending = []
        elif plan:
            plan[-1][1].append(p)
        else:
            pending.append(p)          # zrzut przed pierwszym klipem -> do pierwszego klipu

    opis = day / "opis.txt"
    if args.tytul:
        title = [l.strip() for l in args.tytul.split("|") if l.strip()]
    elif opis.exists():
        title = [l.strip() for l in opis.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        title = [day.name.replace("_", " ").capitalize()]

    work = Path(tempfile.mkdtemp(prefix="montuj_"))
    try:
        title_png = work / "tytul.png"
        draw_title_png(title, title_png)

        segments, total = [], 0.0
        for i, (video, shots, keep) in enumerate(plan):
            prepared = []
            for j, s in enumerate(shots):
                png = work / f"shot_{i}_{j}.png"
                prepared.append((png, prepare_screenshot(s, png)))
            seg, d, speed, keep = render_segment(video, prepared, title_png if i == 0 else None, work, i, keep)
            segments.append(seg)
            total += d
            info = f" + zrzut: {', '.join(s.name for s in shots)}" if shots else ""
            if keep:
                info += "  (wycięte fragmenty, zostaje: " + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in keep) + ")"
            if speed != 1.0:
                info += f"  (przyspieszony x{speed})"
            print(f"[{i + 1}/{len(plan)}] {video.name}{info}", flush=True)

        out_dir = day.parent / "gotowe"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"{args.nazwa or day.name}.mp4"
        finalize(segments, total, work, None if args.bez_muzyki else args.muzyka, out_path)
        print(f"\nGOTOWE ({total:.0f} s): {out_path}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
