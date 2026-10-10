"""Collecte directe des sites d'agences (configurables depuis le tableau de bord).

Pour chaque agence : on ouvre ses pages de liste, on repère les liens vers des fiches de biens
(expression régulière `link_pattern`), puis on lit chaque nouvelle fiche.
"""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import http
from ..enrich import parse_detail_html
from ..store import source_key
from ..textparse import normalize

FULL_LISTING = True
MAX_NEW_DETAILS = 40


def slug(name):
    return "agence:" + re.sub(r"[^a-z0-9]+", "-", normalize(name)).strip("-")


def fetch_agency(agency, known_keys):
    rx = re.compile(agency["link_pattern"], re.I)
    links = set()
    for url in agency["list_urls"]:
        r = http.get(url, min_delay=2.5)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"]).split("#")[0]
            if rx.search(full):
                links.add(full)
    name = slug(agency["name"])
    items, fetched = [], 0
    for link in sorted(links):
        item = {"source": name, "url": link, "agency": agency["name"]}
        if source_key(name, link) in known_keys:
            items.append({**item, "_known": True})  # simplement « vu », pas besoin de relire la fiche
            continue
        if fetched >= MAX_NEW_DETAILS:
            continue
        fetched += 1
        try:
            r = http.get(link, min_delay=2.5)
            item.update({k: v for k, v in parse_detail_html(r.text).items() if v is not None})
            item["enriched"] = True
        except Exception as e:
            item["enrich_error"] = str(e)[:160]
        items.append(item)
    info = f"{len(links)} fiche(s) trouvée(s) sur le site, {fetched} nouvelle(s) lue(s)"
    return items, {"full_listing": True, "info": info, "links_found": len(links)}
