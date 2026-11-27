"""Test produktu: „Kara za parking pod marketem? Sprawdź, czy musisz płacić” – darmowa ocena (AI) + fałszywe drzwi
„gotowe odwołanie za 19 zł” (zapis e-maila), żeby zmierzyć popyt przed budową płatności.
Strona publiczna: /parking · API publiczne: /api/pub/parking/* · statystyki (po zalogowaniu): /api/parking/stats"""
import json, re, threading, time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from social import _read, _write

router = APIRouter()
_lock = threading.Lock()
_ip = {}


def _stat(k):
    with _lock:
        S = _read("parking_stats.json", {})
        d = time.strftime("%Y-%m-%d")
        S.setdefault(d, {}).setdefault(k, 0)
        S[d][k] += 1
        _write("parking_stats.json", S)


def _limit(req, n=8):
    ip = req.headers.get("x-forwarded-for", req.client.host if req.client else "?").split(",")[0].strip()
    now = time.time()
    L = [t for t in _ip.get(ip, []) if now - t < 86400]
    if len(L) >= n:
        return False
    _ip[ip] = L + [now]
    return True


SYS = """Jesteś asystentem, który pomaga kierowcom w Polsce ocenić wezwanie do zapłaty (tzw. opłatę dodatkową) z prywatnego parkingu,
np. pod marketem (operatorzy typu APCOA, EuroPark, ATPark). Piszesz po polsku, prosto, konkretnie, bez straszenia.
Zasady:
- To NIE jest porada prawna. Nie gwarantujesz wyniku. Nie każesz bezwzględnie „nie płacić” – przedstawiasz opcje.
- Fakty, na których możesz się opierać: wezwanie od prywatnego zarządcy to nie mandat karny, tylko roszczenie cywilne;
  można złożyć reklamację do operatora; przy sporze sprawę rozstrzyga sąd cywilny; pomoc bezpłatnie daje miejski/powiatowy
  rzecznik konsumentów; UOKiK interweniował w sprawie praktyk operatorów (np. złe oznakowanie, wezwania wystawiane zanim klient
  wrócił z biletem). Liczy się: czy był wyraźny regulamin i oznakowanie, czy kierowca był klientem (paragon), czy miał bilet,
  błąd w numerze rejestracyjnym, awaria parkomatu, czas pobytu.
- Nie wymyślaj numerów artykułów, wyroków ani dat decyzji.
Zwróć TYLKO JSON:
{"szansa":"niska|średnia|wysoka","dlaczego":"1-2 zdania","argumenty":["3-5 konkretnych argumentów do reklamacji, dopasowanych do sytuacji"],
"dowody":["co zebrać: np. paragon, zdjęcie biletu, zdjęcie oznakowania"],"kroki":["3-4 kroki co zrobić teraz, w kolejności"],
"uwaga":"1 zdanie, czego się wystrzegać"}"""


@router.post("/api/pub/parking/ocena")
async def ocena(req: Request):
    if not _limit(req):
        return JSONResponse({"error": "Za dużo zapytań z tego adresu – spróbuj jutro."}, status_code=429)
    b = await req.json()
    pola = {k: str(b.get(k, ""))[:400] for k in ("operator", "kwota", "klient", "paragon", "co", "oznakowanie", "kiedy", "opis")}
    from agency import claude, as_json, FAST
    import asyncio
    try:
        r = as_json(await asyncio.to_thread(lambda: claude("Sytuacja kierowcy:\n" + json.dumps(pola, ensure_ascii=False), FAST, 1200, system=SYS)))
    except Exception:
        r = None
    if not isinstance(r, dict) or not r.get("argumenty"):
        return JSONResponse({"error": "Nie udało się przygotować oceny – spróbuj ponownie za chwilę."}, status_code=503)
    _stat("ocena")
    return r


