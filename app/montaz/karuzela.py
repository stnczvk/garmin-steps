#!/usr/bin/env python3
"""Karuzela na Instagram z dnia jedzenia: okładka, posiłki (najlepsza stop-klatka z klipu + karta z dziennika),
podsumowanie dnia i pytanie do widzów. Slajdy 1080x1350 (4:5) -> <zadanie>/gotowe/karuzela/01.jpg …

Użycie: python3 karuzela.py <folder dnia> <folder wyjściowy>

Stop-klatka: z każdego klipu z posiłkiem bierzemy kilkanaście klatek ze środka (bez początku i końca, gdzie zwykle
jest ruch telefonu) i wybieramy najlepszą: ostra (bez rozmycia ruchem), kolorowa (jedzenie, nie ściana), dobrze
naświetlona, z ostrością w środku kadru, gdzie zwykle leży talerz.
"""
try:
    import numpy as np
except ImportError:      # bez numpy – zwykła ścieżka ffmpeg
    np = None
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from montuj import FONT_PATH  # noqa: E402

W, H = 1080, 1350
YEL = (255, 214, 0)
INK = (17, 17, 17)


def font(size, weight=800):
    f = ImageFont.truetype(str(FONT_PATH), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def duration(clip):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


_TRC = {}


def _probe_color(clip):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer,color_primaries,pix_fmt",
                        "-of", "json", str(clip)], capture_output=True, text=True)
    try:
        return (json.loads(r.stdout).get("streams") or [{}])[0]
    except Exception:
        return {}


def transfer(clip):
    """HDR z iPhone'a (HLG/PQ) bez mapowania tonów wychodzi w JPG szaro i z dziwnymi kolorami."""
    k = str(clip)
    if k not in _TRC:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=color_transfer",
                            "-of", "csv=p=0", k], capture_output=True, text=True)
        t = r.stdout.strip().split(",")[0].strip()
        if t not in ("arib-std-b67", "smpte2084"):
            c = _probe_color(clip)
            if "10" in (c.get("pix_fmt") or "") and c.get("color_primaries") == "bt2020":
                t = "arib-std-b67"          # 10-bit BT.2020 bez tagu – iPhone HDR (HLG)
        _TRC[k] = t
    return _TRC[k]


HDR_VF = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
          "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def _hlg_srgb(x, peak=1000.0, white=203.0):
    """BT.2020 HLG (0..1) -> sRGB/BT.709 (0..1): OOTF jak w telefonie, łagodne mapowanie świateł (Reinhard).
    Liczone w numpy – nie zależy od tego, czy ffmpeg na serwerze ma zscale."""
    a, b, c = 0.17883277, 0.28466892, 0.55991073
    e = np.where(x <= 0.5, x * x / 3.0, (np.exp((x - c) / a) + b) / 12.0)
    y = 0.2627 * e[..., 0] + 0.6780 * e[..., 1] + 0.0593 * e[..., 2]
    d = e * np.power(np.maximum(y, 1e-6), 0.2)[..., None] * peak / white
    M = np.array([[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]])
    d = np.maximum(d @ M.T, 0)
    l = 0.2126 * d[..., 0] + 0.7152 * d[..., 1] + 0.0722 * d[..., 2]
    lm = peak / white
    d = d * ((l * (1 + l / (lm * lm)) / (1 + l)) / np.maximum(l, 1e-6))[..., None]
    d = np.clip(d, 0, 1)
    return np.where(d <= 0.0031308, 12.92 * d, 1.055 * np.power(d, 1 / 2.4) - 0.055)


def _frame_hlg(clip, t, width=None):
    vf = (f"scale={width}:-2:" if width else "scale=iw:ih:") + "in_color_matrix=bt2020:in_range=tv:out_range=pc,format=rgb48be"
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1", "-vf", vf,
                        "-f", "image2pipe", "-vcodec", "ppm", "-"], capture_output=True)
    raw = r.stdout
    if r.returncode != 0 or not raw.startswith(b"P6"):
        return None
    parts, i = [], 2
    while len(parts) < 3:                       # nagłówek PPM: szerokość, wysokość, maks.
        while raw[i:i + 1].isspace():
            i += 1
        j = i
        while not raw[j:j + 1].isspace():
            j += 1
        parts.append(int(raw[i:j])); i = j
    w, h, mx = parts
    px = np.frombuffer(raw[i + 1:i + 1 + w * h * 6], ">u2").reshape(h, w, 3).astype(np.float32) / mx
    return Image.fromarray((_hlg_srgb(px) * 255 + 0.5).astype(np.uint8))


