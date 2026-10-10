"""Bien'ici : API JSON publique utilisée par leur site (très large couverture des agences).

Bien'ici rassemble les annonces que les agences diffusent depuis leur logiciel : c'est la meilleure
source directe pour couvrir les petites agences sans les collecter une par une.
"""
import json

from .. import http

NAME = "bienici"
FULL_LISTING = True
API = "https://www.bienici.com/realEstateAds.json"
PARIS_ZONE_ID = "-7444"  # relation OpenStreetMap « Paris »
PAGE_SIZE = 100


def _filters(cfg, page):
    b = cfg["budget"]
    # Prix affiché max = budget total max (+ marge) sans travaux ni notaire -> filtre large, l'analyse affine.
    max_price = int(b["total_max"] * (1 + b["negotiable_margin"]) / (1 + b["notary_rate"]))
    return {
        "size": PAGE_SIZE,
        "from": (page - 1) * PAGE_SIZE,
        "page": page,
        "filterType": "buy",
        "propertyType": ["flat", "loft"],
        "minArea": max(0, cfg["surface"]["min"] - 2),
        "maxPrice": max_price,
        "onTheMarket": [True],
        "showAllModels": False,
        "sortBy": "publicationDate",
        "sortOrder": "desc",
        "zoneIdsByTypes": {"zoneIds": [PARIS_ZONE_ID]},
    }


def _to_item(ad):
    pos = ((ad.get("blurInfo") or {}).get("position")) or {}
    precise = (ad.get("blurInfo") or {}).get("type") in ("exact", "address")
    photos = [p.get("url") or p.get("url_photo") for p in ad.get("photos") or [] if isinstance(p, dict)]
    ad_id = ad.get("id")
    return {
        "source": NAME,
        "ext_id": ad_id,
        "url": f"https://www.bienici.com/annonce/{ad_id}",
        "title": ad.get("title") or "",
        "description": ad.get("description") or "",
        "price": ad.get("price") if isinstance(ad.get("price"), (int, float)) else None,
        "surface": ad.get("surfaceArea"),
        "rooms": ad.get("roomsQuantity"),
        "bedrooms": ad.get("bedroomsQuantity"),
        "floor": ad.get("floor"),
        "elevator": ad.get("hasElevator"),
        "cellar": ad.get("hasCellar"),
        "postcode": ad.get("postalCode"),
        "lat": pos.get("lat"),
        "lon": pos.get("lon"),
        "geo_precision": "exact" if precise else ("approx" if pos else None),
        "property_type": ad.get("propertyType"),
        "new_build": bool(ad.get("newProperty")),
        "build_year": ad.get("yearOfConstruction"),
        "dpe": ad.get("energyClassification"),
        "agency": (ad.get("contactRelativeData") or {}).get("contactNameToDisplay") or ad.get("accountDisplayName"),
        "published_at": ad.get("publicationDate"),
        "photos": [p for p in photos if p],
    }


def fetch(cfg, state):
    items, page, total = [], 1, None
    while True:
        r = http.get(API, params={"filters": json.dumps(_filters(cfg, page))}, min_delay=2.0,
                     headers={"Accept": "application/json", "Referer": "https://www.bienici.com/"})
        data = r.json()
        ads = data.get("realEstateAds") or []
        total = data.get("total", total)
        items.extend(_to_item(a) for a in ads)
        if len(ads) < PAGE_SIZE or page >= 10:
            break
        page += 1
    if total is None and not items:
        raise RuntimeError("réponse Bien'ici inattendue (format changé ?)")
    return items, {"full_listing": True, "info": f"{len(items)} annonces Paris dans le filtre large (total annoncé : {total})"}
