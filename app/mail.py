"""Poczta iCloud (mail do współprac) w centrum: odczyt przez IMAP, streszczenie i szkic odpowiedzi od AI,
wysyłka odpowiedzi przez SMTP po Twoim kliknięciu „Wyślij”. Hasło to hasło dla aplikacji z account.apple.com
(zapisane tylko na serwerze, nigdy nie wraca do przeglądarki)."""
import email, email.utils, imaplib, re, smtplib, ssl, threading, time
from email.header import decode_header, make_header
from email.message import EmailMessage

from fastapi import APIRouter, Request

from social import _read, _write

router = APIRouter()
CFG, DATA = "mail_cfg.json", "mail.json"
IMAP_H, SMTP_H = "imap.mail.me.com", "smtp.mail.me.com"
_lk = threading.RLock()
_kick = threading.Event()


def _cfg():
    return _read(CFG, {})


def _dec(s):
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


def _text(msg):
    """Treść maila jako zwykły tekst (preferuje text/plain, inaczej czyści HTML)."""
    plain, html = None, None
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            body = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and plain is None:
            plain = body
        elif part.get_content_type() == "text/html" and html is None:
            html = body
    if plain is None and html:
        plain = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        plain = re.sub(r"<br\s*/?>|</p>|</div>", "\n", plain, flags=re.I)
        plain = re.sub(r"<[^>]+>", " ", plain)
        plain = re.sub(r"&nbsp;", " ", plain)
    t = re.sub(r"[ \t]+", " ", plain or "")
    t = re.sub(r"\n\s*\n+", "\n\n", t).strip()
    # bez cytowanej historii
    t = re.split(r"\n(?:On .+ wrote:|W dniu .+ napisał|-----Original Message-----|From: .+\nSent:)", t)[0]
    return t[:6000]


def _imap(c):
    m = imaplib.IMAP4_SSL(IMAP_H, 993, ssl_context=ssl.create_default_context())
    m.login(c["email"], c["password"])
    return m


def _ai(item):
    from agency import ai_ready, claude, as_json
    if not ai_ready():
        return {}
    p = ("Jesteś asystentem twórcy treści (fitness/redukcja, konto prywatne, sklep Etsy). Oceń maila ze skrzynki do współprac.\n"
         f"OD: {item['from']}\nTEMAT: {item['subject']}\nTREŚĆ:\n{item['body'][:3500]}\n\n"
         "Zwróć TYLKO JSON: {\"cat\": \"wspolpraca|etsy|praca|konto|newsletter|spam|inne\", \"important\": true/false, "
         "\"summary\": \"1-2 zdania po polsku: kto, czego chce, konkrety (kwota, termin)\", "
         "\"reply\": \"krótki, uprzejmy szkic odpowiedzi ZAWSZE PO POLSKU (nawet gdy mail jest po angielsku – Bartłomiej sam zdecyduje, czy przetłumaczyć), od Bartłomieja; przy współpracy dopytaj o szczegóły (budżet, zakres, termin) zamiast od razu się zgadzać; "
         "pusty string, jeśli odpowiedź nie ma sensu (newsletter, spam, powiadomienie)\"}. "
         "important = współpraca, propozycja płatna, pytanie od kupującego, odpowiedź z rekrutacji albo coś wymagającego odpowiedzi.")
    try:
        return as_json(claude(p, max_tokens=900)) or {}
    except Exception:
        return {}


