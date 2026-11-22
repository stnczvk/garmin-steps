"""PozaSkala – długie filmy na YouTube (od 11.2026 historie firm ze stickmanem w czerwonym krawacie, 8–10 min, 16:9).

Robione w centrum (bez n8n): scenariusz (AI) → lektor (OpenAI TTS, po rozdziale) → kadry (OpenAI obrazy, tania
jakość – płaski styl 2D jej nie potrzebuje) → montaż FFmpeg (ruch kamery na każdym kadrze) → kontrola techniczna →
szkic do zatwierdzenia w zakładce PozaSkala → publikacja na kanale PozaSkala. Domyślnie wtorek i piątek.
"""
import base64, concurrent.futures as cf, json, os, re, shutil, subprocess, threading, time, uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from social import DIR, _read, _write
from agency import claude, as_json, log, _pz_account, TZ, SMART
from skills import sk

router = APIRouter()
# Ten sam moduł obsługuje dwa kanały: „dlugi” = PozaSkala (What If? + nauka z humorem), „dlugi_zdrowie” = kanał o zdrowiu 50+
# (ładowany drugi raz pod inną nazwą przez zdrowie.py – osobny stan, katalog, adresy API i konto YouTube).
CH = ("zdrowie" if __name__.endswith("zdrowie") else "historia" if __name__.endswith("historia")
      else "osiedle" if __name__.endswith("osiedle") else "pozaskala")
if CH == "osiedle":
    F, WORK, API, GROUP, TAGLINE, PUSH = "osiedle_dlugie.json", DIR / "osiedle_dlugie", "/api/osiedle", "osiedle", "#OsiedleTłumaczy #shorts", "/#osiedle"
    DEF = {"enabled": False, "days": [3], "hour": 9, "img_quality": "medium", "voice": "onyx", "auto_publish": False, "us_hour": 17, "shorts": 4,
           "veo_max": 36, "veo_model": "veo-3.1-lite-generate-001",
           "voices": {"KAZIK": "onyx", "GRAZYNA": "nova", "ZDZICHU": "echo", "MATI": "alloy"}}
    STYLE = ("Looks exactly like an in-game screenshot of a block-building sandbox game rendered in Mine-imator: low detail, simple flat 16x16 pixel textures on every block, "
             "characters with standard blocky humanoid proportions (one cube head, rectangular body, rectangular arms and legs), faces are FLAT pixel-art painted on the front of the cube head "
             "(simple square eyes, mouth drawn with a few pixels, no 3D nose, no 3D beard, no sculpted features), clothes are flat pixel textures, "
             "plain bright daylight, simple sky with flat blocky clouds, sharp edges, no depth of field, no cinematic lighting, no bloom, no realism, not detailed, simple and clean, "
             "horizontal 16:9 wide shot, no text, no letters, no logos, no game UI, original characters, not Steve, not Alex, no creepers. "
             "Everything is built from classic game blocks only: grass blocks, stone bricks, smooth stone, light grey concrete blocks, oak planks, glass panes, iron bars; "
             "the apartment block is a simple grey block building with glass pane windows, like a player build. Setting: a Polish housing estate (blokowisko).")
    POSTACIE = {"KAZIK": "PAN KAZIK (narrator, kiosk owner): cube head with grey hair texture, grey pixel mustache, beige vest over brown shirt, dark brown pants",
                "GRAZYNA": "PANI GRAZYNA: woman with red curly hair texture, pink tracksuit with white stripes",
                "ZDZICHU": "PAN ZDZICHU: bald cube head, white tank top, blue tracksuit pants with white stripe",
                "MATI": "MATI: boy with blond hair, black cap, orange hoodie, dark jeans"}
elif CH == "historia":
    F, WORK, API, GROUP, TAGLINE, PUSH = "historia_dlugie.json", DIR / "historia_dlugie", "/api/historia", "historia", "#History #shorts", "/#historia"
    DEF = {"enabled": False, "days": [3], "hour": 9, "img_quality": "low", "voice": "fable", "auto_publish": False, "us_hour": 18, "shorts": 4}
    STYLE = ("detailed hand-painted storybook illustration, soft watercolor and ink like an old history book, warm muted earthy palette, "
             "historically accurate clothing, tools and architecture for the period, cinematic calm composition, gentle light, "
             "horizontal 16:9, no text, no letters, no modern objects, no watermark, never gory")
elif CH == "zdrowie":
    F, WORK, API, GROUP, TAGLINE, PUSH = "zdrowie_dlugie.json", DIR / "zdrowie_dlugie", "/api/zdrowie", "zdrowie", "#Health #Over50 #shorts", "/#zdrowie"
    DEF = {"enabled": False, "days": [2], "hour": 9, "img_quality": "low", "voice": "onyx", "auto_publish": False, "us_hour": 10, "shorts": 5}
    STYLE = ("2D flat vector cartoon, the stick figure character Walt (a friendly 60-year-old stick figure: black stick body, round white head, "
             "two dot eyes, small round glasses, short grey hair tuft, light blue sweater vest), thick clean black outlines, "
             "warm calm flat background (soft beige, sage green, light blue), simple clean medical/anatomy icons drawn in the same flat style "
             "when needed (muscles, heart, bones, brain), bold readable shapes, horizontal 16:9, no text, no watermark, never photoreal, never gory")
else:
    F, WORK, API, GROUP, TAGLINE, PUSH = "dlugie.json", DIR / "dlugie", "/api/dlugi", "pozaskala", "#Business #Animation #shorts", "/#pozaskala"
    DEF = {"enabled": True, "days": [1, 4], "hour": 9, "img_quality": "low", "voice": "alloy", "auto_publish": False, "us_hour": 14, "shorts": 2}
    STYLE = ("2D flat vector cartoon, the stick figure character Stick (black stick body, round white head, two dot eyes, red baseball cap always on), "
             "thick clean black outlines, simple flat pastel background, bold readable shapes, correct relative proportions, "
             "horizontal 16:9, no text, no watermark")
LOG = "dlugi" if CH == "pozaskala" else "zdrowie"
US = ZoneInfo("Europe/Warsaw" if CH == "osiedle" else "America/New_York")
LANG = "pl" if CH == "osiedle" else "en"
FULL_EP = "Cały odcinek na kanale" if LANG == "pl" else "Full episode on the channel"
_busy = {}
_START = time.time()


def _account():
    from social import _accounts
    if CH == "pozaskala":
        return _pz_account("youtube")
    for k, a in _accounts().items():
        if a.get("platform") == "youtube" and a.get("group") == GROUP:
            return k, a
    return None, None


def cfg():
    return {**DEF, **_read(F, {}).get("cfg", {})}


def _state():
    S = _read(F, {})
    S.setdefault("items", [])
    return S


def _save(S):
    S["items"] = S["items"][-30:]
    _write(F, S)


def _upd(vid, **kw):
    S = _state()
    for it in S["items"]:
        if it["id"] == vid:
            it.update(kw)
    _save(S)


def _key():
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k:
        raise RuntimeError("brak OPENAI_API_KEY")
    return {"Authorization": "Bearer " + k}


# ---------------------------------------------------------------- 1. scenariusz
def script(used):
    if CH == "zdrowie":
        return script_zdrowie(used)
    if CH == "historia":
        return script_historia(used)
    if CH == "osiedle":
        return script_osiedle(used)
    return script_firmy(used)      # od 11.2026 PozaSkala = historie firm (What If / humor zostają w kodzie, nieużywane)


