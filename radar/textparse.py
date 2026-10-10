"""Extraction d'informations structurées depuis le texte libre d'une annonce (français)."""
import re
import unicodedata


def normalize(text):
    """Minuscules, sans accents, espaces normalisés (y compris insécables)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", text).lower().strip()


def _to_number(raw):
    raw = raw.replace(" ", "").replace(" ", "").replace(" ", "")
    # "1.150.000" ou "1 150 000" -> séparateurs de milliers ; "98,5" -> décimale
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw):
        raw = re.sub(r"[.,]", "", raw)
    raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


PRICE_RE = re.compile(
    r"(\d{1,3}(?:[ .  ]?\d{3}){1,2}|\d{6,7})\s*(?:€|euros?|eur\b)", re.I
)
PRICE_M_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m€|millions?\s*(?:d'?)?\s*(?:€|euros?))", re.I)
SURFACE_RE = re.compile(r"(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:m²|m2|m\s?carres?|metres?\s?carres?)", re.I)
ROOMS_RE = re.compile(r"(\d{1,2})\s*(?:pieces?|p\.)(?![a-z])|\b[tf]\s?(\d{1,2})\b", re.I)
BEDROOMS_RE = re.compile(r"(\d{1,2}|une|deux|trois|quatre|cinq)\s*(?:chambres?|ch\.)", re.I)
POSTCODE_RE = re.compile(r"\b(75\d{3}|9[1-5]\d{3})\b")
ARRONDISSEMENT_RE = re.compile(r"paris\s*(\d{1,2})\s*(?:e|eme|er|ème|ᵉ)?\b", re.I)
WORD_NUM = {"une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5}


LABELED_PRICE_RE = re.compile(
    r"prix(?: de vente| du bien| net vendeur| honoraires inclus| fai)?\s*:?\s*"
    r"(\d{1,3}(?:[ .\u00a0\u202f]\d{3}){1,2}|\d{6,7})\s*(?:€|euros?)", re.I
)


def parse_price(text):
    n = normalize(text)
    m = LABELED_PRICE_RE.search(n)
    if m:
        v = _to_number(m.group(1))
        if v and 50_000 <= v <= 20_000_000:
            return int(v)
    m = PRICE_M_RE.search(n)
    if m:
        v = _to_number(m.group(1))
        if v and 0.1 < v < 20:
            return int(v * 1_000_000)
    candidates = []
    for m in PRICE_RE.finditer(n):
        # ignorer les prix au m² ("10 500 €/m²") et les charges
        tail = n[m.end():m.end() + 6]
        head = n[max(0, m.start() - 25):m.start()]
        if re.match(r"\s*/\s*m|\s*le\s*m|\s*par\s*m", tail) or re.search(r"charges?|/\s*mois|par mois|honoraires|taxe|emprunt", head + tail):
            continue
        v = _to_number(m.group(1))
        if v and 50_000 <= v <= 20_000_000:
            candidates.append(int(v))
    if not candidates:
        return None
    # La valeur la plus répétée est la plus sûre (un chiffre voisin peut se coller au prix une fois) ;
    # à égalité, la première rencontrée.
    counts = {}
    for c in candidates:
        counts[c] = counts.get(c, 0) + 1
    return max(counts, key=lambda c: (counts[c], -candidates.index(c)))


def parse_surface(text):
    n = normalize(text)
    vals = []
    for m in SURFACE_RE.finditer(n):
        head = n[max(0, m.start() - 30):m.start()]
        if re.search(r"terrasse|balcon|jardin|cave|loggia|terrain|cour", head):
            continue
        v = _to_number(m.group(1))
        if v and 9 <= v <= 1000:
            vals.append(v)
    return max(vals) if vals else None


def parse_rooms(text):
    n = normalize(text)
    for m in ROOMS_RE.finditer(n):
        v = int(m.group(1) or m.group(2))
        if 1 <= v <= 15:
            return v
    return None


def parse_bedrooms(text):
    n = normalize(text)
    vals = []
    for m in BEDROOMS_RE.finditer(n):
        raw = m.group(1)
        v = WORD_NUM.get(raw, None) if not raw.isdigit() else int(raw)
        if v and 1 <= v <= 10:
            vals.append(v)
    return max(vals) if vals else None


def parse_postcode(text):
    n = normalize(text)
    m = POSTCODE_RE.search(n)
    if m:
        return m.group(1)
    m = ARRONDISSEMENT_RE.search(n)
    if m:
        a = int(m.group(1))
        if 1 <= a <= 20:
            return f"750{a:02d}"
    return None


ORDINAL_FLOOR_RE = re.compile(r"(\d{1,2})\s*(?:e|eme|er|ere|ieme)\s*(?:et\s*dernier\s*)?(?:etage|et\.)", re.I)
FLOOR_ALT_RE = re.compile(r"etage\s*:?\s*(\d{1,2})\b", re.I)


def parse_floor(text):
    n = normalize(text)
    if re.search(r"\brez[- ]de[- ]chaussee\b|\brdc\b|\brez[- ]de[- ]jardin\b", n):
        return 0
    m = ORDINAL_FLOOR_RE.search(n) or FLOOR_ALT_RE.search(n)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 40:
            return v
    if re.search(r"\bpremier etage\b", n):
        return 1
    return None


def parse_elevator(text):
    """True / False / None (inconnu)."""
    n = normalize(text)
    if re.search(r"sans ascenseur|pas d'?\s*ascenseur|absence d'?\s*ascenseur|ascenseur\s*:\s*non", n):
        return False
    if re.search(r"ascenseur", n):
        return True
    return None


def is_top_floor(text):
    return bool(re.search(r"dernier etage|et dernier|dernier niveau", normalize(text)))


def has_cellar(text):
    n = normalize(text)
    if re.search(r"sans cave|pas de cave", n):
        return False
    if re.search(r"\bcaves?\b|\bcellier\b", n):
        return True
    return None


def parse_build_year(text):
    n = normalize(text)
    m = re.search(r"(?:construit|construction|batie?|edifie|immeuble)\D{0,25}(?:en|de)\s*((?:18|19|20)\d{2})", n)
    if m:
        return int(m.group(1))
    m = re.search(r"annees?\s*((?:19)?[2-9]0)\b", n)
    if m:
        v = m.group(1)
        return int(v) if len(v) == 4 else 1900 + int(v)
    return None


def find_keywords(text, keywords):
    """Renvoie les mots-clés (eux-mêmes normalisés) présents dans le texte."""
    n = normalize(text)
    found = []
    for kw in keywords:
        k = normalize(kw)
        if re.search(r"(?<![a-z])" + re.escape(k) + r"(?![a-z])", n):
            found.append(kw)
    return found


def extract_all(text):
    return {
        "price": parse_price(text),
        "surface": parse_surface(text),
        "rooms": parse_rooms(text),
        "bedrooms": parse_bedrooms(text),
        "postcode": parse_postcode(text),
        "floor": parse_floor(text),
        "elevator": parse_elevator(text),
        "cellar": has_cellar(text),
        "top_floor": is_top_floor(text) or None,
        "build_year": parse_build_year(text),
    }
