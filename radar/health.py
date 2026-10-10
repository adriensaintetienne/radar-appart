"""Santé des sources : détecte les pannes et les silences anormaux, et prévient par WhatsApp."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .sources.agencies import slug as agency_slug
from .store import now_iso
from .textparse import normalize

ERROR_ALERT_AFTER = 3  # passages consécutifs en erreur (~6 à 9 h)


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        try:
            return parsedate_to_datetime(ts)
        except Exception:
            return None


def record(state, name, ok, count=0, info="", error=None, nonzero=None):
    h = state.setdefault("health", {}).setdefault(name, {})
    ts = now_iso()
    h["last_run"] = ts
    h["info"] = info
    if ok:
        h["last_ok"] = ts
        h["consecutive_errors"] = 0
        h.pop("error_alerted", None)
        if count > 0 or nonzero:
            h["last_nonzero"] = ts
            h.pop("silence_alerted", None)
        h["last_count"] = count
    else:
        h["consecutive_errors"] = h.get("consecutive_errors", 0) + 1
        h["last_error"] = (error or "")[:300]
        h["last_error_at"] = ts
    h.setdefault("first_run", ts)
    return h


def check(state, cfg):
    """Renvoie la liste des messages d'alerte système à envoyer."""
    msgs = []
    now = datetime.now(timezone.utc)
    thresholds = {"gmail": cfg["sources"]["gmail"].get("silence_alert_hours", 72),
                  "bienici": cfg["sources"]["bienici"].get("silence_alert_hours", 48)}
    for a in cfg["sources"].get("agencies", []):
        thresholds[agency_slug(a["name"])] = a.get("silence_alert_hours", 168)

    for name, h in state.get("health", {}).items():
        if h.get("consecutive_errors", 0) >= ERROR_ALERT_AFTER and not h.get("error_alerted"):
            msgs.append(f"la source « {name} » est en erreur depuis {h['consecutive_errors']} passages : {h.get('last_error', '')}")
            h["error_alerted"] = True
        limit = thresholds.get(name)
        if not limit:
            continue
        ref = _parse(h.get("last_nonzero")) or _parse(h.get("first_run"))
        if ref and now - ref > timedelta(hours=limit) and not h.get("silence_alerted"):
            msgs.append(f"aucune annonce reçue de « {name} » depuis plus de {limit} h — vérifier la source (tableau de bord › Sources)")
            h["silence_alerted"] = True

    # Silence par portail dans les alertes email (ex. plus aucun email SeLoger depuis 3 jours)
    gm = state.get("gmail", {})
    limit = thresholds["gmail"]
    alerted = gm.setdefault("sender_silence_alerted", {})
    for sender, s in gm.get("senders", {}).items():
        last = _parse(s.get("last_email_at"))
        if not last:
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last > timedelta(hours=limit):
            if not alerted.get(sender):
                msgs.append(f"plus aucune alerte email de « {sender} » depuis {limit} h — l'alerte a peut-être expiré sur le site")
                alerted[sender] = True
        else:
            alerted.pop(sender, None)
    return msgs


def coverage_check(store, state, cfg):
    """Biens d'une agence vus sur les portails mais absents de la collecte directe de son site."""
    now = datetime.now(timezone.utc)
    for a in cfg["sources"].get("agencies", []):
        if not a.get("enabled", True):
            continue
        slug = agency_slug(a["name"])
        names = [normalize(n) for n in a.get("match_names", [a["name"]])]
        misses = []
        for l in store.listings.values():
            if not l.get("active", True) or not l.get("agency"):
                continue
            if not any(n in normalize(l["agency"]) for n in names):
                continue
            if any(s["source"] == slug for s in l["sources"]):
                continue
            first = _parse(l.get("first_seen"))
            if first and now - first > timedelta(hours=24):
                misses.append(l["id"])
        h = state.setdefault("health", {}).setdefault(slug, {})
        h["coverage_misses"] = misses[:50]
