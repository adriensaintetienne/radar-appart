"""Annonces ajoutées à la main depuis le tableau de bord (lien + texte collé + champs facultatifs).

Le tableau de bord les dépose dans tracking.json (« manual_queue ») ; le robot les traite et note le résultat
dans state.json (« manual_done »), que le tableau de bord affiche.
"""
import re
from urllib.parse import urlparse

from ..enrich import enrich
from ..textparse import extract_all

NAME = "manuel"
FIELDS = ("price", "surface", "rooms", "bedrooms", "floor", "elevator", "cellar", "postcode")


def pending(tracking, state):
    done = state.get("manual_done", {})
    return [e for e in tracking.get("manual_queue", []) if e.get("id") and e["id"] not in done]


def build_item(entry):
    """Construit l'annonce à partir de la fiche (si le site la laisse lire), du texte collé et des champs saisis."""
    url = (entry.get("url") or "").strip()
    text = (entry.get("text") or "").strip()
    item = {"source": NAME, "url": url or f"manuel:{entry['id']}", "ext_id": entry["id"], "manual": True}
    page_ok, error = False, None
    if url.startswith("http"):
        probe = {"url": url}
        page_ok = enrich(probe)
        if page_ok:
            item.update({k: v for k, v in probe.items() if k not in ("url", "enriched", "enrich_error") and v not in (None, "", [])})
        else:
            error = probe.get("enrich_error")
    if text:
        if len(text) > len(item.get("description") or ""):
            item["description"] = text[:6000]
            item["full_text"] = len(text) >= 200
        if not item.get("title"):
            item["title"] = text.split("\n", 1)[0][:160]
        for k, v in extract_all(text).items():
            if item.get(k) is None and v is not None:
                item[k] = v
    # Les champs saisis à la main priment sur tout le reste
    for k in FIELDS:
        v = (entry.get("fields") or {}).get(k)
        if v not in (None, ""):
            item[k] = v
    if not item.get("title"):
        item["title"] = urlparse(url).netloc or "Annonce ajoutée à la main"
    useful = page_ok or text or any((entry.get("fields") or {}).get(k) not in (None, "") for k in FIELDS)
    if not useful:
        domain = urlparse(url).netloc or "?"
        return None, (f"{domain} empêche la lecture automatique de la page : collez le texte de l'annonce "
                      f"ou remplissez prix / surface / étage dans le formulaire ({error or 'page illisible'})")
    note = None if page_ok or not url.startswith("http") else \
        f"page non lisible automatiquement ({urlparse(url).netloc}) : analyse faite avec le texte et les champs fournis"
    return item, note
