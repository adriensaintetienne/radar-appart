"""Tests de la logique sur des exemples fictifs (aucune donnée réelle)."""
import copy
import json

import pytest

from radar import analyze, textparse
from radar.config import DEFAULT_CONFIG
from radar.sources import gmail_alerts
from radar.store import Store


@pytest.fixture
def cfg():
    return copy.deepcopy(DEFAULT_CONFIG)


# ---------- extraction de texte
@pytest.mark.parametrize("text,price", [
    ("Appartement 4 pièces 98 m² - 1 150 000 €", 1150000),
    ("Prix : 1.095.000€ FAI", 1095000),
    ("Loft à 1,2 M€", 1200000),
    ("995 000 euros, charges 300 €/mois, soit 10 150 €/m²", 995000),
])
def test_price(text, price):
    assert textparse.parse_price(text) == price


def test_surface_ignores_terrace():
    assert textparse.parse_surface("Appartement de 96,5 m² avec terrasse de 120 m²") == 96.5


def test_rooms_bedrooms_floor_elevator():
    t = "Bel appartement T5 au 4ème étage avec ascenseur, trois chambres, cave."
    d = textparse.extract_all(t)
    assert d["rooms"] == 5 and d["bedrooms"] == 3 and d["floor"] == 4 and d["elevator"] is True and d["cellar"] is True
    assert textparse.parse_elevator("3e étage sans ascenseur") is False
    assert textparse.parse_floor("situé en rez-de-chaussée sur cour") == 0


def test_postcode_from_arrondissement():
    assert textparse.parse_postcode("Paris 11ème - Oberkampf") == "75011"
    assert textparse.parse_postcode("Paris 20e") == "75020"


# ---------- analyse
BASE = dict(title="Appartement 5 pièces", postcode="75011", price=1000000, surface=100, rooms=5, bedrooms=3,
            floor=3, elevator=True, description="Haussmannien lumineux, parquet point de Hongrie, cave. A rénover entièrement.")


def test_perfect_match(cfg):
    l = analyze.analyze(dict(BASE), cfg)
    # 1 000 000 + 7,5 % + 100 m² x 1 500 = 1 225 000
    assert l["total_cost"] == 1225000
    assert l["status"] == "match", l["reasons"]
    assert {"Lumineux", "Beau parquet", "Cave", "À rénover", "Haussmannien / ancien"} <= set(l["score_details"])


def test_budget_with_works_rejects(cfg):
    l = analyze.analyze(dict(BASE, price=1250000), cfg)  # 1 343 750 + 150 000 > 1,47 M
    assert l["status"] == "rejete"
    assert any("coût total" in r for r in l["reasons"])


def test_budget_negotiable(cfg):
    l = analyze.analyze(dict(BASE, price=1170000), cfg)  # 1 257 750 + 150 000 = 1 407 750
    assert l["status"] == "negociable"


def test_refait_allows_higher_price(cfg):
    l = analyze.analyze(dict(BASE, price=1280000, description="Entièrement rénové, lumineux, parquet"), cfg)
    assert l["state"] == "none" and l["status"] == "match"


def test_no_elevator_rejected(cfg):
    l = analyze.analyze(dict(BASE, elevator=False), cfg)
    assert l["status"] == "rejete"


def test_ground_floor_ok_without_elevator(cfg):
    l = analyze.analyze(dict(BASE, floor=0, elevator=None), cfg)
    assert l["status"] == "match", l["reasons"]


def test_two_bedrooms_possible(cfg):
    l = analyze.analyze(dict(BASE, bedrooms=2, rooms=4, surface=92), cfg)
    assert l["status"] == "match" and any("chambre possible" in f for f in l["flags"])
    l = analyze.analyze(dict(BASE, bedrooms=2, rooms=3, surface=86, description="Lumineux"), cfg)
    assert l["status"] == "rejete"


def test_exclusions(cfg):
    for kw in ("Vente en viager occupé", "Vendu loué, locataire en place", "Programme neuf VEFA", "Parts de SCI"):
        l = analyze.analyze(dict(BASE, description=kw), cfg)
        assert l["status"] == "rejete", kw


def test_recent_building(cfg):
    assert analyze.analyze(dict(BASE, description="Immeuble construit en 1972"), cfg)["status"] == "rejete"
    assert analyze.analyze(dict(BASE, description="Résidence récente avec gardien"), cfg)["status"] == "a_verifier"


def test_out_of_sector(cfg):
    assert analyze.analyze(dict(BASE, postcode="75016"), cfg)["status"] == "rejete"