def script_historia(used):
    """Kanał historyczny: jedno wąskie wydarzenie, miejsce albo codzienne życie w dawnych czasach, opowiedziane spokojnie i szczegółowo (~12–14 min, limit 15 min dla niezweryfikowanego kanału)."""
    p = f"""Write ONE long-form YouTube documentary episode (EN, about 12-13 minutes) for a calm, detailed history channel
for viewers in the US/UK/Canada/Australia (many listen while relaxing or falling asleep).
Topic: ONE narrow, specific subject told in rich detail – a single event, place, mystery or "what daily life was like" for one kind of person
in one period (e.g. What a Roman soldier ate on the march, The night the Mary Celeste was found, How medieval peasants survived winter,
Life inside a Viking longhouse, The last day of Pompeii hour by hour). Avoid broad overviews. Do NOT repeat these titles: {"; ".join(used[-60:]) or "none"}.

Rules: only well-established history (say "historians believe" / "according to" when sources are uncertain), no invented quotes or names,
no gore. Narration: calm, warm, immersive second person and sensory details (sounds, smells, cold, light), steady pace, clear American English B2,
no hype, no "smash the like button". Structure: a gentle hook in the first 20 s (the most intriguing detail), 6 chapters that move through
time or through the day, and a quiet reflective ending.

Return ONLY JSON:
""" + JSON_SPEC.format(title="max 60 chars, specific and intriguing (e.g. What Medieval Peasants Ate in Winter)",
                       tags="incl. #History #SleepHistory",
                       thumb="one striking, atmospheric period scene with a single clear focal subject, warm light",
                       scene="a calm, detailed period scene (people, place, objects) matching the narration") \
        .replace('"narration":"150-230 words"', '"narration":"280-320 words"').replace("7-9 visual scene descriptions", "6 visual scene descriptions") + """
The sum of all narration must be 1700-1900 words (NEVER more than 1950 – the video must stay under 15 minutes)."""
    r = _chk(as_json(claude(p, SMART, 12000, system=sk("reel", "hook"))))
    r = _wydluz(r, 1700, "290-320")
    fx = as_json(claude("You are a strict history fact-checker for a general-audience documentary. Below is the script JSON. "
                        "Fix anything wrong, anachronistic or presented as certain when historians disagree (add 'historians believe'); "
                        "remove invented quotes and named people you are not sure existed. Keep style, length, structure and scenes the same. "
                        "Return ONLY the corrected JSON.\n\n" + json.dumps(r, ensure_ascii=False), SMART, 14000))
    try:
        r = {**r, **_chk(fx)} if fx else r
    except Exception:
        pass
    return r


FIRMY_RODZAJE = [
    ("downfall", "why a once-giant company collapsed or shrank (e.g. Blockbuster, Kodak, Sears, Toys R Us, Nokia)"),
    ("money machine", "how a well-known company really makes its money, the surprising business model (e.g. Costco memberships, McDonald's real estate)"),
    ("rise", "how a company went from tiny beginnings to giant, the key decisions (e.g. Nike, LEGO comeback, Zara, Red Bull)"),
    ("rivalry", "a famous business rivalry and who really won (e.g. Coke vs Pepsi, Adidas vs Puma, Boeing vs Airbus)"),
    ("scandal", "a well-documented corporate scandal or disaster explained simply (e.g. Enron, Theranos, Dieselgate, Wells Fargo fake accounts)"),
    ("why now", "an everyday business trend people notice right now and why it happens (e.g. why everything is a subscription, shrinkflation, self-checkout)"),
    ("vintage ad", "the story behind one famous old advertisement or ad campaign and what it did to the company (e.g. Apple 1984, Wendy's Where's the Beef, "
                   "the Marlboro Man, Got Milk?) – describe the ad in words and redraw scenes in Max's blocky style, never copy the original ad's images or slogans' artwork"),
]


def script_firmy(used):
    """PozaSkala – historie firm: Max (blokowa postać z czerwonym krawatem) w stroju pasującym do firmy z odcinka, bez logo."""
    rodz, opis = FIRMY_RODZAJE[len(used) % len(FIRMY_RODZAJE)]
    p = f"""Write ONE long-form YouTube episode (EN, 8-10 minutes) for a business-stories channel aimed at viewers in the US/UK/Canada/Australia,
starring Max (a blocky cube-headed character in a voxel block world, with a red necktie) who walks the viewer through the story like a smart, funny friend.
This episode's type: {rodz} – {opis}.
Pick ONE real, well-known company or trend that fits this type and that people search for. Do NOT repeat these titles: {"; ".join(used[-60:]) or "none"}.

Rules: only well-documented facts (company reports, major news coverage); round numbers and say "about" when unsure; no investment advice,
no defamation (describe proven events, use "according to" for allegations). Strong storytelling: a cold-open hook in the first 15 s
(the surprising twist or number), then 6-7 chapters with tension, a mid-video re-hook, and a clear lesson at the end.
Short spoken sentences (max 16 words), clear American English B1-B2, light humour, no filler, no "in this video".

Return ONLY JSON:
""" + JSON_SPEC.format(title="max 60 chars, curiosity: How X..., Why X..., The Fall of X..., X vs Y",
                       tags="incl. #Business #Animation",
                       thumb="Max with a strong emotional face in the key moment, one big simple object or chart, bold composition",
                       scene="what Max does in a blocky voxel world + a simple object or a chart made of blocks; NEVER logos, brand names or any text") + """
Also add "outfit": "Max's outfit for this episode that hints at the company/industry by colours and style only (e.g. warehouse vest,
pilot uniform, fast-food cap and apron, lab coat, business suit), ALWAYS keeping his red necktie, never logos, names or text".
The sum of all narration must be 1300-1600 words."""
    r = _chk(as_json(claude(p, SMART, 7000, system=sk("reel", "hook"))))
    r = _wydluz(r, 1350, "220-260")
    fx = as_json(claude("You are a strict fact-checker for a general-audience business history video. Below is the script JSON. "
                        "Fix any claim that is wrong, exaggerated or not well documented; round uncertain numbers and add 'about'; "
                        "turn unproven accusations into 'according to reports'. Keep style, length, structure, scenes and outfit the same. "
                        "Return ONLY the corrected JSON.\n\n" + json.dumps(r, ensure_ascii=False), SMART, 8000))
    try:
        r = {**r, **_chk(fx)} if fx else r
    except Exception:
        pass
    return r


def _chk(r):
    if isinstance(r, list):
        r = r[0] if r else {}
    ch = [c for c in (r.get("chapters") or []) if c.get("narration") and c.get("scenes")]
    if not r.get("title") or len(ch) < 4:
        raise RuntimeError("scenariusz niekompletny")
    r["chapters"] = ch[:8]
    return r


JSON_SPEC = """{{"title":"{title}","description":"3-4 sentences + 3 hashtags {tags}",
"tags":["8-12 search phrases"],"thumbnail_prompt":"{thumb}",
"chapters":[{{"title":"short chapter title","narration":"150-230 words","scenes":["7-9 visual scene descriptions, each one focal idea, {scene}"]}}]}}"""


