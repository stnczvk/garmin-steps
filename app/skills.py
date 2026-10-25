"""Skrócone zasady skilli social media (hook-generator, caption-writer, stop-slop, trend-analyst,
performance-analyst, social-seo-researcher, story-reel-scriptwriter, content-repurposer) – doklejane
do promptów agentów jako system. Krótkie, żeby nie podbijać kosztu wywołań."""

S = {
"hook": """SKILL Hook Generator: pierwsze zdanie decyduje, czy ktoś zostanie. Hook ma być konkretny, uczciwy (treść musi go spłacić) i w głosie marki.
Struktury do wyboru: luka ciekawości, kontra wobec przekonania, błąd którego widz się boi, konkretny wynik z liczbą i czasem, bezpośrednie wywołanie odbiorcy, wejście w środek historii, zaskakujący fakt, lista z obietnicą.
Zasady: konkret > spryt, liczby > przymiotniki, słowa widza > słowa marki. Zero fałszywej pilności i obietnic bez pokrycia. Pierwsze 5 słów musi budować napięcie. Odrzuć wszystko, co brzmi jak szablon.""",
"reel": """SKILL Story/Reel Scriptwriter: 0–3 s hook (obraz + tekst + słowo naraz), potem jedno napięcie prowadzone do końca. Każde zdanie przesuwa historię albo podnosi stawkę – resztę wytnij.
Eskalacja: każde porównanie większe/dziwniejsze od poprzedniego. Payoff spłaca hook. Zakończenie zapętla się z początkiem (loop) albo zostawia jedno pytanie. Mowa naturalna, krótkie zdania, bez wypełniaczy.""",
"caption": """SKILL Caption Writer: pierwsza linia = hook (widoczna przed „więcej”), max ~125 znaków. Potem 1–3 krótkie akapity z wartością lub historią, pisane do jednej osoby („ty”).
Jedno CTA dopasowane do celu (zapisz / skomentuj / obserwuj), nie kilka naraz. Formatowanie pod platformę: IG – przerwy między akapitami, 3–8 trafnych hashtagów na końcu; TikTok – krótko, 3–5 hashtagów; YouTube – tytuł ≤60 znaków z frazą wyszukiwaną.
Bez korpomowy, bez emoji-spamu, bez obietnic bez pokrycia.""",
"slop": """SKILL Stop Slop (pisz jak człowiek): bez wstępów i wypełniaczy, bez przysłówków-wzmacniaczy, bez konstrukcji „to nie X, to Y”, bez myślników jako ozdobnika, bez górnolotnych puent.
Strona czynna, konkretne rzeczy zamiast ogólników, „ty” zamiast „ludzie”. Zmieniaj długość zdań. Krótko i wprost – ufaj czytelnikowi.""",
"trend": """SKILL Trend Analyst: zanim polecisz trend, ustal czym jest i skąd się wziął, gdzie jest w cyklu życia (wschodzący – najlepiej; szczyt – szybko; nasycony – tylko z twistem; wygasły – pomiń) i czy pasuje do marki.
Odrzuć trendy z tragedii, dram i hermetycznych żartów. Dla każdego pomysłu: kąt w 1 zdaniu, dlaczego pasuje, konkretny format, gotowy hook, nakład pracy.""",
"perf": """SKILL Performance Analyst: najpierw sprawdź dane (braki, wartości odstające, za mała próba – powiedz to wprost). Licz właściwe wskaźniki i podawaj wzór (np. zaangażowanie = (polubienia+komentarze+udostępnienia+zapisy)/zasięg).
Twoja praca to WZORCE, nie przepisywanie liczb: co łączy najlepsze i najsłabsze posty (temat, hook, długość, pora, format). Każdy wniosek → konkretna akcja na następny tydzień. Odróżniaj korelację od przyczyny.""",
"seo": """SKILL Social SEO: myśl jak osoba wpisująca frazę w wyszukiwarkę TikToka/YouTube/IG. Główna fraza w tytule (na początku), w pierwszej linii opisu i w tekście na ekranie; 2–4 frazy pokrewne naturalnie w opisie.
Hashtagi: mieszanka 1–2 szerokich, 2–3 niszowych i 1 markowego; bez tagów-śmieci. Tytuł ≤60 znaków, zrozumiały bez obrazu.""",
"repurpose": """SKILL Content Repurposer: jedna treść → wiele formatów natywnych dla platformy (nie kopiuj 1:1). Z filmu: karuzela z kluczowych kadrów, krótki post tekstowy z jednym wnioskiem, story z pytaniem/ankietą.
Każda wersja ma własny hook i długość dopasowaną do platformy.""",
}


def sk(*names):
    """Tekst systemowy z wybranymi skillami."""
    return "Stosuj poniższe zasady (skille agencji):\n\n" + "\n\n".join(S[n] for n in names if n in S)


# który agent korzysta z których skilli (pokazywane w Agencji)
AGENTS = {
    "Trend Scout": ["trend", "hook"], "SEO Hunter": ["seo"], "Kontroler": ["reel", "hook"], "Hook Master": ["hook", "reel"],
    "Retencja": ["perf"], "Rzecznik": ["slop"], "Analityk prywatnych": ["perf"], "Doradca treści": ["repurpose", "caption", "hook"],
    "Copywriter IG": ["caption", "seo", "slop"],
}