def test_zone_polygon(cfg):
    # carré autour de Oberkampf / Ménilmontant
    cfg["zone"] = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon",
                   "coordinates": [[[2.37, 48.86], [2.39, 48.86], [2.39, 48.87], [2.37, 48.87], [2.37, 48.86]]]}}]}
    inside = analyze.analyze(dict(BASE, lat=48.865, lon=2.38, geo_precision="exact"), cfg)
    assert inside["status"] == "match"
    outside = analyze.analyze(dict(BASE, lat=48.85, lon=2.40, geo_precision="exact"), cfg)
    assert outside["status"] == "rejete"
    # position floutée (±250 m) à cheval sur la limite : à vérifier
    edge = analyze.analyze(dict(BASE, lat=48.8702, lon=2.38, geo_precision="approx", geo_radius=250), cfg)
    assert edge["status"] == "a_verifier" and "%" in " ".join(edge["reasons"])
    # position floutée dont l'essentiel tombe hors zone : écartée d'office
    far = analyze.analyze(dict(BASE, lat=48.8725, lon=2.38, geo_precision="approx", geo_radius=250), cfg)
    assert far["status"] == "rejete", far["reasons"]
    # flou faible (±50 m) entièrement dans la zone : correspond
    near = analyze.analyze(dict(BASE, lat=48.865, lon=2.38, geo_precision="approx", geo_radius=50), cfg)
    assert near["status"] == "match"
    nopos = analyze.analyze(dict(BASE, description=BASE["description"]), cfg)
    assert nopos["status"] == "a_verifier"


def test_missing_info_goes_to_check(cfg):
    l = analyze.analyze(dict(title="Appartement Paris 11e", description="Bel appartement", price=1000000, postcode="75011"), cfg)
    assert l["status"] == "a_verifier"


# ---------- dédoublonnage et historique
def test_dedupe_and_price_drop():
    st = Store({"listings": {}})
    a = dict(BASE, source="seloger", url="https://www.seloger.com/annonces/achat/appartement/paris-11eme-75/123456789.htm", ext_id="123456789")
    b = dict(BASE, source="bienici", url="https://www.bienici.com/annonce/ag-1234567", ext_id="ag-1234567", price=1005000, surface=99.5)
    lid1, ev1 = st.upsert(a)
    lid2, ev2 = st.upsert(b)
    assert lid1 == lid2 and ev1 == ["new"] and ev2 == []
    assert len(st.listings[lid1]["sources"]) == 2
    lid3, ev3 = st.upsert(dict(a, price=950000))
    assert lid3 == lid1 and "price_drop" in ev3


def test_removed_then_relisted():
    st = Store({"listings": {}})
    item = dict(BASE, source="bienici", url="https://www.bienici.com/annonce/x-123456", ext_id="x-123456")
    lid, _ = st.upsert(item)
    for _ in range(3):
        st.mark_missing("bienici", set())
    assert st.listings[lid]["active"] is False
    _, ev = st.upsert(item)
    assert "relisted" in ev


# ---------- emails d'alerte
EMAIL_HTML = """
<html><body><table>
<tr><td><a href="https://click.example-mailer.com/c?u=https%3A%2F%2Fwww.seloger.com%2Fannonces%2Fachat%2Fappartement%2Fparis-11eme-75%2Ffolie-mericourt%2F223344556.htm%3Fcmp%3Dalert">
  <img src="https://img.example.com/photo1.jpg"></a></td>
  <td><a href="https://click.example-mailer.com/c?u=https%3A%2F%2Fwww.seloger.com%2Fannonces%2Fachat%2Fappartement%2Fparis-11eme-75%2Ffolie-mericourt%2F223344556.htm">Appartement 5 pièces</a>
  <p>1 090 000 € · 102 m² · 3 chambres · Paris 11ème</p></td></tr>
<tr><td><a href="https://www.leboncoin.fr/ad/ventes_immobilieres/2876543210">Loft 4 pièces 110 m² 1 180 000 € Paris 75020</a></td></tr>
<tr><td><a href="https://www.seloger.com/mon-compte">Gérer mes alertes</a></td></tr>
</table></body></html>
"""


def test_email_parsing():
    items = gmail_alerts.parse_email_html(EMAIL_HTML)
    by = {i["source"]: i for i in items}
    assert set(by) == {"seloger", "leboncoin"}
    s = by["seloger"]
    assert s["ext_id"] == "223344556" and s["price"] == 1090000 and s["surface"] == 102 and s["bedrooms"] == 3
    assert s["postcode"] == "75011" and "?" not in s["url"]
    assert by["leboncoin"]["postcode"] == "75020" and by["leboncoin"]["surface"] == 110