def script_humor(used):
    """PozaSkala – nauka wyjaśniona z humorem: Stick tłumaczy codzienne „dlaczego?” (ziewanie, czkawka, czemu niebo jest niebieskie)."""
    p = f"""Write ONE long-form YouTube episode (EN, 8-10 minutes) for the channel "Poza Skala", series "Stick Explains" starring Stick
(a simple stick figure with a round white head and his signature red baseball cap) and his sidekick Bolt (a tiny round white robot).
Topic: one everyday science question people secretly wonder about and that has a surprising real answer (e.g. Why do we yawn,
Why does time feel faster as we get older, Why can't you tickle yourself, Where does fat actually go when you lose weight).
Do NOT repeat these titles: {"; ".join(used[-40:]) or "none"}.

Tone: genuinely funny like a smart friend, Stick tries ridiculous explanations first, Bolt corrects him with the real science,
running gags, playful exaggeration in the visuals, but every fact correct and conservative. Short spoken sentences (max 16 words),
American English B1-B2, no filler, no "in this video".
Structure: a cold-open hook (first 15 s: the question + a funny wrong answer), then 5-6 chapters that go deeper step by step,
a mid-video re-hook, a satisfying final answer and one mind-blowing bonus fact at the end.

Return ONLY JSON:
""" + JSON_SPEC.format(title="max 60 chars, a curiosity question or bold claim", tags="incl. #Science #Stickman",
                       thumb="Stick with a funny confused or shocked face, one clear focal object, bold composition",
                       scene="what Stick and Bolt do, funny visual exaggeration") + """
The sum of all narration must be 1250-1500 words (8-10 minutes: from 8 minutes YouTube can show mid-roll ads)."""
    return _wydluz(_chk(as_json(claude(p, SMART, 7000, system=sk("reel", "hook")))), 1200, "200-240")


def script_zdrowie(used):
    """Kanał zdrowie 50+: Walt (stickman 60+) i oparte na badaniach wyjaśnienia; drugi przebieg AI sprawdza fakty."""
    p = f"""Write ONE long-form YouTube episode (EN, 8-10 minutes) for a health channel for people over 50 in the US/UK/Canada/Australia,
starring Walt (a friendly 60-year-old stick figure with round glasses and a grey hair tuft) who lives through each topic.
Topic: one concrete, practical question about staying strong, mobile and healthy after 50 that people search for
(e.g. What happens to your muscles after 60 if you stop walking, The 3 exercises that protect your knees after 50,
Why you wake up at 3am after 50, How much protein you really need after 60). Do NOT repeat these titles: {"; ".join(used[-40:]) or "none"}.

Rules: ONLY well-established, mainstream evidence (major guidelines like WHO, CDC, NHS, NIH, and widely replicated research);
no miracle cures, no supplements selling, no exact dosages of medicines, no diagnosis; say "talk to your doctor" naturally once where it matters.
Concrete and useful: every chapter ends with one simple thing the viewer can do. Warm, calm, respectful tone (never patronising), a bit of gentle humour,
short spoken sentences (max 16 words), clear American English B1-B2, no filler, no "in this video".
Structure: a cold-open hook (first 15 s: the surprising consequence or myth), then 6-7 chapters, a mid-video re-hook, and a short recap of the
action steps at the end.

Return ONLY JSON:
""" + JSON_SPEC.format(title="max 60 chars, specific benefit or surprising consequence, includes After 50 or After 60 when natural",
                       tags="incl. #HealthyAging #Over50", thumb="Walt with a clear emotional face next to one big simple body/anatomy icon",
                       scene="what Walt does or a simple flat anatomy/body diagram") + """
Also add "sources": ["3-6 real, well-known sources by organisation and topic, e.g. CDC - Physical Activity Guidelines for Older Adults"].
The sum of all narration must be 1300-1600 words."""
    r = _chk(as_json(claude(p, SMART, 7000, system=sk("reel", "hook"))))
    r = _wydluz(r, 1350, "220-260")
    # sprawdzanie faktów: drugi przebieg poprawia przesadzone albo wątpliwe twierdzenia (zdrowie = YouTube patrzy szczególnie)
    fx = as_json(claude("You are a strict medical fact-checker for a general-audience health video. Below is the script JSON. "
                        "Fix any claim that is exaggerated, not mainstream evidence, too specific (exact numbers you are not sure about), "
                        "or could be harmful advice; soften to what is well established. Keep everything else (style, length, structure, scenes) the same. "
                        "Remove any source that you are not sure exists. Return ONLY the corrected JSON.\n\n" + json.dumps(r, ensure_ascii=False),
                        SMART, 8000))
    try:
        r = _chk(fx) if fx else r
    except Exception:
        pass
    src = [x for x in (r.get("sources") or []) if isinstance(x, str)][:6]
    r["description"] = (r.get("description") or "").strip() + ("\n\nSources:\n" + "\n".join("- " + x for x in src) if src else "") + \
        "\n\nThis video is for general education, not medical advice. Talk to your doctor before changing your exercise, diet or medication."
    return r


def _slowa(r):
    return sum(len(str(c.get("narration") or "").split()) for c in r.get("chapters") or [])


def _wydluz(r, min_words, per):
    """AI zwykle pisze krócej, niż prosimy (wychodziło ~5 min zamiast 8–10). Za krótki scenariusz → jedno podejście „rozwiń”:
    więcej konkretu i przykładów w każdym rozdziale, ten sam styl i sceny (+2–3 sceny na rozdział)."""
    n = _slowa(r)
    if n >= min_words:
        return r
    try:
        x = _chk(as_json(claude(
            f"This YouTube script is too short ({n} words of narration, it must be at least {min_words}). Expand EVERY chapter's narration to "
            f"{per} words by adding concrete, useful detail: a relatable everyday example, a simple explanation of why it happens, "
            "and one practical tip. Keep the same voice, style, facts policy, title and structure; add 2-3 extra scenes to each chapter "
            "for the new parts. No filler, no repetition. Return ONLY the full JSON in the same format.\n\n" + json.dumps(r, ensure_ascii=False),
            SMART, 9000)))
        if _slowa(x) > n:
            log(LOG, f"Scenariusz wydłużony: {n} → {_slowa(x)} słów", "info")
            return {**r, **x}
    except Exception as e:
        print("wydluz", e, flush=True)
    return r


def script_whatif(used):
    p = f"""Write ONE long-form YouTube episode (EN, 8-10 minutes) for the channel "Poza Skala", series "What If?" starring Stick
(a simple stick figure with a round white head and his signature red baseball cap). Real science and real scale are the payoff; numbers conservative and correct.
Pick a fresh hypothetical scenario that a 13-year-old instantly understands and wants answered (e.g. What if you fell into a black hole,
What if the Moon disappeared, What if you lived on Jupiter for a day). Do NOT repeat these titles: {"; ".join(used[-40:]) or "none"}.

Structure: a cold-open hook (first 15 s pose the question and tease the craziest moment), then 5-6 chapters that escalate,
a mid-video re-hook, and a final twist that echoes the opening. Narration follows Stick through the scenario, second person allowed,
short spoken sentences (max 16 words), American English B1-B2, no filler, no "in this video".

Return ONLY JSON:
{{"title":"max 60 chars, starts with What If","description":"3-4 sentences + 3 hashtags incl. #WhatIf #Stickman",
"tags":["8-12 search phrases"],"thumbnail_prompt":"Stick with a shocked face in the most dramatic moment, bold composition",
"chapters":[{{"title":"short chapter title","narration":"150-230 words","scenes":["7-9 visual scene descriptions, each one focal idea, what Stick does + the scale object"]}}]}}
The sum of all narration must be 1250-1500 words (8-10 minutes: from 8 minutes YouTube can show mid-roll ads)."""
    return _wydluz(_chk(as_json(claude(p, SMART, 7000, system=sk("reel", "hook")))), 1200, "200-240")


