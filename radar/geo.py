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
        for v in raw.values():
            if v.get("kind") == "quartier_portail" and v.get("polygon"):
                v["polygon"] = _expand(v["polygon"], v["lat"], v["lon"], 80)
        # du plus long au plus court : « Canal Saint-Martin » avant « Saint-Martin »
        _PLACES = sorted(((_key(n).strip(), n, v) for n, v in raw.items()), key=lambda x: -len(x[0]))
    return _PLACES


def _expand(ring, lat, lon, meters):
    """Élargit un contour de `meters` vers l'extérieur, depuis son centre."""
    kx, ky = 111320 * math.cos(math.radians(lat)), 110540
    out = []
    for x, y in ring:
        dx, dy = (x - lon) * kx, (y - lat) * ky
        d = math.hypot(dx, dy) or 1
        out.append([lon + dx * (d + meters) / d / kx, lat + dy * (d + meters) / d / ky])
    return out


SELOGER_DISTRICT_RE = re.compile(r"([A-ZÀ-Üa-zà-ÿ][\w'’ \-À-ÿ]{2,60}), Paris \d{1,2}(?:e|ème|er)\s+arrondissement")


def declared_district(listing):
    """Quartier déclaré par le portail : champ Bien'ici, ou « Quartier, Paris 11ème arrondissement » (SeLoger)."""
    d = listing.get("district") or ""
    if " - " in d and "Arrondissement" in d:
        return d.split("Arrondissement - ", 1)[-1]
    m = SELOGER_DISTRICT_RE.search(listing.get("description") or "")
    return m.group(1).strip() if m else (d or None)


def find_place(text, arrondissements=None, strict=True):
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
            if (strict and key in RISKY_PLACES and not re.search(CONTEXT_RE, before)) or \
                    re.search(r"agence|cabinet|groupe", before) or re.match(r"\s*(immo|immobilier|transaction|conseil)", after):
                start = i + 1  # mot ordinaire, ou nom d'agence (« Agence des Enfants Rouges »)
                continue
            if best is None or i < best[0]:
                best = (i, name, v["lat"], v["lon"])
            break
    return best


def _arrondissements(listing):
    pc = listing.get("postcode") or ""
    return {int(pc[-2:])} if pc.startswith("75") and pc[-2:].isdigit() and 1 <= int(pc[-2:]) <= 20 else None


def _place(text, listing, strict=True):
    hit = find_place(text, _arrondissements(listing), strict) if text else None
    if not hit:
        return None
    by_name = {name: v for _, name, v in places()}
    return hit[1], by_name[hit[1]]


# --- Part de la position possible du bien qui tombe dans la zone -------------------------------------------
PLACE_RADIUS_M = 250   # station, rue, lieu-dit cité dans le texte
STREET_RADIUS_M = 200  # rue géocodée (une rue a une certaine longueur)


def _circle_points(lat, lon, radius_m, rings=4, per_ring=12):
    pts = [(lat, lon)]
    kx, ky = 111320 * math.cos(math.radians(lat)), 110540
    for i in range(1, rings + 1):
        r = radius_m * i / rings
        for j in range(per_ring * i):
            a = 2 * math.pi * j / (per_ring * i)
            pts.append((lat + r * math.sin(a) / ky, lon + r * math.cos(a) / kx))
    return pts


def _polygon_points(ring, n=18):
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    pts = []
    for i in range(n):
        for j in range(n):
            lo = min(lons) + (max(lons) - min(lons)) * (i + 0.5) / n
            la = min(lats) + (max(lats) - min(lats)) * (j + 0.5) / n
            if _point_in_ring(lo, la, ring):
                pts.append((la, lo))
    return pts or [(sum(lats) / len(lats), sum(lons) / len(lons))]


def zone_fraction(points, zone):
    return sum(1 for la, lo in points if in_zone(la, lo, zone)) / len(points)


ZONE_OUT_BELOW = 0.20  # moins de 20 % de la position possible dans la zone -> écarté
ZONE_IN_ABOVE = 0.80   # plus de 80 % -> dans la zone


