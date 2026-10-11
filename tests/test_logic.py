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
    # juste à côté de la zone : écarté par défaut (exclusion stricte)
    edge = analyze.analyze(dict(BASE, lat=48.8702, lon=2.38, geo_precision="approx"), cfg)
    assert edge["status"] == "rejete"
    # avec une tolérance réglée : à vérifier
    cfg["zone_tolerance_m"] = 300
    edge = analyze.analyze(dict(BASE, lat=48.8702, lon=2.38, geo_precision="approx"), cfg)
    assert edge["status"] == "a_verifier"
    # sans aucune position : jamais « correspond »
    cfg["zone_tolerance_m"] = 0
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