MODE = {}


def frame(clip, t, width=None):
    if transfer(clip) == "arib-std-b67":         # iPhone HDR (HLG) – bez tego klatka jest szara / z „filtrem”
        try:
            im = _frame_hlg(clip, t, width)
            if im is not None:
                MODE[str(clip)] = "HLG→SDR (numpy)"
                return im
        except Exception as e:
            MODE[str(clip)] = f"HLG bez numpy ({e.__class__.__name__}) – zapas ffmpeg"
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        out = tf.name
    chain = [HDR_VF] if transfer(clip) in ("arib-std-b67", "smpte2084") else []
    if width:
        chain.append(f"scale={width}:-2")
    vf = ["-vf", ",".join(chain)] if chain else []
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1", *vf, "-q:v", "2", out],
                       capture_output=True)
    if r.returncode != 0 and chain and chain[0] == HDR_VF:   # ffmpeg bez zscale – zwykła klatka zamiast żadnej
        vf = ["-vf", f"scale={width}:-2"] if width else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(clip), "-frames:v", "1", *vf, "-q:v", "2", out],
                       capture_output=True)
    try:
        im = Image.open(out).convert("RGB")
        im.load()
        return im
    except Exception:
        return None
    finally:
        Path(out).unlink(missing_ok=True)


def score(im):
    """Ostrość (krawędzie, mocniej w środku), kolor (nasycenie), naświetlenie."""
    g = im.convert("L")
    w, h = g.size
    edges = g.filter(ImageFilter.FIND_EDGES)
    sharp_all = ImageStat.Stat(edges).var[0] ** 0.5
    c = edges.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    sharp_mid = ImageStat.Stat(c).var[0] ** 0.5
    sat = ImageStat.Stat(im.convert("HSV").split()[1]).mean[0]
    bright = ImageStat.Stat(g).mean[0]
    expo = 1.0 - min(1.0, abs(bright - 130) / 110)
    return {"sharp": 0.4 * sharp_all + 0.6 * sharp_mid, "sat": sat, "expo": expo}


def candidates(clip, n=12):
    """Kilkanaście klatek z klipu z oceną – do ręcznego wyboru zdjęcia w Montażowni."""
    d = duration(clip)
    if d <= 0:
        return []
    a, b = d * 0.08, d * 0.92
    ts = [a + (b - a) * i / (n - 1) for i in range(n)] if d > 2 else [d / 2]
    out = []
    for t in ts:
        im = frame(clip, t, 360)
        if im is not None:
            sc = score(im)
            out.append((t, im, 0.5 * sc["sharp"] + 0.35 * sc["sat"] + 15 * sc["expo"]))
    return out


def best_frame(clip, n=14):
    d = duration(clip)
    if d <= 0:
        return None
    a, b = d * 0.15, d * 0.85
    ts = [a + (b - a) * i / (n - 1) for i in range(n)] if d > 2 else [d / 2]
    cands = []
    for t in ts:
        im = frame(clip, t, 360)
        if im is not None:
            cands.append((t, score(im)))
    if not cands:
        return None
    mx = {k: max(c[1][k] for c in cands) or 1 for k in ("sharp", "sat")}
    best = max(cands, key=lambda c: 0.5 * c[1]["sharp"] / mx["sharp"] + 0.35 * c[1]["sat"] / mx["sat"] + 0.15 * c[1]["expo"])
    return frame(clip, best[0])


def cover(im, w=W, h=H):
    r = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def center_text(d, y, text, f, fill=(255, 255, 255)):
    tw = d.textlength(text, font=f)
    d.text(((W - tw) / 2, y), text, font=f, fill=fill)


def pill(d, x, y, text, f, fill=YEL, fg=INK):
    tw = d.textlength(text, font=f)
    hgt = f.size * 1.75
    d.rounded_rectangle([x, y, x + tw + 52, y + hgt], radius=hgt / 2, fill=fill)
    d.text((x + 26, y + hgt / 2), text, font=f, fill=fg, anchor="lm")


def shade(im, top=0.15, bottom=0.65):
    """Przyciemnienie od góry/dołu, żeby tekst i karta były czytelne."""
    g = Image.new("L", (1, H))
    for y in range(H):
        t = y / H
        g.putpixel((0, y), int(255 * (top * (1 - t) + bottom * t)))
    g = g.resize((W, H))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(black, im, g)


