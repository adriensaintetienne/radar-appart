"""Lecture des alertes email des portails (SeLoger, Leboncoin, PAP, Bien'ici, Logic-Immo, Figaro, Jinka…)
dans une boîte Gmail dédiée, via IMAP + mot de passe d'application.

Principe : chaque lien d'annonce trouvé dans un email est identifié (portail + identifiant), et les
infos visibles autour du lien (prix, surface, pièces, ville) sont extraites. La fiche complète est
ensuite consultée si le portail le permet (enrichissement).
"""
import email
import imaplib
import os
import re
from email.header import decode_header
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

from .. import http
from ..config import gmail_credentials, gmail_credentials_shape
from ..textparse import extract_all

NAME = "gmail"
FULL_LISTING = False

# portail -> regex qui reconnaît une URL de fiche et capture son identifiant
PORTALS = {
    # ancien format …/123456789.htm et nouveau format /annonce/achat/…/26A4PSJDIKBI
    "seloger": re.compile(r"seloger\.com/annonces?/(?:achat|investissement|viager)/[^?#]*/([A-Za-z0-9]{8,}?)(?:\.htm)?(?=[?#/]|$)"),
    "leboncoin": re.compile(r"leboncoin\.fr/(?:ad/[a-z_]+|vi|ventes_immobilieres)/(\d{6,})"),
    "pap": re.compile(r"pap\.fr/annonces/[^?#]*?-r(\d{6,})"),
    "bienici": re.compile(r"bienici\.com/annonce/(?:[^/?#]+/)*?([a-z0-9-]*\d[a-z0-9-]{5,})(?:[/?#]|$)"),
    "logicimmo": re.compile(r"logic-immo\.com/(?:annonces?/(?:achat|vente)/[^?#]*/([A-Za-z0-9]{8,}?)(?:\.htm)?(?=[?#/]|$)|(?:detail-vente-|annonces?/)[^?#]*?([0-9a-f]{8}-[0-9a-f-]{27}|\d{6,}))"),
    "figaro": re.compile(r"immobilier\.lefigaro\.fr/annonces/annonce-(\d{6,})"),
    "bellesdemeures": re.compile(r"bellesdemeures\.com/(?:annonces?/[^?#]*/([A-Za-z0-9]{8,}?)(?:\.htm)?(?=[?#/]|$)|(\d{6,})/detail\.htm)"),
    "paruvendu": re.compile(r"paruvendu\.fr/immobilier/[^?#]*?(\d{6,})"),
    "ouestfrance": re.compile(r"ouestfrance-immo\.com/(?:immobilier/)?[^?#]*?(\d{6,})\.htm"),
    "superimmo": re.compile(r"superimmo\.com/annonces/[^?#]*?([0-9a-f]{20,}|\d{6,})"),
    "avendrealouer": re.compile(r"avendrealouer\.fr/[^?#]*?/(\d{6,})"),
    "green-acres": re.compile(r"green-acres\.fr/[^?#]*?(\d{6,})"),
    "espaces-atypiques": re.compile(r"espaces-atypiques\.com/[^?#]*?(\d{3,})"),
}
SENDER_HINTS = {
    "seloger": "seloger", "leboncoin": "leboncoin", "pap.fr": "pap", "bienici": "bienici",
    "logic-immo": "logicimmo", "lefigaro": "figaro", "jinka": "jinka", "bellesdemeures": "bellesdemeures",
}
TRACKING_HOSTS = ("click.", "clic.", "link.", "links.", "track", "email.", "e.", "t.", "r.", "mailing", "sendgrid",
                  "list-manage", "mjt.lu", "sib", "jinka", "eu.", "url")
_redirect_cache = {}
# liens de pied d'email à ne jamais suivre
SKIP_LINK_RE = re.compile(r"d[ée]sabonn|g[ée]rer mes|arr[êe]ter cette alerte|confidentialit|conditions g[ée]n[ée]rales|cliquez ici|"
                          r"application|t[ée]l[ée]charg|mon compte|pr[ée]f[ée]rences|unsubscribe", re.I)


