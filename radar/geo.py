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


def locate(listing, zone, postcodes):
    """Calcule la situation géographique de l'annonce.

    Renvoie (statut, motif) avec statut ∈ {'in', 'out', 'edge', 'approx', 'unknown'}.
    """
    pc = listing.get("postcode")
    lat, lon = listing.get("lat"), listing.get("lon")
    precision = listing.get("geo_precision")

    # Coordonnées absentes : tenter un géocodage à partir d'un nom de rue dans le texte.
    if (lat is None or lon is None) and zone_defined(zone) and not listing.get("geo_tried"):
        listing["geo_tried"] = True
        street = extract_street((listing.get("title") or "") + " " + (listing.get("description") or ""))
        if street:
            res = geocode(f"{street} Paris", pc)
            if res:
                lat, lon, precision = res
                listing.update(lat=lat, lon=lon, geo_precision=precision)

    if pc and postcodes and pc not in postcodes:
        return "out", f"hors secteur ({pc})"

    if lat is not None and lon is not None and zone_defined(zone):
        if in_zone(lat, lon, zone):
            return ("in", "dans la zone") if precision in ("exact", "street") else ("approx", "dans la zone (position approximative)")
        d = distance_to_zone_m(lat, lon, zone)
        tolerance = 120 if precision in ("exact", "street") else 450
        if d <= tolerance:
            return "edge", f"en bordure de zone (~{int(d)} m, position {'précise' if tolerance == 120 else 'approximative'})"
        return "out", f"hors zone dessinée (~{int(d)} m)"

    if pc and pc in postcodes:
        if zone_defined(zone):
            return "approx", f"localisation approximative ({pc}) — vérifier le quartier"
        return "in", f"secteur {pc}"
    return "unknown", "localisation inconnue"