def meal_card(m):
    """Karta posiłku: nazwa, kcal, B/W/T i WSZYSTKIE produkty z dziennika (przy długiej liście mniejsza czcionka)."""
    items = [i for i in (m.get("items") or []) if i.get("name")]
    n = len(items)
    fs = 34 if n <= 5 else 30 if n <= 8 else 26 if n <= 12 else 22
    row = int(fs * 1.55)
    h = 236 + (44 + n * row if n else 0)
    img = Image.new("RGBA", (980, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 979, h - 1], radius=40, fill=(14, 14, 16, 238))
    d.text((44, 70), m.get("name", ""), font=font(58), fill="white", anchor="lm")
    d.text((936, 70), f"{m.get('kcal', 0)} kcal", font=font(58), fill=YEL, anchor="rm")
    x = 44
    for lab, k in (("B", "p"), ("W", "c"), ("T", "f")):
        t = f"{lab} {m.get(k, 0)} g"
        tw = d.textlength(t, font=font(36, 700))
        d.rounded_rectangle([x, 140, x + tw + 44, 204], radius=32, fill=(255, 255, 255, 30))
        d.text((x + 22, 172), t, font=font(36, 700), fill="white", anchor="lm")
        x += tw + 64
    if n:
        d.line([44, 232, 936, 232], fill=(255, 255, 255, 40), width=2)
        f, fb = font(fs, 500), font(fs, 700)
        y = 256
        for it in items:
            q = str(it.get("qty") or "").strip()
            kc = f"{it.get('kcal', 0)}"
            name = it["name"] + (f"  ·  {q}" if q else "")
            maxw = 892 - d.textlength(kc, font=fb) - 30
            while d.textlength(name, font=f) > maxw and len(name) > 4:   # tylko gdy naprawdę się nie mieści
                name = name[:-2].rstrip() + "…" if not name.endswith("…") else name[:-2].rstrip() + "…"
            d.text((44, y + row / 2), name, font=f, fill=(235, 235, 240), anchor="lm")
            d.text((936, y + row / 2), kc, font=fb, fill="white", anchor="rm")
            y += row
    return img


