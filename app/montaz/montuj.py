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
TITLE_TOP = 230            # odległość tytułu od górnej krawędzi (px)
YELLOW = (255, 214, 0)

NAPISY = True              # napisy z mowy (słowo, które właśnie mówisz, świeci na żółto)
NAPISY_Y = 1300            # środek napisów (px od góry)
NAPISY_SIZE = 80
NAPISY_SLOW = 3            # max słów naraz

ZOOM_NA_CIECIACH = 1.09    # „punch-in” co drugi fragment po wycięciu (1.0 = wyłączone)

LICZNIK = True             # licznik kalorii na górze (rośnie przy każdym posiłku, dane z dziennika)
LICZNIK_Y = 170

KARTY_Z_DZIENNIKA = False # karta posiłku rysowana z danych dziennika (zamiast screena)
KOLORY = True              # delikatna korekcja kolorów (kontrast, nasycenie, ciepło) – jedzenie apetyczniej
EFEKTY_DZWIEKOWE = True    # „whoosh” przy karcie posiłku, „pyk” przy liczniku
PASEK_POSTEPU = True       # cienki żółty pasek na górze, rośnie przez cały film
KONIEC_SECONDS = 2.0       # karta końcowa „Jutro dzień N + Obserwuj” (0 = bez)

PODSUMOWANIE_SECONDS = 4.0 # karta z kaloriami i makro dnia na końcu (z dziennika)

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
    """Tytuł na białych/żółtych paskach (wersja bez animacji)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("L", (1, 700))
    for yy in range(700):
        grad.putpixel((0, yy), int(128 * (1 - yy / 700)))
    shade = Image.new("RGBA", (W, 700), (0, 0, 0, 255))
    shade.putalpha(grad.resize((W, 700)))
    img.alpha_composite(shade)
    y = TITLE_TOP
    for i, line in enumerate(lines):
        f = font(TITLE_FONT_SIZE if i == 0 else int(TITLE_FONT_SIZE * 0.78), 800)
        bb = ImageDraw.Draw(img).textbbox((0, 0), line, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        px, py = (34, 18) if i == 0 else (28, 14)
        bw, bh = tw + 2 * px, th + 2 * py
        bar = Image.new("RGBA", (bw + 80, bh + 80), (0, 0, 0, 0))
        sh = Image.new("RGBA", bar.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle([40, 52, 40 + bw, 52 + bh], 18, fill=(0, 0, 0, 110))
        bar.alpha_composite(sh.filter(ImageFilter.GaussianBlur(14)))
        d = ImageDraw.Draw(bar)
        d.rounded_rectangle([40, 40, 40 + bw, 40 + bh], 18, fill=YELLOW if "kcal" in line.lower() else (255, 255, 255))
        d.text((40 + px - bb[0], 40 + py - bb[1]), line, font=f, fill=(17, 17, 17))
        bar = bar.rotate(-1.2 if i % 2 == 0 else 1.0, resample=Image.BICUBIC, expand=True)
        img.alpha_composite(bar, ((W - bar.width) // 2, y - 40))
        y += bh + 16
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
# ANIMACJE (Remotion) – tytuł wjeżdża linijkami, karty i naklejki „wskakują”.
# Gdy Remotion nie jest zainstalowany albo coś pójdzie nie tak, zostają zwykłe PNG.
# ---------------------------------------------------------------------------
GRAFIKI = HERE / "grafiki"
ANIM = {}          # ścieżka PNG -> (klip .mov z przezroczystością, przesunięcie x, y)


def animate(jobs, work):
    if not jobs or os.environ.get("MONTAZ_BEZ_ANIMACJI") or not (GRAFIKI / "node_modules").is_dir() \
            or not shutil.which("node"):
        return
    spec = work / "animacje.json"
    spec.write_text(json.dumps({"public": str(work), "jobs": [j["job"] for j in jobs]}, ensure_ascii=False))
    print("Animacje grafik…", flush=True)
    try:
        r = subprocess.run(["node", str(GRAFIKI / "render.mjs"), str(spec)], cwd=str(GRAFIKI),
                           capture_output=True, text=True, timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        print(f"  animacje: {e} – zostają zwykłe grafiki", flush=True)
        return
    for j in jobs:
        if Path(j["job"]["out"]).exists():
            ANIM[j["png"]] = (j["job"]["out"], j["dx"], j["dy"])
    msg = next((l for l in out.splitlines() if l.startswith("animacje:")), out.strip()[-300:])
    print(f"  {msg}", flush=True)


def anim_job(png, seconds, style, work):
    """Zadanie animacji dla gotowego PNG (karta/naklejka). Płótno większe o margines na „wyskok”."""
    w, h = Image.open(png).size
    m = int(max(w, h) * 0.08)
    return {"png": str(png), "dx": -m, "dy": -m, "job": {
        "comp": "Obraz", "out": str(work / (Path(png).stem + ".mov")),
        "props": {"width": w + 2 * m, "height": h + 2 * m, "imgW": w, "imgH": h, "seconds": seconds,
                  "src": Path(png).name, "style": style}}}


def title_job(lines, png, work):
    shutil.copy(FONT_PATH, work / "font_tytul.ttf")
    h = int(TITLE_TOP + len(lines) * (TITLE_FONT_SIZE * 1.4 + 20) + 160)
    return {"png": str(png), "dx": 0, "dy": 0, "job": {
        "comp": "Tytul", "out": str(work / "tytul.mov"),
        "props": {"width": W, "height": h, "seconds": TITLE_SECONDS, "lines": lines, "size": TITLE_FONT_SIZE,
                  "top": TITLE_TOP, "font": "font_tytul.ttf"}}}


def overlay_input(png, start, x, y, n, last):
    """Animowany klip zamiast PNG (jeśli jest): (wejścia, filtry) albo None."""
    a = ANIM.get(str(png))
    if not a:
        return None
    mov, dx, dy = a
    return (["-i", mov], [f"[{n}:v]setpts=PTS+{start:.3f}/TB[s{n}]",
                          f"[{last}][s{n}]overlay={x + dx}:{y + dy}:eof_action=pass[v{n}]"])


# ---------------------------------------------------------------------------
# NAPISY Z MOWY (z transkrypcji Groq) – po 1-3 słowa, aktualne słowo na żółto
# ---------------------------------------------------------------------------
def map_words(words, keep, speed, src_dur):
    """Czas słów z oryginalnego klipu -> czas w zmontowanym fragmencie (po cięciach i przyspieszeniu)."""
    pieces = keep or [(0.0, src_dur)]
    out = []
    for w in words:
        s0, e0 = float(w["start"]), float(w["end"])
        off = 0.0
        for a, b in pieces:
            if a - 0.05 <= s0 < b:
                out.append(((off + max(s0, a) - a) / speed, (off + min(e0, b) - a) / speed, w["word"]))
                break
            off += b - a
    return out


def _clean(word):
    return re.sub(r"^[\"'„”(\[]+|[\"'„”)\],.;:…]+$", "", word.strip())


def draw_caption(words, active, out, scale=1.0):
    size = NAPISY_SIZE
    while True:
        f = font(int(size * scale), 850)
        d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        space = d.textlength(" ", font=f)
        widths = [d.textlength(w, font=f) for w in words]
        total = sum(widths) + space * (len(words) - 1)
        if total <= 960 * scale or size < 50:
            break
        size -= 4
    img = Image.new("RGBA", (W, 320), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = (W - total) / 2
    sw = max(4, int(8 * scale))
    for i, (w, ww) in enumerate(zip(words, widths)):
        d.text((x, 160), w, font=f, anchor="lm", fill=YELLOW if i == active else (255, 255, 255),
               stroke_width=sw, stroke_fill=(0, 0, 0))
        x += ww + space
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sh.putalpha(img.getchannel("A").point(lambda v: int(v * 0.55)).filter(ImageFilter.GaussianBlur(10)))
    sh = Image.composite(Image.new("RGBA", img.size, (0, 0, 0, 255)), sh, sh.getchannel("A"))
    base = Image.new("RGBA", img.size, (0, 0, 0, 0))
    base.alpha_composite(sh, (0, 6))
    base.alpha_composite(img)
    base.save(out)


def ai_fix_words(trans, day):
    """Poprawia przekręcone słowa w napisach (np. „owszanymi” → „owsianymi”, „rozdzika” → „z dzika”) przez Claude.
    Czasy słów zostają. Wynik zapamiętany w napisy_poprawki.json. Bez klucza / sieci – napisy bez zmian."""
    import urllib.request, hashlib
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    clips = {n: [w["word"] for w in v.get("words", [])] for n, v in trans.items() if v.get("words")}
    if not key or not clips:
        return trans
    h = hashlib.sha1(json.dumps(clips, ensure_ascii=False).encode()).hexdigest()[:12]
    cf = day / "napisy_poprawki.json"
    fixes = None
    try:
        c = json.loads(cf.read_text(encoding="utf-8"))
        fixes = c["fixes"] if c.get("hash") == h else None
    except Exception:
        pass
    if fixes is None:
        prompt = ("Napisy do polskiego filmu o jedzeniu na redukcji (posiłki, kalorie, makro). Automatyczne rozpoznawanie mowy "
                  "przekręca słowa. Popraw TYLKO słowa źle rozpoznane (np. „owszanymi” → „owsianymi”, „rozdzika” → „z dzika”), "
                  "biorąc pod uwagę sens zdania. Nie zmieniaj poprawnych słów, liczb ani stylu mówienia. Jedno słowo może "
                  "zostać zastąpione krótką frazą, jeśli rozpoznawanie skleiło słowa.\n"
                  'Odpowiedz wyłącznie JSON-em: {"NAZWA_KLIPU": {"NUMER_SŁOWA": "poprawione"}} – tylko zmienione słowa.\n\n'
                  + "\n".join(f"### {n}\n" + " ".join(f"{i}:{w}" for i, w in enumerate(ws)) for n, ws in clips.items()))
        body = {"model": os.environ.get("MODEL_DEFAULT", "claude-sonnet-5"), "max_tokens": 3000,
                "system": "Odpowiadasz wyłącznie poprawnym JSON, bez żadnego tekstu przed ani po.",
                "messages": [{"role": "user", "content": prompt}]}
        try:
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                         headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                                  "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read().decode())
            txt = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
            fixes = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
            cf.write_text(json.dumps({"hash": h, "fixes": fixes}, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"Poprawki napisów (AI): pominięte ({e.__class__.__name__})", flush=True)
            return trans
    n = 0
    for name, fx in (fixes or {}).items():
        ws = trans.get(name, {}).get("words", [])
        for i, new in (fx or {}).items():
            try:
                i = int(i)
            except ValueError:
                continue
            if 0 <= i < len(ws) and str(new).strip() and str(new).strip() != ws[i]["word"]:
                ws[i]["word"] = str(new).strip(); n += 1
    print(f"Poprawki napisów (AI): {n} słów", flush=True)
    return trans


# słowa, które rozpoznawanie mowy notorycznie przekręca (końcówka słowa po normalizacji -> poprawka)
STALE_POPRAWKI = {"owszanymi": "owsianymi", "owszanych": "owsianych", "owszana": "owsiana", "owszanka": "owsianka",
                  "owszankę": "owsiankę", "rozdzika": "dzika", "zdzika": "z dzika", "skyrem": "skyrem"}
HALUCYNACJE = {"dzięki", "dziękuję", "dziękujemy", "napisy", "subskrybuj"}
# całe zwroty, które Whisper dopisuje w ciszy (zwykle na końcu klipu)
HALUCYNACJE_ZWROTY = [["dzięki", "za", "uwagę"], ["dziękuję", "za", "uwagę"], ["dzięki", "za", "obejrzenie"],
                      ["dziękuję", "za", "obejrzenie"], ["dziękuję", "bardzo"], ["dzięki", "za", "oglądanie"]]


def clean_words(trans):
    """Stałe poprawki słów + usuwanie „dzięki” wymyślonego przez rozpoznawanie mowy w ciszy."""
    nrm = lambda w: re.sub(r"[^\wąćęłńóśźż]", "", w.lower())
    for v in trans.values():
        ws = v.get("words", [])
        for i, w in enumerate(ws):
            k = nrm(w["word"])
            if k in STALE_POPRAWKI:
                fix = STALE_POPRAWKI[k]
                # „z rozdzika” -> „z dzika”, ale „sos rozdzika” -> „sos z dzika”
                if k == "rozdzika" and not (i and nrm(ws[i - 1]["word"]) == "z"):
                    fix = "z dzika"
                w["word"] = fix
            if k in HALUCYNACJE:
                gap_before = w["start"] - ws[i - 1]["end"] if i else 9
                gap_after = ws[i + 1]["start"] - w["end"] if i + 1 < len(ws) else 9
                if gap_before > 0.6 and gap_after > 0.6:      # samotne „dzięki” w ciszy = wymyślone
                    w["word"] = ""
        # zwroty-halucynacje: na końcu klipu albo po pauzie – usuwamy całe
        norm_ws = [nrm(w["word"]) for w in ws]
        for z in HALUCYNACJE_ZWROTY:
            for i in range(len(ws) - len(z) + 1):
                if norm_ws[i:i + len(z)] == z:
                    end = i + len(z)
                    gap_before = ws[i]["start"] - ws[i - 1]["end"] if i else 9
                    at_end = end >= len(ws) or all(not norm_ws[j] for j in range(end, len(ws)))
                    if at_end or gap_before > 0.5:
                        for j in range(i, end):
                            ws[j]["word"] = ""
    return trans


def user_subs(trans, day):
    """Zamiany słów w napisach wpisane w aplikacji (uwagi do montażu) – napisy_zamiany.json."""
    try:
        subs = json.loads((day / "napisy_zamiany.json").read_text(encoding="utf-8"))
    except Exception:
        return trans
    nrm = lambda w: re.sub(r"[^\wąćęłńóśźż]", "", w.lower())
    n = 0
    for s in subs:
        z = [nrm(x) for x in s["z"].split() if nrm(x)]
        if not z:
            continue
        for v in trans.values():
            ws = v.get("words", [])
            for i in range(len(ws) - len(z) + 1):
                if [nrm(ws[i + k]["word"]) for k in range(len(z))] == z:
                    ws[i]["word"] = s["na"]
                    for k in range(1, len(z)):
                        ws[i + k]["word"] = ""
                    n += 1
    if n:
        print(f"Zamiany słów z aplikacji: {n}", flush=True)
    return trans


def build_captions(words, hide, work):
    """words: [(start, end, słowo)] na osi całego filmu. Zwraca plik ffconcat z PNG albo None."""
    words = [(a, b, _clean(w).upper()) for a, b, w in sorted(words) if _clean(w)]
    words = [x for x in words if not any(h0 - 0.05 < x[0] < h1 for h0, h1 in hide)]
    if not words:
        return None
    groups, cur = [], []
    for i, wd in enumerate(words):
        if cur and (len(cur) >= NAPISY_SLOW or sum(len(x[2]) + 1 for x in cur) + len(wd[2]) > 16
                    or wd[0] - cur[-1][1] > 0.6 or any(h0 <= wd[0] and cur[-1][0] < h0 for h0, _ in hide)):
            groups.append(cur); cur = []
        cur.append(wd)
    groups.append(cur)
    events = []                                  # (start, koniec, png)
    n = 0
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else 1e9
        g_end = min(g[-1][1] + 0.35, nxt)
        for h0, h1 in hide:
            if g[0][0] < h0 < g_end:
                g_end = h0
        texts = [x[2] for x in g]
        for i, (a, b, _) in enumerate(g):
            st = g[0][0] if i == 0 else a
            en = g[i + 1][0] if i + 1 < len(g) else g_end
            if en - st < 0.04:
                continue
            if i == 0 and en - st > 0.15:      # „wskoczenie” całej grupy
                png = work / f"nap_{n}.png"; n += 1
                draw_caption(texts, i, png, 0.86)
                events.append((st, st + 0.07, png))
                st += 0.07
            png = work / f"nap_{n}.png"; n += 1
            draw_caption(texts, i, png)
            events.append((st, en, png))
    blank = work / "nap_pusty.png"
    Image.new("RGBA", (W, 320), (0, 0, 0, 0)).save(blank)
    lines, t = ["ffconcat version 1.0"], 0.0
    for a, b, png in events:
        if a < t:
            a = t
        if b - a < 0.02:
            continue
        if a - t > 0.01:
            lines += [f"file '{blank}'", f"duration {a - t:.3f}"]
        lines += [f"file '{png}'", f"duration {b - a:.3f}"]
        t = b
    lines += [f"file '{blank}'", "duration 1.0", f"file '{blank}'"]
    cf = work / "napisy.txt"
    cf.write_text("\n".join(lines) + "\n")
    print(f"Napisy: {len(groups)} grup, {len(words)} słów", flush=True)
    return cf


def draw_summary_png(d, out):
    """Podsumowanie dnia bez animacji."""
    img = Image.new("RGBA", (W, H), (8, 8, 10, 158))
    dr = ImageDraw.Draw(img)
    cy = 560
    dr.text((W / 2, cy), "PODSUMOWANIE DNIA", font=font(32, 700), fill=(255, 255, 255, 190), anchor="mm")
    dr.text((W / 2, cy + 90), d["label"], font=font(46, 800), fill="white", anchor="mm")
    dr.text((W / 2, cy + 270), f"{d['kcal']}", font=font(210, 900), fill=YELLOW, anchor="mm")
    dr.text((W / 2, cy + 410), "kcal" + (f"  ·  cel {d['goal']}" if d.get("goal") else ""), font=font(48, 700),
            fill="white", anchor="mm")
    tw = (W - 140 - 40) / 3
    for i, (l, v) in enumerate([("BIAŁKO", d["p"]), ("WĘGLE", d["c"]), ("TŁUSZCZ", d["fat"])]):
        x = 70 + i * (tw + 20)
        dr.rounded_rectangle([x, cy + 500, x + tw, cy + 700], 28, fill=(16, 16, 18, 215), outline=(255, 255, 255, 40), width=2)
        dr.text((x + 24, cy + 530), l, font=font(23, 700), fill=(255, 255, 255, 170))
        dr.text((x + 24, cy + 640), f"{v} g", font=font(70, 900), fill="white", anchor="ls")
    img.save(out)


LW, LH = 560, 150


def draw_counter(val, goal, out, hot=False):
    img = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([20, 26, LW - 20, LH - 14], 34, fill=(0, 0, 0, 120))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 18, LW - 20, LH - 22], 34, fill=(12, 12, 14, 215), outline=(255, 255, 255, 40), width=2)
    fn, fs = font(66, 900), font(34, 700)
    num = f"{val}"
    rest = f" / {goal} kcal" if goal else " kcal"
    w1, w2 = d.textlength(num, font=fn), d.textlength(rest, font=fs)
    x = (LW - w1 - w2) / 2
    d.text((x, 76), num, font=fn, fill=YELLOW if hot else (255, 255, 255), anchor="ls")
    d.text((x + w1, 76), rest, font=fs, fill=(255, 255, 255, 170), anchor="ls")
    if goal:
        d.rounded_rectangle([60, 94, LW - 60, 104], 5, fill=(255, 255, 255, 40))
        p = max(0.0, min(1.0, val / goal))
        if p > 0.01:
            d.rounded_rectangle([60, 94, 60 + (LW - 120) * p, 104], 5, fill=YELLOW)
    img.save(out)


def build_counter(adds, goal, start, end, work):
    """adds: [(czas, kcal)] – licznik od 0, przy każdym posiłku szybko odlicza w górę."""
    adds = sorted((t, k) for t, k in adds if start <= t < end)
    if not adds:
        return None
    lines, t, val, n = ["ffconcat version 1.0"], 0.0, 0, 0
    blank = work / "lic_pusty.png"
    Image.new("RGBA", (LW, LH), (0, 0, 0, 0)).save(blank)

    def put(png, a, b):
        nonlocal t
        if b - a > 0.005:
            lines.extend([f"file '{png}'", f"duration {b - a:.3f}"])
            t = b

    put(blank, 0.0, start)
    cur = work / "lic_start.png"; draw_counter(0, goal, cur)   # osobna nazwa – lic_0 to pierwsza klatka odliczania
    for at, k in adds:
        put(cur, t, at)
        steps = 14
        for i in range(1, steps + 1):
            v = round(val + k * (1 - (1 - i / steps) ** 3))
            png = work / f"lic_{n}.png"; n += 1
            draw_counter(v, goal, png, hot=True)
            put(png, t, t + 0.045)
        val += k
        cur = work / f"lic_{n}.png"; n += 1
        draw_counter(val, goal, cur)
    put(cur, t, end)
    lines += [f"file '{blank}'", "duration 1.0", f"file '{blank}'"]
    cf = work / "licznik.txt"
    cf.write_text("\n".join(lines) + "\n")
    print(f"Licznik kalorii: {len(adds)} posiłków, razem {val} kcal", flush=True)
    return cf


def draw_meal_card(meal, out):
    """Karta posiłku z danych dziennika: nazwa, kalorie, produkty, B/W/T. Zwraca (x, y) na ekranie."""
    cw, pad = 880, 46
    items = [i for i in (meal.get("items") or []) if i.get("name")][:5]
    ch = pad + 76 + 22 + len(items) * 52 + (18 if items else 0) + 120 + pad
    img = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([40, 52, 40 + cw, 52 + ch], 44, fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22)))
    d = ImageDraw.Draw(img)
    x0, y0 = 40, 40
    d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 44, fill=(16, 16, 18, 238), outline=(255, 255, 255, 34), width=2)
    y = y0 + pad
    d.text((x0 + pad, y + 60), str(meal.get("name") or "Posiłek"), font=font(60, 800), fill="white", anchor="ls")
    kc = f"{int(meal.get('kcal') or 0)}"
    fk, fu = font(70, 900), font(32, 700)
    wu = d.textlength(" kcal", font=fu)
    d.text((x0 + cw - pad - wu, y + 60), kc, font=fk, fill=YELLOW, anchor="rs")
    d.text((x0 + cw - pad, y + 60), " kcal", font=fu, fill=(255, 255, 255, 210), anchor="rs")
    y += 76 + 22
    fi = font(34, 500)
    for it in items:
        name = str(it["name"])
        while d.textlength(name, font=fi) > cw - 2 * pad - 140 and len(name) > 4:
            name = name[:-2]
        if name != str(it["name"]):
            name = name.rstrip() + "…"
        d.text((x0 + pad, y + 38), name, font=fi, fill=(255, 255, 255, 220), anchor="ls")
        d.text((x0 + cw - pad, y + 38), str(int(it.get("kcal") or 0)), font=font(34, 700), fill=(255, 255, 255, 235), anchor="rs")
        y += 52
    if items:
        y += 18
    tw = (cw - 2 * pad - 2 * 18) / 3
    for k, (lab, key) in enumerate([("B", "p"), ("W", "c"), ("T", "f")]):
        tx = x0 + pad + k * (tw + 18)
        d.rounded_rectangle([tx, y, tx + tw, y + 100], 24, fill=(36, 36, 40, 245))
        d.text((tx + 24, y + 66), lab, font=font(30, 700), fill=(255, 255, 255, 150), anchor="ls")
        d.text((tx + 66, y + 70), f"{int(meal.get(key) or 0)}", font=font(56, 900), fill="white", anchor="ls")
        vw = d.textlength(f"{int(meal.get(key) or 0)}", font=font(56, 900))
        d.text((tx + 72 + vw, y + 70), "g", font=font(28, 700), fill=(255, 255, 255, 190), anchor="ls")
    img.save(out)
    return (W - img.width) // 2, (H - img.height) // 2 - 60


def koniec_job(nxt, png, work):
    shutil.copy(FONT_PATH, work / "font_tytul.ttf")
    return {"png": str(png), "dx": 0, "dy": 0, "job": {
        "comp": "Koniec", "out": str(work / "koniec.mov"),
        "props": {"width": W, "height": H, "seconds": KONIEC_SECONDS, "font": "font_tytul.ttf", "next": nxt}}}


def draw_koniec_png(nxt, out):
    img = Image.new("RGBA", (W, H), (8, 8, 10, 150))
    d = ImageDraw.Draw(img)
    d.text((W / 2, 780), "JUTRO", font=font(36, 700), fill=(255, 255, 255, 200), anchor="mm")
    t = f"Dzień {nxt}" if nxt else "Kolejny dzień"
    f = font(100, 900)
    tw = d.textlength(t, font=f)
    d.rounded_rectangle([W / 2 - tw / 2 - 42, 850, W / 2 + tw / 2 + 42, 990], 22, fill="white")
    d.text((W / 2, 922), t, font=f, fill=(17, 17, 17), anchor="mm")
    c = "Obserwuj, żeby nie przegapić"
    fc_ = font(44, 800)
    cw_ = d.textlength(c, font=fc_)
    d.rounded_rectangle([W / 2 - cw_ / 2 - 42, 1060, W / 2 + cw_ / 2 + 42, 1150], 45, fill=YELLOW)
    d.text((W / 2, 1106), c, font=fc_, fill=(17, 17, 17), anchor="mm")
    img.save(out)


def make_sfx(work):
    """Krótkie efekty dźwiękowe generowane na miejscu (bez plików z internetu)."""
    wh, pop = work / "sfx_whoosh.wav", work / "sfx_pop.wav"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:duration=0.45:amplitude=0.6",
         "-af", "highpass=f=500,lowpass=f=5000,afade=t=in:d=0.18,afade=t=out:st=0.2:d=0.25,volume=0.35",
         "-ar", "44100", "-ac", "2", str(wh)])
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=1150:duration=0.09",
         "-af", "afade=t=out:st=0.0:d=0.09,volume=0.30", "-ar", "44100", "-ac", "2", str(pop)])
    return wh, pop


def summary_job(d, png, work):
    shutil.copy(FONT_PATH, work / "font_tytul.ttf")
    return {"png": str(png), "dx": 0, "dy": 0, "job": {
        "comp": "Podsumowanie", "out": str(work / "podsumowanie.mov"),
        "props": {"width": W, "height": H, "seconds": PODSUMOWANIE_SECONDS, "font": "font_tytul.ttf",
                  "cta": "Obserwuj – jutro kolejny dzień", **d}}}


def load_summary(day):
    """podsumowanie.json (zapisuje go zrob_dzien.py z danych dziennika)."""
    try:
        d = json.loads((day / "podsumowanie.json").read_text(encoding="utf-8"))
        if int(d.get("kcal") or 0) <= 0:
            return None
        return {"label": str(d.get("label") or "DZIEŃ"), "kcal": int(d["kcal"]), "goal": d.get("goal"),
                "p": int(d.get("p") or 0), "c": int(d.get("c") or 0), "fat": int(d.get("f") or 0),
                "gp": d.get("gp"), "gc": d.get("gc"), "gf": d.get("gf"), "train": bool(d.get("train")), "karty": d.get("karty") or {},
                "licznik": {k: int(v) for k, v in (d.get("licznik") or {}).items() if v}}
    except Exception:
        return None


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
            f"crop={W}:{H},setsar=1,fps={FPS},unsharp=5:5:{SHARPEN}:5:5:0"
            + (",eq=contrast=1.05:saturation=1.16,colorbalance=rm=0.025:bm=-0.025:rh=0.015:bh=-0.02" if KOLORY else ""))


def render_segment(video, shots, title_png, workdir, idx, keep=None, tail=0.0):
    dur, has_audio, transfer = probe(video)
    src_dur = dur
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
    extra = max(0.0, need + 0.8 - dur) + tail  # za krótki klip / podsumowanie -> zamrożenie ostatniej klatki
    total = dur + extra

    inputs = ["-i", str(video)]
    fc = pre + [f"[{vsrc}]" + (f"setpts=PTS/{speed}," if speed != 1.0 else "") + base_filter(transfer)
          + (f",tpad=stop_mode=clone:stop_duration={extra:.3f}" if extra > 0 else "")
          + "[v0]"]
    last, n = "v0", 1
    if keep and len(keep) > 1 and ZOOM_NA_CIECIACH > 1.0:
        # co drugi fragment po cięciu lekko przybliżony – cięcie wygląda na zamierzone
        zw, zh = int(W * ZOOM_NA_CIECIACH) // 2 * 2, int(H * ZOOM_NA_CIECIACH) // 2 * 2
        t, wins = 0.0, []
        for i, (a, b) in enumerate(keep):
            if i % 2:
                wins.append(f"between(t,{t:.3f},{t + (b - a) / speed - 0.001:.3f})")
            t += (b - a) / speed
        fc.append(f"[v0]split[za][zb];[zb]scale={zw}:{zh}:flags=bicubic,crop={W}:{H}[zc];"
                  f"[za][zc]overlay=0:0:enable='{'+'.join(wins)}'[vz]")
        last = "vz"

    if title_png:
        an = overlay_input(title_png, 0.0, 0, 0, n, last)
        if an:
            inputs += an[0]; fc += an[1]
        else:
            inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(title_png)]
            fc.append(f"[{last}][{n}:v]overlay=0:0:enable='lte(t,{TITLE_SECONDS})'[v{n}]")
        last, n = f"v{n}", n + 1

    start = total - need - tail
    for png, (x, y) in shots:
        s, e = start, start + SHOT_SECONDS
        an = overlay_input(png, s, x, y, n, last)
        if an:
            inputs += an[0]; fc += an[1]
        else:
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
    return out, total, speed, keep, src_dur, total - need - tail


def finalize(segments, total, workdir, music, out_path, captions=None, summary_png=None, counter=None,
             koniec_png=None, sfx=()):
    lst = workdir / "lista.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segments))
    joined = workdir / "polaczone.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])

    inputs, fc, last, n = ["-i", str(joined)], [], "0:v", 1
    if captions:
        inputs += ["-f", "concat", "-safe", "0", "-i", str(captions)]
        fc.append(f"[{last}][{n}:v]overlay=0:{NAPISY_Y - 160}:eof_action=pass[c{n}]")
        last, n = f"c{n}", n + 1
    if counter:
        inputs += ["-f", "concat", "-safe", "0", "-i", str(counter)]
        fc.append(f"[{last}][{n}:v]overlay={(W - LW) // 2}:{LICZNIK_Y}:eof_action=pass[c{n}]")
        last, n = f"c{n}", n + 1
    if summary_png:
        st = max(0.0, total - PODSUMOWANIE_SECONDS - (KONIEC_SECONDS if koniec_png else 0))
        a = ANIM.get(str(summary_png))
        if a:
            inputs += ["-i", a[0]]
            fc.append(f"[{n}:v]setpts=PTS+{st:.3f}/TB[q{n}]")
            fc.append(f"[{last}][q{n}]overlay=0:0:eof_action=pass[c{n}]")
        else:
            inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(summary_png)]
            fc.append(f"[{n}:v]format=rgba,fade=t=in:st={st:.3f}:d=0.25:alpha=1[q{n}]")
            fc.append(f"[{last}][q{n}]overlay=0:0:enable='gte(t,{st:.3f})'[c{n}]")
        last, n = f"c{n}", n + 1
    if koniec_png:
        st = max(0.0, total - KONIEC_SECONDS)
        a = ANIM.get(str(koniec_png))
        if a:
            inputs += ["-i", a[0]]
            fc.append(f"[{n}:v]setpts=PTS+{st:.3f}/TB[q{n}]")
            fc.append(f"[{last}][q{n}]overlay=0:0:eof_action=pass[c{n}]")
        else:
            inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(koniec_png)]
            fc.append(f"[{n}:v]format=rgba,fade=t=in:st={st:.3f}:d=0.2:alpha=1[q{n}]")
            fc.append(f"[{last}][q{n}]overlay=0:0:enable='gte(t,{st:.3f})'[c{n}]")
        last, n = f"c{n}", n + 1
    if PASEK_POSTEPU:
        inputs += ["-f", "lavfi", "-i", f"color=c=0xFFD600:s={W}x10:r={FPS}:d={total + 1:.3f}"]
        fc.append(f"[{last}]drawbox=x=0:y=0:w=iw:h=10:color=black@0.35:t=fill[pb{n}]")
        fc.append(f"[pb{n}][{n}:v]overlay=x='-w+w*t/{total:.3f}':y=0:shortest=1[c{n}]")
        last, n = f"c{n}", n + 1

    fw = Image.open(workdir / "follow.png").width
    lw = Image.open(workdir / "like.png").width
    f_start = max(0.0, total - FOLLOW_SECONDS - LIKE_SECONDS)
    l_start = total - LIKE_SECONDS
    for png, st, en, x in [(workdir / "follow.png", f_start, l_start, W - fw - 50),
                           (workdir / "like.png", l_start, total, W - lw - 50)]:
        a = ANIM.get(str(png))
        if a:
            inputs += ["-i", a[0]]
            fc += [f"[{n}:v]setpts=PTS+{st:.3f}/TB[p{n}]",
                   f"[{last}][p{n}]overlay={x + a[1]}:{230 + a[2]}:eof_action=pass[c{n}]"]
        else:
            inputs += ["-loop", "1", "-t", f"{total:.3f}", "-i", str(png)]
            fc.append(f"[{last}][{n}:v]overlay={x}:230:enable='between(t,{st:.3f},{en:.3f})'[c{n}]")
        last, n = f"c{n}", n + 1
    fc.append(f"[{last}]null[v]")
    if music and Path(music).exists():
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        fc.append(f"[{n}:a]volume={MUSIC_VOLUME},aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
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
        n += 1
    else:
        amap = "0:a"
    if sfx:      # efekty dźwiękowe: [(czas, plik)]
        labels = []
        for k, (t, f) in enumerate(sfx):
            inputs += ["-i", str(f)]
            ms = int(max(0, t) * 1000)
            fc.append(f"[{n}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
                      f"adelay={ms}|{ms}[sx{k}]")
            labels.append(f"[sx{k}]"); n += 1
        base = amap if amap.startswith("[") else f"[{amap}]"
        fc.append(f"{base}aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[ab]")
        fc.append(f"[ab]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:normalize=0[asfx]")
        amap = "[asfx]"

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
        draw_pill("FOLLOW", "check", work / "follow.png")
        draw_pill("LIKE", "heart", work / "like.png")
        prepared_all = []
        for i, (video, shots, keep) in enumerate(plan):
            prepared = []
            for j, s in enumerate(shots):
                png = work / f"shot_{i}_{j}.png"
                prepared.append((png, prepare_screenshot(s, png)))
            prepared_all.append(prepared)
        summary = load_summary(day)
        summary_png = None
        if summary:
            summary_png = work / "podsumowanie.png"
            draw_summary_png(summary, summary_png)
            if KARTY_Z_DZIENNIKA:       # karty posiłków z danych dziennika zamiast screenów
                for i, (video, shots, keep) in enumerate(plan):
                    meal = summary["karty"].get(video.name)
                    if meal:
                        png = work / f"karta_dz_{i}.png"
                        prepared_all[i] = [(png, draw_meal_card(meal, png))]
        m_n = re.search(r"dzie[nń]\s*(\d+)", " ".join(title), re.I)
        koniec_png = None
        if KONIEC_SECONDS > 0:
            koniec_png = work / "koniec.png"
            draw_koniec_png(int(m_n.group(1)) + 1 if m_n else None, koniec_png)
        try:
            trans = json.loads((day / "transkrypcja.json").read_text(encoding="utf-8")) if NAPISY else {}
        except Exception:
            trans = {}
        trans = ai_fix_words(trans, day)
        trans = user_subs(trans, day)
        trans = clean_words(trans)
        jobs = [title_job(title, title_png, work)]
        if summary:
            jobs.append(summary_job(dict(summary, cta="Zapisz, jeśli też redukujesz"), summary_png, work))
        if koniec_png:
            jobs.append(koniec_job(int(m_n.group(1)) + 1 if m_n else None, koniec_png, work))
        jobs += [anim_job(png, SHOT_SECONDS, "karta", work) for pr in prepared_all for png, _ in pr]
        jobs += [anim_job(work / "follow.png", FOLLOW_SECONDS, "naklejka", work),
                 anim_job(work / "like.png", LIKE_SECONDS, "naklejka", work)]
        animate(jobs, work)

        segments, total, cap_words, hide, adds, sfx_at = [], 0.0, [], [], [], []
        licz = (summary or {}).get("licznik") or {}
        for i, (video, shots, keep) in enumerate(plan):
            prepared = prepared_all[i]
            tail = ((PODSUMOWANIE_SECONDS if summary else 0.0) + (KONIEC_SECONDS if koniec_png else 0.0)) \
                if i == len(plan) - 1 else 0.0
            seg, d, speed, keep, src_dur, card_at = render_segment(
                video, prepared, title_png if i == 0 else None, work, i, keep, tail)
            segments.append(seg)
            for a, b, w in map_words(trans.get(video.name, {}).get("words", []), keep, speed, src_dur):
                cap_words.append((total + a, total + b, w))
            if LICZNIK and licz.get(video.name):
                if prepared:     # licznik rośnie tylko razem z kartą posiłku
                    adds.append((total + card_at + 0.3, licz[video.name]))
            if prepared and EFEKTY_DZWIEKOWE:
                sfx_at.append((total + card_at, "whoosh"))
            if prepared:
                hide.append((total + card_at, total + card_at + len(prepared) * SHOT_SECONDS))
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
        end_extra = KONIEC_SECONDS if koniec_png else 0.0
        if summary:
            hide.append((total - PODSUMOWANIE_SECONDS - end_extra, total + 1))
        if koniec_png:
            hide.append((total - end_extra, total + 1))
        captions = build_captions(cap_words, hide, work) if cap_words else None
        counter = None
        if adds:
            end = total - (PODSUMOWANIE_SECONDS if summary else 0) - end_extra
            adds = [(max(t, TITLE_SECONDS + 0.2), k) for t, k in adds]
            counter = build_counter(adds, summary.get("goal"), TITLE_SECONDS, end, work)
        sfx = []
        if sfx_at:
            wh, pop = make_sfx(work)
            sfx = [(t, wh) for t, _ in sfx_at] + [(t, pop) for t, _ in adds if t < total - end_extra]
        finalize(segments, total, work, None if args.bez_muzyki else args.muzyka, out_path,
                 captions, summary_png, counter, koniec_png, sfx)
        print(f"\nGOTOWE ({total:.0f} s): {out_path}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
