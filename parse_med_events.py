#!/usr/bin/env python3
"""
Parser liekových eventov → návrh riadkov event_meds. IBA REPORT.

Read-only: DB sa otvára cez URI file:...?mode=ro. Skript NEMÁ --apply,
zámerne nič nezapisuje — slúži na kontrolu kvality rozkladu očami.

Rozkladová logika žije v med_parser.py (zdieľa ju migrácia aj živý zápis
v database.py). Tento súbor je len report nad ňou. Kvôli spätnej
kompatibilite re-exportuje parserové názvy — staré `from parse_med_events
import ...` (testy, add_aliases.py) tak fungujú bez zmeny.

Všetko je deterministické, bez LLM.

Spusti:  python3 parse_med_events.py --db /var/www/daylog/daylog.db
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# re-export: iné moduly a testy importujú tieto názvy odtiaľto
from med_parser import (  # noqa: F401
    MED_TYPE, HOMOGLYPHS, NEGATIVE_MARKERS, GENERIC, VULGAR,
    strip_diacritics, norm, has_homoglyph,
    is_dose_only, split_meds, parse_qty, unit_of, detect_status,
    load_catalog, variant_matches, match_catalog, parse_event,
)


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
        rows, flags = parse_event(e["value"], e["note"], catalog, source="migracia")
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
