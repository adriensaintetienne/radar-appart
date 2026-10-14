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
SUGGEST = "https://res.bienici.com/suggest.json"


def zone_ids(cfg, state):
    """Identifiants de zone Bien'ici des codes postaux recherchés (mis en cache)."""
    cache = state.setdefault("bienici", {}).setdefault("zone_ids", {})
    ids = []
    for pc in cfg.get("postcodes") or []:
        if pc not in cache:
            try:
                arr = int(pc[-2:]) if pc.startswith("75") else None
                q = f"paris {arr}e" if arr else pc
                sugg = http.get(SUGGEST, params={"q": q}, min_delay=1.0).json()
                match = next((z for z in sugg if pc in (z.get("postalCodes") or [])), None)
                if match and match.get("zoneIds"):
                    cache[pc] = match["zoneIds"]
            except Exception:
                pass
        ids.extend(cache.get(pc, []))
    return ids or [PARIS_ZONE_ID]


def _filters(cfg, page, zones=None):
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
        "zoneIdsByTypes": {"zoneIds": zones or [PARIS_ZONE_ID]},
    }


def _num(v):
    """Les programmes donnent parfois des fourchettes [min, max] : on garde le minimum."""
    if isinstance(v, list):
        v = min((x for x in v if isinstance(x, (int, float))), default=None)
    return v if isinstance(v, (int, float)) else None


def _to_item(ad):
    blur = ad.get("blurInfo") or {}
    pos = blur.get("position") or {}
    # type "exact" ou disque de petit rayon = position fiable ; sinon approximative
    precise = blur.get("type") == "exact" or (blur.get("type") == "disk" and (blur.get("radius") or 999) <= 60)
    photos = [p.get("url") or p.get("url_photo") for p in ad.get("photos") or [] if isinstance(p, dict)]
    ad_id = ad.get("id")
    return {
        "source": NAME,
        "ext_id": ad_id,
        "url": f"https://www.bienici.com/annonce/{ad_id}",
        "title": ad.get("title") or "",
        "description": ad.get("description") or "",
        "price": _num(ad.get("price")),
        "surface": _num(ad.get("surfaceArea")),
        "rooms": _num(ad.get("roomsQuantity")),
        "bedrooms": _num(ad.get("bedroomsQuantity")),
        "floor": _num(ad.get("floor")),
        "elevator": ad.get("hasElevator"),
        "cellar": ad.get("hasCellar"),
        "fireplace": ad.get("hasFirePlace"),
        "bathrooms": (_num(ad.get("bathroomsQuantity")) or 0) + (_num(ad.get("showerRoomsQuantity")) or 0) or None,
        "postcode": ad.get("postalCode"),
        "lat": pos.get("lat"),
        "lon": pos.get("lon"),
        # centre de l'arrondissement ou flou ≥ 500 m : inutilisable pour la zone, le texte prendra le relais
        "geo_precision": "exact" if precise else (
            "arrondissement" if blur.get("type") == "cityOrArrondissement" or (blur.get("radius") or 0) >= 500
            else ("approx" if pos else None)),
        "geo_radius": blur.get("radius"),
        "district": (ad.get("district") or {}).get("name") if isinstance(ad.get("district"), dict) else None,
        "property_type": ad.get("propertyType"),
        "new_build": bool(ad.get("newProperty")),
        "build_year": _num(ad.get("yearOfConstruction")),
        "dpe": ad.get("energyClassification"),
        "agency": ad.get("accountDisplayName"),
        "published_at": ad.get("publicationDate"),
        "photos": [p for p in photos if p],
        "full_text": True,
    }


def fetch(cfg, state):
    items, page, total = [], 1, None
    zones = zone_ids(cfg, state)
    while True:
        r = http.get(API, params={"filters": json.dumps(_filters(cfg, page, zones))}, min_delay=2.0,
                     headers={"Accept": "application/json", "Referer": "https://www.bienici.com/"})
        data = r.json()
        ads = data.get("realEstateAds") or []
        total = data.get("total", total)
        items.extend(_to_item(a) for a in ads)
        if len(ads) < PAGE_SIZE or page >= 20:
            break
        page += 1
    if total is None and not items:
        raise RuntimeError("réponse Bien'ici inattendue (format changé ?)")
    return items, {"full_listing": True, "info": f"{len(items)} annonces dans le filtre large ({len(zones)} zone(s), total annoncé : {total})"}