def test_end_to_end(tmp_path, monkeypatch):
    """Déroulé complet avec des sources simulées : premier passage silencieux, puis alerte sur une nouveauté."""
    from radar import main, notify
    (tmp_path / "config.json").write_text(json.dumps({"sources": {"gmail": {"enabled": False}, "websearch": {"enabled": False}, "agencies": []},
                                                      "notifications": {"whatsapp": [{"name": "Test", "phone": "+33600000000", "apikey": "x"}]}}))
    feed = [[dict(BASE, source="bienici", url="https://www.bienici.com/annonce/a-111111", ext_id="a-111111")]]
    monkeypatch.setattr(main.bienici, "fetch", lambda cfg, st: (feed[0], {"full_listing": True, "info": "test"}))
    sent = []
    monkeypatch.setattr(notify, "send_whatsapp", lambda rec, text: sent.append(text) or [])
    main.run(str(tmp_path))
    assert len(sent) == 1 and "en route" in sent[0]
    feed[0] = feed[0] + [dict(BASE, source="bienici", url="https://www.bienici.com/annonce/b-222222", ext_id="b-222222",
                              surface=110, price=1050000, postcode="75020", lat=None)]
    main.run(str(tmp_path))
    assert len(sent) == 2 and "Nouvelle annonce" in sent[1] and "110 m²" in sent[1]
    feed[0][1] = dict(feed[0][1], price=990000)
    main.run(str(tmp_path))
    assert len(sent) == 3 and "Baisse de prix" in sent[2]
    data = json.loads((tmp_path / "listings.json").read_text())
    assert len(data["listings"]) == 2


def test_different_agency_price_is_not_a_drop():
    st = Store({"listings": {}})
    a = dict(BASE, source="bienici", url="https://www.bienici.com/annonce/ag-1", ext_id="ag-1", price=1081500, surface=89, rooms=4)
    b = dict(BASE, source="bienici", url="https://www.bienici.com/annonce/ap-2", ext_id="ap-2", price=1050000, surface=89.5, rooms=4)
    lid, _ = st.upsert(a)
    lid2, ev = st.upsert(b)
    assert lid == lid2 and "price_drop" not in ev
    assert st.listings[lid]["price"] == 1050000  # prix le plus bas retenu
    _, ev = st.upsert(dict(a, price=1000000))   # la première agence baisse vraiment
    assert "price_drop" in ev and st.listings[lid]["price"] == 1000000


def test_undelivered_messages_are_retried(tmp_path, monkeypatch):
    """Si ni WhatsApp ni l'email ne passent, le message de bienvenue et les alertes sont renvoyés ensuite."""
    from radar import main, notify
    (tmp_path / "config.json").write_text(json.dumps({"sources": {"gmail": {"enabled": False}, "websearch": {"enabled": False}, "agencies": []},
                                                      "notifications": {"whatsapp": [{"name": "T", "phone": "+33600000000", "apikey": "x"}]}}))
    feed = [[dict(BASE, source="bienici", url="https://www.bienici.com/annonce/a-111111", ext_id="a-111111")]]
    monkeypatch.setattr(main.bienici, "fetch", lambda cfg, st: (feed[0], {"full_listing": True, "info": "test"}))
    up = {"ok": False}
    sent = []
    monkeypatch.setattr(notify, "send_whatsapp", lambda rec, text: (sent.append(text) or []) if up["ok"] else ["T"])
    main.run(str(tmp_path))                       # 1er passage : envoi en échec
    feed[0] = feed[0] + [dict(BASE, source="bienici", url="https://www.bienici.com/annonce/b-222222", ext_id="b-222222",
                              surface=110, price=1050000, postcode="75020")]
    main.run(str(tmp_path))                       # nouvelle annonce, toujours en échec
    assert sent == []
    up["ok"] = True
    main.run(str(tmp_path))                       # ça repasse : tout est renvoyé
    assert any("en route" in t for t in sent) and any("110 m²" in t for t in sent)
    n = len(sent)
    main.run(str(tmp_path))                       # et pas de doublon ensuite
    assert len(sent) == n


def test_email_unknown_encoding():
    from radar.sources.gmail_alerts import _bytes_to_str, _decode
    assert _bytes_to_str("Évry".encode("latin-1"), "unknown-8bit") == "Évry"
    assert _bytes_to_str("Évry".encode("utf-8"), "unknown-8bit") == "Évry"
    assert "Alerte" in _decode("=?unknown-8bit?q?Alerte_=E9t=E9?=")


