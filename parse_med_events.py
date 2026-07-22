#!/usr/bin/env python3
"""
Parser liekových eventov → návrh riadkov event_meds. IBA REPORT.

Read-only: DB sa otvára cez URI file:...?mode=ro. Skript NEMÁ --apply,
v tomto bloku (3A) sa zámerne nič nezapisuje — zápis pribudne v 3B, až
keď bude kvalita rozkladu odsúhlasená očami.

Všetko je deterministické, bez LLM.

Spusti:  python3 parse_med_events.py --db /var/www/daylog/daylog.db
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
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

def parse_event(value, note, catalog):
    """Vráti (riadky, flagy). Riadok = návrh riadku event_meds."""
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
            "source": "migracia",
            "_how": how,
            "_generic_only": not meaningful,
        })
    if all(r["_generic_only"] for r in rows):
        flags.append("žiadny konkrétny liek v texte (len všeobecné slová)")
    if any(r["qty"] is None for r in rows):
        flags.append("množstvo sa nedalo určiť (NULL, nehádame)")
    return rows, flags


# ── report ─────────────────────────────────────────────────────────────────

def out(s=""):
    print(s)


def h1(t):
    out(); out("=" * 100); out(t); out("=" * 100)


def h2(t):
    out(); out("── " + t + " " + "─" * max(0, 96 - len(t)))


def open_ro(path):
    if not os.path.exists(path):
        sys.exit(f"CHYBA: databáza neexistuje: {path}")
    conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main():
    ap = argparse.ArgumentParser(description="Rozklad liekových eventov → návrh event_meds (IBA REPORT)")
    ap.add_argument("--db", default="daylog.db")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    conn = open_ro(args.db)
    catalog = load_catalog(conn)
    cat_name = {c["id"]: c["name"] for c in catalog}
    evs = [dict(r) for r in conn.execute(
        "SELECT ev.id, e.entry_date, ev.event_time, ev.value, ev.note, ev.catalog_id "
        "FROM events ev JOIN entries e ON ev.entry_id = e.id "
        "WHERE ev.event_type = ? ORDER BY e.entry_date, ev.event_time, ev.id",
        (MED_TYPE,))]
    conn.close()

    out(f"DB: {args.db}  (READ-ONLY, mode=ro)   Skript NEMÁ --apply, nič nezapisuje.")
    out(f"Liekových eventov: {len(evs)}   Položiek katalógu: {len(catalog)}")

    parsed = []
    for e in evs:
        rows, flags = parse_event(e["value"], e["note"], catalog)
        parsed.append((e, rows, flags))

    all_rows = [r for _, rows, _ in parsed for r in rows]

    h1("SÚHRN")
    out(f"1. {len(evs)} eventov → {len(all_rows)} navrhovaných riadkov event_meds "
        f"(priemer {len(all_rows) / max(len(evs), 1):.2f} lieku na event)")
    per = Counter(len(rows) for _, rows, _ in parsed)
    for k in sorted(per):
        out(f"     {k} liek(ov) na event: {per[k]:>3} eventov")

    with_cat = [r for r in all_rows if r["catalog_id"]]
    out(f"\n2. catalog_id vyplnené: {len(with_cat)} / {len(all_rows)} "
        f"({100.0 * len(with_cat) / max(len(all_rows), 1):.1f} %), "
        f"NULL: {len(all_rows) - len(with_cat)}")
    nullnames = Counter(r["raw_name"] for r in all_rows if not r["catalog_id"])
    out("   Nespárované raw_name (kandidáti na alias / novú položku katalógu):")
    for nm, n in nullnames.most_common():
        out(f"     {n:>3}×  {nm!r}")

    with_qty = [r for r in all_rows if r["qty"] is not None]
    out(f"\n3. qty vyplnené: {len(with_qty)} / {len(all_rows)}, "
        f"NULL: {len(all_rows) - len(with_qty)}")
    out("   podľa spôsobu určenia: " +
        ", ".join(f"{k}={v}" for k, v in Counter(r["_how"] for r in all_rows).most_common()))
    out("   jednotky: " +
        ", ".join(f"{k}={v}" for k, v in Counter(str(r["unit"]) for r in all_rows).most_common()))

    st = Counter(r["status"] for r in all_rows)
    out(f"\n4. status: " + ", ".join(f"{k}={v}" for k, v in st.most_common()))
    ev_unknown = [e["id"] for e, rows, _ in parsed if rows and rows[0]["status"] == "neznamy"]
    out(f"   eventy so statusom 'neznamy': {ev_unknown}")

    h1("5. ÚPLNÝ ZOZNAM NAVRHOVANÝCH RIADKOV event_meds")
    out(f"{'ev':>5} | {'dátum':<10} {'čas':<5} | {'pôvodné value':<46} | "
        f"{'raw_name':<34} | {'qty':>6} | {'unit':<8} | {'cat':>4} | {'kat. názov':<22} | status")
    out("-" * 100)
    for e, rows, flags in parsed:
        val = (e["value"] or "")
        for i, r in enumerate(rows):
            shown_val = val if i == 0 else ""
            qty_s = "" if r["qty"] is None else ("%.2f" % r["qty"])
            unit_s = str(r["unit"] or "")
            cid_s = str(r["catalog_id"] or "")
            cname_s = str(cat_name.get(r["catalog_id"], ""))
            time_s = e["event_time"] or "—"
            out(f"{e['id']:>5} | {e['entry_date']:<10} {time_s:<5} | "
                f"{shown_val:<46} | {r['raw_name']:<34} | {qty_s:>6} | {unit_s:<8} | "
                f"{cid_s:>4} | {cname_s:<22} | {r['status']}")
        if not rows:
            time_s = e["event_time"] or "—"
            out(f"{e['id']:>5} | {e['entry_date']:<10} {time_s:<5} | "
                f"{val:<46} | (NIČ — nerozložené)")

    h1("6. EVENTY, KTORÉ SA NEPODARILO ROZLOŽIŤ SPOĽAHLIVO")
    problem = [(e, rows, flags) for e, rows, flags in parsed if flags]
    out(f"{len(problem)} z {len(evs)} eventov má aspoň jeden dôvod na pochybnosť\n")
    for e, rows, flags in problem:
        out(f"  #{e['id']} {e['entry_date']} {e['value']!r}"
            + (f"  note={e['note']!r}" if (e["note"] or "").strip() else ""))
        for f in flags:
            out(f"      → {f}")
    out()
    out("Skript nič nezapísal (mode=ro, --apply neexistuje).")


if __name__ == "__main__":
    main()