# ---------------------------------------------------------------- 2. lektor i kadry
# ---------------------------------------------------------------- Osiedle Tłumaczy (PL): dialogi kilku postaci + sceny Veo
TEMATY_OSIEDLE = ["pieniądze i ceny", "kredyty i banki", "podatki i ZUS", "praca i pensje", "mieszkania i czynsze", "oszczędzanie i inwestowanie",
                  "zakupy i promocje", "państwo i urzędy", "ekonomia wytłumaczona prosto", "psychologia pieniędzy", "oszustwa i pułapki",
                  "historia pieniądza w Polsce"]
BIB = DIR / "osiedle_biblioteka"


def script_osiedle(used):
    """Odcinek 8–10 min: Pan Kazik z kiosku tłumaczy jedno pojęcie, sąsiedzi przeżywają je na własnej skórze (dialogi)."""
    import random
    kat = random.choice(TEMATY_OSIEDLE)
    p = f"""Napisz JEDEN długi odcinek (8–10 minut) polskiego kanału YouTube „Osiedle Tłumaczy”.
Format: animowany serial w klockowym świecie gry na polskim blokowisku. Pan Kazik (narrator, właściciel kiosku, mądry, spokojny, z humorem)
tłumaczy JEDNO pojęcie z kategorii „{kat}” na prostej historyjce z osiedla. Sąsiedzi przeżywają temat na własnej skórze:
Pani Grażyna (głośna, wszystko wie najlepiej, narzeka na ceny), Pan Zdzichu (prosty chłop, ma pomysły na szybki zarobek, często się myli),
Mati (nastolatek z telefonem, zadaje pytania, które zadałby widz). Nie powtarzaj tematów: {"; ".join(used[-40:]) or "brak"}.

Zasady: fakty prawdziwe i aktualne dla Polski, proste słowa, liczby na przykładach z życia (bułka, czynsz, pensja), humor sytuacyjny,
bez wulgaryzmów, bez polityki partyjnej. Hook w pierwszych 15 s: zaskakujące zdanie albo kłótnia sąsiadów. 6 rozdziałów, każdy
to scenka z dialogiem i komentarzem Kazika. Koniec: Kazik podsumowuje w 3 zdaniach i pyta widzów o ich zdanie w komentarzu.
Kwestie krótkie (1–3 zdania), naturalna mówiona polszczyzna. Kazik mówi ok. 60% tekstu.

Zwróć TYLKO JSON:
{{"title":"max 60 znaków, ciekawy, po polsku (np. Dlaczego bułka kosztuje 2 zł? Inflacja na osiedlu)",
"description":"3-4 zdania po polsku + 3 hashtagi w tym #OsiedleTłumaczy",
"tags":["8-12 fraz wyszukiwania po polsku"],
"thumbnail_prompt":"EN: one clear funny scene with 1-2 characters and a big emotion, simple background",
"chapters":[{{"title":"krótki tytuł rozdziału",
  "lines":[{{"who":"KAZIK|GRAZYNA|ZDZICHU|MATI","text":"kwestia po polsku"}}],
  "scenes":[{{"opis":"EN visual description of ONE shot: which characters (use names KAZIK, GRAZYNA, ZDZICHU, MATI), where on the estate (kiosk, bench by the stairwell, carpet-beating rack, corner shop, courtyard, flat interior, bus stop), what they hold/do",
             "ruch":"EN: the simple motion in this 8-second shot (who walks, gestures, reacts)"}}]}}]}}
Każdy rozdział: 220–260 słów w lines i 5 scen. Suma słów wszystkich kwestii 1350–1550."""
    r = as_json(claude(p, SMART, 12000, system=sk("reel", "hook")))
    if isinstance(r, list):
        r = r[0] if r else {}
    for ch in r.get("chapters") or []:
        ch["lines"] = [l for l in (ch.get("lines") or []) if isinstance(l, dict) and str(l.get("text", "")).strip()]
        ch["narration"] = " ".join(l["text"] for l in ch["lines"])
        ch["scenes"] = [x for x in (ch.get("scenes") or []) if isinstance(x, dict) and x.get("opis")]
    r = _chk(r)
    r["kategoria"] = kat
    return r


def _postacie_w(opis):
    """Dopisuje do opisu sceny wygląd postaci, które w niej występują (żeby wyglądały zawsze tak samo)."""
    o = opis
    for k, v in POSTACIE.items():
        if k in opis.upper() or k[:5] in opis.upper():
            o += "; " + v
    return o


def tts_linie(lines, out, d, tag):
    """Każda kwestia swoim głosem, krótka pauza między kwestiami, sklejone w jeden plik rozdziału."""
    vo = cfg().get("voices") or {}
    parts = []
    for k, l in enumerate(lines):
        f = d / f"{tag}_l{k}.mp3"
        tts(l["text"], f, vo.get(str(l.get("who", "KAZIK")).upper(), "onyx"))
        parts.append(f)
    inp = sum([["-i", str(f)] for f in parts], [])
    fc = "".join(f"[{k}:a]aresample=24000,apad=pad_dur=0.3[a{k}];" for k in range(len(parts))) + \
         "".join(f"[a{k}]" for k in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[o]"
    subprocess.run(["ffmpeg", "-y", *inp, "-filter_complex", fc, "-map", "[o]", "-b:a", "160k", str(out)], check=True, capture_output=True)
    for f in parts:
        f.unlink(missing_ok=True)


def _ma_audio(f):
    return "audio" in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(f)],
                                     capture_output=True, text=True).stdout


