"""Zone de recherche : test point-dans-polygone et géocodage (services publics gratuits)."""
import math
import re

from . import http

GEOCODERS = [
    "https://data.geopf.fr/geocodage/search",
    "https://api-adresse.data.gouv.fr/search/",
]

STREET_RE = re.compile(
    r"\b(rue|boulevard|bd|avenue|av\.|passage|place|quai|impasse|villa|cite|square|allee|faubourg)\s+"
    r"((?:de |du |des |d'|la |le |l')?[A-ZÉÈÀÂÎÔÛÇa-zéèàâîôûç'\- ]{3,40})",
)


def _point_in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _polygons(zone):
    for feat in (zone or {}).get("features", []):
        g = feat.get("geometry") or {}
        if g.get("type") == "Polygon":
            yield g["coordinates"]
        elif g.get("type") == "MultiPolygon":
            yield from g["coordinates"]


def zone_defined(zone):
    return any(True for _ in _polygons(zone))


def in_zone(lat, lon, zone):
    for poly in _polygons(zone):
        if _point_in_ring(lon, lat, poly[0]) and not any(_point_in_ring(lon, lat, h) for h in poly[1:]):
            return True
    return False


def distance_to_zone_m(lat, lon, zone):
    """Distance approximative (mètres) entre un point et le bord le plus proche de la zone."""
    best = float("inf")
    kx = 111320 * math.cos(math.radians(lat))
    ky = 110540
    for poly in _polygons(zone):
        for ring in poly:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
                ax, ay = (x1 - lon) * kx, (y1 - lat) * ky
                bx, by = (x2 - lon) * kx, (y2 - lat) * ky
                dx, dy = bx - ax, by - ay
                t = max(0, min(1, -(ax * dx + ay * dy) / ((dx * dx + dy * dy) or 1e-12)))
                px, py = ax + t * dx, ay + t * dy
                best = min(best, math.hypot(px, py))
    return best


def extract_street(text):
    m = STREET_RE.search(text or "")
    if not m:
        return None
    return f"{m.group(1)} {m.group(2).strip()}"


def geocode(query, postcode=None):
    """Renvoie (lat, lon, précision) ou None. précision ∈ {'street', 'locality'}."""
    params = {"q": query, "limit": 1}
    if postcode:
        params["postcode"] = postcode
    for url in GEOCODERS:
        try:
            r = http.get(url, params=params, min_delay=0.3, timeout=15)
            feats = r.json().get("features") or []
        except Exception:
            continue
        if not feats:
            return None
        f = feats[0]
        props = f.get("properties", {})
        if props.get("score", 0) < 0.45:
            return None
        lon, lat = f["geometry"]["coordinates"][:2]
        kind = props.get("type", "")
        return lat, lon, ("street" if kind in ("street", "housenumber") else "locality")
    return None


_PLACES = None
# Noms courants qui sont aussi des mots ordinaires : on exige un contexte (« rue de Paradis », « métro Nation »…)
RISKY_PLACES = {"paradis", "temple", "nation", "combat", "amerique", "danube", "cadet", "avron", "martyrs", "simplon",
                "couronnes", "aligre", "flandre", "reunion", "charonne", "roquette", "saint maur", "enghien", "riquet",
                "ourcq", "crimee", "belleville", "voltaire", "jourdain", "lancry", "bastille", "goncourt", "botzaris",
                "pyrenees", "boulets", "picpus", "bel air", "arts et metiers", "bonne nouvelle", "porte saint denis"}
CONTEXT_RE = r"(?:rue|quartier|secteur|metro|station|place|proche|pres|boulevard|bd|avenue|av|square|canal|parc|mairie|marche|cite|passage|du|de la|des|de l|d)\s+(?:de |du |des |la |l )?$"
PLACE_WORDS_RE = re.compile(r"[^a-z0-9]+")


def _key(text):
    """Normalisation pour comparer des noms de lieux : minuscules, sans accents, tirets/apostrophes = espaces."""
    from .textparse import normalize
    return " " + PLACE_WORDS_RE.sub(" ", normalize(text)).strip() + " "


def places():
    global _PLACES
    if _PLACES is None:
        import json, os
        path = os.path.join(os.path.dirname(__file__), "data", "places_paris.json")
        raw = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
        # Arrondissement de chaque lieu : celui du quartier administratif le plus proche
        quarters = [v for v in raw.values() if v.get("kind") == "quartier"]
        for v in raw.values():
            if "arr" not in v and quarters:
                v["arr"] = min(quarters, key=lambda q: (q["lat"] - v["lat"]) ** 2 + ((q["lon"] - v["lon"]) * 0.66) ** 2)["arr"]
        # du plus long au plus court : « Canal Saint-Martin » avant « Saint-Martin »
        _PLACES = sorted(((_key(n).strip(), n, v) for n, v in raw.items()), key=lambda x: -len(x[0]))
    return _PLACES


