"""
Testy živého zápisu do event_meds (Blok 3C): každý liekový event vzniknutý cez
create_entry_with_events (source='confirm') alebo update_entry_with_events
(source='edit') dostane v TEJ ISTEJ transakcii rozložené riadky v event_meds.
Editácia navyše najprv zmaže staré event_meds (PRAGMA foreign_keys je OFF,
ON DELETE CASCADE nefunguje) — nesmú vzniknúť osirené riadky.

Každý test si robí VLASTNÚ dočasnú DB (tempfile) — daylog.db ani produkcie
sa nedotýka.

Spusti:   pytest tests/test_live_event_meds.py -v
alebo:    python tests/test_live_event_meds.py
"""
import os
import sys
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import database
from database import (create_entry_with_events, update_entry_with_events,
                      delete_entry, get_db)

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn

USER_ID = 1

# Event, ktorý zaručene zhodí INSERT (dict sa nedá naviazať ako SQL parameter) —
# rovnaký princíp ako v test_entry_atomicity. Slúži na overenie rollbacku.
BAD_EVENT = {"event_time": "09:00", "event_type": "liek", "value": {"nedá": "sa"},
             "note": None, "catalog_id": None}


def _ev(value, time="08:00", etype="liek", note=None, catalog_id=None):
    return {"event_time": time, "event_type": etype, "value": value,
            "note": note, "catalog_id": catalog_id}


def _seed(path):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    for cid, name, aliases in ((8, 'Orfiril long', '["Orfiril", "Ofriril"]'),
                               (16, 'TISERCIN', '["Tisercin", "Tisercinu"]'),
                               (17, 'Fevarin', '[]')):
        c.execute("INSERT INTO med_catalog (id, canonical_name, aliases, created_at, updated_at) "
                  "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')", (cid, name, aliases))
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


# ── dopytové pomôcky ───────────────────────────────────────────────────────

def event_ids(entry_id):
    c = get_db()
    rows = c.execute("SELECT id FROM events WHERE entry_id=? ORDER BY id",
                     (entry_id,)).fetchall()
    c.close()
    return [r[0] for r in rows]


