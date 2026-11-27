"""Render shortów faceless (9:16): „Teoria w 30 sekund” (quiz z prawa jazdy) i „Rekrutacja bez Stresu”. PIL + ffmpeg."""
import math, shutil, subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
FD = Path(__file__).parent / "faceless_fonts"
BGC, YEL, RED, GRN, GREY = (16, 20, 32), (255, 200, 0), (210, 55, 55), (45, 175, 95), (44, 50, 66)
_FC = {}


def F(sz, w="Black"):
    k = (sz, w)
    if k not in _FC:
        _FC[k] = ImageFont.truetype(str(FD / f"Inter-{w}.otf"), sz)
    return _FC[k]


def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]).strip())


def wrap(d, text, font, maxw):
    out, line = [], ""
    for w in str(text).split():
        t = (line + " " + w).strip()
        if d.textlength(t, font=font) <= maxw:
            line = t
        else:
            if line:
                out.append(line)
            line = w
    if line:
        out.append(line)
    return out


def text_c(d, y, text, font, fill="white", maxw=940, gap=1.18):
    for ln in wrap(d, text, font, maxw):
        w = d.textlength(ln, font=font)
        d.text(((W - w) / 2, y), ln, font=font, fill=fill)
        y += int(font.size * gap)
    return y


def fit(d, text, maxw, maxh, w="Black", start=96, low=40):
    """Największa czcionka, przy której tekst mieści się w ramce."""
    for sz in range(start, low - 1, -4):
        f = F(sz, w)
        if len(wrap(d, text, f, maxw)) * sz * 1.18 <= maxh:
            return f
    return F(low, w)


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def pill(d, txt, y=90):
    f = F(36, "ExtraBold")
    w = d.textlength(txt, font=f)
    d.rounded_rectangle(((W - w) / 2 - 30, y, (W + w) / 2 + 30, y + 60), 30, fill=YEL)
    d.text(((W - w) / 2, y + 8), txt, font=f, fill=BGC)


def mix(parts, total, out):
    ins, flt = [], []
    for i, (p, st, v) in enumerate(parts):
        ins += ["-i", str(p)]
        ms = int(st * 1000)
        flt.append(f"[{i}:a]aresample=44100,adelay={ms}|{ms},volume={v}[a{i}]")
    n = len(parts)
    flt.append("".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0,apad,atrim=0:{total:.2f}[o]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *ins, "-filter_complex", ";".join(flt), "-map", "[o]", "-ar", "44100", "-ac", "2", str(out)], check=True)


def beep(out, f=880, d=0.12):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={f}:duration={d}",
                    "-af", f"afade=out:st={d - 0.05:.2f}:d=0.05", str(out)], check=True)


def encode(frame_fn, total, audio, out, tmp):
    """Klatki prosto do ffmpeg przez pipe (bez tysięcy plików na dysku)."""
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
                          "-i", str(audio), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                          "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
    try:
        for i in range(int(total * FPS)):
            p.stdin.write(frame_fn(i / FPS).tobytes())
        p.stdin.close()
    except BrokenPipeError:
        pass
    if p.wait():
        raise RuntimeError("ffmpeg: błąd kodowania")


# ================================================================ TEORIA: skrzyżowanie
SW, SH = 1000, 820
CX, CY, R = SW // 2, SH // 2, 130
DIRS = {"S": (0, -1), "N": (0, 1), "E": (-1, 0), "W": (1, 0)}   # skąd przyjeżdża -> kierunek jazdy
COLS = {"czerwony": (225, 50, 50), "niebieski": (40, 110, 230), "zielony": (40, 170, 80), "zolty": (240, 190, 20),
        "bialy": (235, 235, 235), "tramwaj": (230, 120, 20), "rower": (120, 60, 200)}


