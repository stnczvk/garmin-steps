"""Comiesięczne odświeżenie bazy produktów z Open Food Facts.

Buduje w katalogu docelowym (domyślnie /root/dziennik/data):
  food-pl.json   – produkty z polskich sklepów z pełnym makro (szybkie wyszukiwanie)
  food-bc/N.json – wszystkie produkty z Europy po kodzie kreskowym (N = kod % 400)
  food-sx/N.json – indeks wyszukiwania po nazwie (N = pierwsze 3 litery pierwszego słowa)
food-gen.json (produkty ogólne) zostaje bez zmian.
Stara baza jest podmieniana dopiero, gdy nowa zbuduje się poprawnie.
"""
import csv, gzip, io, json, re, shutil, sys, time, unicodedata, urllib.request
from datetime import date
from pathlib import Path

URL = "https://static.openfoodfacts.org/data/en.openfoodfacts.org.products.csv.gz"
EUROPE = {"poland", "germany", "france", "italy", "spain", "united-kingdom", "netherlands", "belgium", "austria", "switzerland",
          "czech-republic", "slovakia", "hungary", "romania", "bulgaria", "croatia", "slovenia", "serbia", "portugal", "ireland",
          "denmark", "sweden", "norway", "finland", "lithuania", "latvia", "estonia", "greece", "luxembourg", "ukraine",
          "bosnia-and-herzegovina", "montenegro", "north-macedonia", "albania", "moldova", "iceland", "malta", "cyprus"}
BC_SHARDS, SX_SHARDS = 400, 96


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).replace("ł", "l")


def sxkey(k):
    h = 0
    for ch in k:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % SX_SHARDS


def num(v):
    try:
        x = float(v)
        return round(x, 1) if x == x and 0 <= x < 5000 else None
    except Exception:
        return None


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def main(dest):
    dest = Path(dest)
    tmp = dest.parent / (dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "food-bc").mkdir(parents=True)
    (tmp / "food-sx").mkdir(parents=True)
    csv.field_size_limit(10 ** 8)
    log("pobieram", URL)
    req = urllib.request.Request(URL, headers={"User-Agent": "DziennikTreningowy/1.0 (prywatny)"})
    resp = urllib.request.urlopen(req, timeout=120)
    rd = csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=resp), encoding="utf-8", errors="replace", newline=""),
                    delimiter="\t", quoting=csv.QUOTE_NONE)
    head = next(rd)
    ix = {k: i for i, k in enumerate(head)}
    g = lambda row, k: row[ix[k]] if k in ix and ix[k] < len(row) else ""
    pl, bc, sx = [], [{} for _ in range(BC_SHARDS)], [[] for _ in range(SX_SHARDS)]
    n = 0
    for row in rd:
        n += 1
        if n % 500000 == 0:
            log(n, "wierszy, Polska:", len(pl))
        code = g(row, "code").strip()
        if not code.isdigit() or len(code) < 8:
            continue
        countries = {c.split(":", 1)[-1] for c in g(row, "countries_tags").split(",") if c}
        if not countries & EUROPE:
            continue
        kcal = num(g(row, "energy-kcal_100g"))
        if kcal is None:
            kj = num(g(row, "energy_100g"))
            kcal = round(kj / 4.184, 1) if kj is not None else None
        if kcal is None:
            continue
        name = (g(row, "product_name_pl") or g(row, "product_name")).strip()[:90]
        brand = g(row, "brands").split(",")[0].strip()[:40]
        p, c, f = num(g(row, "proteins_100g")), num(g(row, "carbohydrates_100g")), num(g(row, "fat_100g"))
        portion = num(g(row, "serving_quantity")) or 0
        if portion > 2000:
            portion = 0
        if not name:
            continue
        bc[int(code) % BC_SHARDS][code] = [name, brand, kcal, p, c, f, portion]
        words = [w for w in re.split(r"[^a-z0-9]+", norm(name)) if len(w) >= 3 and not w.isdigit()]
        if words:
            sx[sxkey(words[0][:3])].append([code, name, brand, kcal])
        if "poland" in countries and None not in (p, c, f):
            pl.append([code, name, brand, kcal, p, c, f, portion])
    nbc = sum(len(x) for x in bc)
    log("koniec pliku:", n, "wierszy, Polska:", len(pl), "Europa:", nbc)
    if len(pl) < 5000 or nbc < 300000:
        log("ZA MAŁO produktów – zostawiam starą bazę")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    (tmp / "food-pl.json").write_text(json.dumps({"src": "Open Food Facts (ODbL)", "date": date.today().isoformat(), "shards": BC_SHARDS, "rows": pl},
                                                 ensure_ascii=False, separators=(",", ":")))
    for i, d in enumerate(bc):
        (tmp / "food-bc" / f"{i}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    for i, d in enumerate(sx):
        (tmp / "food-sx" / f"{i}.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")))
    if (dest / "food-gen.json").exists():
        shutil.copy2(dest / "food-gen.json", tmp / "food-gen.json")
    old = dest.parent / (dest.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    log("gotowe – nowa baza produktów podmieniona")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/root/dziennik/data"))