def find_place(text, arrondissements=None):
    """Premier lieu connu cité dans le texte (limité aux arrondissements donnés). Renvoie (position, nom, lat, lon) ou None."""
    k = _key(text)
    best = None
    for key, name, v in places():
        if arrondissements and v.get("arr") not in arrondissements:
            continue
        start = 0
        while True:
            i = k.find(" " + key + " ", start)
            if i < 0:
                break
            before, after = k[max(0, i - 30):i + 1], k[i + len(key) + 1:i + len(key) + 30]
            if (key in RISKY_PLACES and not re.search(CONTEXT_RE, before)) or \
                    re.search(r"agence|cabinet|groupe", before) or re.match(r"\s*(immo|immobilier|transaction|conseil)", after):
                start = i + 1  # mot ordinaire, ou nom d'agence (« Agence des Enfants Rouges »)
                continue
            if best is None or i < best[0]:
                best = (i, name, v["lat"], v["lon"])
            break
    return best


def _place_from_text(listing):
    """Position d'après un lieu (quartier, station, rue) cité dans le titre, puis dans la description."""
    title, desc = listing.get("title") or "", listing.get("description") or ""
    pc = listing.get("postcode") or ""
    arrs = {int(pc[-2:])} if pc.startswith("75") and pc[-2:].isdigit() and 1 <= int(pc[-2:]) <= 20 else None
    for text in (title, desc[:3000]):
        hit = find_place(text, arrs)
        if hit:
            return hit[2], hit[3], hit[1]
    return None


QUARTER_TOLERANCE_M = 400  # un quartier cité dans le texte ne donne qu'une position à quelques centaines de mètres près


def locate(listing, zone, postcodes, tolerance_m=0):
    """Calcule la situation géographique de l'annonce.

    Renvoie (statut, motif) avec statut ∈ {'in', 'out', 'edge', 'approx', 'unknown'}.
    """
    pc = listing.get("postcode")
    lat, lon = listing.get("lat"), listing.get("lon")
    precision = listing.get("geo_precision")

    # Pas de position fiable (aucune, ou seulement le centre de l'arrondissement) : on lit le texte.
    if precision in (None, "arrondissement", "quartier") or lat is None:
        hit = _place_from_text(listing)
        if hit:
            lat, lon, name = hit
            precision = "quartier"
            listing.update(lat=lat, lon=lon, geo_precision="quartier", geo_place=name)
        elif zone_defined(zone) and not listing.get("geo_tried"):
            # Sinon, un nom de rue géocodé par la Base Adresse Nationale
            listing["geo_tried"] = True
            street = extract_street(listing.get("title", "") + " " + (listing.get("description") or ""))
            if street:
                res = geocode(f"{street} Paris", pc)
                if res:
                    lat, lon, precision = res
                    listing.update(lat=lat, lon=lon, geo_precision=precision, geo_place=street)
        if precision == "arrondissement":
            lat = lon = None  # le centre de l'arrondissement ne dit rien de la zone

    if pc and postcodes and pc not in postcodes:
        return "out", f"hors secteur ({pc})"

    where = f" d'après « {listing['geo_place']} »" if precision == "quartier" and listing.get("geo_place") else ""
    if lat is not None and lon is not None and zone_defined(zone):
        if in_zone(lat, lon, zone):
            if precision in ("exact", "street"):
                return "in", "dans la zone"
            return "approx", f"dans la zone (position approximative{where})"
        d = distance_to_zone_m(lat, lon, zone)
        tol = max(tolerance_m or 0, QUARTER_TOLERANCE_M if precision == "quartier" else 0)
        if tol and d <= tol:
            return "edge", f"en bordure de zone (~{int(d)} m{where})"
        return "out", f"hors zone dessinée (~{int(d)} m{where})"

    if pc and pc in postcodes:
        if zone_defined(zone):
            # Sans position, impossible de savoir si le bien est dans la zone : à vérifier, jamais « correspond »
            return "unknown", f"position inconnue (seulement {pc}) — vérifier qu'il est dans la zone"
        return "in", f"secteur {pc}"
    return "unknown", "localisation inconnue"
