"""Base d'annonces : dédoublonnage multi-sources, historique des prix, retraits et remises en ligne."""
import hashlib
import re
from datetime import datetime, timezone

from .textparse import normalize

MERGE_FIELDS = ("title", "description", "price", "surface", "rooms", "bedrooms", "floor", "elevator",
                "cellar", "top_floor", "build_year", "postcode", "lat", "lon", "geo_precision",
                "property_type", "new_build", "agency", "dpe", "published_at")


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_url(url):
    url = re.sub(r"[?#].*$", "", url or "").rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", url).lower()


def source_key(source, url, ext_id=None):
    base = f"{source}:{ext_id}" if ext_id else canonical_url(url)
    return hashlib.sha1(base.encode()).hexdigest()[:12]


def _similar(a, b):
    """Deux annonces désignent-elles probablement le même bien ?"""
    if a.get("postcode") and b.get("postcode") and a["postcode"] != b["postcode"]:
        return False
    sa, sb = a.get("surface"), b.get("surface")
    pa, pb = a.get("price"), b.get("price")
    if not (sa and sb and pa and pb):
        return False
    if abs(sa - sb) > max(1.5, 0.02 * sa):
        return False
    if abs(pa - pb) / max(pa, pb) > 0.04:
        return False
    ra, rb = a.get("rooms"), b.get("rooms")
    if ra and rb and ra != rb:
        return False
    # Si les deux ont une position précise, elles doivent être proches (~250 m).
    if a.get("lat") and b.get("lat") and a.get("geo_precision") == b.get("geo_precision") == "exact":
        if abs(a["lat"] - b["lat"]) > 0.0025 or abs(a["lon"] - b["lon"]) > 0.0035:
            return False
    return True


class Store:
    def __init__(self, data):
        self.listings = data.get("listings", {})
        self.by_source_key = {}
        for lid, l in self.listings.items():
            for s in l.get("sources", []):
                self.by_source_key[s["key"]] = lid

    def to_json(self):
        return {"updated_at": now_iso(), "listings": self.listings}

    def find_duplicate(self, item):
        for lid, l in self.listings.items():
            if _similar(l, item):
                return lid
        return None

    def upsert(self, item):
        """Ajoute ou met à jour une annonce. Renvoie (id, liste d'événements)."""
        ts = now_iso()
        key = source_key(item["source"], item["url"], item.get("ext_id"))
        events = []
        lid = self.by_source_key.get(key) or self.find_duplicate(item)
        src_entry = {"key": key, "source": item["source"], "url": item["url"], "first_seen": ts, "last_seen": ts}
        if item.get("price"):
            src_entry["price"] = item["price"]

        if lid is None:
            lid = key
            listing = {"id": lid, "first_seen": ts, "last_seen": ts, "sources": [src_entry],
                       "url": item["url"], "photos": item.get("photos", [])[:8], "active": True,
                       "price_history": [], "relisted_count": 0}
            for f in MERGE_FIELDS:
                if item.get(f) is not None:
                    listing[f] = item[f]
            if item.get("price"):
                listing["price_history"].append({"date": ts, "price": item["price"]})
            self.listings[lid] = listing
            events.append("new")
        else:
            listing = self.listings[lid]
            known = next((s for s in listing["sources"] if s["key"] == key), None)
            new_price = item.get("price")
            if known:
                known["last_seen"] = ts
                # Baisse de prix : uniquement si CETTE annonce baisse (deux agences peuvent afficher des prix différents)
                if new_price and known.get("price") and new_price < known["price"] * 0.995:
                    events.append("price_drop")
                if new_price:
                    known["price"] = new_price
            else:
                listing["sources"].append(src_entry)
            if not listing.get("active", True):
                listing["active"] = True
                listing["relisted_count"] = listing.get("relisted_count", 0) + 1
                listing.pop("removed_at", None)
                events.append("relisted")
            for f in MERGE_FIELDS:
                v = item.get(f)
                if v is None or f == "price":
                    continue
                if f == "description" and len(v) <= len(listing.get(f) or ""):
                    continue
                if listing.get(f) is None:
                    listing[f] = v
            # Prix retenu = le plus bas parmi les annonces de ce bien
            prices = [s["price"] for s in listing["sources"] if s.get("price")]
            best = min(prices) if prices else listing.get("price")
            if best and best != listing.get("price"):
                listing["price"] = best
                if known:  # vrai changement de prix d'une annonce (pas une autre agence moins chère)
                    listing["price_history"].append({"date": ts, "price": best})
                elif listing["price_history"]:
                    listing["price_history"][-1]["price"] = best
            if item.get("photos") and not listing.get("photos"):
                listing["photos"] = item["photos"][:8]
            listing["last_seen"] = ts
        self.by_source_key[key] = lid
        if listing.get("description"):
            listing["description"] = listing["description"][:4000]
        return lid, events

    def mark_missing(self, source, seen_keys, full_listing=True, grace_runs=3):
        """Pour une source qui renvoie TOUT son stock : marquer retirées les annonces absentes plusieurs fois de suite."""
        if not full_listing:
            return []
        removed = []
        for lid, l in self.listings.items():
            srcs = [s for s in l["sources"] if s["source"] == source]
            if not srcs or not l.get("active", True):
                continue
            for s in srcs:
                if s["key"] in seen_keys:
                    s["missing_runs"] = 0
                else:
                    s["missing_runs"] = s.get("missing_runs", 0) + 1
            # Retirée seulement si absente de toutes ses sources « stock complet ».
            if all(s.get("missing_runs", 0) >= grace_runs for s in srcs) and len(srcs) == len(l["sources"]):
                l["active"] = False
                l["removed_at"] = now_iso()
                removed.append(lid)
        return removed


def agency_norm(name):
    return normalize(name or "")
