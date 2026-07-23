#!/usr/bin/env python3
"""
Migrácia existujúcich liekových eventov do tabuľky event_meds (Blok 3B).

Dry-run je default — bez --apply sa DB otvára read-only a nič sa nezapisuje.
So --apply prebehne zápis v JEDNEJ transakcii (BEGIN/COMMIT/ROLLBACK, vzor
merge_catalog_items). Po zápise sa počty overia a pri nesúhlase je ROLLBACK.

Parsovacia logika sa NEDUPLIKUJE — importuje sa z parse_med_events.py.

IDEMPOTENCIA: migrujú sa len eventy, ktoré ešte NEMAJÚ žiadny riadok
v event_meds. Event s existujúcimi riadkami (hocijaký source) sa preskočí,
takže druhý beh vloží 0 riadkov a neskoršia ľudská úprava sa neprepíše.

Spusti:  python3 migrate_event_meds.py --db /var/www/daylog/daylog.db
         python3 migrate_event_meds.py --db /var/www/daylog/daylog.db --apply
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from med_parser import load_catalog, parse_event

SOURCE = "migracia"
MED_TYPE = "liek"


def out(s=""):
    print(s)


def h1(t):
    out(); out("=" * 100); out(t); out("=" * 100)


def h2(t):
    out(); out("── " + t + " " + "─" * max(0, 96 - len(t)))


def open_db(path, readonly=True):
    if not os.path.exists(path):
        sys.exit(f"CHYBA: databáza neexistuje: {path}")
    conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True) if readonly \
        else sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def require_table(conn):
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name='event_meds'").fetchone()
    if not row:
        sys.exit("CHYBA: tabuľka event_meds neexistuje — najprv nasaď Blok 3A "
                 "(init_db ju vytvorí pri štarte appky).")


def existing_by_event(conn):
    """{event_id: [sources]} — eventy, ktoré už riadky v event_meds majú."""
    res = {}
    for r in conn.execute("SELECT event_id, source, COUNT(*) n FROM event_meds "
                          "GROUP BY event_id, source"):
        res.setdefault(r["event_id"], []).append((r["source"], r["n"]))
    return res


def plan_migration(conn, catalog):
    """(rows, skipped) — rows sú návrhy na vloženie, skipped už migrované eventy."""
    existing = existing_by_event(conn)
    rows, skipped = [], []
    for e in conn.execute(
            "SELECT ev.id, e.entry_date, ev.event_time, ev.value, ev.note "
            "FROM events ev JOIN entries e ON ev.entry_id = e.id "
            "WHERE ev.event_type = ? ORDER BY e.entry_date, ev.event_time, ev.id",
            (MED_TYPE,)):
        if e["id"] in existing:
            skipped.append({"event_id": e["id"], "date": e["entry_date"],
                            "value": e["value"], "existing": existing[e["id"]]})
            continue
        parsed, flags = parse_event(e["value"], e["note"], catalog, source="migracia")
        for r in parsed:
            rows.append({
                "event_id": e["id"],
                "date": e["entry_date"],
                "time": e["event_time"],
                "catalog_id": r["catalog_id"],
                "raw_name": r["raw_name"],
                "qty": r["qty"],
                "unit": r["unit"],
                "status": r["status"],
                "status_note": r["status_note"],
                "source": SOURCE,
            })
    return rows, skipped


def apply_rows(path, rows):
    """Vloží riadky v jednej transakcii a overí počty. Vráti súhrn."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        before = cur.execute("SELECT COUNT(*) FROM event_meds WHERE source=?",
                             (SOURCE,)).fetchone()[0]
        before_all = cur.execute("SELECT COUNT(*) FROM event_meds").fetchone()[0]
        # Poistka proti zdvojeniu: eventy, ktoré UŽ v DB riadky majú (napr. ich
        # medzitým pridal človek), sa nesmú migrovať — plán vznikol skôr.
        already = {r[0] for r in cur.execute(
            "SELECT DISTINCT event_id FROM event_meds")}
        clash = sorted({r["event_id"] for r in rows} & already)
        if clash:
            raise RuntimeError(
                f"eventy {clash} medzitým dostali riadky event_meds — "
                f"migrácia zastavená, aby sa nezdvojili")
        now = datetime.utcnow().isoformat()
        for r in rows:
            cur.execute(
                "INSERT INTO event_meds (event_id, catalog_id, raw_name, qty, unit, "
                "status, status_note, source, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["event_id"], r["catalog_id"], r["raw_name"], r["qty"], r["unit"],
                 r["status"], r["status_note"], r["source"], now))
        after = cur.execute("SELECT COUNT(*) FROM event_meds WHERE source=?",
                            (SOURCE,)).fetchone()[0]
        after_all = cur.execute("SELECT COUNT(*) FROM event_meds").fetchone()[0]
        if after - before != len(rows) or after_all - before_all != len(rows):
            raise RuntimeError(
                f"kontrola počtov zlyhala: source={SOURCE} {before}→{after} "
                f"(očakávané +{len(rows)}), spolu {before_all}→{after_all} — ROLLBACK")
        orphan = cur.execute(
            "SELECT COUNT(*) FROM event_meds em LEFT JOIN events ev ON em.event_id = ev.id "
            "WHERE ev.id IS NULL").fetchone()[0]
        if orphan:
            raise RuntimeError(f"vzniklo {orphan} osirených event_meds — ROLLBACK")
        cur.execute("COMMIT")
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        raise
    conn.close()
    return {"inserted": len(rows), "source_before": before, "source_after": after,
            "total_before": before_all, "total_after": after_all}


