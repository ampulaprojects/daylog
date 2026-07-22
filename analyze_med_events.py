#!/usr/bin/env python3
"""
Analýza liekových eventov — READ-ONLY diagnostika pred Fázou 2.

Nič nezapisuje: DB sa otvára cez URI file:...?mode=ro. Skript je určený
na spustenie aj nad produkčnou databázou.

Spusti:  python3 analyze_med_events.py --db /var/www/daylog/daylog.db
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta

MED_TYPE = "liek"

# Cyrilické (a iné) homoglyfy → latinka. Návrh, NEAPLIKUJE sa na dáta —
# používa sa len na normalizáciu pri párovaní a na report v sekcii E.
HOMOGLYPHS = {
    "а": "a",  # а CYRILLIC SMALL A
    "е": "e",  # е CYRILLIC SMALL IE
    "о": "o",  # о CYRILLIC SMALL O
    "р": "p",  # р CYRILLIC SMALL ER
    "с": "c",  # с CYRILLIC SMALL ES
    "у": "y",  # у CYRILLIC SMALL U
    "х": "x",  # х CYRILLIC SMALL HA
    "і": "i",  # і CYRILLIC SMALL BYELORUSSIAN-UKRAINIAN I
    "ј": "j",  # ј CYRILLIC SMALL JE
    "л": "l",  # л CYRILLIC SMALL EL (vizuálne odlišné, ale v dátach nahrádza l)
    "м": "m",  # м
    "н": "h",  # н
    "в": "v",  # в
    "А": "A", "Е": "E", "О": "O", "Р": "P",
    "С": "C", "У": "Y", "Х": "X", "М": "M",
    "В": "V", "Н": "H", "К": "K", "Т": "T",
    "ВВ": "VV",
}


def out(s=""):
    print(s)


def h1(title):
    out()
    out("=" * 78)
    out(title)
    out("=" * 78)


def h2(title):
    out()
    out("── " + title + " " + "─" * max(0, 74 - len(title)))


def strip_diacritics(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def norm(s):
    """Normalizácia na párovanie: homoglyfy → latinka, bez diakritiky, lowercase."""
    if not s:
        return ""
    s = "".join(HOMOGLYPHS.get(ch, ch) for ch in s)
    s = strip_diacritics(s).lower()
    return re.sub(r"\s+", " ", s).strip()


def open_ro(path):
    if not os.path.exists(path):
        sys.exit(f"CHYBA: databáza neexistuje: {path}")
    uri = "file:" + path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── A) Pokrytie zápisov ────────────────────────────────────────────────────

def section_coverage(conn):
    h1("A) POKRYTIE ZÁPISOV")
    rows = conn.execute(
        "SELECT MIN(entry_date), MAX(entry_date), COUNT(*) FROM entries").fetchone()
    first, last, n_entries = rows[0], rows[1], rows[2]
    if not first:
        out("Žiadne entries — nič na analýzu.")
        return None, None, {}
    d0 = date.fromisoformat(first)
    d1 = date.fromisoformat(last)
    span = (d1 - d0).days + 1
    out(f"1. Rozsah: {first} → {last}  ({span} kalendárnych dní, {n_entries} entries)")

    med_by_day = Counter()
    for r in conn.execute(
            "SELECT e.entry_date AS d, COUNT(*) AS n FROM events ev "
            "JOIN entries e ON ev.entry_id = e.id WHERE ev.event_type = ? "
            "GROUP BY e.entry_date", (MED_TYPE,)):
        med_by_day[r["d"]] = r["n"]

    all_days = [d0 + timedelta(days=i) for i in range(span)]
    with_med = [d for d in all_days if med_by_day.get(d.isoformat())]
    without = [d for d in all_days if not med_by_day.get(d.isoformat())]
    pct = 100.0 * len(with_med) / span if span else 0
    out(f"2. Dní s aspoň 1 liekovým eventom: {len(with_med)} / {span}  ({pct:.1f} %)")
    out(f"3. Dní BEZ liekového eventu: {len(without)}  ({100 - pct:.1f} %)")
    if without:
        show = [d.isoformat() for d in without[:30]]
        out("   " + ", ".join(show) + (f"  … a ďalších {len(without) - 30}"
                                       if len(without) > 30 else ""))

    h2("4. Histogram: koľko liekových eventov pripadá na deň")
    hist = Counter(med_by_day.get(d.isoformat(), 0) for d in all_days)
    for k in sorted(hist):
        label = "0 (nič nezapísané)" if k == 0 else str(k)
        out(f"   {label:>18} eventov: {hist[k]:>3} dní  {'█' * min(hist[k], 60)}")

    h2("5. Najdlhšia súvislá séria dní BEZ liekového eventu")
    best = cur = 0
    best_end = cur_start = None
    best_start = None
    for d in all_days:
        if not med_by_day.get(d.isoformat()):
            if cur == 0:
                cur_start = d
            cur += 1
            if cur > best:
                best, best_start, best_end = cur, cur_start, d
        else:
            cur = 0
    if best:
        out(f"   {best} dní za sebou: {best_start} → {best_end}")
    else:
        out("   Žiadna — každý deň má liekový event.")
    return d0, d1, med_by_day


# ── B/C) Liekové eventy, kombá, tvar dát ───────────────────────────────────

SPLIT_RE = re.compile(r"\s*[;,\n]\s*|\s+a\s+|\s+\+\s+", re.IGNORECASE)
QTY_PATTERNS = [
    ("násobok  (3×, 2x)", re.compile(r"\d+\s*[x×]", re.IGNORECASE)),
    ("zlomok   (1/2, 1/4)", re.compile(r"\d\s*/\s*\d")),
    ("desatinné (0.5, 1,5)", re.compile(r"\d+[.,]\d+")),
    ("mg / g / ml", re.compile(r"\d+\s*(mg|g|ml)\b", re.IGNORECASE)),
    ("tableta/kapsula", re.compile(r"\b(tabl?|tablet\w*|kaps\w*|kvapk\w*|ml\b)", re.IGNORECASE)),
    ("slovné množstvo", re.compile(r"\b(pol|polovic\w*|stvrt\w*|štvrt\w*|cel\w*)\b", re.IGNORECASE)),
]


def load_catalog(conn):
    """[(id, canonical_name, [normalizované varianty názvu])]"""
    items = []
    for r in conn.execute("SELECT id, canonical_name, aliases FROM med_catalog"):
        names = [r["canonical_name"] or ""]
        try:
            names += json.loads(r["aliases"] or "[]")
        except (ValueError, TypeError):
            pass
        variants = sorted({norm(n) for n in names if n and len(norm(n)) >= 3},
                          key=len, reverse=True)
        items.append((r["id"], r["canonical_name"], variants))
    return items


def match_catalog(text, catalog):
    """Deterministické párovanie: ktoré položky katalógu sa v texte vyskytujú."""
    t = norm(text)
    hits = []
    for cid, cname, variants in catalog:
        for v in variants:
            if v and v in t:
                hits.append((cid, cname))
                break
    return hits


def section_events(conn, catalog):
    rows = conn.execute(
        "SELECT ev.id, e.entry_date, ev.event_time, ev.value, ev.note, ev.catalog_id "
        "FROM events ev JOIN entries e ON ev.entry_id = e.id "
        "WHERE ev.event_type = ? ORDER BY e.entry_date, ev.event_time, ev.id",
        (MED_TYPE,)).fetchall()
    evs = [dict(r) for r in rows]

    h1("B) KOMBO EVENTY — koľko liekov je v jednom zázname")
    out(f"Liekových eventov spolu: {len(evs)}")
    out(f"Z toho s vyplneným catalog_id: {sum(1 for e in evs if e['catalog_id'])} "
        f"({100.0 * sum(1 for e in evs if e['catalog_id']) / max(len(evs), 1):.1f} %)")

    h2("7. Heuristiky na počet liekov v jednom evente")
    out("   H1 — oddeľovače: čiarka, bodkočiarka, nový riadok, ' a ', ' + '")
    out("   H2 — počet RÔZNYCH položiek katalógu nájdených v texte (po normalizácii")
    out("        homoglyfov a diakritiky, substring match cez canonical_name + aliases)")
    out("   Výsledný odhad = max(H1, H2).")
    out("   NEISTOTA: H1 nadhodnocuje (čiarka oddeľuje aj dávku od názvu, napr.")
    out("   'Orfiril, 1/2' = 1 liek); H2 podhodnocuje (liek mimo katalógu nenájde")
    out("   a rovnaký liek 2× zaráta raz). Preto sú nižšie uvedené OBE čísla.")

    dist_h1, dist_h2, dist_max = Counter(), Counter(), Counter()
    for e in evs:
        val = e["value"] or ""
        parts = [p for p in SPLIT_RE.split(val) if p.strip()]
        # segment bez písmena (samotná dávka) sa neráta ako ďalší liek
        parts = [p for p in parts if re.search(r"[a-zA-Zá-žÁ-ŽЀ-ӿ]{3,}", p)]
        h1c = max(1, len(parts))
        hits = match_catalog(val, catalog)
        h2c = max(1, len(hits))
        e["_h1"], e["_h2"], e["_hits"] = h1c, h2c, hits
        dist_h1[h1c] += 1
        dist_h2[h2c] += 1
        dist_max[max(h1c, h2c)] += 1

    h2("8. Rozdelenie počtu liekov na event")
    out(f"   {'liekov':>8} | {'H1 (oddeľovače)':>16} | {'H2 (katalóg)':>13} | {'max(H1,H2)':>11}")
    for k in sorted(set(dist_h1) | set(dist_h2) | set(dist_max)):
        label = f"{k}" if k < 4 else f"{k}+"
        out(f"   {label:>8} | {dist_h1.get(k, 0):>16} | {dist_h2.get(k, 0):>13} | {dist_max.get(k, 0):>11}")
    multi = sum(v for k, v in dist_max.items() if k > 1)
    out(f"\n   Eventov s VIAC než jedným liekom (odhad max): {multi} "
        f"({100.0 * multi / max(len(evs), 1):.1f} %)")

    h1("C) TVAR DÁT")
    h2("9. DISTINCT hodnoty value (od najčastejších)")
    vc = Counter((e["value"] or "").strip() for e in evs)
    out(f"   Rôznych hodnôt: {len(vc)} pri {len(evs)} eventoch")
    for val, n in vc.most_common():
        out(f"   {n:>4}×  {val!r}")

    h2("10. Notácie množstva")
    for label, rx in QTY_PATTERNS:
        ex = [e["value"] for e in evs if e["value"] and rx.search(e["value"])]
        out(f"   {label:<22} {len(ex):>4} eventov" +
            (f"   napr.: {'; '.join(repr(x) for x in ex[:3])}" if ex else ""))
    no_qty = [e["value"] for e in evs
              if e["value"] and not any(rx.search(e["value"]) for _, rx in QTY_PATTERNS)]
    out(f"   {'BEZ akejkoľvek dávky':<22} {len(no_qty):>4} eventov" +
        (f"   napr.: {'; '.join(repr(x) for x in no_qty[:5])}" if no_qty else ""))

    h2("11. Neprázdne note pri liekových eventoch")
    notes = [(e["id"], e["entry_date"], e["note"]) for e in evs if (e["note"] or "").strip()]
    out(f"   {len(notes)} z {len(evs)} eventov má note")
    for eid, d, note in notes:
        out(f"   #{eid} {d}: {note!r}")
    return evs


# ── D) Názvy a katalóg ─────────────────────────────────────────────────────

STOPWORDS = set("""rano ráno vecer večer obed poobede podvecer noc doobeda pred po
tablet tableta tablety tabliet kapsula kapsule kvapky kvapiek mg ml gram gramov
dal dala dostal dostala bral brala vzal vzala uz už este ešte asi okolo cca
pol polovica stvrt štvrť cela celá cely celý kus kusy davka dávka davky dávky
a alebo aj s so na do od za pri""".split())


def section_catalog(conn, evs, catalog):
    h1("D) NÁZVY A PÁROVANIE S KATALÓGOM")
    h2("Katalóg (med_catalog)")
    for cid, cname, variants in catalog:
        out(f"   #{cid:<3} {cname!r}  varianty: {len(variants)}  {variants[:6]}")

    exact = [e for e in evs if len(e["_hits"]) == 1]
    multi = [e for e in evs if len(e["_hits"]) > 1]
    none_ = [e for e in evs if not e["_hits"]]
    n = max(len(evs), 1)
    h2("13. Výsledok deterministického párovania (bez LLM)")
    out(f"   jednoznačne (1 zhoda):   {len(exact):>4}  ({100.0 * len(exact) / n:.1f} %)")
    out(f"   viacznačne (2+ zhody):   {len(multi):>4}  ({100.0 * len(multi) / n:.1f} %)")
    out(f"   žiadna zhoda:            {len(none_):>4}  ({100.0 * len(none_) / n:.1f} %)")

    stored = sum(1 for e in evs if e["catalog_id"])
    agree = sum(1 for e in evs if e["catalog_id"] and len(e["_hits"]) == 1
                and e["_hits"][0][0] == e["catalog_id"])
    out(f"\n   Pre porovnanie — uložené catalog_id: {stored}")
    out(f"   z toho súhlasí s textovým párovaním: {agree}")
    conflict = [e for e in evs if e["catalog_id"] and e["_hits"]
                and e["catalog_id"] not in [c for c, _ in e["_hits"]]]
    out(f"   uložené catalog_id NEsúhlasí s textom: {len(conflict)}")
    for e in conflict[:10]:
        out(f"      #{e['id']} {e['entry_date']} catalog_id={e['catalog_id']} "
            f"text={e['value']!r} → {[c for _, c in e['_hits']]}")

    h2("14. Fragmenty, ktoré vyzerajú ako názov lieku, ale NIE SÚ v katalógu")
    unknown = Counter()
    examples = defaultdict(list)
    for e in evs:
        val = e["value"] or ""
        matched = norm(val)
        for _, _, variants in catalog:
            for v in variants:
                if v and v in matched:
                    matched = matched.replace(v, " ")
        for tok in re.findall(r"[^\W\d_]{4,}", matched, re.UNICODE):
            if tok in STOPWORDS:
                continue
            unknown[tok] += 1
            if len(examples[tok]) < 2:
                examples[tok].append(f"#{e['id']} {val!r}")
    if not unknown:
        out("   Žiadne — všetko sa spárovalo.")
    for tok, cnt in unknown.most_common(40):
        out(f"   {cnt:>3}×  {tok:<20} {examples[tok]}")
    out("\n   (kandidáti na nové aliasy alebo nové položky katalógu; slová z bežnej")
    out("    reči sú odfiltrované cez zoznam stopwords, filter nie je dokonalý)")


# ── E) Homoglyfy ───────────────────────────────────────────────────────────

def section_homoglyphs(conn, evs):
    h1("E) HOMOGLYFY A ZNEČISTENIE ZNAKOV")
    ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  "áäčďéíĺľňóôŕšťúýžÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ"
                  "0123456789 .,;:!?-–—+/()[]%×x'\"\n\t°&*=_")
    found = defaultdict(list)   # znak → [(event_id, pole, hodnota)]
    for e in evs:
        for field in ("value", "note"):
            s = e[field] or ""
            for ch in s:
                if ch not in ALLOWED:
                    found[ch].append((e["id"], field, s))
    if not found:
        out("15. Žiadne podozrivé znaky — dáta sú čisté.")
        return
    out("15. Znaky mimo bežnej slovenskej abecedy / číslic / interpunkcie:")
    out(f"    {'znak':<6} {'kód':<10} {'krát':<6} {'Unicode názov':<40} nahrádza")
    for ch, occ in sorted(found.items(), key=lambda kv: -len(kv[1])):
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = "?"
        repl = HOMOGLYPHS.get(ch, "—")
        out(f"    {ch!r:<6} U+{ord(ch):04X}{'':<4} {len(occ):<6} {name[:38]:<40} {repl!r}")
        ids = sorted({o[0] for o in occ})
        out(f"           eventy: {ids[:15]}{' …' if len(ids) > 15 else ''}")
        for eid, field, val in occ[:2]:
            out(f"           napr. #{eid} {field}={val!r}")
    h2("16. NÁVRH deterministickej mapy náhrad (NEAPLIKOVANÉ)")
    for ch in sorted(found):
        if ch in HOMOGLYPHS:
            out(f'    "\\u{ord(ch):04x}": "{HOMOGLYPHS[ch]}",   # {ch!r} → {HOMOGLYPHS[ch]!r}')
    missing = [ch for ch in found if ch not in HOMOGLYPHS]
    if missing:
        out(f"    NEZARADENÉ (rozhodni ručne): {[f'{c!r} U+{ord(c):04X}' for c in missing]}")


# ── F) Časy vs režim ───────────────────────────────────────────────────────

def parse_hhmm(s):
    if not s:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", s.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if 0 <= h < 24 and 0 <= mi < 60 else None


def section_schedule(conn, evs):
    h1("F) ČASY vs REŽIM LIEKOV")
    h2("17. med_schedule (vrátane neaktívnych)")
    rows = conn.execute("SELECT * FROM med_schedule ORDER BY sort_order, id").fetchall()
    if not rows:
        out("   Tabuľka je prázdna.")
    cols = rows[0].keys() if rows else []
    for r in rows:
        d = dict(r)
        out("   " + " | ".join(f"{c}={d[c]!r}" for c in cols if d[c] not in (None, "")))
    sched_times = sorted({t for t in (parse_hhmm(dict(r).get("time_exact")) for r in rows)
                          if t is not None})
    out(f"\n   Presné časy v režime: {[f'{t // 60:02d}:{t % 60:02d}' for t in sched_times]}")

    h2("18. Histogram časov liekových eventov (po hodinách)")
    times = [parse_hhmm(e["event_time"]) for e in evs]
    valid = [t for t in times if t is not None]
    out(f"   Eventov s platným časom: {len(valid)} / {len(evs)}"
        f"   (bez času alebo neparsovateľné: {len(evs) - len(valid)})")
    byhour = Counter(t // 60 for t in valid)
    for h in range(24):
        n = byhour.get(h, 0)
        mark = " ←režim" if any(h == t // 60 for t in sched_times) else ""
        if n or mark:
            out(f"   {h:02d}:00  {n:>3}  {'█' * n}{mark}")

    h2("19. Odchýlka od najbližšieho času v režime")
    if not sched_times or not valid:
        out("   Nedá sa spočítať (chýbajú časy v režime alebo v eventoch).")
        return
    devs = []
    for t in valid:
        devs.append(min(abs(t - s) for s in sched_times))
    devs.sort()
    med = devs[len(devs) // 2]
    out(f"   medián: {med} min | priemer: {sum(devs) / len(devs):.0f} min | "
        f"max: {devs[-1]} min")
    buckets = [(0, 15), (16, 30), (31, 60), (61, 120), (121, 10 ** 9)]
    for lo, hi in buckets:
        n = sum(1 for d in devs if lo <= d <= hi)
        label = f"{lo}–{hi} min" if hi < 10 ** 9 else f"{lo}+ min"
        out(f"   {label:>12}: {n:>3}  ({100.0 * n / len(devs):.0f} %)  {'█' * n}")


# ── G) Výpis všetkých eventov ──────────────────────────────────────────────

def section_dump(evs):
    h1("G) VŠETKY LIEKOVÉ EVENTY (surový výpis)")
    out(f"{'id':>5} | {'dátum':<10} | {'čas':<5} | {'cat':>4} | {'~n':>2} | value / note")
    out("-" * 78)
    for e in evs:
        out(f"{e['id']:>5} | {e['entry_date']:<10} | {(e['event_time'] or ''):<5} | "
            f"{(e['catalog_id'] or ''):>4} | {max(e['_h1'], e['_h2']):>2} | {e['value']!r}")
        if (e["note"] or "").strip():
            out(f"{'':>5} | {'':<10} | {'':<5} | {'':>4} | {'':>2} |   note: {e['note']!r}")


def main():
    ap = argparse.ArgumentParser(description="Read-only analýza liekových eventov")
    ap.add_argument("--db", default="daylog.db", help="cesta k daylog.db")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    conn = open_ro(args.db)
    out(f"DB: {args.db}  (otvorená READ-ONLY, mode=ro)")
    tot = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    med = conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?", (MED_TYPE,)).fetchone()[0]
    out(f"Eventov spolu: {tot}, z toho typu '{MED_TYPE}': {med}")
    types = conn.execute("SELECT event_type, COUNT(*) n FROM events GROUP BY event_type "
                         "ORDER BY n DESC").fetchall()
    out("Typy eventov: " + ", ".join(f"{r['event_type']}={r['n']}" for r in types))

    catalog = load_catalog(conn)
    section_coverage(conn)
    evs = section_events(conn, catalog)
    section_catalog(conn, evs, catalog)
    section_homoglyphs(conn, evs)
    section_schedule(conn, evs)
    section_dump(evs)
    conn.close()
    out()
    out("Koniec reportu. Skript nič nezapísal (mode=ro).")


if __name__ == "__main__":
    main()