def poll(n=120):
    c = _cfg()
    if not c.get("email") or not c.get("password"):
        return
    with _lk:
        D = _read(DATA, {"items": []})
        have = {str(x["uid"]): x for x in D.get("items", [])}
        m = _imap(c)
        try:
            m.select("INBOX")
            typ, data = m.uid("search", None, "ALL")
            uids = (data[0] or b"").split()[-n:]
            unseen = set((m.uid("search", None, "UNSEEN")[1][0] or b"").split())
            items, new_imp = [], []
            for u in reversed(uids):
                us = u.decode()
                if us in have:
                    it = have[us]; it["seen"] = u not in unseen; items.append(it); continue
                typ, md = m.uid("fetch", u, "(BODY.PEEK[])")
                if not md or not md[0]:
                    continue
                msg = email.message_from_bytes(md[0][1])
                frm = _dec(msg.get("From"))
                it = {"uid": us, "from": frm, "addr": email.utils.parseaddr(frm)[1], "subject": _dec(msg.get("Subject")) or "(bez tematu)",
                      "date": msg.get("Date"), "ts": (email.utils.parsedate_to_datetime(msg.get("Date")).timestamp() if msg.get("Date") else time.time()),
                      "msgid": msg.get("Message-ID"), "refs": msg.get("References"), "body": _text(msg), "seen": u not in unseen}
                a = _ai(it)
                it.update(cat=a.get("cat") or "inne", important=bool(a.get("important")), summary=a.get("summary") or "", draft=a.get("reply") or "")
                items.append(it)
                if it["important"] and not it["seen"]:
                    new_imp.append(it)
        finally:
            try:
                m.logout()
            except Exception:
                pass
        redo = 0   # stare szkice (po angielsku) – jednorazowo na nowo, po polsku
        for it in items:
            if it.get("dv") != 2 and it.get("important") and redo < 8 and str(it["uid"]) not in (D.get("sent") or {}):
                a = _ai(it); it["draft"] = a.get("reply") or it.get("draft", ""); redo += 1
            it["dv"] = 2
        D = {"items": sorted(items, key=lambda x: -x.get("ts", 0))[:n], "at": time.time(), "error": None,
             "sent": D.get("sent", {}), "done": D.get("done", [])}
        _write(DATA, D)
    for it in new_imp[:3]:
        try:
            from extra import push_all
            push_all(f"✉️ {it['from'].split('<')[0].strip()[:40]}", it["summary"] or it["subject"], "/#home")
        except Exception:
            pass


def _loop():
    time.sleep(60)
    while True:
        try:
            poll()
        except Exception as e:
            D = _read(DATA, {"items": []}); D["error"] = str(e)[:200]; D["at"] = time.time(); _write(DATA, D)
        _kick.wait(300); _kick.clear()


threading.Thread(target=_loop, daemon=True).start()


# ---------- API ----------
@router.get("/api/mail")
def api_mail():
    c, D = _cfg(), _read(DATA, {"items": []})
    done = set(D.get("done", []))
    items = [{k: v for k, v in x.items() if k not in ("msgid", "refs")} for x in D.get("items", [])]
    for x in items:
        x["replied"] = x["uid"] in (D.get("sent") or {}); x["done"] = x["uid"] in done
    return {"configured": bool(c.get("email") and c.get("password")), "email": c.get("email", ""), "at": D.get("at"), "error": D.get("error"),
            "unread": sum(1 for x in items if not x["seen"]), "items": items}


@router.post("/api/mail/cfg")
async def api_cfg(req: Request):
    b = await req.json()
    em, pw = str(b.get("email", "")).strip(), re.sub(r"\s+", "", str(b.get("password", "")))
    if b.get("disconnect"):
        _write(CFG, {}); _write(DATA, {"items": []}); return {"ok": True}
    if not em or not pw:
        return {"ok": False, "error": "Podaj adres iCloud i hasło dla aplikacji."}
    try:
        _imap({"email": em, "password": pw}).logout()
    except Exception as e:
        return {"ok": False, "error": "iCloud nie przyjął logowania – sprawdź adres i hasło dla aplikacji (nie główne hasło Apple). " + str(e)[:80]}
    _write(CFG, {"email": em, "password": pw}); _kick.set()
    return {"ok": True}


@router.post("/api/mail/refresh")
def api_refresh():
    _kick.set(); return {"ok": True}


def _find(uid):
    D = _read(DATA, {"items": []})
    return D, next((x for x in D.get("items", []) if str(x["uid"]) == str(uid)), None)


@router.post("/api/mail/{uid}/seen")
def api_seen(uid: str):
    c = _cfg()
    try:
        m = _imap(c); m.select("INBOX"); m.uid("store", uid, "+FLAGS", "(\\Seen)"); m.logout()
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    with _lk:
        D, it = _find(uid)
        if it: it["seen"] = True
        D.setdefault("done", []).append(str(uid)); D["done"] = D["done"][-300:]; _write(DATA, D)
    return {"ok": True}


