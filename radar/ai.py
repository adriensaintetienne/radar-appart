"""Analyse optionnelle par l'IA Gemini (offre gratuite de Google) : texte + premières photos.

Activée seulement si le secret GEMINI_API_KEY existe et que l'option est cochée dans le tableau de bord.
Le résultat complète l'analyse par mots-clés (il ne remplace jamais un critère éliminatoire certain).
"""
import base64
import json
import os

import requests

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """Tu analyses une annonce immobilière parisienne pour des acheteurs. Réponds UNIQUEMENT en JSON :
{{"etat": "heavy|refresh|none|unknown",          // heavy = rénovation lourde à prévoir, refresh = rafraîchissement, none = refait
  "renovation_architecte": true|false,
  "immeuble_avant_1960": true|false|null,        // d'après le style (haussmannien, faubourien, années 30...) ou le texte
  "lumineux": true|false|null, "beau_parquet": true|false|null, "traversant": true|false|null,
  "cave": true|false|null, "ascenseur": true|false|null, "etage": entier|null,
  "chambres": entier|null, "troisieme_chambre_possible": true|false|null,
  "deux_salles_de_bain_ou_possible": true|false|null,
  "style": "haussmannien|loft|ancien|annees30|moderne|autre|inconnu",
  "arnaque_suspecte": true|false,
  "resume": "2 phrases max en français, points forts et points faibles"}}

Annonce :
{text}"""

BONUS_MAP = {"lumineux": "Lumineux", "beau_parquet": "Beau parquet", "traversant": "Traversant", "cave": "Cave",
             "deux_salles_de_bain_ou_possible": "2e salle de bain"}


def enabled(cfg):
    return bool(cfg.get("gemini", {}).get("enabled") and os.environ.get("GEMINI_API_KEY"))


def analyze(listing, cfg):
    key = os.environ["GEMINI_API_KEY"]
    text = f"{listing.get('title', '')}\n{listing.get('description', '')}"[:6000]
    parts = [{"text": PROMPT.format(text=text)}]
    for url in (listing.get("photos") or [])[:3]:
        try:
            r = requests.get(url, timeout=15)
            if r.ok and r.headers.get("content-type", "").startswith("image/") and len(r.content) < 4_000_000:
                parts.append({"inline_data": {"mime_type": r.headers["content-type"].split(";")[0],
                                              "data": base64.b64encode(r.content).decode()}})
        except requests.RequestException:
            pass
    body = {"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1}}
    r = requests.post(API.format(model=cfg["gemini"]["model"]), params={"key": key}, json=body, timeout=60)
    r.raise_for_status()
    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(raw)
    data["bonus"] = [label for k, label in BONUS_MAP.items() if data.get(k) is True]
    if data.get("style") == "haussmannien" or data.get("style") == "ancien":
        data["bonus"].append("Haussmannien / ancien")
    if data.get("style") == "loft":
        data["bonus"].append("Loft / atelier")
    return data


def apply(listing, ai):
    """Utilise les réponses de l'IA pour combler les infos manquantes (jamais pour écraser une info lue)."""
    listing["ai"] = ai
    for src, dst in (("ascenseur", "elevator"), ("etage", "floor"), ("chambres", "bedrooms"), ("cave", "cellar")):
        if listing.get(dst) is None and ai.get(src) is not None:
            listing[dst] = ai[src]
    if ai.get("immeuble_avant_1960") is False and not listing.get("build_year"):
        listing["ai_recent_building"] = True
