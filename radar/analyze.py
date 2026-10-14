"""Filtrage (critères éliminatoires), estimation du coût total et score de chaque annonce.

Statuts possibles :
  match       : respecte tous les critères connus
  a_verifier  : aucun critère éliminatoire violé, mais une info clé manque ou est ambiguë
  negociable  : coût total jusqu'à +5 % au-dessus du budget
  rejete      : au moins un critère éliminatoire violé (le motif est conservé et visible)
"""
from . import geo
from .textparse import extract_all, find_keywords, normalize


def _fill_from_text(listing):
    """Complète les champs manquants à partir du titre + description."""
    text = " ".join(filter(None, [listing.get("title"), listing.get("description")]))
    parsed = extract_all(text)
    for k, v in parsed.items():
        if listing.get(k) is None and v is not None:
            listing[k] = v
    return text


def detect_state(text, cfg):
    sk = cfg["state_keywords"]
    found = {k: find_keywords(text, sk.get(k, [])) for k in ("heavy", "refresh", "none", "architect")}
    if found["heavy"]:
        state = "heavy"
    elif found["refresh"]:
        state = "refresh"
    elif found["none"]:
        state = "none"
    else:
        state = "unknown"
    return state, bool(found["architect"]), found


def estimate_total(listing, cfg):
    b = cfg["budget"]
    price, surface = listing.get("price"), listing.get("surface")
    if not price:
        return None, None
    works_m2 = b["works_per_m2"].get(listing.get("state") or "unknown", b["works_per_m2"]["unknown"])
    works = works_m2 * (surface or cfg["surface"]["ideal"])
    notary = price * b["notary_rate"]
    total = int(round(price + notary + works, -3))
    return total, {"price": price, "notary": int(notary), "works": int(works), "works_per_m2": works_m2}