def main():
    ap = argparse.ArgumentParser(description="Migrácia liekových eventov → event_meds")
    ap.add_argument("--db", default="daylog.db")
    ap.add_argument("--apply", action="store_true", help="zapísať (inak iba dry-run)")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    conn = open_db(args.db, readonly=True)
    require_table(conn)
    catalog = load_catalog(conn)
    rows, skipped = plan_migration(conn, catalog)
    n_events_total = conn.execute("SELECT COUNT(*) FROM events WHERE event_type=?",
                                  (MED_TYPE,)).fetchone()[0]
    existing_total = conn.execute("SELECT COUNT(*) FROM event_meds").fetchone()[0]
    conn.close()

    mode = "APPLY (zapisuje sa)" if args.apply else "DRY-RUN (nič sa nezapisuje)"
    out(f"DB: {args.db}   režim: {mode}")
    out(f"Liekových eventov v DB: {n_events_total}   "
        f"riadkov v event_meds teraz: {existing_total}")

    events_to_do = sorted({r["event_id"] for r in rows})
    h1("SÚHRN MIGRÁCIE")
    out(f"Eventov na migráciu : {len(events_to_do)}")
    out(f"Riadkov na vloženie : {len(rows)}")
    out(f"Preskočených eventov (už majú event_meds): {len(skipped)}")

    st = Counter(r["status"] for r in rows)
    out(f"\n  status      : podane={st.get('podane', 0)}, neznamy={st.get('neznamy', 0)}")
    with_cat = sum(1 for r in rows if r["catalog_id"])
    out(f"  catalog_id  : vyplnené={with_cat}, NULL={len(rows) - with_cat}")
    with_qty = sum(1 for r in rows if r["qty"] is not None)
    out(f"  qty         : vyplnené={with_qty}, NULL={len(rows) - with_qty}")
    out(f"  unit        : " + ", ".join(f"{k}={v}" for k, v in
                                        Counter(str(r["unit"]) for r in rows).most_common()))
    out(f"  source      : " + ", ".join(f"{k}={v}" for k, v in
                                        Counter(r["source"] for r in rows).most_common()))

    if skipped:
        h2("Preskočené eventy (chránené pred prepísaním)")
        for s in skipped:
            src = ", ".join(f"{a}×{b}" for a, b in s["existing"])
            out(f"   #{s['event_id']} {s['date']} {s['value']!r} — už má: {src}")

    h1("ÚPLNÝ ZOZNAM VKLADANÝCH RIADKOV")
    out(f"{'ev':>5} | {'dátum':<10} {'čas':<5} | {'raw_name':<40} | {'qty':>7} | "
        f"{'unit':<8} | {'cat':>4} | {'status':<8} | status_note")
    out("-" * 100)
    for r in rows:
        qty_s = "" if r["qty"] is None else ("%.2f" % r["qty"])
        note_s = (r["status_note"] or "")
        if len(note_s) > 40:
            note_s = note_s[:37] + "…"
        out(f"{r['event_id']:>5} | {r['date']:<10} {(r['time'] or '—'):<5} | "
            f"{r['raw_name']:<40} | {qty_s:>7} | {str(r['unit'] or ''):<8} | "
            f"{str(r['catalog_id'] or ''):>4} | {r['status']:<8} | {note_s}")

    h1("ZÁPIS")
    if not args.apply:
        out("DRY-RUN — nič sa nezapísalo. Na zápis spusti ten istý príkaz s --apply.")
        return
    if not rows:
        out("Niet čo vkladať — všetky liekové eventy už majú event_meds. "
            "(Idempotencia: 0 vložených riadkov.)")
        return
    res = apply_rows(args.db, rows)
    out(f"   vložených riadkov      : {res['inserted']}")
    out(f"   event_meds source={SOURCE}: {res['source_before']} → {res['source_after']}")
    out(f"   event_meds spolu       : {res['total_before']} → {res['total_after']}")
    out("   kontrola počtov aj osirených odkazov prešla, COMMIT")


if __name__ == "__main__":
    main()
