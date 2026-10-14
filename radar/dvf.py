"""Comparaison avec les ventes réelles récentes (DVF — Demandes de valeurs foncières, DGFiP / Etalab).

`build()` télécharge les ventes géolocalisées de Paris et en garde une version compacte (appartements uniquement) ;
`compare(listing)` situe le prix au m² d'une annonce par rapport aux ventes de surface comparable, toutes proches.
"""
import csv
import gzip
import io
import math
import os
import statistics
from datetime import date, datetime

from . import http

DATA = os.path.join(os.path.dirname(__file__), "data", "dvf_paris.csv.gz")
URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/departements/75.csv.gz"
FIELDS = ["date", "prix", "surface", "pieces", "cp", "lat", "lon"]


def build(years=None):
    """Reconstruit le fichier compact à partir des derniers millésimes DVF disponibles."""
    years = years or range(date.today().year, date.today().year - 5, -1)
    rows, kept_years = [], []
    for y in years:
        try:
            raw = http.get(URL.format(year=y), min_delay=1, timeout=120).content
        except Exception:
            continue
        kept_years.append(y)
        muts = {}
        for r in csv.DictReader(io.StringIO(gzip.decompress(raw).decode("utf-8"))):
            muts.setdefault(r["id_mutation"], []).append(r)
        for lines in muts.values():
            first = lines[0]
            if first["nature_mutation"] != "Vente":
                continue
            apts = [l for l in lines if l["type_local"] == "Appartement"]
            # une seule vente d'appartement (les ventes en bloc mélangent plusieurs biens dans un seul prix)
            if len(apts) != 1 or any(l["type_local"] in ("Maison", "Local industriel. commercial ou assimilé") for l in lines):
                continue
            a = apts[0]
            try:
                prix, surf = float(first["valeur_fonciere"]), float(a["surface_reelle_bati"] or 0)
                lat, lon = float(a["latitude"]), float(a["longitude"])
            except ValueError:
                continue
            if not (15 <= surf <= 400) or not (2000 <= prix / surf <= 40000):
                continue
            rows.append([first["date_mutation"], int(prix), surf, a["nombre_pieces_principales"] or "", a["code_postal"],
                         round(lat, 5), round(lon, 5)])
        if len(kept_years) >= 3:
            break
    rows.sort()
    with gzip.open(DATA, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    return len(rows), kept_years


_SALES = None
_CANDIDATES = {}


def sales():
    global _SALES
    if _SALES is None:
        _SALES = []
        if os.path.exists(DATA):
            with gzip.open(DATA, "rt", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    _SALES.append((r["date"], int(r["prix"]), float(r["surface"]), r["cp"], float(r["lat"]), float(r["lon"])))
    return _SALES


def _dist_m(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * 110540, (lon1 - lon2) * 111320 * math.cos(math.radians(lat1)))


def compare(listing, cfg):
    """Prix au m² de l'annonce vs ventes réelles proches, de surface comparable. Renvoie un dict ou None."""
    m = cfg.get("market", {})
    price, surface = listing.get("price"), listing.get("surface")
    if not price or not surface or not sales():
        return None
    smin, smax = m.get("surface_min", 70), m.get("surface_max", 130)
    months = m.get("months", 24)
    min_n = m.get("min_sales", 12)
    last = max(s[0] for s in sales())
    lat, lon = listing.get("lat"), listing.get("lon")
    key = (smin, smax)
    if key not in _CANDIDATES:
        _CANDIDATES[key] = [s for s in sales() if smin <= s[2] <= smax]
    candidates = _CANDIDATES[key]
    chosen, where = [], ""
    for period in (months, 36):
        recent = [s for s in candidates if _months_between(s[0], last) <= period]
        if lat is not None:
            for radius in (300, 500, 800, 1200):
                chosen = [s for s in recent if _dist_m(lat, lon, s[4], s[5]) <= radius]
                if len(chosen) >= min_n:
                    where = f"à moins de {radius} m"
                    break
        if len(chosen) < min_n and listing.get("postcode"):
            chosen = [s for s in recent if s[3] == listing["postcode"]]
            where = f"dans le {int(listing['postcode'][-2:])}e"
        if len(chosen) >= min_n:
            break
    if len(chosen) < min_n:
        return None
    ppm2 = sorted(s[1] / s[2] for s in chosen)
    q = statistics.quantiles(ppm2, n=4)
    fee = m.get("agency_fee_rate", 0.04)  # prix d'annonce = honoraires inclus ; DVF = prix net vendeur
    listing_ppm2 = price / (1 + fee) / surface
    median = statistics.median(ppm2)
    dates = sorted(s[0] for s in chosen)
    return {
        "median_m2": round(median), "p25_m2": round(q[0]), "p75_m2": round(q[2]), "n": len(chosen),
        "where": where, "period": f"{dates[0][:7]} → {dates[-1][:7]}", "surface_range": [smin, smax],
        "listing_m2_net": round(listing_ppm2), "gap_pct": round(100 * (listing_ppm2 / median - 1), 1),
        "fee_rate": fee,
        "local": where.startswith("à moins"),   # comparaison de voisinage (sinon : arrondissement entier, indicatif)
        "surface_outside": not (smin <= surface <= smax),
    }


def _months_between(d1, d2):
    a, b = datetime.fromisoformat(d1), datetime.fromisoformat(d2)
    return (b.year - a.year) * 12 + b.month - a.month


if __name__ == "__main__":
    n, years = build()
    print(f"{n} ventes d'appartements ({', '.join(map(str, years))})")
