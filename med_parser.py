#!/usr/bin/env python3
"""
Zdieľaný deterministický parser liekových eventov (bez LLM).

Rozklad surového `value` liekového eventu ("3× Orfiril, 1/2 Tisercin") na
jednotlivé lieky = návrhy riadkov event_meds. Používajú ho:
  - migrate_event_meds.py / parse_med_events.py / add_aliases.py (offline, source="migracia")
  - database.py pri živom zápise (source="confirm" / "edit")

DÔLEŽITÉ: tento modul NESMIE importovať database.py — database.py importuje jeho,
opačný import by vyrobil kruh. Modul preto pracuje len nad odovzdaným spojením
(load_catalog(conn)) a čistými reťazcami; sám žiadne spojenie neotvára.

Logika je zámerne deterministická, bez hádania: čo sa nedá určiť, ostáva NULL.
"""
import json
import re
import unicodedata
from fractions import Fraction

MED_TYPE = "liek"

# Cyrilické homoglyfy z diktovania → latinka. Používa sa LEN pri párovaní
# s katalógom; raw_name zostáva v pôvodnom tvare (audit trail).
HOMOGLYPHS = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "л": "l", "м": "m", "н": "h", "в": "v", "и": "i",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X",
    "М": "M", "В": "V", "Н": "H", "К": "K", "Т": "T",
}

# Negatívne / neisté markery. Pri zhode NEURČUJEME status automaticky —
# radšej priznaná neistota než nesprávny záver.
NEGATIVE_MARKERS = [
    "nedostal", "nedostala", "vynechan", "nepodan", "nebol podan", "nebola podan",
    "zabudl", "odmietol", "odmietla", "neskor", "neskôr", "oneskoren", "bez ",
]

# Slová, ktoré samy o sebe neoznačujú konkrétny liek
GENERIC = {"lieky", "liek", "lieku", "podanie", "vitaminy", "vitamin", "davka",
           "davky", "zoznamu", "podla", "rannе", "ranne", "podane", "kasi"}

SPLIT_RE = re.compile(r"\s*[;,\n]\s*|\s+a\s+|\s+\+\s+", re.IGNORECASE)
WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

VULGAR = {"½": Fraction(1, 2), "¼": Fraction(1, 4), "¾": Fraction(3, 4),
          "⅓": Fraction(1, 3), "⅔": Fraction(2, 3)}


def strip_diacritics(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm(s):
    """Na párovanie: homoglyfy → latinka, bez diakritiky, lowercase."""
    if not s:
        return ""
    s = "".join(HOMOGLYPHS.get(ch, ch) for ch in s)
    return re.sub(r"\s+", " ", strip_diacritics(s).lower()).strip()


def has_homoglyph(s):
    return any(ch in HOMOGLYPHS for ch in (s or ""))


# ── rozklad na jednotlivé lieky ────────────────────────────────────────────

QTY_STRIP_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:mg|g|ml)\b|\d+\s*/\s*\d+|\d+(?:[.,]\d+)?\s*[x×]|[½¼¾⅓⅔]|\d+",
    re.IGNORECASE)


def is_dose_only(seg):
    """True, ak segment po odstránení množstva neobsahuje žiadne písmeno.

    '1/2', '3x', '600 mg' → dávka (patrí k predošlému lieku)
    '1× B6', 'C', 'Mg', '1x D K3' → názov lieku, hoci je kratší než 3 znaky
    """
    rest = QTY_STRIP_RE.sub(" ", seg or "")
    return not re.search(r"[^\W\d_]", rest, re.UNICODE)


def split_meds(text):
    """Rozdelí text na segmenty = jednotlivé lieky.

    Vráti (segmenty, merged) — merged je počet prípadov, keď oddeľovač
    (typicky čiarka) oddeľoval DÁVKU od názvu, nie dva lieky, a segment
    sa musel prilepiť k predchádzajúcemu. Toto číslo = miera neistoty.
    """
    raw = [p.strip() for p in SPLIT_RE.split(text or "") if p.strip()]
    segs, merged = [], 0
    for part in raw:
        if is_dose_only(part):
            if segs:
                segs[-1] = segs[-1] + ", " + part
                merged += 1
            else:
                segs.append(part)
        else:
            segs.append(part)
    return segs, merged


# ── množstvo a jednotka ────────────────────────────────────────────────────

def parse_qty(seg):
    """(qty, unit, ako) — NULL, ak sa nedá určiť. Nehádame."""
    s = seg or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(mg|g|ml)\b", s, re.IGNORECASE)
    if m:
        unit = m.group(2).lower()
        return float(m.group(1).replace(",", ".")), unit, "hmotnost/objem"
    m = re.search(r"(\d+)\s*/\s*(\d+)", s)
    if m and int(m.group(2)) != 0:
        return float(Fraction(int(m.group(1)), int(m.group(2)))), unit_of(s), "zlomok"
    for ch, fr in VULGAR.items():
        if ch in s:
            return float(fr), unit_of(s), "zlomok (unicode)"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[x×]", s, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ".")), unit_of(s), "nasobok"
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s+(?=[^\W\d_])", s, re.UNICODE)
    if m:
        return float(m.group(1).replace(",", ".")), unit_of(s), "holé číslo"
    return None, unit_of(s, strict=True), "neurcene"