def veo_klip(sc, d, name):
    """Kadr (OpenAI, przycięty do 16:9) → Veo (8 s, dźwięk otoczenia bez mowy). Gdy Veo zawiedzie: sam kadr (bez ruchu)."""
    import montaz as M
    c = cfg()
    png, jpg, mp4 = d / f"{name}.png", d / f"{name}.jpg", d / f"{name}.mp4"
    image(_postacie_w(sc["opis"]), png, c["img_quality"], "1536x1024")
    subprocess.run(["ffmpeg", "-y", "-i", str(png), "-vf", "crop=1536:864", "-q:v", "3", str(jpg)], check=True, capture_output=True)
    try:
        prompt = (sc.get("ruch") or "Subtle idle motion.") + (" Blocky game animation, characters move stiffly like rigid blocks, heads are rigid cubes, "
                  "keep the flat pixel textures, no morphing. Sound: only ambient sounds of the scene (birds, footsteps, wind, distant city). "
                  "No speech, no dialogue, no voices, no singing, no music.")
        model = c.get("veo_model") or "veo-3.1-lite-generate-001"
        r = httpx.post(M._vx_url(model, "predictLongRunning"), timeout=60, headers={"Authorization": "Bearer " + M._vertex_token()},
                       json={"instances": [{"prompt": prompt, "image": {"bytesBase64Encoded": base64.b64encode(jpg.read_bytes()).decode(), "mimeType": "image/jpeg"}}],
                             "parameters": {"aspectRatio": "16:9", "durationSeconds": 8, "sampleCount": 1, "generateAudio": True, "resolution": "720p"}})
        op = r.json().get("name")
        if not op:
            raise RuntimeError(str(r.json())[:200])
        for _ in range(60):
            time.sleep(15)
            j = httpx.post(M._vx_url(model, "fetchPredictOperation"), timeout=120, headers={"Authorization": "Bearer " + M._vertex_token()},
                           json={"operationName": op}).json()
            if j.get("error"):
                raise RuntimeError(str(j["error"])[:200])
            if j.get("done"):
                v = ((j.get("response") or {}).get("videos") or [{}])[0]
                if not v.get("bytesBase64Encoded"):
                    raise RuntimeError("Veo bez wideo (filtr?)")
                mp4.write_bytes(base64.b64decode(v["bytesBase64Encoded"]))
                break
        else:
            raise RuntimeError("Veo: za długo")
        if not _ma_audio(mp4):
            tmp = d / f"{name}_x.mp4"
            subprocess.run(["ffmpeg", "-y", "-i", str(mp4), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-c:v", "copy", "-c:a", "aac",
                            "-shortest", str(tmp)], check=True, capture_output=True)
            tmp.replace(mp4)
    except Exception as e:
        print("veo", name, e, flush=True)
        subprocess.run(["ffmpeg", "-y", "-loop", "1", "-framerate", "25", "-i", str(jpg), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-t", "8", "-vf", "scale=1280:720,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", str(mp4)],
                       check=True, capture_output=True)
    BIB.mkdir(parents=True, exist_ok=True)
    try:   # biblioteka scen – do powtarzania w kolejnych odcinkach
        shutil.copy(mp4, BIB / f"{d.name}_{name}.mp4")
        idx = _read("osiedle_biblioteka.json", [])
        idx.append({"f": f"{d.name}_{name}.mp4", "opis": sc["opis"], "at": time.time()})
        _write("osiedle_biblioteka.json", idx[-400:])
    except Exception:
        pass
    png.unlink(missing_ok=True)
    return mp4


def _segment(src, out, t, wariant):
    """Klip 1920x1080/25 fps z cichym dźwiękiem otoczenia; wariant ≠ 0 = lustro / zbliżenie, żeby powtórka nie była identyczna."""
    vf = "scale=1920:1080:flags=lanczos"
    if wariant % 3 == 1:
        vf = "hflip," + vf
    elif wariant % 3 == 2:
        vf = "crop=iw*0.8:ih*0.8:(iw-iw*0.8)/2:(ih-ih*0.8)/3," + vf
    subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-filter_complex", f"[0:v]{vf},fps=25,format=yuv420p[v];[0:a]aresample=48000,volume=0.18[x];[x][1:a]amix=inputs=2:duration=first[a]",
                    "-map", "[v]", "-map", "[a]", "-t", f"{t:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(out)],
                   check=True, capture_output=True)


def render_osiedle(d, chapters, klipy):
    """Rozdział = jego własne sceny Veo, a gdy lektor mówi dłużej – powtórki scen z odcinka i z biblioteki (lustro/zbliżenie)."""
    import random
    pula = [k for ks in klipy for k in ks]
    try:
        bib = [BIB / x["f"] for x in _read("osiedle_biblioteka.json", []) if (BIB / x["f"]).exists() and not x["f"].startswith(d.name)]
    except Exception:
        bib = []
    parts = []
    for ci, ch in enumerate(chapters):
        a = d / f"a{ci}.mp3"
        D = dur(a)
        own = klipy[ci]
        segs, t, j, prev = [], 0.0, 0, None
        while t < D - 0.05:
            ln = min(8.0, D - t)
            if j < len(own):
                src, war = own[j], 0
            else:
                cand = [x for x in (pula + random.sample(bib, min(len(bib), 10))) if x != prev] or pula
                src, war = random.choice(cand), random.randint(1, 2)
            seg = d / f"s{ci}_{j}.mp4"
            _segment(src, seg, ln, war)
            segs.append(seg)
            prev, t, j = src, t + ln, j + 1
        lst = d / f"l{ci}.txt"
        lst.write_text("".join(f"file '{x.name}'\n" for x in segs))
        v = d / f"v{ci}.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(v)], check=True, capture_output=True, cwd=d)
        part = d / f"p{ci}.mp4"
        subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-i", str(v), "-i", str(a), "-filter_complex",
                        "[1:a]aresample=48000,aformat=channel_layouts=stereo[g];[0:a][g]amix=inputs=2:duration=longest:normalize=0[o]",
                        "-map", "0:v", "-map", "[o]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{D:.3f}", str(part)],
                       check=True, capture_output=True)
        parts.append(part)
        for x in segs + [v]:
            x.unlink(missing_ok=True)
    lst = d / "all.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = d / "final.mp4"
    subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], check=True, capture_output=True, cwd=d)
    return out


def produce_osiedle(vid, d, c, sc):
    chs = sc["chapters"]
    # limit scen Veo na odcinek (koszt) – równo na rozdziały, reszta czasu = powtórki
    per = max(2, int(c.get("veo_max", 36)) // len(chs))
    for ch in chs:
        ch["scenes"] = ch["scenes"][:per]
    (d / "script.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
    n = sum(len(ch["scenes"]) for ch in chs)
    _upd(vid, title=sc["title"], step=f"lektor (głosy postaci) + {n} scen Veo (ok. 15–25 min)")
    klipy = [[None] * len(ch["scenes"]) for ch in chs]
    with cf.ThreadPoolExecutor(6) as ex:
        jobs = [ex.submit(tts_linie, ch["lines"], d / f"a{ci}.mp3", d, f"c{ci}") for ci, ch in enumerate(chs)]
        jobs.append(ex.submit(image, sc.get("thumbnail_prompt") or sc["title"], d / "thumb.png", "medium", "1536x1024"))
        vj = {ex.submit(veo_klip, s, d, f"k{ci}_{si}"): (ci, si) for ci, ch in enumerate(chs) for si, s in enumerate(ch["scenes"])}
        for j in jobs:
            j.result()
        for f_, (ci, si) in vj.items():
            klipy[ci][si] = f_.result()
    _upd(vid, step="montaż (FFmpeg)")
    cds = [dur(d / f"a{ci}.mp3") for ci in range(len(chs))]
    render_osiedle(d, chs, klipy)
    for p in d.glob("k*_*.*"):
        p.unlink(missing_ok=True)
    return finish(vid, d, sc, cds, c)


def tts(text, out, voice):
    r = httpx.post("https://api.openai.com/v1/audio/speech", headers=_key(), timeout=300,
                   json={"model": "tts-1", "voice": voice, "input": text[:4000], "response_format": "mp3"})
    if r.status_code >= 300:
        raise RuntimeError("TTS: " + r.text[:200])
    out.write_bytes(r.content)


def image(prompt, out, quality, size="1536x1024", style=None):
    for a in range(3):
        try:
            r = httpx.post("https://api.openai.com/v1/images/generations", headers=_key(), timeout=300,
                           json={"model": os.environ.get("DLUGI_IMG_MODEL", "gpt-image-2.5-flare"), "prompt": prompt + ". " + (style or STYLE),
                                 "size": size, "quality": quality, "n": 1})
            j = r.json()
            if r.status_code < 300 and j.get("data"):
                d = j["data"][0]
                out.write_bytes(base64.b64decode(d["b64_json"]) if d.get("b64_json") else httpx.get(d["url"], timeout=120).content)
                return
            err = str((j.get("error") or {}).get("message", j))[:200]
        except Exception as e:
            err = str(e)[:200]
        time.sleep(5 * (a + 1))
    raise RuntimeError("obraz: " + err)


def dur(f):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                                capture_output=True, text=True).stdout.strip() or 0)