# Domaines de portails : si l'URL ne correspond à aucun format connu, on retient quand même
# le dernier segment qui ressemble à un identifiant (évite de perdre une annonce sur un changement de format).
PORTAL_DOMAINS = {
    "seloger.com": "seloger", "logic-immo.com": "logicimmo", "bellesdemeures.com": "bellesdemeures",
    "leboncoin.fr": "leboncoin", "pap.fr": "pap", "bienici.com": "bienici", "lefigaro.fr": "figaro",
}
FALLBACK_ID_RE = re.compile(r"/([A-Za-z0-9-]*\d[A-Za-z0-9-]{5,})(?:\.htm|/detail\.htm)?(?=[/?#]|$)")
NOT_LISTING_RE = re.compile(r"/(?:compte|account|alerte|alert|recherche|search|list|aide|help|legal|cgu)", re.I)


def identify(url):
    for portal, rx in PORTALS.items():
        m = rx.search(url)
        if m:
            return portal, next(g for g in m.groups() if g)
    host = urlparse(url).netloc.lower()
    portal = next((p for d, p in PORTAL_DOMAINS.items() if host.endswith(d)), None)
    if portal and not NOT_LISTING_RE.search(urlparse(url).path):
        ids = FALLBACK_ID_RE.findall(urlparse(url).path)
        if ids:
            return portal, ids[-1]
    return None, None


def _unwrap(url):
    """Retrouve l'URL réelle derrière un lien de tracking (paramètre d'URL ou redirection HTTP)."""
    portal, _ = identify(url)
    if portal:
        return url
    q = parse_qs(urlparse(url).query)
    for vals in q.values():
        for v in vals:
            v = unquote(v)
            if v.startswith("http") and identify(v)[0]:
                return v
    host = urlparse(url).netloc.lower()
    if not any(h in host for h in TRACKING_HOSTS):
        return url
    if url in _redirect_cache:
        return _redirect_cache[url]
    # On suit les redirections une par une, sans ouvrir la page finale (souvent protégée contre les robots).
    final, current = url, url
    try:
        for _ in range(4):
            r = http.session().get(current, allow_redirects=False, timeout=12)
            loc = r.headers.get("location")
            r.close()
            if not loc:
                break
            current = requests.compat.urljoin(current, loc)
            if identify(current)[0]:
                final = current
                break
    except Exception:
        pass
    _redirect_cache[url] = final
    return final


def _block_text(a_tag):
    """Texte du bloc HTML qui entoure le lien (carte d'annonce dans l'email)."""
    node, best = a_tag, a_tag.get_text(" ", strip=True)
    for _ in range(7):
        node = node.parent
        if node is None:
            break
        t = node.get_text(" ", strip=True)
        if len(t) > 700:
            break
        best = t
        # la carte d'annonce est complète quand on y trouve prix, surface et localisation
        if "€" in t and re.search(r"m²|m2", t) and re.search(r"\(\d{5}\)|\b\d{5}\b|paris", t, re.I):
            break
    return best, node