def meds_for_event(event_id):
    c = get_db()
    rows = c.execute("SELECT * FROM event_meds WHERE event_id=? ORDER BY id",
                     (event_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def meds_for_entry(entry_id):
    c = get_db()
    rows = c.execute(
        "SELECT em.* FROM event_meds em JOIN events e ON em.event_id = e.id "
        "WHERE e.entry_id=? ORDER BY em.id", (entry_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def total_meds():
    c = get_db()
    n = c.execute("SELECT COUNT(*) FROM event_meds").fetchone()[0]
    c.close()
    return n


def orphan_meds():
    c = get_db()
    n = c.execute("SELECT COUNT(*) FROM event_meds em "
                  "LEFT JOIN events e ON em.event_id = e.id "
                  "WHERE e.id IS NULL").fetchone()[0]
    c.close()
    return n


def entry_event_counts():
    c = get_db()
    en = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    ev = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    c.close()
    return en, ev


# ── 1. confirm multi-liek ──────────────────────────────────────────────────

def test_confirm_multi_med_writes_two_rows(db):
    """confirm s "3× Orfiril, 1/2 Tisercin" → 2 riadky event_meds, source='confirm'."""
    entry_id = create_entry_with_events(
        entry_date="2026-01-01", text="ranné lieky",
        events=[_ev("3× Orfiril, 1/2 Tisercin")], user_id=USER_ID)

    eids = event_ids(entry_id)
    assert len(eids) == 1, eids
    meds = meds_for_event(eids[0])
    assert len(meds) == 2, [m["raw_name"] for m in meds]
    assert all(m["source"] == "confirm" for m in meds), meds
    assert all(m["raw_name"] for m in meds), "raw_name je povinný"
    assert [m["qty"] for m in meds] == [3.0, 0.5], meds
    # katalóg spárovaný (Orfiril→8, Tisercin→16)
    assert [m["catalog_id"] for m in meds] == [8, 16], meds
    assert orphan_meds() == 0


# ── 2. ne-liekový event ────────────────────────────────────────────────────

def test_non_med_event_writes_no_rows(db):
    """Ne-liekový event (nálada) nesmie vyrobiť žiadny riadok event_meds."""
    entry_id = create_entry_with_events(
        entry_date="2026-01-01", text="nálada",
        events=[_ev("dobrá nálada", etype="nalada")], user_id=USER_ID)
    assert meds_for_entry(entry_id) == []
    assert total_meds() == 0


# ── 3. editácia vymení event_meds ──────────────────────────────────────────

def test_edit_replaces_event_meds(db):
    """Edit: staré event_meds zmazané, nové so source='edit', 0 osirených."""
    entry_id = create_entry_with_events(
        entry_date="2026-01-01", text="pôvodné",
        events=[_ev("3x Orfiril")], user_id=USER_ID)
    old_eid = event_ids(entry_id)[0]
    assert len(meds_for_event(old_eid)) == 1
    assert meds_for_event(old_eid)[0]["source"] == "confirm"

    update_entry_with_events(entry_id, USER_ID, "nový text",
                             [_ev("1/2 Tisercin")])

    # starý event aj jeho event_meds sú preč
    assert meds_for_event(old_eid) == [], "staré event_meds neboli zmazané"
    new_meds = meds_for_entry(entry_id)
    assert len(new_meds) == 1, new_meds
    assert new_meds[0]["source"] == "edit", new_meds[0]
    assert new_meds[0]["catalog_id"] == 16, new_meds[0]
    assert orphan_meds() == 0, "vznikli osirené event_meds"


# ── 4. editácia, ktorá liekový event odstráni ──────────────────────────────

def test_edit_removing_med_event_clears_meds(db):
    """Edit, ktorý liekový event odstráni → 0 riadkov event_meds, 0 osirených."""
    entry_id = create_entry_with_events(
        entry_date="2026-01-01", text="pôvodné",
        events=[_ev("3x Orfiril")], user_id=USER_ID)
    assert len(meds_for_entry(entry_id)) == 1

    update_entry_with_events(entry_id, USER_ID, "už bez liekov", [])

    assert meds_for_entry(entry_id) == []
    assert total_meds() == 0
    assert orphan_meds() == 0


# ── 5. rollback nezapíše nič ────────────────────────────────────────────────

def test_rollback_writes_no_event_meds(db):
    """Zlyhanie uprostred zápisu → v event_meds nepribudne NIČ (ani z 1. eventu)."""
    try:
        create_entry_with_events(
            entry_date="2026-01-01", text="pokus",
            events=[_ev("3x Orfiril"), BAD_EVENT], user_id=USER_ID)
        raise AssertionError("zápis NEMAL prejsť — 2. event je neplatný")
    except AssertionError:
        raise
    except Exception:
        pass
    assert total_meds() == 0, "event_meds prežili rollback"
    assert entry_event_counts() == (0, 0), entry_event_counts()


# ── 6. negatívny marker v note ─────────────────────────────────────────────

def test_negative_marker_in_note_sets_unknown(db):
    """Negatívny marker v note → status='neznamy', status_note vyplnený."""
    entry_id = create_entry_with_events(
        entry_date="2026-01-01", text="Tisercin",
        events=[_ev("1/2 Tisercin", note="Tisercin nedostal")], user_id=USER_ID)
    meds = meds_for_entry(entry_id)
    assert len(meds) == 1, meds
    assert meds[0]["status"] == "neznamy", meds[0]
    assert meds[0]["status_note"], "status_note má byť vyplnený"


# ── 7. liek mimo katalógu ──────────────────────────────────────────────────

def test_med_outside_catalog_gets_null_catalog_id(db):
    """Liek mimo katalógu → catalog_id NULL, raw_name zachovaný."""
    entry_id = create_entry_with_events(
        entry_date="2026-01-01", text="doplnok",
        events=[_ev("2x Magnetit")], user_id=USER_ID)
    meds = meds_for_entry(entry_id)
    assert len(meds) == 1, meds
    assert meds[0]["catalog_id"] is None, meds[0]
    assert meds[0]["raw_name"] == "2x Magnetit", meds[0]
    assert meds[0]["source"] == "confirm", meds[0]


# ── 8. delete_entry zmaže aj event_meds ────────────────────────────────────

def test_delete_entry_removes_event_meds(db):
    """delete_entry → event_meds jeho eventov zmazané, 0 osirených; iný záznam
    a jeho event_meds ostávajú nedotknuté."""
    keep_id = create_entry_with_events(
        entry_date="2026-01-01", text="ostáva",
        events=[_ev("3x Orfiril")], user_id=USER_ID)
    drop_id = create_entry_with_events(
        entry_date="2026-01-02", text="na zmazanie",
        events=[_ev("3× Orfiril, 1/2 Tisercin")], user_id=USER_ID)
    assert len(meds_for_entry(drop_id)) == 2
    keep_meds_before = meds_for_entry(keep_id)
    assert len(keep_meds_before) == 1

    delete_entry(drop_id)

    assert meds_for_entry(drop_id) == [], "event_meds zmazaného záznamu prežili"
    assert orphan_meds() == 0, "vznikli osirené event_meds"
    # druhý záznam a jeho event_meds sa nezmenili
    assert meds_for_entry(keep_id) == keep_meds_before
    assert event_ids(drop_id) == []
    assert event_ids(keep_id), "eventy zachovaného záznamu zmizli"


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