# ---------------------------------------------------------------- 3. montaż
def render(d, chapters):
    fps, parts = 25, []
    for ci, ch in enumerate(chapters):
        a = d / f"a{ci}.mp3"
        n = len(ch["scenes"])
        per = max(dur(a) / n, 2.0) + 0.04
        clips = []
        for si in range(n):
            img, clip = d / f"i{ci}_{si}.png", d / f"c{ci}_{si}.mp4"
            fr = int(per * fps)
            # bez zoompan – przy powolnym zbliżeniu ffmpeg zaokrągla pozycję kadru do pikseli i obraz drga („trzęsienie”)
            vf = "scale=1920:1080:force_original_aspect_ratio=increase:flags=lanczos,crop=1920:1080,format=yuv420p"
            subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(img), "-vf", vf, "-t", f"{per:.3f}",
                            "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "22", str(clip)],
                           check=True, capture_output=True)
            clips.append(clip)
        lst = d / f"l{ci}.txt"
        lst.write_text("".join(f"file '{c.name}'\n" for c in clips))
        part = d / f"p{ci}.mp4"
        subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(a), "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", str(part)], check=True, capture_output=True, cwd=d)
        parts.append(part)
        for c in clips:
            c.unlink(missing_ok=True)
    lst = d / "all.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts))
    out = d / "final.mp4"
    subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], check=True, capture_output=True, cwd=d)
    return out


def check(f):
    """Kontrola techniczna jak w Montażu shortów: długość, obraz, dźwięk, czarne klatki, cisza."""
    probs = []
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(f)],
                                   capture_output=True, text=True).stdout or "{}")
    d = float((pr.get("format") or {}).get("duration") or 0)
    st = pr.get("streams") or []
    if not any(s.get("codec_type") == "video" for s in st):
        probs.append("brak obrazu")
    if not any(s.get("codec_type") == "audio" for s in st):
        probs.append("brak dźwięku")
    lo, hi = (480, 899) if CH == "historia" else (240, 899)
    if d < lo or d > hi:
        probs.append(f"długość {d / 60:.1f} min (powinno {lo // 60}–{hi // 60})")
    out = subprocess.run(["nice", "-n", "15", "ffmpeg", "-hide_banner", "-nostats", "-i", str(f), "-vf", "blackdetect=d=1:pix_th=0.10",
                          "-af", "silencedetect=noise=-45dB:d=2.5", "-f", "null", "-"], capture_output=True, text=True).stderr
    if re.search(r"black_start:([\d.]+)", out):
        probs.append("czarny ekran")
    sil = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", out) if float(x) < d - 3]
    if sil:
        probs.append("cisza od " + ", ".join(f"{s:.0f}s" for s in sil[:3]))
    return d, probs


# ---------------------------------------------------------------- całość
def produce(why="harmonogram"):
    if _busy.get("run"):
        return {"ok": False, "error": "już trwa"}
    _busy["run"] = True
    c = cfg()
    vid = datetime.now(TZ).strftime("%y%m%d") + "-" + uuid.uuid4().hex[:4]
    d = WORK / vid
    d.mkdir(parents=True, exist_ok=True)
    S = _state()
    used = [x.get("title", "") for x in S["items"]]
    S["items"].append({"id": vid, "status": "working", "step": "scenariusz", "at": time.time(), "why": why})
    _save(S)
    try:
        sc = script(used)
        (d / "script.json").write_text(json.dumps(sc, ensure_ascii=False, indent=1))
        if CH == "osiedle":
            return produce_osiedle(vid, d, c, sc)
        st = STYLE_BIZ.format(outfit=sc.get("outfit") or "a simple dark business suit") if CH == "pozaskala" else None
        n_img = sum(len(ch["scenes"]) for ch in sc["chapters"])
        _upd(vid, title=sc["title"], step=f"lektor + {n_img} kadrów")
        jobs = []
        with cf.ThreadPoolExecutor(4) as ex:
            for ci, ch in enumerate(sc["chapters"]):
                jobs.append(ex.submit(tts, ch["narration"], d / f"a{ci}.mp3", c["voice"]))
                for si, s in enumerate(ch["scenes"]):
                    jobs.append(ex.submit(image, s, d / f"i{ci}_{si}.png", c["img_quality"], "1536x1024", st))
            jobs.append(ex.submit(image, sc.get("thumbnail_prompt") or sc["title"], d / "thumb.png", "medium", "1536x1024", st))
            for j in jobs:
                j.result()
        _upd(vid, step="montaż (FFmpeg)")
        cds = [dur(d / f"a{ci}.mp3") for ci in range(len(sc["chapters"]))]
        render(d, sc["chapters"])
        return finish(vid, d, sc, cds, c)
    except Exception as e:
        _upd(vid, status="error", step=str(e)[:200])
        log(LOG, f"Długi film nie wyszedł: {str(e)[:150]}", "error")
        return {"ok": False, "error": str(e)[:200]}
    finally:
        _busy.pop("run", None)


def finish(vid, d, sc, cds, c):
    """Od gotowego final.mp4: kontrola, zwiastuny, sprzątanie, status (też przy wznawianiu po restarcie serwera)."""
    f = d / "final.mp4"
    length, probs = check(f)
    n_img = sum(len(ch["scenes"]) for ch in sc["chapters"])
    if not probs:
        _upd(vid, step="zwiastuny (shorty)")
        try:
            trailers(d, sc, cds)
        except Exception as e:
            log(LOG, f"Zwiastuny nie wyszły: {str(e)[:120]}", "warn")
    for p in d.glob("*.png"):
        if p.name != "thumb.png":
            p.unlink(missing_ok=True)
    for p in list(d.glob("a*.mp3")) + list(d.glob("p*.mp4")) + list(d.glob("*.txt")):
        p.unlink(missing_ok=True)
    st = "error" if probs else "review"
    _upd(vid, status=st, step="; ".join(probs) if probs else "gotowy do zatwierdzenia", length=round(length),
         chapters=[ch["title"] for ch in sc["chapters"]], images=n_img)
    log(LOG, f"Długi film {'zatrzymany: ' + '; '.join(probs) if probs else 'gotowy'}: {sc['title']} ({length / 60:.1f} min, {n_img} kadrów)",
        "error" if probs else "ok")
    if not probs:
        try:
            from extra import push_all
            push_all("🎬 Długi film do zatwierdzenia" + (" (zdrowie)" if CH == "zdrowie" else ""), sc["title"], PUSH)
        except Exception:
            pass
        if c.get("auto_publish"):
            publish(vid)
    return {"ok": not probs, "id": vid}


def recover():
    """Po restarcie serwera (np. aktualizacja apki) film „w trakcie” nie ma już wątku, który go robi.
    Gotowy final.mp4 → dokończ (zwiastuny, publikacja); bez niego → usuń wpis, harmonogram zrobi film od nowa."""
    S = _state()
    # tylko filmy zaczęte PRZED startem serwera – film puszczony „Zrób teraz” w pierwszych 2 min po starcie to nie sierota
    for it in [x for x in S["items"] if x.get("status") == "working" and x.get("at", 0) < _START and x["id"] not in _busy.get("ids", set())]:
        d = WORK / it["id"]
        try:
            sc = json.loads((d / "script.json").read_text()) if (d / "script.json").exists() else None
            if sc and (d / "final.mp4").exists() and (d / "final.mp4").stat().st_size > 100_000:
                log(LOG, f"Wznawiam po restarcie serwera: {it.get('title', it['id'])} (był na etapie: {it.get('step', '?')})", "info")
                cds = [dur(d / f"a{ci}.mp3") if (d / f"a{ci}.mp3").exists() else 0 for ci in range(len(sc["chapters"]))]
                if not all(cds):
                    cds = [cds[0] or 50.0]
                _busy["run"] = True
                try:
                    finish(it["id"], d, sc, cds, cfg())
                finally:
                    _busy.pop("run", None)
            else:
                S2 = _state()
                S2["items"] = [x for x in S2["items"] if x["id"] != it["id"]]
                _save(S2)
                shutil.rmtree(d, ignore_errors=True)
                log(LOG, f"Film przerwany restartem serwera na etapie „{it.get('step', '?')}” – zrobię go od nowa", "warn")
        except Exception as e:
            _upd(it["id"], status="error", step=f"przerwany restartem serwera: {str(e)[:120]}")


FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"]


def _esc(t):
    return str(t).replace("\\", "").replace(":", "\\:").replace("'", "\u2019").replace("%", " percent")


def trailers(d, sc, cds):
    """2 pionowe shorty-zwiastuny z długiego filmu: początek (hook) i najmocniejszy moment (przedostatni rozdział).
    Kadr 16:9 na rozmytym tle 9:16 + tytuł u góry i „Full episode on the channel” na dole."""
    f = d / "final.mp4"
    n = max(1, int(cfg().get("shorts", 2)))
    # 1: początek (hook); dalej: najmocniejszy moment (przedostatni rozdział), potem kolejne rozdziały od początku
    ks = [0] + ([len(cds) - 2] if len(cds) >= 3 else []) + [k for k in range(1, len(cds)) if k != len(cds) - 2]
    ks = ks[:n]
    starts = [(sum(cds[:k]), min(55.0, max(20.0, cds[k] if k < len(cds) else 50))) for k in ks] if cds else [(0.0, 50.0)]
    chs = sc.get("chapters") or []
    (d / "trailers.json").write_text(json.dumps([{"n": i + 1, "ch": k, "title": (chs[k].get("title") if k and k < len(chs) else "") or ""}
                                                 for i, k in enumerate(ks)], ensure_ascii=False))
    font = next((x for x in FONTS if os.path.exists(x)), None)
    made = []
    for i, (ss, ln) in enumerate(starts):
        out = d / f"trailer{i + 1}.mp4"
        vf = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:3,eq=brightness=-0.08[bg];"
              "[b]scale=1080:-2[fg];[bg][fg]overlay=0:(H-h)/2")
        if font:
            lines, cur = [], ""
            for w_ in sc["title"].split():
                if len(cur) + len(w_) + 1 > 22 and cur:
                    lines.append(cur); cur = w_
                else:
                    cur = (cur + " " + w_).strip()
            lines = (lines + [cur])[:3]
            for li, tx in enumerate(lines):
                vf += f",drawtext=fontfile={font}:text='{_esc(tx)}':fontcolor=white:fontsize=66:x=(w-text_w)/2:y={250 + li * 92}:box=1:boxcolor=black@0.5:boxborderw=18"
            vf += f",drawtext=fontfile={font}:text='{FULL_EP}':fontcolor=white:fontsize=44:x=(w-text_w)/2:y=h-420:box=1:boxcolor=0x5FA987@0.9:boxborderw=20"
        subprocess.run(["nice", "-n", "15", "ffmpeg", "-y", "-ss", f"{ss:.2f}", "-t", f"{ln:.2f}", "-i", str(f), "-filter_complex", vf, "-r", "30",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
                       check=True, capture_output=True)
        made.append(out.name)
    return made


def _yt_upload(tok, f, meta):
    r = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=60,
                   headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(f.stat().st_size)}, json=meta)
    if r.status_code >= 300 or "location" not in r.headers:
        raise RuntimeError("YouTube: " + r.text[:200])
    with open(f, "rb") as fh:
        u = httpx.put(r.headers["location"], content=fh.read(), timeout=3600, headers={"Content-Type": "video/mp4"})
    if u.status_code >= 300:
        raise RuntimeError("YouTube (wysyłka): " + u.text[:200])
    return u.json().get("id")


def us_slot(after=None, min_lead=1800):
    """Najbliższa godzina publikacji pod widzów z USA (domyślnie 14:00 czasu wschodniego – szczyt oglądania YouTube)."""
    now = datetime.fromtimestamp(after or time.time(), US)
    t = now.replace(hour=int(cfg().get("us_hour", 14)), minute=0, second=0, microsecond=0)
    while t.timestamp() < (after or time.time()) + min_lead:
        t += timedelta(days=1)
    return t


def _iso(t):
    return datetime.fromtimestamp(t.timestamp(), ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_trailer(vid, n, at=None):
    """Zwiastun n (1 = zaraz po długim filmie, 2 = dzień później) jako Short z linkiem do pełnego odcinka."""
    from social import _google_access
    S = _state()
    it = next((x for x in S["items"] if x["id"] == vid), None)
    f = WORK / vid / f"trailer{n}.mp4"
    if not it or not it.get("yt") or not f.exists():
        return None
    sc = json.loads((WORK / vid / "script.json").read_text())
    _, acc = _account()
    tok = _google_access(acc)
    title = sc["title"]
    try:   # kolejne shorty z tego samego filmu: tytuł rozdziału, żeby nie było 5 takich samych tytułów
        tj = json.loads((WORK / vid / "trailers.json").read_text())
        ct = next((x["title"] for x in tj if x["n"] == n), "")
        if ct and n > 1:
            title = ct
    except Exception:
        pass
    yid = _yt_upload(tok, f, {"snippet": {"title": (title[:80] + " #shorts"), "categoryId": "27", "defaultLanguage": LANG,
                                          "description": (f"Cały odcinek 👉 https://youtu.be/" if LANG == "pl" else f"Full episode 👉 https://youtu.be/") + f"{it['yt']}\n\n{TAGLINE}",
                                          "tags": (sc.get("tags") or [])[:10]},
                              "status": ({"privacyStatus": "private", "publishAt": _iso(at)} if at else {"privacyStatus": "public"}) | {"selfDeclaredMadeForKids": False}})
    f.unlink(missing_ok=True)
    tr = it.get("trailers", {})
    tr[str(n)] = yid
    _upd(vid, trailers=tr)
    log(LOG, f"Zwiastun {n} (Short) wrzucony: {sc['title']}", "ok")
    return yid


def publish(vid):
    from social import _google_access
    S = _state()
    it = next((x for x in S["items"] if x["id"] == vid), None)
    d = WORK / vid
    if not it or not (d / "final.mp4").exists():
        return {"ok": False, "error": "nie ma filmu"}
    sc = json.loads((d / "script.json").read_text())
    _, acc = _account()
    if not acc:
        return {"ok": False, "error": "brak podłączonego kanału YouTube " + ("PozaSkala" if CH == "pozaskala" else "Zdrowie 50+")}
    tok = _google_access(acc)
    _upd(vid, status="uploading", step="wysyłam na YouTube")
    meta = {"snippet": {"title": sc["title"][:100], "description": sc.get("description", "")[:4900], "tags": (sc.get("tags") or [])[:15],
                        "categoryId": "27", "defaultLanguage": LANG},
            "status": {"privacyStatus": "private", "publishAt": _iso(when := us_slot()), "selfDeclaredMadeForKids": False}}
    f = d / "final.mp4"
    try:
        r = httpx.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status", timeout=60,
                       headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json", "X-Upload-Content-Type": "video/mp4",
                                "X-Upload-Content-Length": str(f.stat().st_size)}, json=meta)
        if r.status_code >= 300 or "location" not in r.headers:
            raise RuntimeError("YouTube: " + r.text[:200])
        with open(f, "rb") as fh:
            u = httpx.put(r.headers["location"], content=fh.read(), timeout=3600, headers={"Content-Type": "video/mp4"})
        if u.status_code >= 300:
            raise RuntimeError("YouTube (wysyłka): " + u.text[:200])
        yid = u.json().get("id")
        if (d / "thumb.png").exists():   # okładka – działa tylko na zweryfikowanym kanale, błąd ignorujemy
            try:
                httpx.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={yid}", timeout=120,
                           headers={"Authorization": "Bearer " + tok, "Content-Type": "image/png"}, content=(d / "thumb.png").read_bytes())
            except Exception:
                pass
        pl = datetime.fromtimestamp(when.timestamp(), TZ)
        _upd(vid, status="published", step=f"zaplanowany: {pl:%d.%m %H:%M} (w USA {when:%H:%M} ET)", yt=yid, published=time.time(), publish_at=when.timestamp())
        log(LOG, f"Długi film zaplanowany na {pl:%d.%m %H:%M} ({when:%H:%M} w USA): {sc['title']}", "ok")
        (d / "final.mp4").unlink(missing_ok=True)   # oszczędność dysku – film jest już na YouTube
        try:
            publish_trailer(vid, 1, at=datetime.fromtimestamp(when.timestamp() + 1800, US))   # zwiastun pół godziny po filmie
        except Exception as e:
            log(LOG, f"Zwiastun 1 nie wyszedł: {str(e)[:120]}", "warn")
        return {"ok": True, "yt": yid}
    except Exception as e:
        _upd(vid, status="review", step="publikacja nie wyszła: " + str(e)[:150])
        return {"ok": False, "error": str(e)[:200]}