def _bytes_to_str(data, charset):
    """Décode en tolérant les encodages inconnus ou fantaisistes (ex. « unknown-8bit »)."""
    if data is None:
        return ""
    if charset:
        try:
            return data.decode(charset, "replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _decode(s):
    out = ""
    for part, enc in decode_header(s or ""):
        out += _bytes_to_str(part, enc) if isinstance(part, bytes) else part
    return out


def parse_email_html(html, sender=""):
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    done_blocks = set()  # cartes déjà identifiées : inutile de résoudre leurs autres liens
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("http"):
            continue
        if SKIP_LINK_RE.search(a.get_text(" ", strip=True)):
            continue
        text, block = _block_text(a)
        if block is not None and id(block) in done_blocks:
            continue
        url = _unwrap(href)
        portal, ext_id = identify(url)
        if not portal:
            continue
        if block is not None:
            done_blocks.add(id(block))
        key = (portal, ext_id)
        prev = results.get(key)
        if prev and len(prev["_text"]) >= len(text):
            continue
        img = None
        if block is not None:
            im = block.find("img", src=True)
            if im and not re.search(r"logo|pixel|spacer|icon", im["src"], re.I):
                img = im["src"]
        data = extract_all(text)
        results[key] = {
            "source": portal, "ext_id": ext_id, "url": re.sub(r"[?#].*$", "", url),
            "title": text[:160], "description": text, "photos": [img] if img else [],
            "via": "email", **{k: v for k, v in data.items() if v is not None}, "_text": text,
        }
    for r in results.values():
        r.pop("_text", None)
    return list(results.values())


def fetch(cfg, state):
    addr, pwd = gmail_credentials()
    if not addr or not pwd:
        raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD non configurés (secrets GitHub)")
    st = state.setdefault("gmail", {})
    if st.get("folder") != (os.environ.get("GMAIL_FOLDER") or "INBOX"):
        st["last_uid"] = 0  # les numéros de messages sont propres à chaque libellé
        st["folder"] = os.environ.get("GMAIL_FOLDER") or "INBOX"
    last_uid = int(st.get("last_uid", 0))
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    try:
        imap.login(addr, pwd)
    except imaplib.IMAP4.error as e:
        raise RuntimeError(f"Gmail refuse la connexion ({e}) — {gmail_credentials_shape()}") from None
    # Boîte personnelle : on ne lit qu'un libellé (ex. « Radar ») et que les emails adressés à l'alias +appart.
    folder = os.environ.get("GMAIL_FOLDER") or "INBOX"
    alias = os.environ.get("GMAIL_ALIAS")
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        raise RuntimeError(f"libellé Gmail « {folder} » introuvable")
    criteria = [f"UID {last_uid + 1}:*"] if last_uid else ["SINCE", "01-Jan-2020"]
    if alias:
        criteria += ["X-GM-RAW", f'"to:{alias}"']
    typ, data = imap.uid("search", None, *criteria)
    uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid]
    uids = uids[-300:]  # premier passage : on se limite aux 300 derniers emails
    items, per_sender, unparsed = [], {}, []
    for uid in uids:
        try:
            typ, msg_data = imap.uid("fetch", str(uid), "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            sender = _decode(msg.get("From", "")).lower()
            subject = _decode(msg.get("Subject", ""))
            html = None
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = _bytes_to_str(part.get_payload(decode=True), part.get_content_charset())
                    break
            if html is None:
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        txt = _bytes_to_str(part.get_payload(decode=True), part.get_content_charset())
                        html = re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', txt).replace("\n", "<br>")
                        break
            found = parse_email_html(html or "", sender)
            label = next((v for k, v in SENDER_HINTS.items() if k in sender), sender.split("<")[-1].strip(">"))
            per_sender.setdefault(label, {"emails": 0, "listings": 0, "last_email_at": None})
            per_sender[label]["emails"] += 1
            per_sender[label]["listings"] += len(found)
            per_sender[label]["last_email_at"] = msg.get("Date")
            if not found and any(k in sender for k in SENDER_HINTS):
                unparsed.append({"uid": uid, "from": sender, "subject": subject[:120]})
            items.extend(found)
        except Exception as e:  # un email illisible ne doit pas bloquer les autres
            unparsed.append({"uid": uid, "from": "?", "subject": f"email illisible ({type(e).__name__})"})
        last_uid = max(last_uid, uid)
    imap.logout()
    st["last_uid"] = last_uid
    senders = st.setdefault("senders", {})
    for label, v in per_sender.items():
        s = senders.setdefault(label, {"emails": 0, "listings": 0})
        s["emails"] += v["emails"]
        s["listings"] += v["listings"]
        s["last_email_at"] = v["last_email_at"]
    if unparsed:
        st["unparsed"] = (st.get("unparsed", []) + unparsed)[-30:]
    info = f"{len(uids)} email(s) lu(s), {len(items)} annonce(s) extraite(s)"
    if unparsed:
        info += f" — ⚠️ {len(unparsed)} email(s) de portail sans annonce reconnue (format à adapter)"
    return items, {"full_listing": False, "info": info, "per_sender": per_sender, "unparsed": len(unparsed)}
