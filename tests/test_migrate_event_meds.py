"""
Testy migrácie liekových eventov do event_meds (Blok 3B).

Vlastná dočasná DB (tempfile) — daylog.db ani produkcie sa nedotýka.

Spusti:   pytest tests/test_migrate_event_meds.py -v
alebo:    python tests/test_migrate_event_meds.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import database
from migrate_event_meds import plan_migration, apply_rows, SOURCE
from parse_med_events import load_catalog

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn

CAT = (
    (8,  "Orfiril long", ["Orfiril", "Ofriril"]),
    (12, "Magtein Magnesium L-Threonat", ["Magtein", "Magnetit"]),
    (13, "Vitamin B6", ["B6"]),
    (16, "TISERCIN", ["Tisercin", "Tisercinu"]),
    (17, "Fevarin", []),
)

# (id, dátum, čas, value, note)
EVENTS = (
    (1, "2026-01-01", "17:30", "3× Orfiril, 1/2 Tisercin, 1/4 Fevarin", None),
    (2, "2026-01-01", "20:30", "1/2 Tisercin, 2x Magnetit, 1x B6", None),
    (3, "2026-01-02", "08:00", "600 mg Orfiril", None),
    (4, "2026-01-02", "13:00", "1/2 Tisercin", "zabudla podať sesterička"),
    (5, "2026-01-03", "14:00", "probiotikum, glycín", None),
)


def _seed(path):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    c.execute("INSERT INTO entries (id, user_id, created_at, entry_date, text) "
              "VALUES (1, 1, '2026-01-01', '2026-01-01', 't')")
    for cid, name, al in CAT:
        c.execute("INSERT INTO med_catalog (id, canonical_name, aliases, created_at, updated_at) "
                  "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
                  (cid, name, json.dumps(al, ensure_ascii=False)))
    for eid, d, t, val, note in EVENTS:
        c.execute("UPDATE entries SET entry_date=? WHERE id=1", (d,))
        c.execute("INSERT INTO entries (id, user_id, created_at, entry_date, text) "
                  "VALUES (?, 1, ?, ?, 't')", (100 + eid, d, d))
        c.execute("INSERT INTO events (id, entry_id, user_id, event_time, event_type, "
                  "value, note, created_at) VALUES (?, ?, 1, ?, 'liek', ?, ?, ?)",
                  (eid, 100 + eid, t, val, note, d))
    # kontrolný event iného typu — migrácia sa ho nesmie dotknúť
    c.execute("INSERT INTO events (id, entry_id, user_id, event_time, event_type, "
              "value, created_at) VALUES (99, 101, 1, '09:00', 'nalada', 'dobrá', '2026-01-01')")
    c.commit()
    c.close()


@contextmanager
def temp_db():
    d = tempfile.mkdtemp(prefix="daylog_test_")
    path = os.path.join(d, "test.db")
    old = database.DB_PATH
    database.DB_PATH = path
    try:
        database.init_db()
        _seed(path)
        yield path
    finally:
        database.DB_PATH = old
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def db():
    with temp_db() as path:
        yield path


def plan(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    cat = load_catalog(c)
    rows, skipped = plan_migration(c, cat)
    c.close()
    return rows, skipped


def meds(path, event_id=None):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    q = "SELECT * FROM event_meds"
    args = ()
    if event_id is not None:
        q += " WHERE event_id=?"
        args = (event_id,)
    rows = [dict(r) for r in c.execute(q + " ORDER BY id", args)]
    c.close()
    return rows


def migrate(path):
    rows, _ = plan(path)
    return apply_rows(path, rows) if rows else {"inserted": 0}


# ── rozklad ────────────────────────────────────────────────────────────────

def test_combo_event_splits_with_correct_qty(db):
    """Kombo event → správny počet riadkov so správnymi qty a catalog_id."""
    migrate(db)
    rows = meds(db, 1)
    assert len(rows) == 3, rows
    assert [r["catalog_id"] for r in rows] == [8, 16, 17], rows
    assert [r["qty"] for r in rows] == [3.0, 0.5, 0.25], rows
    assert all(r["unit"] == "tableta" for r in rows), rows


def test_milligrams_keep_unit_mg(db):
    migrate(db)
    r = meds(db, 3)
    assert len(r) == 1
    assert (r[0]["qty"], r[0]["unit"]) == (600.0, "mg"), r[0]


def test_four_meds_event(db):
    migrate(db)
    rows = meds(db, 2)
    assert len(rows) == 3, [r["raw_name"] for r in rows]
    assert [r["catalog_id"] for r in rows] == [16, 12, 13], rows


# ── status ─────────────────────────────────────────────────────────────────

def test_negative_marker_is_neznamy_with_note(db):
    """Negatívny marker → 'neznamy' + status_note, NIKDY 'podane'."""
    migrate(db)
    rows = meds(db, 4)
    assert rows and all(r["status"] == "neznamy" for r in rows), rows
    assert all(r["status_note"] for r in rows), rows
    assert "zabudla" in rows[0]["status_note"], rows[0]["status_note"]


def test_plain_events_are_podane(db):
    migrate(db)
    for eid in (1, 2, 3):
        assert all(r["status"] == "podane" for r in meds(db, eid)), eid
        assert all(r["status_note"] is None for r in meds(db, eid)), eid


# ── katalóg a source ───────────────────────────────────────────────────────

def test_supplement_without_catalog_keeps_raw_name(db):
    """Doplnok mimo katalógu → catalog_id NULL, raw_name zachovaný."""
    migrate(db)
    rows = meds(db, 5)
    assert len(rows) == 2, rows
    assert all(r["catalog_id"] is None for r in rows), rows
    assert [r["raw_name"] for r in rows] == ["probiotikum", "glycín"], rows


def test_source_is_always_migracia(db):
    migrate(db)
    assert all(r["source"] == SOURCE for r in meds(db)), meds(db)


def test_non_med_events_are_ignored(db):
    """Event typu 'nalada' nesmie dostať žiadny riadok."""
    migrate(db)
    assert meds(db, 99) == []


# ── idempotencia ───────────────────────────────────────────────────────────

def test_second_run_inserts_nothing(db):
    """Druhý beh vloží 0 riadkov a nič nezdvojí."""
    first = migrate(db)
    n1 = len(meds(db))
    assert first["inserted"] == n1 and n1 > 0

    rows2, skipped2 = plan(db)
    assert rows2 == [], rows2
    assert len(skipped2) == len(EVENTS), skipped2

    second = migrate(db)
    assert second["inserted"] == 0, second
    assert len(meds(db)) == n1, "druhý beh zdvojil riadky"


def test_manually_edited_event_is_skipped(db):
    """Event s ručne pridaným riadkom (iný source) sa preskočí a NEPREPÍŠE."""
    c = sqlite3.connect(db)
    c.execute("INSERT INTO event_meds (event_id, catalog_id, raw_name, qty, unit, "
              "status, source, created_at) VALUES (1, 8, 'ručne zadané', 1, 'tableta', "
              "'podane', 'manual', '2026-01-01')")
    c.commit()
    c.close()

    rows, skipped = plan(db)
    assert all(r["event_id"] != 1 for r in rows), "event s ručnou úpravou sa migruje!"
    assert any(s["event_id"] == 1 for s in skipped), skipped

    migrate(db)
    ev1 = meds(db, 1)
    assert len(ev1) == 1, ev1
    assert ev1[0]["source"] == "manual" and ev1[0]["raw_name"] == "ručne zadané", ev1


def test_apply_aborts_when_rows_appear_midway(db):
    """Ak event medzitým dostane riadky, celý zápis sa zruší (rollback)."""
    rows, _ = plan(db)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO event_meds (event_id, raw_name, status, source, created_at) "
              "VALUES (3, 'medzitým', 'podane', 'manual', '2026-01-01')")
    c.commit()
    c.close()
    try:
        apply_rows(db, rows)
        raise AssertionError("apply NEMAL prejsť — event #3 medzitým dostal riadky")
    except AssertionError:
        raise
    except Exception:
        pass
    assert len(meds(db)) == 1, "rollback nezafungoval, zapísalo sa niečo navyše"


def test_apply_counts_match(db):
    res = migrate(db)
    assert res["source_after"] - res["source_before"] == res["inserted"]
    assert res["total_after"] - res["total_before"] == res["inserted"]


# ── beh bez pytestu ────────────────────────────────────────────────────────

def _main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        with temp_db() as path:
            try:
                fn(path)
                print(f"PASS  {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} prešlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