@router.post("/api/mail/{uid}/unseen")
def api_unseen(uid: str):
    c = _cfg()
    try:
        m = _imap(c); m.select("INBOX"); m.uid("store", uid, "-FLAGS", "(\\Seen)"); m.logout()
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    with _lk:
        D, it = _find(uid)
        if it: it["seen"] = False
        D["done"] = [x for x in D.get("done", []) if x != str(uid)]; _write(DATA, D)
    return {"ok": True}


@router.post("/api/mail/{uid}/open")
def api_open(uid: str):
    """Otwarcie w zakładce Poczta = przeczytany (bez chowania z listy)."""
    c = _cfg()
    try:
        m = _imap(c); m.select("INBOX"); m.uid("store", uid, "+FLAGS", "(\\Seen)"); m.logout()
    except Exception:
        pass
    with _lk:
        D, it = _find(uid)
        if it: it["seen"] = True; _write(DATA, D)
    return {"ok": True}


@router.post("/api/mail/{uid}/draft")
def api_draft(uid: str):
    with _lk:
        D, it = _find(uid)
        if not it:
            return {"ok": False, "error": "nie ma takiego maila"}
        a = _ai(it); it["draft"] = a.get("reply") or it.get("draft", ""); _write(DATA, D)
    return {"ok": True, "draft": it["draft"]}


@router.post("/api/mail/translate")
async def api_translate(req: Request):
    b = await req.json()
    txt = str(b.get("text", "")).strip()
    if not txt:
        return {"ok": False, "error": "Pusty tekst."}
    from agency import claude
    try:
        out = claude("Przetłumacz tę odpowiedź na mail na naturalny, uprzejmy angielski (zachowaj sens i ton, bez dodawania treści). "
                     "Zwróć TYLKO przetłumaczony tekst.\n\n" + txt[:4000], max_tokens=1200).strip()
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
    return {"ok": True, "text": out}


@router.post("/api/mail/{uid}/reply")
async def api_reply(uid: str, req: Request):
    b = await req.json()
    txt = str(b.get("text", "")).strip()
    if not txt:
        return {"ok": False, "error": "Pusta odpowiedź."}
    c = _cfg()
    D, it = _find(uid)
    if not it or not it.get("addr"):
        return {"ok": False, "error": "Nie znam adresu nadawcy."}
    msg = EmailMessage()
    msg["From"], msg["To"] = c["email"], it["addr"]
    msg["Subject"] = it["subject"] if it["subject"].lower().startswith("re:") else "Re: " + it["subject"]
    if it.get("msgid"):
        msg["In-Reply-To"] = it["msgid"]; msg["References"] = ((it.get("refs") or "") + " " + it["msgid"]).strip()
    msg["Date"] = email.utils.formatdate(localtime=True); msg["Message-ID"] = email.utils.make_msgid(domain=c["email"].split("@")[-1])
    quote = "\n".join("> " + l for l in (it.get("body") or "").splitlines()[:40])
    msg.set_content(f"{txt}\n\n{it.get('date') or ''}, {it['from']} napisał(a):\n{quote}")
    try:
        with smtplib.SMTP(SMTP_H, 587, timeout=30) as s:
            s.starttls(context=ssl.create_default_context()); s.login(c["email"], c["password"]); s.send_message(msg)
    except Exception as e:
        return {"ok": False, "error": "Nie udało się wysłać: " + str(e)[:120]}
    try:   # kopia w „Wysłanych” i oznaczenie jako przeczytany
        m = _imap(c)
        for box in ("Sent Messages", "Sent"):
            if m.append(box, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())[0] == "OK":
                break
        m.select("INBOX"); m.uid("store", uid, "+FLAGS", "(\\Seen \\Answered)"); m.logout()
    except Exception:
        pass
    with _lk:
        D, it = _find(uid)
        D.setdefault("sent", {})[str(uid)] = time.time()
        if it: it["seen"] = True
        _write(DATA, D)
    return {"ok": True}