def _car(color, long=False):
    h = 260 if long else 170
    im = Image.new("RGBA", (96, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((4, 4, 92, h - 4), 26, fill=color, outline=(20, 20, 20), width=4)
    d.rounded_rectangle((16, 34, 80, 70), 10, fill=(170, 215, 240))
    d.rounded_rectangle((18, h - 50, 78, h - 24), 8, fill=(140, 185, 210))
    d.rectangle((10, 6, 26, 14), fill=(255, 245, 180))
    d.rectangle((70, 6, 86, 14), fill=(255, 245, 180))
    return im


def _bike(color):
    im = Image.new("RGBA", (60, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((24, 6, 36, 114), 6, fill=(30, 30, 30))
    d.ellipse((12, 40, 48, 80), fill=color, outline=(20, 20, 20), width=3)
    d.line((8, 30, 52, 30), fill=(30, 30, 30), width=6)
    return im


def _sign(d, x, y, kind):
    if kind == "ustap":
        d.polygon([(x - 34, y - 28), (x + 34, y - 28), (x, y + 32)], fill="white", outline=(200, 20, 20), width=8)
    elif kind == "stop":
        pts = [(x + 34 * math.cos(math.radians(22.5 + 45 * i)), y + 34 * math.sin(math.radians(22.5 + 45 * i))) for i in range(8)]
        d.polygon(pts, fill=(200, 20, 20), outline="white", width=4)
        d.text((x - 24, y - 11), "STOP", font=F(18), fill="white")
    elif kind == "pierwszenstwo":
        d.polygon([(x, y - 36), (x + 36, y), (x, y + 36), (x - 36, y)], fill="white", outline=(30, 30, 30), width=3)
        d.polygon([(x, y - 22), (x + 22, y), (x, y + 22), (x - 22, y)], fill=(250, 200, 0))
    elif kind == "zielone":
        d.rounded_rectangle((x - 18, y - 46, x + 18, y + 46), 8, fill=(25, 25, 25))
        for i, c in enumerate([(70, 20, 20), (70, 60, 10), (60, 230, 90)]):
            d.ellipse((x - 11, y - 38 + i * 27, x + 11, y - 16 + i * 27), fill=c)


def _vadd(a, b, k=1):
    return (a[0] + b[0] * k, a[1] + b[1] * k)


def _path(skad, manewr):
    d = DIRS[skad]
    r = (-d[1], d[0])
    c = (CX, CY)
    lane = _vadd(c, r, 65)
    start = _vadd(lane, d, -300)
    if manewr == "prosto":
        return [("L", start, _vadd(lane, d, 620))]
    d2 = r if manewr == "prawo" else (-r[0], -r[1])
    r2 = (-d2[1], d2[0])
    lane2 = _vadd(c, r2, 65)
    ent = _vadd(lane, d, -R)
    ext = _vadd(lane2, d2, R)
    X = (CX + r[0] * 65 + r2[0] * 65, CY + r[1] * 65 + r2[1] * 65)
    return [("L", start, ent), ("Q", ent, X, ext), ("L", ext, _vadd(lane2, d2, 620))]


def _pt(path, t):
    """Pozycja i kąt na ścieżce, t 0..1 (segmenty po równo długości)."""
    def seglen(s):
        if s[0] == "L":
            return math.dist(s[1], s[2])
        return sum(math.dist(_q(s, i / 10)[0], _q(s, (i + 1) / 10)[0]) for i in range(10))
    L = [seglen(s) for s in path]
    tot, x = sum(L), t * sum(L)
    for s, l in zip(path, L):
        if x <= l or s is path[-1]:
            u = min(1.0, x / l) if l else 1.0
            if s[0] == "L":
                p = _vadd(s[1], ((s[2][0] - s[1][0]) * u, (s[2][1] - s[1][1]) * u))
                dx, dy = s[2][0] - s[1][0], s[2][1] - s[1][1]
                return p, math.degrees(math.atan2(-dx, -dy))
            return _q(s, u)
        x -= l
    return path[-1][-1], 0


def _q(s, t):
    p0, p1, p2 = s[1], s[2], s[3]
    x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
    y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
    dx = 2 * (1 - t) * (p1[0] - p0[0]) + 2 * t * (p2[0] - p1[0])
    dy = 2 * (1 - t) * (p1[1] - p0[1]) + 2 * t * (p2[1] - p1[1])
    return (x, y), math.degrees(math.atan2(-dx, -dy))


def _base(scena):
    im = Image.new("RGB", (SW, SH), (96, 160, 80))
    d = ImageDraw.Draw(im)
    wloty = scena.get("wloty") or ["N", "S", "E", "W"]
    road = (70, 72, 78)
    d.rectangle((CX - R, CY - R, CX + R, CY + R), fill=road)
    for k in wloty:
        if k == "N": d.rectangle((CX - R, 0, CX + R, CY), fill=road)
        if k == "S": d.rectangle((CX - R, CY, CX + R, SH), fill=road)
        if k == "E": d.rectangle((CX, CY - R, SW, CY + R), fill=road)
        if k == "W": d.rectangle((0, CY - R, CX, CY + R), fill=road)
    for y in range(0, SH, 60):
        if abs(y + 15 - CY) > R + 10 and (("N" in wloty and y < CY) or ("S" in wloty and y > CY)):
            d.rectangle((CX - 4, y, CX + 4, y + 30), fill="white")
    for x in range(0, SW, 60):
        if abs(x + 15 - CX) > R + 10 and (("W" in wloty and x < CX) or ("E" in wloty and x > CX)):
            d.rectangle((x, CY - 4, x + 30, CY + 4), fill="white")
    if scena.get("tory"):   # tory tramwajowe na osi pionowej
        for dx in (-22, 22):
            d.rectangle((CX + dx - 3, 0, CX + dx + 3, SH), fill=(150, 150, 150))
    pos = {"S": (CX + R + 50, CY + R + 60), "N": (CX - R - 50, CY - R - 60), "E": (CX + R + 60, CY - R - 50), "W": (CX - R - 60, CY + R + 50)}
    for k, v in (scena.get("znaki") or {}).items():
        if k in pos and v:
            _sign(d, *pos[k], v)
    return im


def teoria_scene_fn(scena):
    base = _base(scena)
    poj = []
    for p in scena.get("pojazdy") or []:
        col = COLS.get(p.get("kolor"), (200, 200, 200))
        typ = p.get("typ") or "auto"
        spr = _bike(col) if typ == "rower" else _car(col, long=typ == "tramwaj")
        poj.append({**p, "spr": spr, "path": _path(p.get("skad", "S"), p.get("manewr", "prosto"))})
    order = scena.get("kolejnosc") or [p.get("kolor") for p in poj]

    def draw(t_anim, show_paths=True):
        sc = base.copy()
        ov = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for p in poj:
            k = (order.index(p.get("kolor")) if p.get("kolor") in order else len(order))
            u = 0 if t_anim is None else ease((t_anim - k * 1.5) / 2.2)
            p["_u"] = u
            if show_paths and u == 0:
                pts = [_pt(p["path"], 0.12 + 0.5 * i / 30)[0] for i in range(31)]
                c = COLS.get(p.get("kolor"), (200, 200, 200))
                od.line(pts, fill=c + (170,), width=12, joint="curve")
                (ex, ey), a = _pt(p["path"], 0.62)
                ar = math.radians(a)
                fx, fy = -math.sin(ar), -math.cos(ar)
                od.polygon([(ex + fx * 26, ey + fy * 26), (ex - fy * 20, ey + fx * 20), (ex + fy * 20, ey - fx * 20)], fill=c + (200,))
        sc.paste(ov, (0, 0), ov)
        d = ImageDraw.Draw(sc)
        for p in poj:
            (x, y), a = _pt(p["path"], 0.12 + 0.88 * p["_u"])
            s = p["spr"].rotate(a, expand=True, resample=Image.BICUBIC)
            sc.paste(s, (int(x - s.width / 2), int(y - s.height / 2)), s)
            if p.get("ty") and p["_u"] < 0.05:
                tw = d.textlength("TY", font=F(40))
                d.text((x - tw / 2, y - 24), "TY", font=F(40), fill="white", stroke_width=5, stroke_fill="black")
        return sc
    return draw


def karta_scene_fn(karta):
    def draw(t_anim, show_paths=True):
        sc = Image.new("RGB", (SW, SH), (28, 34, 52))
        d = ImageDraw.Draw(sc)
        big = str(karta.get("duzy_tekst") or "?")
        f = F(220 if len(big) <= 6 else 150 if len(big) <= 10 else 110)
        lines = wrap(d, big, f, 900)
        y = SH / 2 - len(lines) * f.size * 0.6 - 40
        for ln in lines:
            w = d.textlength(ln, font=f)
            d.text(((SW - w) / 2, y), ln, font=f, fill=YEL)
            y += f.size * 1.15
        pod = karta.get("podpis") or ""
        if pod:
            ff = F(48, "Bold")
            for ln in wrap(d, pod, ff, 880):
                w = d.textlength(ln, font=ff)
                d.text(((SW - w) / 2, y + 10), ln, font=ff, fill=(220, 225, 235))
                y += 60
        return sc
    return draw


def render_teoria(sc, aud, out, tmp):
    """sc: skrypt z AI, aud: {hook,q,a,cta: (plik, długość)}"""
    tmp = Path(tmp)
    t_hook = 0.2
    t_q = t_hook + aud["hook"][1] + 0.15
    t_cd = t_q + aud["q"][1] + 0.2
    t_a = t_cd + 3.0
    t_cta = t_a + max(aud["a"][1], 5.5) + 0.3
    total = t_cta + aud["cta"][1] + 0.6
    beep(tmp / "b1.wav", 880)
    beep(tmp / "b2.wav", 1320, 0.3)
    mix([(aud["hook"][0], t_hook, 1), (aud["q"][0], t_q, 1), (aud["a"][0], t_a, 1), (aud["cta"][0], t_cta, 1)]
        + [(tmp / "b1.wav", t_cd + i, 0.5) for i in range(3)] + [(tmp / "b2.wav", t_a - 0.05, 0.5)], total, tmp / "audio.wav")
    scene = teoria_scene_fn(sc["scena"]) if sc.get("typ") == "skrzyzowanie" else karta_scene_fn(sc.get("karta") or {})
    odp = sc["odpowiedzi"][:3]
    ok = int(sc.get("poprawna", 0))
    bg = Image.new("RGB", (W, H), BGC)
    prev = {}

    def frame(t):
        im = bg.copy()
        d = ImageDraw.Draw(im)
        pill(d, "TEORIA W 30 SEKUND")
        hk = sc.get("hook_ekran") or "Zdałbyś to?"
        if t >= t_a:
            hk = sc.get("werdykt_ekran") or ("Odpowiedź: " + odp[ok])
        if t >= t_cta:
            hk = "Trafiłeś?"
        text_c(d, 185 if len(wrap(d, hk, fit(d, hk, 980, 130, start=92), 980)) == 1 else 175, hk, fit(d, hk, 980, 130, start=92), maxw=980, fill=GRN if t_a <= t < t_cta else "white")
        ta = None if t < t_a else t - t_a
        key = "s" if ta is None else round(ta * FPS)
        if key not in prev:
            prev.clear()
            prev[key] = scene(ta, show_paths=ta is None)
        im.paste(prev[key], (40, 330))
        d.rounded_rectangle((40, 330, 1040, 1150), 30, outline="white", width=6)
        if t_cd <= t < t_a:
            n = 3 - int(t - t_cd)
            fr = (t - t_cd) % 1
            r = int(150 * (1.15 - 0.15 * ease(fr * 4)))
            d.ellipse((540 - r, 740 - r, 540 + r, 740 + r), fill=BGC, outline=YEL, width=12)
            fn = F(190)
            w = d.textlength(str(n), font=fn)
            d.text((540 - w / 2, 740 - 120), str(n), font=fn, fill=YEL)
        if t >= t_q - 0.1:
            q = sc.get("pytanie_ekran") or ""
            text_c(d, 1180, q, fit(d, q, 960, 230, "Bold", 58, 40), maxw=960)
            n = len(odp)
            bh = 170 if n == 2 else 110
            for i, lab in enumerate(odp):
                if n == 2:
                    box = (90 + i * 480, 1440, 510 + i * 480, 1440 + bh)
                else:
                    box = (90, 1420 + i * 128, 990, 1420 + i * 128 + bh)
                col = (40, 46, 62)
                if t >= t_a:
                    col = GRN if i == ok else (120, 40, 44)
                d.rounded_rectangle(box, 30, fill=col, outline="white", width=5)
                fl = fit(d, lab, box[2] - box[0] - 40, bh - 30, "Black", 84 if n == 2 else 52, 30)
                lines = wrap(d, lab, fl, box[2] - box[0] - 40)
                yy = (box[1] + box[3]) / 2 - len(lines) * fl.size * 0.6
                for ln in lines:
                    w = d.textlength(ln, font=fl)
                    d.text(((box[0] + box[2]) / 2 - w / 2, yy), ln, font=fl, fill="white")
                    yy += fl.size * 1.18
        if t_a <= t < t_cta and sc.get("wyjasnienie_ekran"):
            yb = 1640 if len(odp) == 2 else 1810
            text_c(d, yb, sc["wyjasnienie_ekran"], F(42 if len(odp) == 2 else 36, "SemiBold"), fill=(200, 205, 220))
        if t >= t_cta:
            d.rounded_rectangle((90, 1660, 990, 1820), 34, fill=YEL)
            text_c(d, 1700, "Napisz w komentarzu", F(66), fill=BGC)
        return im
    encode(frame, total, tmp / "audio.wav", out, tmp)
    return total


# ================================================================ REKRUTACJA
def icon(d, x, y, ok, r=34):
    d.ellipse((x - r, y - r, x + r, y + r), fill=GRN if ok else RED)
    if ok:
        d.line([(x - 16, y + 2), (x - 4, y + 15), (x + 18, y - 13)], fill="white", width=9)
    else:
        d.line([(x - 13, y - 13), (x + 13, y + 13)], fill="white", width=9)
        d.line([(x - 13, y + 13), (x + 13, y - 13)], fill="white", width=9)


def render_rekrutacja(sc, aud, out, tmp):
    """aud: {hook,q,zla,dobra,zasada,cta: (plik, długość)}"""
    tmp = Path(tmp)
    ST, t = {}, 0.2
    for k in ("hook", "q", "zla", "dobra", "zasada", "cta"):
        ST[k] = t
        t += aud[k][1] + 0.25
    total = t + 0.5
    mix([(aud[k][0], ST[k], 1) for k in ST], total, tmp / "audio.wav")
    bg = Image.new("RGB", (W, H), (14, 18, 28))
    dobra = sc["dobra"]

    def reveal(t):
        w = dobra.split()
        p = (t - ST["dobra"] - 0.3) / max(0.1, aud["dobra"][1] - 0.5)
        return " ".join(w[:max(1, min(len(w), int(p * len(w)) + 1))])

    def frame(t):
        im = bg.copy()
        d = ImageDraw.Draw(im)
        pill(d, "PYTANIE Z REKRUTACJI")
        if t < ST["q"]:
            h1 = sc.get("hook_ekran") or "Nie odpowiadaj tak"
            f = fit(d, h1, 960, 420, start=104)
            text_c(d, 600, h1, f, maxw=960)
            return im
        q = sc["pytanie"]
        fq = fit(d, q, 880, 140, "ExtraBold", 60, 40)
        nl = len(wrap(d, q, fq, 880))
        bb = 285 + int(nl * fq.size * 1.18) + 25
        d.rounded_rectangle((60, 210, 1020, bb), 40, fill=GREY)
        d.polygon([(110, bb), (180, bb), (110, bb + 60)], fill=GREY)
        d.text((100, 232), "Rekruter", font=F(34, "SemiBold"), fill=(170, 178, 200))
        text_c(d, 285, q, fq, maxw=880)
        y0 = max(540, bb + 90)
        if t >= ST["zla"]:
            dim = t >= ST["dobra"]
            z = "„" + sc["zla"].strip("„”\"") + "”"
            fz = fit(d, z, 860, 140, "ExtraBold", 62, 36)
            hz = 110 + int(len(wrap(d, z, fz, 860)) * fz.size * 1.18) + 20
            d.rounded_rectangle((60, y0, 1020, y0 + hz), 36, fill=(60, 26, 30) if not dim else (34, 26, 32), outline=RED, width=5)
            icon(d, 130, y0 + 70, False)
            d.text((185, y0 + 45), "ŹLE", font=F(50), fill=RED)
            text_c(d, y0 + 120, z, fz, fill="white" if not dim else (150, 150, 160), maxw=860)
            y0 += hz + 50
        if t >= ST["dobra"]:
            fd = fit(d, dobra, 860, 1560 - y0 - 150, "Bold", 54, 34)
            d.rounded_rectangle((60, y0, 1020, 1560), 36, fill=(20, 52, 36), outline=GRN, width=5)
            icon(d, 130, y0 + 70, True)
            d.text((185, y0 + 45), "LEPIEJ", font=F(50), fill=GRN)
            y = y0 + 130
            for ln in wrap(d, reveal(t) if t < ST["zasada"] else dobra, fd, 860):
                d.text((110, y), ln, font=fd, fill="white")
                y += int(fd.size * 1.22)
        if ST["zasada"] <= t < ST["cta"]:
            d.rounded_rectangle((60, 1610, 1020, 1810), 36, fill=YEL)
            z = sc.get("zasada_ekran") or ""
            text_c(d, 1640, z, fit(d, z, 900, 140, "Black", 56, 34), fill=(14, 18, 28), maxw=900)
        if t >= ST["cta"]:
            d.rounded_rectangle((60, 1610, 1020, 1810), 36, fill=YEL)
            text_c(d, 1670, "Zapisz przed rozmową", F(70), fill=(14, 18, 28))
        return im
    encode(frame, total, tmp / "audio.wav", out, tmp)
    return total