def main():
    day, out = Path(sys.argv[1]), Path(sys.argv[2])
    try:
        summ = json.loads((day / "podsumowanie.json").read_text(encoding="utf-8"))
    except Exception:
        print("karuzela: brak podsumowania z dziennika – pomijam")
        return 0
    karty = summ.get("karty") or {}
    # kolejność i karty PNG z planu montażu
    order, png = [], {}
    ko = day / "kolejnosc.txt"
    if ko.exists():
        for line in ko.read_text(encoding="utf-8").splitlines():
            try:
                toks = shlex.split(line, comments=True)
            except ValueError:
                continue
            names = [t for t in toks if "=" not in t]
            if not names:
                continue
            order.append(names[0])
            if len(names) > 1 and (day / names[1]).exists():
                png[names[0]] = day / names[1]
    meals = [c for c in order if c in karty] or list(karty)
    if not meals:
        print("karuzela: brak posiłków z kartami – pomijam")
        return 0
    out.mkdir(parents=True, exist_ok=True)
    if "--kandydaci" not in sys.argv:   # przy podglądzie kandydatów gotowe slajdy zostają
        for f in out.glob("*.jpg"):
            f.unlink()
    if "--kandydaci" in sys.argv:
        kd = out / "kand"; kd.mkdir(exist_ok=True)
        for f in kd.glob("*.jpg"):
            f.unlink()
        res = []
        for i, c in enumerate(meals):
            cs = candidates(day / c)
            items = []
            for k, (t, im, sc) in enumerate(cs):
                fn = f"{i}_{k}.jpg"; im.save(kd / fn, quality=82)
                items.append({"t": round(t, 2), "file": fn, "score": round(sc, 1)})
            res.append({"clip": c, "name": (karty.get(c) or {}).get("name", c), "cands": items})
        (out / "kandydaci.json").write_text(json.dumps({"meals": res}, ensure_ascii=False), encoding="utf-8")
        print(f"karuzela: kandydaci gotowi ({sum(len(r['cands']) for r in res)} klatek)")
        return 0
    try:
        wyb = json.loads((out / "wybor.json").read_text(encoding="utf-8"))
    except Exception:
        wyb = {}
    shots = {}
    for c in meals:
        im = frame(day / c, float(wyb[c])) if c in wyb else best_frame(day / c)
        if im is not None:
            shots[c] = im
    slides = []
    # 1. okładka: najbardziej kaloryczny posiłek – stop-klatka bez filtrów (naturalne światło),
    #    tekst na półprzezroczystym panelu zamiast przyciemniania/rozmywania całego zdjęcia
    top = max((c for c in meals if c in shots), key=lambda c: karty[c].get("kcal", 0), default=None)
    bg = cover(shots[top]) if top else Image.new("RGB", (W, H), (20, 20, 24))
    s = bg.copy().convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rounded_rectangle([60, 380, W - 60, 960], radius=44, fill=(12, 12, 14, 175))
    s.alpha_composite(ov)
    d = ImageDraw.Draw(s)
    pill(d, 60, 80, f"{summ.get('label', 'DZIEŃ')} · REDUKCJA", font(34))
    for i, line in enumerate(["Co zjadłem", "w jeden dzień", "na redukcji?"]):
        center_text(d, 440 + i * 122, line, font(96, 850))
    kc = f"{summ['kcal']:,}".replace(",", " ")
    center_text(d, 860, f"{kc} kcal  ·  {summ.get('p', 0)} g białka", font(46), YEL)
    pill(d, (W - d.textlength("przesuń >>", font=font(34, 600)) - 52) / 2, 1200, "przesuń >>", font(34, 600), fill=(12, 12, 14), fg=(255, 255, 255))
    s = s.convert("RGB")
    slides.append(s)
    # 2…: posiłki
    for i, c in enumerate(meals):
        s = cover(shots[c]) if c in shots else Image.new("RGB", (W, H), (20, 20, 24))
        d = ImageDraw.Draw(s)  # bez przyciemniania – zdjęcie zostaje naturalne, karta i licznik mają własne tło
        pill(d, 60, 70, f"{i + 1}/{len(meals)}", font(30))
        # własna karta ze WSZYSTKIMI produktami; PNG z dziennika tylko gdy nie mamy listy produktów
        card = meal_card(karty[c]) if (karty[c].get("items") or c not in png) else Image.open(png[c]).convert("RGBA")
        cw = 980
        card = card.resize((cw, round(card.height * cw / card.width)), Image.LANCZOS)
        if card.height > 900:
            card = card.resize((round(card.width * 900 / card.height), 900), Image.LANCZOS)
        s = s.convert("RGBA")
        s.alpha_composite(card, ((W - card.width) // 2, H - card.height - 60))
        slides.append(s.convert("RGB"))
    # podsumowanie dnia
    s = Image.new("RGB", (W, H), (14, 14, 16))  # plansza informacyjna – jednolite tło zamiast rozmytego zdjęcia
    d = ImageDraw.Draw(s)
    center_text(d, 150, "Podsumowanie dnia", font(72))
    center_text(d, 300, kc, font(170, 850), YEL)
    goal = summ.get("goal")
    center_text(d, 500, f"kcal{f'  ·  cel {goal}' if goal else ''}", font(42, 600), (230, 230, 235))
    y = 640
    for lab, k, gk in (("Białko", "p", "gp"), ("Węglowodany", "c", "gc"), ("Tłuszcze", "f", "gf")):
        v, g = summ.get(k, 0), summ.get(gk)
        g = g[-1] if isinstance(g, list) else g
        d.text((100, y), lab, font=font(40, 700), fill="white")
        d.text((980, y), f"{v} g", font=font(40, 700), fill="white", anchor="ra")
        d.rounded_rectangle([100, y + 66, 980, y + 84], radius=9, fill=(70, 70, 76))
        if g:
            p = max(0.02, min(1.0, v / g))
            d.rounded_rectangle([100, y + 66, 100 + 880 * p, y + 84], radius=9, fill=YEL)
        y += 170
    center_text(d, 1190, "Zapisz, żeby mieć pomysły na jutro", font(34, 600), (210, 210, 215))
    slides.append(s)
    # pytanie
    s = Image.new("RGB", (W, H), (14, 14, 16))
    d = ImageDraw.Draw(s)
    center_text(d, 450, "Który posiłek", font(92, 850))
    center_text(d, 570, "wybierasz?", font(92, 850))
    nums = ", ".join(str(i + 1) for i in range(len(meals) - 1)) + f" czy {len(meals)}" if len(meals) > 1 else "1"
    center_text(d, 740, f"{nums} – napisz w komentarzu", font(42, 700), YEL)
    slides.append(s)
    for i, s in enumerate(slides[:10]):
        s.convert("RGB").save(out / f"{i + 1:02d}.jpg", quality=90)
    for c in meals:
        print(f"karuzela: {c} – kolor {transfer(day / c) or '?'} · {MODE.get(str(day / c), 'zwykła klatka')}")
    print(f"karuzela: {len(slides[:10])} slajdów -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