def analyze(listing, cfg):
    text = _fill_from_text(listing)
    ntext = normalize(text)
    reasons_reject, reasons_check, flags = [], [], []
    ai = listing.get("ai") or {}
    if ai.get("arnaque_suspecte"):
        flags.append("⚠️ l'IA soupçonne une annonce douteuse")

    # --- exclusions par mots-clés
    excl = find_keywords(ntext, cfg["exclude_keywords"])
    if excl:
        reasons_reject.append("type de vente exclu : " + ", ".join(sorted(set(excl))))
    if listing.get("new_build"):
        reasons_reject.append("programme neuf")
    ptype = normalize(listing.get("property_type") or "")
    if ptype and any(w in ptype for w in ("terrain", "parking", "bureau", "local", "immeuble", "programme", "house")):
        reasons_reject.append(f"type de bien exclu ({listing.get('property_type')})")

    # --- localisation
    gstatus, gmsg = geo.locate(listing, cfg["zone"], cfg["postcodes"], cfg.get("zone_tolerance_m", 0),
                                cfg.get("zone_out_below", 0.20), cfg.get("zone_in_above", 0.80))
    listing["geo_status"] = gstatus
    if gstatus == "out":
        reasons_reject.append(gmsg)
    elif gstatus in ("edge", "unknown"):
        reasons_check.append(gmsg)
    elif gstatus == "approx":
        flags.append("📍 " + gmsg)

    # --- surface
    smin = cfg["surface"]["min"]
    if listing.get("surface") is None:
        reasons_check.append("surface non précisée")
    elif listing["surface"] < smin:
        reasons_reject.append(f"surface {listing['surface']:g} m² < {smin} m²")

    # --- chambres
    bc = cfg["bedrooms"]
    bedrooms = listing.get("bedrooms")
    if bedrooms is None and listing.get("rooms"):
        bedrooms = max(listing["rooms"] - 1, 0)
        flags.append(f"chambres estimées à {bedrooms} (d'après {listing['rooms']} pièces)")
    listing["bedrooms_eff"] = bedrooms
    if bedrooms is None:
        reasons_check.append("nombre de chambres inconnu")
    elif bedrooms < bc["min"]:
        possible = (
            bc.get("allow_one_less_if_possible") and bedrooms == bc["min"] - 1 and (
                (listing.get("surface") or 0) >= bc["possible_min_surface"]
                or (listing.get("rooms") or 0) >= bc["possible_min_rooms"]
                or find_keywords(ntext, ["double sejour", "double salon", "possibilite de 3 chambres", "possibilite 3 chambres",
                                         "possibilite d'une troisieme chambre", "3eme chambre possible", "troisieme chambre possible",
                                         "chambre supplementaire", "bureau pouvant servir de chambre"])
                or ai.get("troisieme_chambre_possible") is True
            )
        )
        if possible:
            flags.append(f"🛠️ {bc['min']}e chambre possible")
        else:
            reasons_reject.append(f"{bedrooms} chambre(s) < {bc['min']}")

    # --- étage / ascenseur
    floor, elevator = listing.get("floor"), listing.get("elevator")
    req = cfg["elevator_required_from_floor"]
    # Description complète disponible (Bien'ici, site d'agence, fiche ouverte) et aucun ascenseur mentionné :
    # on considère qu'il n'y en a pas. Les résumés d'email (2 lignes) ne permettent pas cette conclusion.
    full_text = (listing.get("full_text") or any(s["source"] == "bienici" or s["source"].startswith("agence:")
                                                 for s in listing.get("sources", []))) \
        and len(listing.get("description") or "") >= 200
    assumed = elevator is None and full_text and floor != 0 and cfg.get("no_mention_means_no_elevator", True)
    if assumed:
        elevator = False
    if floor is not None and floor >= req:
        if elevator is False:
            reasons_reject.append(f"{floor}e étage sans ascenseur" + (" (aucun ascenseur mentionné)" if assumed else ""))
        elif elevator is None:
            reasons_check.append(f"{floor}e étage, ascenseur non précisé (annonce incomplète)")
    elif floor is None and elevator is False and assumed:
        reasons_reject.append("aucun ascenseur mentionné (étage non précisé)")
    elif floor is None and elevator is not True:
        reasons_check.append("étage / ascenseur non précisés")
    if floor == 0:
        flags.append("rez-de-chaussée")

    # --- immeuble récent
    year = listing.get("build_year")
    if year and year >= cfg["exclude_built_from_year"]:
        reasons_reject.append(f"immeuble de {year}")
    elif not year:
        recent = find_keywords(ntext, cfg["recent_building_keywords"])
        if recent:
            reasons_check.append("immeuble peut-être récent (" + ", ".join(recent) + ")")
        elif listing.get("ai_recent_building"):
            reasons_check.append("immeuble peut-être postérieur à 1960 (d'après l'IA)")

    # --- état & budget
    state, architect, _ = detect_state(ntext, cfg)
    if state == "unknown" and ai.get("etat") in ("heavy", "refresh", "none"):
        state = ai["etat"]
    architect = architect or bool(ai.get("renovation_architecte"))
    listing["state"], listing["architect"] = state, architect
    total, breakdown = estimate_total(listing, cfg)
    listing["total_cost"], listing["cost_breakdown"] = total, breakdown
    negotiable = False
    b = cfg["budget"]
    if total is None:
        reasons_check.append("prix non précisé")
    else:
        if total > b["total_max"] * (1 + b["negotiable_margin"]):
            reasons_reject.append(f"coût total estimé {total/1e6:.2f} M€ > {b['total_max']/1e6:.2f} M€")
        elif total > b["total_max"]:
            negotiable = True
            flags.append(f"💶 {100*(total/b['total_max']-1):.1f} % au-dessus du budget")
        if total < b["total_min"]:
            reasons_reject.append(f"coût total estimé {total/1e6:.2f} M€ < {b['total_min']/1e6:.2f} M€")
        if state == "unknown":
            flags.append("état inconnu : travaux estimés à %d €/m²" % b["works_per_m2"]["unknown"])

    # --- score
    score, details = compute_score(listing, ntext, cfg)
    listing["score"], listing["score_details"] = score, details

    if reasons_reject:
        status = "rejete"
    elif reasons_check:
        status = "a_verifier"
    elif negotiable:
        status = "negociable"
    else:
        status = "match"
    listing["status"] = status
    listing["reasons"] = reasons_reject + reasons_check
    listing["flags"] = flags
    return listing


def compute_score(listing, ntext, cfg):
    surf = listing.get("surface") or 0
    ideal = cfg["surface"]["ideal"]
    fields = {
        "cellar": bool(listing.get("cellar")),
        "state_heavy": listing.get("state") == "heavy",
        "surface_ideal": surf >= ideal - 7,
        "top_floor": bool(listing.get("top_floor")),
        "state_none_not_architect": listing.get("state") == "none" and not listing.get("architect"),
    }
    ai = listing.get("ai") or {}
    total_pos = sum(r["points"] for r in cfg["scoring"] if r["points"] > 0) or 1
    score, details = 0, []
    for rule in cfg["scoring"]:
        hit = False
        if "field" in rule:
            hit = fields.get(rule["field"], False)
        if not hit and rule.get("keywords"):
            hit = bool(find_keywords(ntext, rule["keywords"]))
        if not hit and ai.get("bonus") and rule["label"] in ai["bonus"]:
            hit = True
        if hit:
            score += rule["points"]
            details.append(rule["label"])
    return max(0, min(100, round(100 * score / total_pos))), details
