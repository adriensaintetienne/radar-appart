"""Configuration : valeurs par défaut + fusion avec data/config.json (modifié depuis le tableau de bord)."""
import copy
import json
import os

DEFAULT_CONFIG = {
    "search_name": "Paris Est - achat résidence principale",
    # Zone dessinée sur la carte (GeoJSON FeatureCollection de polygones).
    # Si vide, on se contente des codes postaux ci-dessous.
    "zone": {"type": "FeatureCollection", "features": []},
    "postcodes": ["75010", "75011", "75019", "75020"],
    "property_types": ["appartement", "loft", "atelier", "duplex"],
    "budget": {
        "total_max": 1400000,
        "total_min": 900000,
        "notary_rate": 0.075,
        "negotiable_margin": 0.05,
        # €/m² de travaux estimés selon l'état détecté
        "works_per_m2": {"heavy": 1500, "refresh": 500, "none": 0, "unknown": 500},
    },
    "surface": {"min": 85, "ideal": 100},
    "bedrooms": {
        "min": 3,
        # accepter min-1 chambres si une chambre supplémentaire est faisable
        "allow_one_less_if_possible": True,
        "possible_min_surface": 90,
        "possible_min_rooms": 4,
    },
    "elevator_required_from_floor": 1,
    "exclude_built_from_year": 1960,
    "exclude_keywords": [
        "viager", "nue-propriete", "nue propriete", "usufruit", "vendu loue", "vente occupee",
        "vendu occupe", "locataire en place", "bail en cours", "loue actuellement",
        "encheres", "enchere", "vente aux encheres", "parts de sci", "cession de parts",
        "vefa", "programme neuf", "frais de notaire reduits", "livraison prevue",
        "residence services", "residence senior", "droit au bail",
    ],
    # Signes d'un immeuble récent (>= 1960)
    "recent_building_keywords": [
        "immeuble recent", "residence recente", "construction recente", "immeuble moderne",
        "residence moderne", "batiment recent", "norme bbc", "rt 2012", "rt2012", "re2020",
    ],
    "state_keywords": {
        "heavy": [
            "a renover", "entierement a renover", "renovation complete", "a restructurer",
            "travaux a prevoir", "gros travaux", "a rehabiliter", "renovation totale",
            "a reprendre entierement", "a moderniser", "potentiel a exploiter", "plateau a amenager",
            "a amenager", "dans son jus",
        ],
        "refresh": ["a rafraichir", "rafraichissement", "quelques travaux", "travaux de decoration", "a remettre au gout du jour"],
        "none": [
            "refait a neuf", "entierement renove", "renove recemment", "renovation recente",
            "aucun travaux", "sans travaux", "pret a habiter", "recemment renove", "renove avec gout",
            "prestations haut de gamme", "renove par architecte", "renovation d'architecte",
        ],
        "architect": ["architecte", "renovation d'architecte", "renove par un architecte"],
    },
    # Points de score. Le score final est ramené sur 100.
    "scoring": [
        {"label": "Lumineux", "points": 15, "keywords": ["lumineux", "lumineuse", "luminosite", "baigne de lumiere", "plein sud", "ensoleille", "tres clair", "belle lumiere"]},
        {"label": "Beau parquet", "points": 12, "keywords": ["parquet", "point de hongrie", "chevron", "parquet massif", "parquet ancien"]},
        {"label": "Cave", "points": 12, "field": "cellar"},
        {"label": "À rénover", "points": 12, "field": "state_heavy"},
        {"label": "Haussmannien / ancien", "points": 10, "keywords": ["haussmannien", "haussmannienne", "pierre de taille", "moulures", "cheminee", "cheminees", "hauteur sous plafond", "immeuble ancien", "art deco", "belle epoque"]},
        {"label": "Loft / atelier", "points": 10, "keywords": ["loft", "atelier", "ancienne usine", "ancien atelier", "verriere", "esprit loft", "ambiance loft", "ancien entrepot", "plateau"]},
        {"label": "Surface ~100 m²", "points": 6, "field": "surface_ideal"},
        {"label": "Dernier étage", "points": 6, "field": "top_floor"},
        {"label": "Traversant", "points": 6, "keywords": ["traversant", "traversante", "double exposition", "triple exposition"]},
        {"label": "2e salle de bain", "points": 6, "keywords": ["deux salles de bains", "deux salles de bain", "2 salles de bains", "2 salles de bain", "deux salles d'eau", "2 salles d'eau", "salle de bains et une salle d'eau", "salle de bain et une salle d'eau", "salle d'eau et une salle de bain"]},
        {"label": "Extérieur", "points": 4, "keywords": ["balcon", "terrasse", "jardin", "loggia", "patio", "cour privative"]},
        {"label": "Calme", "points": 4, "keywords": ["calme", "au calme", "sur cour", "sans vis-a-vis", "sans vis a vis", "dégagé", "degage", "silencieux"]},
        {"label": "Rénovation standard clé en main", "points": -6, "field": "state_none_not_architect"},
    ],
    "notifications": {
        # [{"name": "...", "phone": "+336...", "apikey": "..."}] — clé CallMeBot de chaque personne
        "whatsapp": [],
        "email_to": [],
        "alert_new": True,
        "alert_price_drop": True,
        "alert_relisted": True,
        "alert_statuses": ["match", "a_verifier", "negociable"],
        "min_score": 0,
        "max_messages_per_run": 12,
        "daily_digest_email": False,
    },
    "sources": {
        "gmail": {"enabled": True, "silence_alert_hours": 72},
        "bienici": {"enabled": True, "silence_alert_hours": 48},
        "websearch": {
            "enabled": True,
            "silence_alert_hours": 0,
            "queries": [
                "loft à vendre Paris 11e", "loft à vendre Paris 20e", "atelier à vendre Paris 19e",
                "appartement haussmannien à vendre Paris 10e", "appartement à rénover à vendre Paris 11e",
                "appartement 3 chambres à vendre Paris 20e", "appartement à vendre Paris 19e cave parquet",
            ],
        },
        # Sites d'agences collectés directement. "list_urls" = pages listant les biens à vendre,
        # "link_pattern" = expression régulière qui reconnaît un lien vers une fiche de bien.
        "agencies": [
            {
                "name": "Espaces Atypiques",
                "enabled": True,
                # Les 5 premières pages (triées par date) contiennent les nouveautés de toute la France ;
                # le code postal présent dans l'adresse de chaque fiche permet de ne lire que le secteur.
                "list_urls": ["https://www.espaces-atypiques.com/ventes/"]
                + [f"https://www.espaces-atypiques.com/ventes/page/{i}/" for i in range(2, 6)],
                "link_pattern": r"espaces-atypiques\.com/ventes/\d{5}-",
                "postcode_in_url": r"/ventes/(\d{5})-",
                "full_listing": False,
                "match_names": ["espaces atypiques"],
                "silence_alert_hours": 72,
            }
        ],
    },
    "gemini": {"enabled": False, "model": "gemini-2.5-flash", "max_per_run": 20},
    "enrich_detail_pages": True,
}


def _merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(data_dir):
    path = os.path.join(data_dir, "config.json")
    user = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
    return _merge(DEFAULT_CONFIG, user)


def load_json(path, default):
    if not os.path.exists(path):
        return copy.deepcopy(default)
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return copy.deepcopy(default)


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=False)
    os.replace(tmp, path)