SELOGER_HTML = """<table>
<tr><td><table class="card">
 <tr><td><a href="https://click.by.seloger.com/?qs=A1">Localisation différente</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=A2">709 000 € 6 817,31 €/m²</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=A3">5 PIECES AU 7EME ET DERNIER ETAGE</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=A4">5 pièces . 104 m²</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=A5">Les Lilas (93260)</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=A6">Voir l'annonce</a></td></tr>
</table></td></tr>
<tr><td><table class="card">
 <tr><td><a href="https://click.by.seloger.com/?qs=B2">1 090 000 € 10 480 €/m²</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=B3">Appartement familial lumineux</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=B4">5 pièces . 104 m²</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=B5">Paris 11ème (75011)</a></td></tr>
 <tr><td><a href="https://click.by.seloger.com/?qs=B6">Voir l'annonce</a></td></tr>
</table></td></tr>
<tr><td><a href="https://click.by.seloger.com/?qs=Z1">Se désabonner des offres alternatives</a></td></tr>
</table>"""


def test_seloger_tracking_links(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, loc): self.headers = {"location": loc}
        def close(self): pass

    def fake_get(url, **kw):
        calls.append(url)
        card = url.split("qs=")[1][0]
        ids = {"A": "les-lilas-93260/26A4PSJDIKBI", "B": "paris-11eme-75011/27B5QTKEJLCJ"}
        return Resp(f"https://www.seloger.com/annonce/achat/ile-de-france/{ids.get(card, 'x/NOTANAD')}?utm_source=crm")

    gmail_alerts._redirect_cache.clear()
    monkeypatch.setattr(gmail_alerts.http.session(), "get", fake_get)
    items = gmail_alerts.parse_email_html(SELOGER_HTML)
    by = {i["ext_id"]: i for i in items}
    assert set(by) == {"26A4PSJDIKBI", "27B5QTKEJLCJ"}
    assert by["27B5QTKEJLCJ"]["price"] == 1090000 and by["27B5QTKEJLCJ"]["surface"] == 104
    assert by["27B5QTKEJLCJ"]["postcode"] == "75011" and by["26A4PSJDIKBI"]["postcode"] == "93260"
    assert not any("Z1" in c for c in calls)  # lien de désabonnement jamais suivi
    assert len(calls) <= 4                    # une seule résolution (ou presque) par annonce


def test_bienici_tracking_links_with_embedded_url():
    import base64
    def link(target):
        return "https://link.bienici.com/lnk/AWEAAKaD/" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    html = f"""<table>
    <tr><td><a href="{link('https://www.bienici.com/mon-alerte/11f2ba?x=1')}">Acheter à Paris 11e - 800k à 1,3m € - 90 m² min</a></td></tr>
    <tr><td><table><tr><td><a href="{link('https://www.bienici.com/annonce/netty-robert-appt-6201?fromSavedSearchId=1')}">Appartement 4 pièces 90 m² 75020 Paris 20e 895 000 €</a></td></tr>
      <tr><td><a href="{link('https://www.bienici.com/annonce/netty-robert-appt-6201?at_link=Voir')}">Voir l’annonce ></a></td></tr></table></td></tr>
    <tr><td><a href="https://advertisement.bienici.com/redirect/mail-buy-w728h90-1">pub</a></td></tr>
    </table>"""
    items = gmail_alerts.parse_email_html(html)
    assert [(i["ext_id"], i["price"], i["postcode"]) for i in items] == [("netty-robert-appt-6201", 895000, "75020")]


def test_place_from_text(cfg):
    from radar import geo
    cfg["zone"] = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon",
                   "coordinates": [[[2.36, 48.86], [2.385, 48.86], [2.385, 48.875], [2.36, 48.875], [2.36, 48.86]]]}}]}
    base = dict(BASE, lat=None, lon=None, geo_precision=None)
    inside = analyze.analyze(dict(base, title="Paris 11e - FOLIE-MÉRICOURT - Appartement", postcode="75011"), cfg)
    assert inside["geo_place"] == "Folie-Méricourt" and inside["geo_status"] == "approx"
    svp = analyze.analyze(dict(base, title="Paris 10e - SAINT-VINCENT DE PAUL - Appartement", postcode="75010"), cfg)
    assert svp["geo_place"] == "Saint-Vincent-de-Paul" and svp["status"] == "rejete"
    # nom d'agence et lieu d'un autre arrondissement ignorés
    assert geo.find_place("AGENCE DES ENFANTS ROUGES vous propose", {11}) is None
    assert geo.find_place("à 10 minutes de la gare de Lyon", {11}) is None
    assert geo.find_place("un vrai paradis de lumière", {10}) is None
    assert geo.find_place("rue de Paradis, beau 4 pièces", {10})[1] == "Rue de Paradis"


