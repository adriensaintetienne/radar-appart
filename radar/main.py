"""Point d'entrée : collecte toutes les sources, analyse, met à jour la base et envoie les alertes.

Usage : python -m radar.main --data ./data [--dry-run] [--only bienici,gmail,...]
"""
import argparse
import os
import sys
import time
import traceback
from urllib.parse import urlparse

from . import ai, analyze, health, notify
from .config import load_config, load_json, save_json
from .enrich import enrich
from .http import Blocked
from .sources import agencies, bienici, gmail_alerts, websearch
from .store import Store, now_iso, source_key

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def prefilter(item, cfg):
    """Écarte tout de suite ce qui est hors secteur pour ne pas encombrer la base."""
    pc = item.get("postcode")
    return not (pc and cfg["postcodes"] and pc not in cfg["postcodes"])


def run(data_dir, dry_run=False, only=None):
    cfg = load_config(data_dir)
    if cfg.get("gmail_address"):  # adresse saisie dans la configuration : prioritaire sur le secret GitHub
        os.environ["GMAIL_ADDRESS_OVERRIDE"] = cfg["gmail_address"]
    paths = {k: os.path.join(data_dir, f"{k}.json") for k in ("listings", "state", "tracking")}
    store = Store(load_json(paths["listings"], {"listings": {}}))
    state = load_json(paths["state"], {})
    tracking = load_json(paths["tracking"], {"items": {}})
    first_run = not state.get("runs")
    events = {}  # listing id -> set(événements)
    blocked_domains = set()
    run_summary = {"started_at": now_iso(), "sources": {}}

    def wanted(name):
        return only is None or name in only

    def ingest(items, source_name, full_listing):
        seen_keys, n_new = set(), 0
        for item in items:
            if item.pop("_known", False):
                key = source_key(item["source"], item["url"], item.get("ext_id"))
                seen_keys.add(key)
                lid = store.by_source_key.get(key)
                if lid:
                    for s in store.listings[lid]["sources"]:
                        if s["key"] == key:
                            s["last_seen"] = now_iso()
                    if not store.listings[lid].get("active", True):
                        store.upsert(item)  # remise en ligne
                        events.setdefault(lid, set()).add("relisted")
                continue
            if not prefilter(item, cfg):
                continue
            key = source_key(item["source"], item["url"], item.get("ext_id"))
            is_new = key not in store.by_source_key
            needs_detail = item.get("via") == "email"  # les sources directes fournissent déjà la fiche complète
            if is_new and needs_detail and cfg.get("enrich_detail_pages") and not item.get("enriched"):
                dom = urlparse(item["url"]).netloc
                if dom not in blocked_domains:
                    if not enrich(item) and "bloqué" in item.get("enrich_error", ""):
                        blocked_domains.add(dom)  # inutile d'insister pendant ce passage
            if not prefilter(item, cfg):
                continue
            lid, evs = store.upsert(item)
            seen_keys.add(key)
            if evs:
                events.setdefault(lid, set()).update(evs)
                n_new += "new" in evs
        removed = store.mark_missing(source_name, seen_keys, full_listing=full_listing)
        return n_new, removed

    # ---------- 1. Collecte
    sources = []
    if cfg["sources"]["gmail"].get("enabled") and wanted("gmail"):
        sources.append(("gmail", lambda: gmail_alerts.fetch(cfg, state), None))
    if cfg["sources"]["bienici"].get("enabled") and wanted("bienici"):
        sources.append(("bienici", lambda: bienici.fetch(cfg, state), "bienici"))
    for ag in cfg["sources"].get("agencies", []):
        name = agencies.slug(ag["name"])
        if ag.get("enabled", True) and (wanted(name) or (only and "agencies" in only)):
            known = {s["key"] for l in store.listings.values() for s in l["sources"] if s["source"] == name}
            sources.append((name, (lambda a=ag, k=known: agencies.fetch_agency(a, k, cfg["postcodes"])), name))
    if cfg["sources"]["websearch"].get("enabled") and wanted("websearch"):
        sources.append(("websearch", lambda: websearch.fetch(cfg, state), None))

    for name, fn, full_name in sources:
        log(f"Source {name}…")
        try:
            items, meta = fn()
            if meta.get("skipped"):
                log(f"  {meta['info']}")
                if "veille" in meta.get("info", ""):
                    health.record(state, name, True, info=meta["info"])
                continue
            n_new, removed = ingest(items, full_name or name, bool(full_name) and meta.get("full_listing", False))
            info = meta.get("info", "") + (f" — {n_new} nouvelle(s)" if n_new else "")
            health.record(state, name, True, count=len(items), info=info, nonzero=meta.get("nonzero"))
            run_summary["sources"][name] = {"ok": True, "count": len(items), "new": n_new, "removed": len(removed)}
            log(f"  OK : {len(items)} élément(s), {n_new} nouveau(x), {len(removed)} retiré(s)")
        except Blocked as e:
            health.record(state, name, False, error=f"bloqué par le site : {e}")
            run_summary["sources"][name] = {"ok": False, "error": "bloqué"}
            log(f"  BLOQUÉ : {e}")
        except Exception as e:
            health.record(state, name, False, error=f"{type(e).__name__}: {e}")
            run_summary["sources"][name] = {"ok": False, "error": type(e).__name__}
            log(f"  ERREUR : {type(e).__name__}")
            traceback.print_exc(limit=2, file=sys.stdout) if os.environ.get("RADAR_DEBUG") else None

    # ---------- 2. Analyse (toutes les annonces, pour refléter d'éventuels changements de critères)
    for l in store.listings.values():
        analyze.analyze(l, cfg)

    if ai.enabled(cfg):
        budget = cfg["gemini"].get("max_per_run", 20)
        for lid, l in store.listings.items():
            if budget <= 0:
                break
            if l.get("ai") or l.get("ai_error") or l["status"] == "rejete" or not l.get("active", True):
                continue
            try:
                ai.apply(l, ai.analyze(l, cfg))
                analyze.analyze(l, cfg)
            except Exception as e:
                l["ai_error"] = str(e)[:160]
            budget -= 1

    health.coverage_check(store, state, cfg)

    # ---------- 3. Alertes
    n = cfg["notifications"]
    user_rejected = {lid for lid, t in tracking.get("items", {}).items() if t.get("status") == "rejete"}
    alerts = []
    for lid, l in store.listings.items():
        if not l.get("active", True) or lid in user_rejected:
            continue
        ok = l["status"] in n["alert_statuses"] and l.get("score", 0) >= n.get("min_score", 0)
        evs = events.get(lid, set())
        if ok and not l.get("alerted"):
            if n["alert_new"] and not first_run:
                alerts.append((l, "relisted" if "relisted" in evs else "new"))
            l["alerted"] = now_iso()
        elif ok and "relisted" in evs and n["alert_relisted"]:
            alerts.append((l, "relisted"))
        elif ok and "price_drop" in evs and n["alert_price_drop"]:
            alerts.append((l, "price_drop"))
    alerts.sort(key=lambda a: (-a[0].get("score", 0)))

    system_msgs = health.check(state, cfg)
    if not state.get("welcome_sent"):
        matched = sum(1 for l in store.listings.values() if l["status"] != "rejete" and l.get("active", True))
        system_msgs.insert(0, f"le radar est en route ✅ — {matched} annonce(s) correspondante(s) déjà en ligne, à voir sur le tableau de bord. "
                              "Les prochaines alertes ne concerneront que les nouveautés.")

    # Messages qui n'avaient pas pu être délivrés au passage précédent : on les renvoie.
    pending = state.get("pending", {})
    queued = {(a[0].get("id"), a[1]) for a in alerts}
    for lid, ev in pending.get("alerts", []):
        if lid in store.listings and (lid, ev) not in queued:
            alerts.append((store.listings[lid], ev))
    system_msgs = [m for m in pending.get("system", []) if m not in system_msgs and "en route" not in m] + system_msgs

    # ---------- 4. Sauvegarde puis envoi
    run_summary.update(finished_at=now_iso(), alerts=len(alerts), system_messages=len(system_msgs))
    dashboard = os.environ.get("DASHBOARD_URL")
    if not dry_run and (alerts or system_msgs):
        rep = notify.dispatch(cfg, alerts, system_msgs, dashboard)
        run_summary["notify"] = rep
        delivered = rep["email_sent"] or (rep["whatsapp_sent"] > 0 and not rep["whatsapp_failed"])
        if delivered:
            state.pop("pending", None)
            state["welcome_sent"] = True
        else:
            # Rien n'est parti (WhatsApp et email en échec) : on garde tout pour le prochain passage.
            state["pending"] = {"alerts": [[l["id"], ev] for l, ev in alerts][-50:], "system": system_msgs[-10:]}
            health.record(state, "notifications", False, error="aucun message n'a pu être envoyé (WhatsApp et email en échec) — nouvel essai au prochain passage")
        if rep["whatsapp_failed"]:
            health.record(state, "whatsapp", False, error="échec CallMeBot pour : " + ", ".join(rep["whatsapp_failed"]))
        elif rep["whatsapp_sent"]:
            health.record(state, "whatsapp", True, count=rep["whatsapp_sent"], info=f"{rep['whatsapp_sent']} message(s) envoyé(s)")
        if delivered and state.get("health", {}).get("notifications"):
            health.record(state, "notifications", True, info="messages envoyés")
    state["runs"] = (state.get("runs", []) + [run_summary])[-60:]
    save_json(paths["listings"], store.to_json())
    save_json(paths["state"], state)
    log(f"Terminé : {len(store.listings)} annonce(s) en base, {len(alerts)} alerte(s), {len(system_msgs)} message(s) système.")
    return run_summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--dry-run", action="store_true", help="n'envoie aucune notification")
    p.add_argument("--only", help="liste de sources séparées par des virgules")
    a = p.parse_args()
    run(a.data, dry_run=a.dry_run, only=set(a.only.split(",")) if a.only else None)


if __name__ == "__main__":
    main()