@router.post("/api/pub/parking/chce")
async def chce(req: Request):
    """Fałszywe drzwi: ktoś chce gotowe odwołanie za 19 zł → zapis e-maila (za zgodą) i licznik."""
    b = await req.json()
    mail = str(b.get("email", "")).strip()[:120]
    if not mail:
        _stat("chce")
    if mail and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail) and b.get("zgoda"):
        with _lock:
            L = _read("parking_leady.json", [])
            L.append({"email": mail, "at": time.time()})
            _write("parking_leady.json", L[-2000:])
        _stat("email")
        try:
            from extra import push_all
            push_all("🅿️ Ktoś chce odwołanie (19 zł)", "Test parkingowy: nowy chętny zostawił e-mail", "/#home")
        except Exception:
            pass
    return {"ok": True}


@router.get("/api/parking/stats")
def stats():
    S = _read("parking_stats.json", {})
    tot = {}
    for d in S.values():
        for k, v in d.items():
            tot[k] = tot.get(k, 0) + v
    return {"razem": tot, "dni": S, "emaile": len(_read("parking_leady.json", []))}


@router.get("/parking")
def strona():
    _stat("wejscie")
    return HTMLResponse(PAGE)


PAGE = r"""<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kara za parking pod marketem? Sprawdź, czy musisz płacić</title>
<meta name="description" content="Dostałeś wezwanie do zapłaty za parking pod Lidlem, Biedronką albo innym marketem? Sprawdź za darmo swoje szanse na skuteczną reklamację.">
<style>
:root{--bg:#f6f5f1;--ink:#16181d;--mut:#5d6270;--acc:#1f6feb;--ok:#1a7f37;--warn:#b54708;--card:#fff;--line:#e3e1da}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
.w{max-width:640px;margin:0 auto;padding:28px 18px 60px}h1{font-size:30px;line-height:1.15;margin:8px 0 10px;letter-spacing:-.02em}
.lead{color:var(--mut);margin:0 0 22px}.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:20px;margin-bottom:16px}
label{display:block;font-weight:600;margin:14px 0 6px}select,input,textarea{width:100%;font:inherit;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:#fff}
textarea{min-height:80px}.row{display:flex;gap:10px}.row>*{flex:1}
button{font:inherit;font-weight:700;border:0;border-radius:12px;padding:14px 18px;background:var(--acc);color:#fff;width:100%;margin-top:18px;cursor:pointer}
button:disabled{opacity:.6}.tag{display:inline-block;font-size:13px;font-weight:700;padding:3px 10px;border-radius:99px;background:#eef2ff;color:var(--acc)}
.sz{font-size:22px;font-weight:800;margin:6px 0}.sz.wysoka{color:var(--ok)}.sz.średnia{color:var(--warn)}.sz.niska{color:#b42318}
ul{padding-left:20px;margin:6px 0}li{margin:4px 0}.small{font-size:13px;color:var(--mut)}.cta{border:2px solid var(--acc)}
.ck{display:flex;gap:8px;align-items:flex-start;font-weight:400;font-size:14px}.ck input{width:auto;margin-top:4px}
</style></head><body><div class="w">
<span class="tag">Bezpłatna ocena w 1 minutę</span>
<h1>Kartka za wycieraczką pod marketem? Sprawdź, czy musisz płacić</h1>
<p class="lead">Wezwanie od prywatnego parkingu (Lidl, Biedronka, Aldi, galerie) to nie mandat. Odpowiedz na kilka pytań, a powiemy Ci, jakie masz szanse na reklamację i co zrobić teraz.</p>
<div class="card"><form id="f">
<label>Kto wystawił wezwanie?</label><select name="operator"><option>APCOA</option><option>EuroPark</option><option>ATPark</option><option>Inny / nie wiem</option></select>
<div class="row"><div><label>Kwota</label><input name="kwota" inputmode="numeric" placeholder="np. 150 zł"></div>
<div><label>Kiedy dostałeś?</label><select name="kiedy"><option>w ciągu ostatnich 14 dni</option><option>ponad 14 dni temu</option><option>przyszło pismo od windykacji</option></select></div></div>
<label>Co się stało?</label><select name="co"><option>Nie zauważyłem parkometru / nie wiedziałem, że trzeba pobrać bilet</option><option>Zabrałem bilet ze sobą zamiast zostawić za szybą</option><option>Przekroczyłem darmowy czas</option><option>Źle wpisany numer rejestracyjny</option><option>Parkomat nie działał</option><option>Wezwanie wystawili, gdy szedłem po bilet</option><option>Inna sytuacja</option></select>
<div class="row"><div><label>Robiłeś tam zakupy?</label><select name="klient"><option>tak</option><option>nie</option></select></div>
<div><label>Masz paragon?</label><select name="paragon"><option>tak</option><option>nie</option><option>nie wiem</option></select></div></div>
<label>Było wyraźne oznakowanie płatnego parkingu?</label><select name="oznakowanie"><option>nie wiem / nie zwróciłem uwagi</option><option>raczej nie, słabo widoczne</option><option>tak, wyraźne</option></select>
<label>Coś jeszcze? <span class="small">(opcjonalnie)</span></label><textarea name="opis" maxlength="400" placeholder="np. byłem w sklepie 25 minut, bilet mam w telefonie na zdjęciu"></textarea>
<button id="b">Sprawdź moje szanse</button>
<p class="small">To nie jest porada prawna. Nie zapisujemy Twoich odpowiedzi.</p>
</form></div>
<div id="out"></div>
<p class="small">Bezpłatną pomoc daje też miejski lub powiatowy rzecznik konsumentów.</p>
</div>
<script>
const f=document.getElementById('f'),out=document.getElementById('out'),b=document.getElementById('b');
const E=t=>String(t||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
f.onsubmit=async e=>{e.preventDefault();b.disabled=true;b.textContent='Analizuję…';out.innerHTML='';
 try{const r=await fetch('/api/pub/parking/ocena',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(f)))});
  const d=await r.json();if(!r.ok){out.innerHTML='<div class="card">'+E(d.error)+'</div>';return}
  const L=a=>'<ul>'+(a||[]).map(x=>'<li>'+E(x)+'</li>').join('')+'</ul>';
  out.innerHTML=`<div class="card"><div class="small">Szansa na skuteczną reklamację</div><div class="sz ${E(d.szansa)}">${E(d.szansa)}</div><p>${E(d.dlaczego)}</p>
  <b>Twoje argumenty</b>${L(d.argumenty)}<b>Co zebrać</b>${L(d.dowody)}<b>Co zrobić teraz</b>${L(d.kroki)}<p class="small">⚠️ ${E(d.uwaga)}</p></div>
  <div class="card cta"><b>Chcesz gotowe odwołanie do wysłania?</b><p class="small">Przygotujemy pismo z Twoimi danymi i tymi argumentami, w PDF i jako tekst do maila. Cena: 19 zł.</p>
  <button id="c">Chcę gotowe odwołanie (19 zł)</button><div id="cw"></div></div>`;
  document.getElementById('c').onclick=async()=>{fetch('/api/pub/parking/chce',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   document.getElementById('cw').innerHTML=`<p>Usługa startuje za kilka dni. Zostaw e-mail, a damy znać jako pierwszemu (z rabatem).</p>
   <input id="em" type="email" placeholder="twój@email.pl"><label class="ck"><input type="checkbox" id="zg"> Zgadzam się na jeden e-mail z informacją o starcie usługi.</label><button id="s">Zapisz mnie</button>`;
   document.getElementById('s').onclick=async()=>{const em=document.getElementById('em').value,zg=document.getElementById('zg').checked;if(!zg){alert('Zaznacz zgodę');return}
    await fetch('/api/pub/parking/chce',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:em,zgoda:zg})});
    document.getElementById('cw').innerHTML='<p>✅ Dzięki! Odezwiemy się, gdy usługa wystartuje.</p>'}}}
 catch(e){out.innerHTML='<div class="card">Coś poszło nie tak, spróbuj ponownie.</div>'}finally{b.disabled=false;b.textContent='Sprawdź moje szanse'}};
</script></body></html>"""