def test_no_elevator_mention_means_none(cfg):
    long_desc = "Bel appartement familial, parquet, moulures, lumineux, au calme sur cour. " * 4
    direct = [{"source": "bienici", "url": "u", "key": "k"}]
    l = analyze.analyze(dict(BASE, elevator=None, floor=3, description=long_desc, sources=direct), cfg)
    assert l["status"] == "rejete" and any("aucun ascenseur mentionné" in r for r in l["reasons"])
    l = analyze.analyze(dict(BASE, elevator=None, floor=0, description=long_desc, sources=direct), cfg)
    assert not any("ascenseur" in r for r in l["reasons"])          # rez-de-chaussée : pas besoin
    l = analyze.analyze(dict(BASE, elevator=None, floor=3, description=long_desc + " Ascenseur.", sources=direct), cfg)
    assert not any("ascenseur" in r for r in l["reasons"])          # mentionné -> présent
    email = [{"source": "seloger", "url": "u", "key": "k"}]
    l = analyze.analyze(dict(BASE, elevator=None, floor=3, description="4 pièces 100 m² Paris 11e", sources=email), cfg)
    assert l["status"] == "a_verifier"                               # résumé d'email : on ne conclut pas


def test_manual_listing(tmp_path, monkeypatch):
    """Annonce ajoutée depuis le tableau de bord : texte collé + champs saisis ; site bloqué sans info -> message."""
    from radar import main, enrich as enrich_mod
    from radar.sources import manual
    (tmp_path / "config.json").write_text(json.dumps({"sources": {"gmail": {"enabled": False}, "bienici": {"enabled": False},
                                                                  "websearch": {"enabled": False}, "agencies": []}}))
    (tmp_path / "state.json").write_text(json.dumps({"runs": [{}], "welcome_sent": True}))
    (tmp_path / "tracking.json").write_text(json.dumps({"items": {}, "manual_queue": [
        {"id": "m1", "url": "https://www.seloger.com/annonce/achat/x/26ABCDEFGH",
         "text": "Paris 11e - FOLIE-MÉRICOURT - Appartement 5 pièces 104 m², 3 chambres, 4e étage avec ascenseur, "
                 "lumineux, parquet, cave. À rénover. " * 2, "fields": {"price": 1000000}},
        {"id": "m2", "url": "https://www.leboncoin.fr/ad/ventes_immobilieres/123456789", "text": "", "fields": {}},
    ]}))
    def blocked(item):
        item["enrich_error"] = "www.seloger.com a bloqué la requête (HTTP 403)"; return False
    monkeypatch.setattr(manual, "enrich", blocked)
    main.run(str(tmp_path), dry_run=True)
    state = json.loads((tmp_path / "state.json").read_text())
    listings = json.loads((tmp_path / "listings.json").read_text())["listings"]
    m1, m2 = state["manual_done"]["m1"], state["manual_done"]["m2"]
    assert m1["ok"] and "page non lisible" in m1["message"]
    l = listings[m1["listing_id"]]
    assert l["manual"] and l["price"] == 1000000 and l["surface"] == 104 and l["floor"] == 4 and l["elevator"] is True
    assert l["status"] in ("match", "a_verifier", "negociable", "rejete") and l["score"] > 0
    assert not m2["ok"] and "collez le texte" in m2["message"]
    main.run(str(tmp_path), dry_run=True)  # déjà traitées : rien ne bouge
    assert len(json.loads((tmp_path / "listings.json").read_text())["listings"]) == 1


def test_market_comparison(cfg, monkeypatch):
    from radar import dvf
    fake = [("2025-0%d-15" % (1 + i % 9), int(10000 * s), s, "75011", 48.865 + i * 1e-5, 2.38) for i, s in enumerate([80, 90, 100, 110, 120] * 4)]
    monkeypatch.setattr(dvf, "_SALES", fake); monkeypatch.setattr(dvf, "_CANDIDATES", {})
    l = analyze.analyze(dict(BASE, price=1040000, surface=100, lat=48.865, lon=2.38, geo_precision="exact"), cfg)
    mk = l["market"]
    assert mk["median_m2"] == 10000 and mk["n"] == 20 and mk["listing_m2_net"] == 10000 and mk["gap_pct"] == 0
    cheap = analyze.analyze(dict(BASE, price=900000, surface=100, lat=48.865, lon=2.38, geo_precision="exact"), cfg)
    assert any("sous les ventes" in f for f in cheap["flags"]) and "Prix sous les ventes du quartier" in cheap["score_details"]
