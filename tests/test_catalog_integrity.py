"""
Testy integrity katalógu (Blok 1): mazanie použitej položky, zlúčenie
vrátane med_schedule, a dry-run opravného skriptu.

Každý test si robí VLASTNÚ dočasnú DB (tempfile) — daylog.db ani produkcie
sa nedotýka.

Spusti:   pytest tests/test_catalog_integrity.py -v
alebo:    python tests/test_catalog_integrity.py
"""
import os
import sys
import shutil
import sqlite3
import tempfile
import subprocess
from contextlib import contextmanager

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import database
from database import (delete_catalog_item, merge_catalog_items,
                      CatalogInUseError, get_db, _orphan_counts)

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn

# id, ktoré zodpovedajú ORPHAN_MAP = {1: 8} vo fix_orphan_events.py
CAT_OLD, CAT_KEEP, CAT_TARGET = 1, 2, 8


def _seed(path):
    """Prázdna schéma + minimum riadkov: 1 user, 1 entry, 3 položky katalógu."""
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    c.execute("INSERT INTO entries (id, user_id, created_at, entry_date, text) "
              "VALUES (1, 1, '2026-01-01', '2026-01-01', 'test')")
    for cid, name in ((CAT_OLD, 'Orfiril'), (CAT_KEEP, 'Tisercin'), (CAT_TARGET, 'Orfiril long')):
        c.execute("INSERT INTO med_catalog (id, canonical_name, created_at, updated_at) "
                  "VALUES (?, ?, '2026-01-01', '2026-01-01')", (cid, name))
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


def add_event(catalog_id, n=1):
    c = get_db()
    for _ in range(n):
        c.execute("INSERT INTO events (entry_id, user_id, event_time, event_type, value, "
                  "catalog_id, created_at) VALUES (1, 1, '08:00', 'liek', 'x', ?, '2026-01-01')",
                  (catalog_id,))
    c.commit()
    c.close()


def add_schedule(catalog_id, n=1):
    c = get_db()
    for _ in range(n):
        c.execute("INSERT INTO med_schedule (name, kind, catalog_id, created_at, updated_at) "
                  "VALUES ('x', 'liek', ?, '2026-01-01', '2026-01-01')", (catalog_id,))
    c.commit()
    c.close()


def exists(catalog_id):
    c = get_db()
    row = c.execute("SELECT 1 FROM med_catalog WHERE id=?", (catalog_id,)).fetchone()
    c.close()
    return row is not None


# ── mazanie ────────────────────────────────────────────────────────────────

def test_delete_blocked_by_events(db):
    """Položku, na ktorú odkazujú eventy, nemožno zmazať — a ostane v DB."""
    add_event(CAT_KEEP, n=3)
    try:
        delete_catalog_item(CAT_KEEP)
        raise AssertionError("delete NEMAL prejsť — na položku odkazujú eventy")
    except CatalogInUseError as e:
        assert e.events == 3, e.events
        assert e.schedules == 0, e.schedules
    assert exists(CAT_KEEP), "položka po zablokovanom delete zmizla z DB"


def test_delete_blocked_by_schedule(db):
    """Blokuje aj samotná položka režimu (bez jediného eventu)."""
    add_schedule(CAT_KEEP, n=1)
    try:
        delete_catalog_item(CAT_KEEP)
        raise AssertionError("delete NEMAL prejsť — odkazuje naň režim")
    except CatalogInUseError as e:
        assert e.events == 0, e.events
        assert e.schedules == 1, e.schedules
    assert exists(CAT_KEEP)


def test_delete_unused_ok(db):
    """Nepoužitú položku možno zmazať ako doteraz."""
    assert exists(CAT_KEEP)
    delete_catalog_item(CAT_KEEP)
    assert not exists(CAT_KEEP), "nepoužitá položka sa nezmazala"


# ── zlúčenie ───────────────────────────────────────────────────────────────

def test_merge_moves_schedule_and_events(db):
    """Merge prepojí events AJ med_schedule z B na A a nenechá osirené odkazy."""
    add_event(CAT_KEEP, n=3)
    add_schedule(CAT_KEEP, n=2)
    summary = merge_catalog_items(keep_id=CAT_TARGET, merge_id=CAT_KEEP)

    assert summary["moved_events"] == 3, summary
    assert summary["moved_schedules"] == 2, summary

    c = get_db()
    left_ev = c.execute("SELECT COUNT(*) FROM events WHERE catalog_id=?", (CAT_KEEP,)).fetchone()[0]
    left_sch = c.execute("SELECT COUNT(*) FROM med_schedule WHERE catalog_id=?", (CAT_KEEP,)).fetchone()[0]
    moved_sch = c.execute("SELECT COUNT(*) FROM med_schedule WHERE catalog_id=?", (CAT_TARGET,)).fetchone()[0]
    assert (left_ev, left_sch) == (0, 0), (left_ev, left_sch)
    assert moved_sch == 2, moved_sch
    assert not exists(CAT_KEEP), "zlúčená položka B sa nezmazala"
    c.close()


def test_no_orphans_after_merge(db):
    """Po zlúčení je 0 osirených odkazov v events aj med_schedule."""
    add_event(CAT_KEEP, n=2)
    add_schedule(CAT_KEEP, n=1)
    merge_catalog_items(keep_id=CAT_TARGET, merge_id=CAT_KEEP)

    c = get_db()
    ev_orphans, sch_orphans = _orphan_counts(c.cursor())
    c.close()
    assert ev_orphans == 0, f"osirené events: {ev_orphans}"
    assert sch_orphans == 0, f"osirené med_schedule: {sch_orphans}"


# ── opravný skript ─────────────────────────────────────────────────────────

def test_dry_run_writes_nothing(db):
    """fix_orphan_events.py bez --apply nájde osirené, ale NIČ nezapíše."""
    add_event(CAT_OLD, n=2)          # budú osirené (mapované 1 → 8)
    add_event(999, n=1)              # neznáme catalog_id — nemá sa meniť
    c = get_db()
    c.execute("DELETE FROM med_catalog WHERE id=?", (CAT_OLD,))   # zámerne holý DELETE
    c.commit()
    c.close()

    r = subprocess.run([sys.executable, "-X", "utf8",
                        os.path.join(BASE, "fix_orphan_events.py"), "--db", db],
                       capture_output=True, text=True, encoding="utf-8")
    out = r.stdout
    assert r.returncode == 0, r.stderr
    assert "Osirených eventov spolu: 3" in out, out
    assert "v ORPHAN_MAP: 2" in out, out
    assert "neznámych: 1" in out, out
    assert "NIČ nezapísalo" in out, out

    c = get_db()
    still = c.execute("SELECT COUNT(*) FROM events WHERE catalog_id=?", (CAT_OLD,)).fetchone()[0]
    unknown = c.execute("SELECT COUNT(*) FROM events WHERE catalog_id=999").fetchone()[0]
    c.close()
    assert still == 2, f"dry-run zmenil dáta! ostalo {still} namiesto 2"
    assert unknown == 1


# ── beh bez pytestu ────────────────────────────────────────────────────────

def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        with temp_db() as path:
            try:
                fn(path)
                print(f"PASS  {fn.__name__}")
            except Exception as e:
                failed += 1
                print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} prešlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