def _loop():
    def run():
        time.sleep(120)
        try:
            recover()
        except Exception as e:
            print("dlugi recover", e, flush=True)
        while True:
            try:
                c, now = cfg(), datetime.now(TZ)
                S = _state()
                today = now.strftime("%y%m%d")
                if c["enabled"] and now.weekday() in c["days"] and now.hour >= c["hour"] and not any(x["id"].startswith(today) for x in S["items"]):
                    produce()
                for it in S["items"]:
                    if it.get("status") != "published" or not it.get("yt") or time.time() - it.get("published", 0) < 3600:
                        continue
                    for n in range(2, 9):   # kolejne shorty z filmu – po jednym dziennie, o tej samej godzinie dla USA
                        if str(n) in (it.get("trailers") or {}) or not (WORK / it["id"] / f"trailer{n}.mp4").exists():
                            continue
                        try:
                            publish_trailer(it["id"], n, at=us_slot(after=it.get("publish_at", it.get("published", time.time())) + (n - 1) * 86400 - 1800, min_lead=0))
                        except Exception as e:
                            print("dlugi trailer", n, e, flush=True)
                # sprzątanie: odrzucone/stare katalogi > 10 dni
                for p in WORK.glob("*"):
                    if p.is_dir() and time.time() - p.stat().st_mtime > 10 * 86400:
                        shutil.rmtree(p, ignore_errors=True)
            except Exception as e:
                print("dlugi", e, flush=True)
            time.sleep(600)
    threading.Thread(target=run, daemon=True).start()


_loop()


# ---------------------------------------------------------------- API
@router.get(API)
def api_state():
    S = _state()
    return {"cfg": cfg(), "busy": bool(_busy.get("run")), "items": list(reversed(S["items"]))[:12]}


@router.post(API + "/run")
def api_run():
    if _busy.get("run"):
        return {"ok": False, "error": "już trwa"}
    threading.Thread(target=produce, args=("ręcznie",), daemon=True).start()
    return {"ok": True}


@router.post(API + "/{vid}/{action}")
def api_action(vid: str, action: str):
    if not re.fullmatch(r"[\w-]{4,20}", vid):
        return {"ok": False}
    if action == "publish":
        threading.Thread(target=publish, args=(vid,), daemon=True).start()
        return {"ok": True}
    if action == "finish":
        d = WORK / vid
        if not (d / "final.mp4").exists() or not (d / "script.json").exists() or _busy.get("run"):
            return {"ok": False, "error": "brak gotowego filmu"}
        sc = json.loads((d / "script.json").read_text())
        w = [len(ch["narration"].split()) for ch in sc["chapters"]]
        tot = dur(d / "final.mp4")
        cds = [tot * x / max(1, sum(w)) for x in w]
        _upd(vid, status="working", step="kontrola i zwiastuny")

        def _go():
            _busy["run"] = True
            try:
                finish(vid, d, sc, cds, cfg())
            except Exception as e:
                _upd(vid, status="error", step=str(e)[:200])
            finally:
                _busy.pop("run", None)
        threading.Thread(target=_go, daemon=True).start()
        return {"ok": True}
    if action == "reject":
        _upd(vid, status="rejected", step="odrzucony")
        shutil.rmtree(WORK / vid, ignore_errors=True)
        return {"ok": True}
    return {"ok": False}


@router.post(API + "-cfg")
async def api_cfg(req: Request):
    b = await req.json()
    S = _state()
    c = {**cfg(), **{k: b[k] for k in ("enabled", "days", "hour", "auto_publish", "us_hour") if k in b}}
    S["cfg"] = c
    _save(S)
    return {"ok": True, "cfg": c}


@router.get(API + "/{vid}/{name}")
def api_file(vid: str, name: str):
    if not re.fullmatch(r"[\w-]{4,20}", vid) or name not in ("final.mp4", "thumb.png"):
        return JSONResponse({"error": "nie"}, status_code=404)
    f = WORK / vid / name
    return FileResponse(f) if f.exists() else JSONResponse({"error": "nie ma"}, status_code=404)


# ---------------------------------------------------------------- podgląd nowej postaci (historie firm)
STYLE_BIZ = ("3D voxel art render, blocky cube-shaped world and characters made of cubes like a sandbox block-building game, "
             "the main character Max: a blocky cube-headed man with a square head, simple pixel face, short brown hair and a RED NECKTIE always on "
             "as his signature, wearing {outfit}; soft warm cinematic lighting, colorful, depth of field, simple charts and coins also made of blocks, "
             "horizontal 16:9, no text, no letters, no logos, no real brand names, no watermark, original generic characters")

if CH == "pozaskala":
    @router.post("/api/mont-hook/postac-biz")
    async def postac_biz(req: Request):
        """Podgląd stickmana do historii firm w kilku strojach (bez logo) – wywoływane z Maca tokenem montażu."""
        import montaz
        if not montaz._tok(req):
            return JSONResponse({"error": "brak dostępu"}, status_code=403)
        b = await req.json()
        out = []
        for i, w in enumerate((b.get("warianty") or [])[:4]):
            f = WORK / f"_postac_{i}.png"
            WORK.mkdir(parents=True, exist_ok=True)
            global STYLE
            stare = STYLE
            try:
                STYLE = w["styl"] if w.get("styl") else STYLE_BIZ.format(outfit=w["stroj"])
                import asyncio
                await asyncio.to_thread(image, w["scena"], f, b.get("quality", "medium"), w.get("size", "1536x1024"))
            except Exception as e:
                out.append({"error": str(e)[:200]}); continue
            finally:
                STYLE = stare
            out.append({"b64": base64.b64encode(f.read_bytes()).decode()})
        return {"ok": True, "obrazy": out}
