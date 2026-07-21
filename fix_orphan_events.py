"""
Opraví osirené eventy — riadky events, ktorých catalog_id ukazuje na položku
katalógu, ktorá už neexistuje (vznikli starým holým DELETE nad med_catalog).

Spusti (len plán, nič nemení):   python fix_orphan_events.py
Zápis do DB:                     python fix_orphan_events.py --apply
Iná DB:                          python fix_orphan_events.py --db /var/www/daylog/daylog.db

Prepája sa LEN podľa ORPHAN_MAP nižšie. Eventy s neznámym catalog_id sa
NEMENIA — skript ich iba nahlási ako "vyžaduje rozhodnutie".
"""
import os
import sys
import sqlite3
import argparse

BASE = os.path.dirname(os.path.abspath(__file__))

# Mapovanie: staré (neexistujúce) catalog_id → dnešné platné catalog_id
ORPHAN_MAP = {
    1: 8,   # bývalé Orfiril id=1 → dnešné "Orfiril long" id=8
}

ORPHAN_SQL = """
    SELECT e.id, e.entry_id, e.event_time, e.event_type, e.value, e.catalog_id
    FROM events e
    LEFT JOIN med_catalog c ON c.id = e.catalog_id
    WHERE e.catalog_id IS NOT NULL AND c.id IS NULL
    ORDER BY e.catalog_id, e.id
"""


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def find_orphans(cur):
    return [dict(r) for r in cur.execute(ORPHAN_SQL).fetchall()]


def show(rows):
    for r in rows:
        val = (r["value"] or "")[:48]
        print(f"    event id={r['id']:<5} entry={str(r['entry_id'] or '-'):<5} "
              f"{str(r['event_time'] or '-'):<6} {str(r['event_type'] or '-'):<10} "
              f"catalog_id={r['catalog_id']:<4} value={val!r}")


def main():
    ap = argparse.ArgumentParser(description="Oprava osirených events.catalog_id")
    ap.add_argument("--apply", action="store_true", help="reálne zapíše zmeny do DB")
    ap.add_argument("--db", default=os.environ.get("DAYLOG_DB", os.path.join(BASE, "daylog.db")),
                    help="cesta k DB (default: DAYLOG_DB alebo daylog.db pri skripte)")
    args = ap.parse_args()

    print("=" * 78)
    print("OPRAVA OSIRENÝCH EVENTOV (events.catalog_id → neexistujúca položka)")
    print("Režim:", "ZÁPIS (--apply)" if args.apply else "len plán (bez --apply nič nemení)")
    print("DB:", args.db)
    print("=" * 78)

    if not os.path.isfile(args.db):
        print(f"CHYBA: DB neexistuje: {args.db}")
        return 1

    conn = connect(args.db)
    conn.isolation_level = None   # manuálna transakcia (ako v database.py)
    cur = conn.cursor()

    orphans = find_orphans(cur)
    if not orphans:
        print("\nŽiadne osirené eventy — niet čo opravovať.")
        conn.close()
        return 0

    known = [r for r in orphans if r["catalog_id"] in ORPHAN_MAP]
    unknown = [r for r in orphans if r["catalog_id"] not in ORPHAN_MAP]

    print(f"\nOsirených eventov spolu: {len(orphans)} "
          f"(v ORPHAN_MAP: {len(known)}, neznámych: {len(unknown)})")

    if known:
        print(f"\n── OPRAVITEĽNÉ ({len(known)}) ─────────────────────────────────────────")
        show(known)
        for old, new in sorted(ORPHAN_MAP.items()):
            n = sum(1 for r in known if r["catalog_id"] == old)
            if n:
                row = cur.execute("SELECT canonical_name FROM med_catalog WHERE id=?",
                                  (new,)).fetchone()
                target = row["canonical_name"] if row else "!!! CIEĽ NEEXISTUJE !!!"
                print(f"  → {n} eventov: catalog_id {old} → {new} ({target})")

    if unknown:
        print(f"\n── NEZNÁME — VYŽADUJE ROZHODNUTIE ({len(unknown)}) ────────────────────")
        show(unknown)
        ids = sorted({r["catalog_id"] for r in unknown})
        print(f"  catalog_id bez mapovania: {ids}")
        print("  → NEMENÍM. Doplň ich do ORPHAN_MAP, ak ich chceš opraviť.")

    if not args.apply:
        print("\n(bez --apply sa NIČ nezapísalo)")
        conn.close()
        return 0

    if not known:
        print("\nNič na opravu (žiadny osirený event nie je v ORPHAN_MAP).")
        conn.close()
        return 0

    # ── --apply: over ciele, oprav v jednej transakcii, prekontroluj ────────
    targets = sorted({ORPHAN_MAP[r["catalog_id"]] for r in known})
    for t in targets:
        if not cur.execute("SELECT 1 FROM med_catalog WHERE id=?", (t,)).fetchone():
            print(f"\nCHYBA: cieľové catalog_id={t} v med_catalog NEEXISTUJE — končím, nič nemením.")
            conn.close()
            return 1
    print(f"\nCiele overené v med_catalog: {targets}")

    try:
        cur.execute("BEGIN")
        moved = 0
        for old, new in sorted(ORPHAN_MAP.items()):
            cur.execute("UPDATE events SET catalog_id=? WHERE catalog_id=?", (new, old))
            if cur.rowcount > 0:
                print(f"  opravené: {cur.rowcount} eventov {old} → {new}")
                moved += cur.rowcount

        # prekontroluj: smú zostať LEN tie neznáme (tie zámerne nemeníme)
        left = find_orphans(cur)
        still_known = [r for r in left if r["catalog_id"] in ORPHAN_MAP]
        if still_known:
            raise RuntimeError(f"po oprave zostalo {len(still_known)} osirených "
                               f"eventov s mapovaným catalog_id")
        cur.execute("COMMIT")
    except Exception as e:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        print(f"\nCHYBA — ROLLBACK, DB nezmenená: {e}")
        conn.close()
        return 1

    after = find_orphans(cur)
    print(f"\nHOTOVO: opravených {moved} eventov.")
    print(f"Osirených eventov po oprave: {len(after)}"
          + (f" (všetko neznáme bez mapovania: {sorted({r['catalog_id'] for r in after})})"
             if after else " — čisté"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
