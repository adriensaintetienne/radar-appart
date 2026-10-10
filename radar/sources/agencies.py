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


def fetch_agency(agency, known_keys, postcodes=None):
    rx = re.compile(agency["link_pattern"], re.I)
    pc_rx = re.compile(agency["postcode_in_url"]) if agency.get("postcode_in_url") else None
    links, matched = set(), 0
    for url in agency["list_urls"]:
        r = http.get(url, min_delay=2.5)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"]).split("#")[0]
            if not rx.search(full):
                continue
            matched += 1
            if pc_rx and postcodes:
                m = pc_rx.search(full)
                if m and m.group(1) not in postcodes:
                    continue  # hors secteur d'après l'adresse de la fiche : inutile de l'ouvrir
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
    full = agency.get("full_listing", True)
    info = f"{matched} lien(s) de fiche reconnus, {len(links)} dans le secteur, {fetched} nouvelle(s) lue(s)"
    # nonzero : le site renvoie bien des fiches reconnues, même si aucune n'est dans le secteur ce jour-là.
    # Zéro lien reconnu = le site a probablement changé de présentation -> alerte de silence.
    return items, {"full_listing": full, "info": info, "links_found": len(links), "nonzero": matched > 0}