def unit_of(s, strict=False):
    """Jednotka z kontextu. strict=True → nehádaj 'tableta', vráť NULL."""
    t = norm(s)
    if re.search(r"\b(ml|mililitr\w*)\b", t):
        return "ml"
    if re.search(r"kvapk|kvapiek", t):
        return "kvapka"
    if re.search(r"tablet|tabl\b|kaps", t):
        return "tableta"
    return None if strict else "tableta"


# ── status ─────────────────────────────────────────────────────────────────

def detect_status(value, note):
    """('podane'|'neznamy', status_note). Negatívny marker → 'neznamy'."""
    joined = " ".join(x for x in (value, note) if x)
    t = norm(joined)
    hits = [m for m in NEGATIVE_MARKERS if m in t]
    if hits:
        return "neznamy", joined
    return "podane", None


# ── katalóg ────────────────────────────────────────────────────────────────

def load_catalog(conn):
    items = []
    for r in conn.execute("SELECT id, canonical_name, aliases FROM med_catalog"):
        names = [r["canonical_name"] or ""]
        try:
            names += json.loads(r["aliases"] or "[]")
        except (ValueError, TypeError):
            pass
        # 2-znakové varianty (B6) sú povolené, ale párujú sa len ako celé slovo
        variants = sorted({norm(n) for n in names if n and len(norm(n)) >= 2},
                          key=len, reverse=True)
        items.append({"id": r["id"], "name": r["canonical_name"], "variants": variants})
    return items


def variant_matches(v, t):
    """Dlhý variant = podreťazec; krátky (<3 znaky) = LEN celé slovo.

    Krátky variant ako podreťazec by chytal náhodné výskyty ('mg' v '600 mg').
    """
    if not v or not t:
        return False
    if len(v) >= 3:
        return v in t
    return re.search(r"(?<![0-9a-z])" + re.escape(v) + r"(?![0-9a-z])", t) is not None


def match_catalog(seg, catalog):
    """catalog_id alebo None. Deterministicky, najdlhší variant vyhráva."""
    t = norm(seg)
    best = None
    for it in catalog:
        for v in it["variants"]:
            if variant_matches(v, t):
                if best is None or len(v) > best[1]:
                    best = (it["id"], len(v))
                break
    return best[0] if best else None


# ── rozklad jedného eventu ─────────────────────────────────────────────────

def parse_event(value, note, catalog, source):
    """Vráti (riadky, flagy). Riadok = návrh riadku event_meds.

    `source` je povinný (žiadny default): 'migracia' pre offline migráciu,
    'confirm'/'edit' pre živý zápis z appky. Zapisuje sa do každého riadku,
    aby bolo natrvalo jasné, čím riadok vznikol.
    """
    segs, merged = split_meds(value)
    status, status_note = detect_status(value, note)
    flags = []
    if merged:
        flags.append(f"čiarka oddeľovala dávku, nie liek ({merged}×) — spojené")
    if status == "neznamy":
        flags.append("negatívny/neistý marker → status 'neznamy'")
    if has_homoglyph(value) or has_homoglyph(note):
        flags.append("cyrilický homoglyf v texte (párovanie normalizované)")
    if not segs:
        return [], flags + ["prázdny text — nedá sa rozložiť"]

    rows = []
    for seg in segs:
        qty, unit, how = parse_qty(seg)
        cid = match_catalog(seg, catalog)
        # "len všeobecné slová" sa posudzuje po odstránení množstva; krátke názvy
        # (B6, C, Mg) sa za všeobecné slová NEpovažujú
        residual = QTY_STRIP_RE.sub(" ", seg or "")
        words = WORD_RE.findall(norm(residual))
        if words:
            meaningful = [w for w in words if w not in GENERIC]
        else:
            meaningful = re.findall(r"[^\W\d_]", residual, re.UNICODE)
        rows.append({
            "raw_name": seg,          # verbatim, vrátane homoglyfov — audit trail
            "qty": qty,
            "unit": unit,
            "catalog_id": cid,
            "status": status,
            "status_note": status_note,
            "source": source,
            "_how": how,
            "_generic_only": not meaningful,
        })
    if all(r["_generic_only"] for r in rows):
        flags.append("žiadny konkrétny liek v texte (len všeobecné slová)")
    if any(r["qty"] is None for r in rows):
        flags.append("množstvo sa nedalo určiť (NULL, nehádame)")
    return rows, flags