def locate(listing, zone, postcodes, tolerance_m=0, out_below=ZONE_OUT_BELOW, in_above=ZONE_IN_ABOVE):
    """Situation géographique de l'annonce : (statut, motif), statut ∈ {'in', 'out', 'edge', 'approx', 'unknown'}.

    Chaque indice de position est une surface (point précis, disque flouté du portail, contour du quartier cité,
    rayon autour d'une station ou d'une rue). On calcule la part de cette surface qui tombe dans la zone :
    0 % -> hors zone (écarté), 100 % -> dans la zone, entre les deux -> à vérifier.
    """
    pc = listing.get("postcode")
    if pc and postcodes and pc not in postcodes:
        return "out", f"hors secteur ({pc})"
    if not zone_defined(zone):
        if pc and pc in postcodes:
            return "in", f"secteur {pc}"
        return "unknown", "localisation inconnue"

    lat, lon = listing.get("lat"), listing.get("lon")
    precision = listing.get("geo_precision")
    if precision in ("quartier",):  # ancienne position déduite du texte : recalculée ci-dessous
        lat = lon = precision = None
    evidences = []  # (libellé, points)

    if lat is not None and precision == "exact":
        evidences.append(("position précise", [(lat, lon)]))
    elif lat is not None and precision == "street":
        evidences.append((f"rue « {listing.get('geo_place', '?')} »", _circle_points(lat, lon, STREET_RADIUS_M)))
    elif lat is not None and precision == "approx":
        r = listing.get("geo_radius") or 250
        evidences.append((f"position floutée par le portail (±{int(r)} m)", _circle_points(lat, lon, r)))

    precise = bool(evidences) and (precision == "exact" or (precision == "approx" and (listing.get("geo_radius") or 250) <= 100))
    if not precise:
        # Quartier déclaré par le portail, puis lieu cité dans le titre ; la description en dernier recours
        found = []
        district = declared_district(listing)
        for text, strict, origin in ((district, False, "quartier déclaré"), (listing.get("title"), True, "titre")):
            hit = _place(text, listing, strict)
            if hit and hit[0] not in [f[0] for f in found]:
                found.append((hit[0], hit[1], origin))
        if not found and not evidences:
            hit = _place((listing.get("description") or "")[:3000], listing)
            if hit:
                found.append((hit[0], hit[1], "description"))
        for name, v, origin in found:
            pts = _polygon_points(v["polygon"]) if v.get("polygon") else _circle_points(v["lat"], v["lon"], PLACE_RADIUS_M)
            evidences.append((f"{'quartier' if v.get('polygon') else 'lieu'} « {name} » ({origin})", pts))
        if found:
            listing["geo_place"] = found[0][0]
            if lat is None:  # pour l'affichage sur la carte
                listing.update(lat=found[0][1]["lat"], lon=found[0][1]["lon"], geo_precision="quartier")
        elif lat is None and not listing.get("geo_tried"):
            listing["geo_tried"] = True
            street = extract_street((listing.get("title") or "") + " " + (listing.get("description") or ""))
            if street:
                res = geocode(f"{street} Paris", pc)
                if res:
                    lat, lon, prec = res
                    listing.update(lat=lat, lon=lon, geo_precision=prec, geo_place=street)
                    evidences.append((f"rue « {street} »", _circle_points(lat, lon, STREET_RADIUS_M)))

    if not evidences:
        if pc and pc in postcodes:
            return "unknown", f"position inconnue (seulement {pc}) — vérifier qu'il est dans la zone"
        return "unknown", "localisation inconnue"

    fracs = [(label, zone_fraction(pts, zone), pts) for label, pts in evidences]
    outs = [f for f in fracs if f[1] < out_below]
    ins = [f for f in fracs if f[1] > in_above]
    if outs and not ins:
        label, _, pts = outs[0]
        if tolerance_m:
            d = min(distance_to_zone_m(la, lo, zone) for la, lo in pts)
            if d <= tolerance_m:
                return "edge", f"juste hors zone (~{int(d)} m, {label})"
        f = outs[0][1]
        detail = "entièrement hors de la zone" if f == 0 else f"seulement {int(round(100 * f))} % dans la zone"
        return "out", f"hors zone dessinée ({label} : {detail})"
    if ins and not outs:
        label = ins[0][0]
        return ("in", "dans la zone") if label == "position précise" else ("approx", f"dans la zone ({label})")
    if outs and ins:
        return "edge", "indices de position contradictoires (" + " / ".join(f"{l} : {int(100 * f)} %" for l, f, _ in fracs) + ")"
    label, f, _ = min(fracs, key=lambda x: len(x[2]))
    return "edge", f"à cheval sur la limite de zone ({int(round(100 * f))} % dans la zone, d'après {label})"
