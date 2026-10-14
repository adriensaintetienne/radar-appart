"""Consultation de la fiche complète d'une annonce pour compléter les infos (description, étage, ascenseur…).

Générique : JSON-LD (schema.org), balises OpenGraph, données Next.js (Leboncoin notamment) et texte brut.
Si le site bloque les robots, on garde simplement les infos déjà connues.
"""
import json
import re

from bs4 import BeautifulSoup

from . import http
from .textparse import extract_all

CONTENT_SELECTORS = [".classified-content", "#classified-description", "[class*=annonce-description]",
                     "[class*=property-description]", "[itemprop=description]"]

LBC_ATTR_MAP = {
    "square": "surface", "rooms": "rooms", "bedrooms": "bedrooms", "floor_number": "floor",
    "elevator": "elevator", "nb_floors_building": None,
}


def _jsonld(soup):
    out = {}
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
                continue
            if not isinstance(d, dict):
                continue
            stack.extend(v for v in d.values() if isinstance(v, (dict, list)))
            if d.get("description") and len(d["description"]) > len(out.get("description", "")):
                out["description"] = d["description"]
            offers = d.get("offers")
            if isinstance(offers, dict) and offers.get("price"):
                try:
                    out["price"] = int(float(str(offers["price"]).replace(" ", "")))
                except ValueError:
                    pass
            fs = d.get("floorSize")
            if isinstance(fs, dict) and fs.get("value"):
                try:
                    out["surface"] = float(str(fs["value"]).replace(",", "."))
                except ValueError:
                    pass
            if d.get("numberOfRooms"):
                try:
                    out["rooms"] = int(float(str(d["numberOfRooms"])))
                except ValueError:
                    pass
            geo = d.get("geo")
            if isinstance(geo, dict) and geo.get("latitude"):
                try:
                    out["lat"], out["lon"] = float(geo["latitude"]), float(geo["longitude"])
                    out["geo_precision"] = "approx"
                except (TypeError, ValueError):
                    pass
            img = d.get("image")
            if isinstance(img, str):
                out.setdefault("photos", []).append(img)
            elif isinstance(img, list):
                out.setdefault("photos", []).extend(i for i in img if isinstance(i, str))
    return out


def _next_data(soup):
    s = soup.find("script", id="__NEXT_DATA__")
    if not s or not s.string:
        return {}
    try:
        data = json.loads(s.string)
    except Exception:
        return {}
    ad = (data.get("props", {}).get("pageProps", {}) or {}).get("ad") or {}
    out = {}
    if ad.get("body"):
        out["description"] = ad["body"]
    if ad.get("subject"):
        out["title"] = ad["subject"]
    price = ad.get("price")
    if isinstance(price, list) and price:
        out["price"] = price[0]
    for a in ad.get("attributes", []) or []:
        k, v = a.get("key"), a.get("value")
        f = LBC_ATTR_MAP.get(k)
        if f and v is not None:
            if f == "elevator":
                out[f] = str(v) in ("1", "true", "oui")
            else:
                try:
                    out[f] = float(v) if f == "surface" else int(v)
                except ValueError:
                    pass
    loc = ad.get("location") or {}
    if loc.get("zipcode"):
        out["postcode"] = loc["zipcode"]
    if loc.get("lat"):
        out["lat"], out["lon"], out["geo_precision"] = loc["lat"], loc["lng"], "approx"
    imgs = (ad.get("images") or {}).get("urls_large") or (ad.get("images") or {}).get("urls")
    if imgs:
        out["photos"] = imgs
    return out


def parse_detail_html(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    out.update({k: v for k, v in _jsonld(soup).items() if v})
    out.update({k: v for k, v in _next_data(soup).items() if v})
    og = {m.get("property"): m.get("content") for m in soup.find_all("meta", property=True)}
    if og.get("og:title") and not out.get("title"):
        out["title"] = og["og:title"]
    if og.get("og:image") and not out.get("photos"):
        out["photos"] = [og["og:image"]]
    for t in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        t.decompose()
    body = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    # Blocs « contenu de l'annonce » connus (Figaro Immobilier…) : description complète + atouts + caractéristiques
    for sel in CONTENT_SELECTORS:
        el = soup.select_one(sel)
        if el:
            txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
            if len(txt) > len(out.get("description") or ""):
                out["description"] = txt[:6000]
            break
    if not out.get("description"):
        desc = og.get("og:description") or ""
        out["description"] = desc if len(desc) > 200 else body[:4000]
    parsed = extract_all((out.get("title") or "") + " " + (out.get("description") or "") + " " + body[:6000])
    for k, v in parsed.items():
        if out.get(k) is None and v is not None:
            out[k] = v
    return out


def _bienici_api(url):
    """Fiche complète d'une annonce Bien'ici via son API (la page web, elle, est vide sans JavaScript)."""
    from .sources import bienici
    m = re.search(r"bienici\.com/annonce/(?:[^/?#]+/)*?([^/?#]*\d[^/?#]*)(?:[?#]|$)", url)
    if not m:
        return None
    r = http.get("https://www.bienici.com/realEstateAd.json", params={"id": m.group(1)}, min_delay=1.5,
                 headers={"Accept": "application/json"})
    item = bienici._to_item(r.json())
    item.pop("source", None)
    return {k: v for k, v in item.items() if v not in (None, "", [])}


def enrich(item):
    """Complète `item` à partir de sa fiche. Renvoie True si la fiche a pu être lue."""
    try:
        if "bienici.com/annonce/" in item["url"]:
            data = _bienici_api(item["url"]) or {}
        else:
            r = http.get(item["url"], min_delay=3.0)
            data = parse_detail_html(r.text)
    except Exception as e:
        item["enrich_error"] = str(e)[:160]
        return False
    for k, v in data.items():
        if v is None:
            continue
        if k == "description":
            if len(v) > len(item.get("description") or ""):
                item[k] = v
        elif k == "photos":
            if not item.get("photos"):
                item[k] = v[:8]
        elif item.get(k) is None:
            item[k] = v
    item["enriched"] = True
    item["full_text"] = True
    return True
