"""Filet de sécurité : recherches web quotidiennes pour repérer des sites d'annonces qu'on ne surveille pas.

Les résultats ne deviennent pas des annonces : ils alimentent la liste « nouvelles sources découvertes »
du tableau de bord, à valider (et à ajouter comme agence si pertinent).
"""
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .. import http

NAME = "websearch"
KNOWN_DOMAINS = (
    "seloger.com", "leboncoin.fr", "pap.fr", "bienici.com", "logic-immo.com", "lefigaro.fr", "bellesdemeures.com",
    "jinka.fr", "meilleursagents.com", "paruvendu.fr", "ouestfrance-immo.com", "superimmo.com", "avendrealouer.fr",
    "green-acres.fr", "wikipedia.org", "youtube.com", "facebook.com", "instagram.com", "pinterest.", "linkedin.com",
    "lemonde.fr", "leparisien.fr", "tripadvisor", "airbnb", "booking.com", "pagesjaunes.fr", "google.",
)
SEARCH_URL = "https://html.duckduckgo.com/html/"


def _real_url(href):
    if "duckduckgo.com/l/" in href:
        q = parse_qs(urlparse(href).query)
        if "uddg" in q:
            return unquote(q["uddg"][0])
    return href


def fetch(cfg, state):
    st = state.setdefault("websearch", {})
    today = datetime.now(timezone.utc).date().isoformat()
    if st.get("last_run_date") == today:
        return [], {"full_listing": False, "info": "déjà effectuée aujourd'hui", "skipped": True}
    queries = cfg["sources"]["websearch"].get("queries", [])
    known_agency_domains = {urlparse(u).netloc.replace("www.", "")
                            for a in cfg["sources"].get("agencies", []) for u in a.get("list_urls", [])}
    discovered = st.setdefault("discovered", {})
    hits, errors = 0, 0
    for q in queries:
        try:
            r = http.post(SEARCH_URL, data={"q": q, "kl": "fr-fr"}, min_delay=4.0)
        except Exception:
            errors += 1
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__a, a.result__url"):
            url = _real_url(a.get("href", ""))
            dom = urlparse(url).netloc.replace("www.", "")
            if not dom or any(k in dom for k in KNOWN_DOMAINS) or dom in known_agency_domains:
                continue
            if not re.search(r"immo|agence|vente|loft|atelier|appartement|habitat|propriet|transaction|estate", dom + url, re.I):
                continue
            hits += 1
            d = discovered.setdefault(dom, {"first_seen": today, "count": 0, "examples": [], "status": "nouveau"})
            d["count"] += 1
            d["last_seen"] = today
            if url not in d["examples"]:
                d["examples"] = (d["examples"] + [url])[-5:]
    if errors == len(queries) and queries:
        raise RuntimeError("moteur de recherche inaccessible")
    st["last_run_date"] = today
    new = sum(1 for d in discovered.values() if d.get("first_seen") == today)
    return [], {"full_listing": False, "info": f"{hits} résultat(s) hors sites connus, {new} nouveau(x) site(s) découvert(s)",
                "nonzero": True}
