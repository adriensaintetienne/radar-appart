"""Alertes : WhatsApp via CallMeBot (gratuit), email Gmail en secours ou en récapitulatif."""
import os
import smtplib
import time
from email.mime.text import MIMEText

import requests

from .config import gmail_credentials

CALLMEBOT = "https://api.callmebot.com/whatsapp.php"
STATUS_LABEL = {"match": "✅ Correspond", "a_verifier": "🔎 À vérifier", "negociable": "💶 Négociable"}
EVENT_LABEL = {"new": "🏠 Nouvelle annonce", "price_drop": "📉 Baisse de prix", "relisted": "🔁 Remise en ligne"}


def _fmt_price(v):
    return f"{v:,.0f} €".replace(",", " ") if v else "prix ?"


def format_listing(l, event, dashboard_url=None):
    lines = [f"{EVENT_LABEL.get(event, event)} — {STATUS_LABEL.get(l.get('status'), l.get('status'))} (score {l.get('score', 0)}/100)"]
    bits = [_fmt_price(l.get("price"))]
    if l.get("surface"):
        bits.append(f"{l['surface']:g} m²")
    if l.get("rooms"):
        bits.append(f"{l['rooms']} p.")
    if l.get("bedrooms_eff") is not None:
        bits.append(f"{l['bedrooms_eff']} ch.")
    if l.get("floor") is not None:
        bits.append("RDC" if l["floor"] == 0 else f"{l['floor']}e ét.")
    if l.get("postcode"):
        bits.append(l["postcode"])
    lines.append(" · ".join(bits))
    if event == "price_drop" and len(l.get("price_history", [])) >= 2:
        old = l["price_history"][-2]["price"]
        lines.append(f"Avant : {_fmt_price(old)} ({100 * (l['price'] / old - 1):+.1f} %)")
    if l.get("total_cost"):
        lines.append(f"Coût total estimé : {_fmt_price(l['total_cost'])}")
    if l.get("score_details"):
        lines.append("+ " + ", ".join(d for d in l["score_details"] if not d.startswith("Rénovation standard")))
    if l.get("flags"):
        lines.append("• " + " • ".join(l["flags"][:3]))
    if l.get("status") == "a_verifier" and l.get("reasons"):
        lines.append("À vérifier : " + "; ".join(l["reasons"][:3]))
    lines.append(l.get("url", ""))
    if len(l.get("sources", [])) > 1:
        lines.append("Aussi sur : " + ", ".join(sorted({s["source"] for s in l["sources"]})))
    return "\n".join(lines)


def send_whatsapp(recipients, text):
    """Renvoie la liste des destinataires en échec."""
    failed = []
    for r in recipients:
        if not r.get("phone") or not r.get("apikey"):
            continue
        ok = False
        for attempt in range(2):
            try:
                resp = requests.get(CALLMEBOT, params={"phone": r["phone"], "text": text[:1500], "apikey": r["apikey"]}, timeout=30)
                body = resp.text.lower()
                ok = resp.status_code == 200 and ("queued" in body or "sent" in body) and "error" not in body
            except requests.RequestException:
                ok = False
            if ok:
                break
            time.sleep(8)
        if not ok:
            failed.append(r.get("name") or r["phone"])
        time.sleep(4)  # CallMeBot demande d'espacer les messages
    return failed


def send_email(to, subject, body):
    addr, pwd = gmail_credentials()
    if not (addr and pwd and to):
        return False
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = addr
    msg["To"] = ", ".join(to)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(addr, pwd)
            s.sendmail(addr, to, msg.as_string())
        return True
    except Exception:
        return False


def dispatch(cfg, alerts, system_messages, dashboard_url=None):
    """alerts = [(listing, event)], system_messages = [str]. Renvoie un compte-rendu."""
    n = cfg["notifications"]
    recipients = n.get("whatsapp", [])
    emails = n.get("email_to", [])
    report = {"whatsapp_sent": 0, "whatsapp_failed": [], "email_sent": False}
    texts = [format_listing(l, ev, dashboard_url) for l, ev in alerts]
    max_msgs = n.get("max_messages_per_run", 12)
    wa_texts = texts[:max_msgs]
    if len(texts) > max_msgs:
        wa_texts.append(f"… et {len(texts) - max_msgs} autre(s) annonce(s) — voir le tableau de bord" + (f" : {dashboard_url}" if dashboard_url else ""))
    wa_texts += [f"⚙️ Radar appart : {m}" for m in system_messages]

    pending_email = []
    if recipients:
        for t in wa_texts:
            failed = send_whatsapp(recipients, t)
            if failed:
                report["whatsapp_failed"] = sorted(set(report["whatsapp_failed"]) | set(failed))
                pending_email.append(t)
            else:
                report["whatsapp_sent"] += 1
    else:
        pending_email = texts + [f"⚙️ {m}" for m in system_messages]

    if pending_email and emails:
        subject = f"Radar appart : {len(alerts)} alerte(s)" if alerts else "Radar appart : message système"
        if report["whatsapp_failed"]:
            subject += " (WhatsApp en échec)"
        body = "\n\n——————————\n\n".join(pending_email)
        if dashboard_url:
            body += f"\n\nTableau de bord : {dashboard_url}"
        report["email_sent"] = send_email(emails, subject, body)
    return report
