"""
Testy tabuľky event_meds a parsera liekových eventov (Blok 3A).

Každý test si robí VLASTNÚ dočasnú DB (tempfile) — daylog.db ani produkcie
sa nedotýka. Parser je read-only, testuje sa priamo volaním funkcií.

Spusti:   pytest tests/test_event_meds.py -v
alebo:    python tests/test_event_meds.py
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
from database import get_db
from parse_med_events import (split_meds, parse_qty, detect_status,
                              match_catalog, parse_event, load_catalog)

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn


def _seed(path):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    c.execute("INSERT INTO entries (id, user_id, created_at, entry_date, text) "
              "VALUES (1, 1, '2026-01-01', '2026-01-01', 'test')")
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


def catalog_of(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    cat = load_catalog(c)
    c.close()
    return cat


def add_event(entry_id=1):
    c = get_db()
    cur = c.execute("INSERT INTO events (entry_id, user_id, event_time, event_type, value, "
                    "created_at) VALUES (?, 1, '08:00', 'liek', 'x', '2026-01-01')", (entry_id,))
    eid = cur.lastrowid
    c.commit()
    c.close()
    return eid


def add_meds(event_id, n=2):
    c = get_db()
    for i in range(n):
        c.execute("INSERT INTO event_meds (event_id, raw_name, status, source, created_at) "
                  "VALUES (?, ?, 'podane', 'migracia', '2026-01-01')", (event_id, f"liek{i}"))
    c.commit()
    c.close()


def count_meds(event_id):
    c = get_db()
    n = c.execute("SELECT COUNT(*) FROM event_meds WHERE event_id=?", (event_id,)).fetchone()[0]
    c.close()
    return n


# ── schéma ─────────────────────────────────────────────────────────────────

def test_init_db_is_idempotent(db):
    """Opakované init_db() nesmie spadnúť ani zduplikovať tabuľku/indexy."""
    database.init_db()
    database.init_db()
    c = get_db()
    cols = [r[1] for r in c.execute("PRAGMA table_info(event_meds)")]
    idx = [r[1] for r in c.execute("PRAGMA index_list(event_meds)")]
    c.close()
    for col in ("id", "event_id", "catalog_id", "raw_name", "qty", "unit",
                "status", "status_note", "source", "created_at"):
        assert col in cols, f"chýba stĺpec {col}: {cols}"
    assert any("event" in i for i in idx), idx
    assert any("catalog" in i for i in idx), idx


def test_cascade_delete_removes_event_meds(db):
    """Zmazanie eventu zmaže jeho event_meds.

    POZOR: ON DELETE CASCADE funguje LEN s PRAGMA foreign_keys ON, ktorú
    v produkčnom runtime zatiaľ NEZAPÍNAME (get_db() ju nenastavuje) —
    rieši sa v Bloku 3C. Tu ju zapíname explicitne, aby sme overili, že
    samotná definícia FK je správna.
    """
    eid = add_event()
    add_meds(eid, n=3)
    assert count_meds(eid) == 3

    c = get_db()
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("DELETE FROM events WHERE id = ?", (eid,))
    c.commit()
    c.close()
    assert count_meds(eid) == 0, "event_meds prežili zmazanie eventu"


def test_cascade_does_not_fire_without_pragma(db):
    """Dokumentuje SÚČASNÝ stav: bez PRAGMA foreign_keys osirejú riadky.

    Tento test nie je "želaný stav" — je to dôkaz, prečo Blok 3C treba.
    """
    eid = add_event()
    add_meds(eid, n=2)
    c = get_db()                      # bez PRAGMA foreign_keys, ako v appke
    c.execute("DELETE FROM events WHERE id = ?", (eid,))
    c.commit()
    c.close()
    assert count_meds(eid) == 2, "cascade zafungovala aj bez PRAGMA — zmeň komentár v 3C"


# ── parser: rozklad na lieky ───────────────────────────────────────────────

def test_split_combo_event(db):
    """Kombo event sa rozloží na správny počet riadkov."""
    cat = catalog_of(db)
    rows, _ = parse_event("3× Ofriril, 1/2 Tisercin, 1/4 Fevarin", None, cat, source="migracia")
    assert len(rows) == 3, [r["raw_name"] for r in rows]
    assert [r["catalog_id"] for r in rows] == [8, 16, 17], rows


def test_comma_before_dose_is_not_a_second_med(db):
    """Čiarka oddeľujúca DÁVKU od názvu nesmie vyrobiť druhý liek."""
    cat = catalog_of(db)
    rows, flags = parse_event("3x Orfiril, 1x 1/2 Tisercin", None, cat, source="migracia")
    assert len(rows) == 2, [r["raw_name"] for r in rows]

    rows2, flags2 = parse_event("Orfiril, 1/2", None, cat, source="migracia")
    assert len(rows2) == 1, [r["raw_name"] for r in rows2]
    assert any("čiarka" in f for f in flags2), flags2


def test_single_med_stays_single(db):
    cat = catalog_of(db)
    rows, _ = parse_event("1/2 Tisercin", None, cat, source="migracia")
    assert len(rows) == 1 and rows[0]["catalog_id"] == 16


# ── parser: množstvo a jednotka ────────────────────────────────────────────

def test_qty_fraction():
    qty, unit, how = parse_qty("1/2 Tisercin")
    assert qty == 0.5 and unit == "tableta", (qty, unit)
    assert parse_qty("1/4 Fevarin")[0] == 0.25


def test_qty_multiplier():
    assert parse_qty("3× Orfiril")[:2] == (3.0, "tableta")
    assert parse_qty("3x Orfiril")[:2] == (3.0, "tableta")


def test_qty_milligrams():
    qty, unit, _ = parse_qty("600 mg Orfiril")
    assert (qty, unit) == (600.0, "mg"), (qty, unit)
    assert parse_qty("300mg Orfiril")[:2] == (300.0, "mg")


def test_qty_unknown_is_null_not_guessed():
    """Keď sa množstvo nedá určiť, qty je NULL a unit sa NEHÁDA."""
    qty, unit, _ = parse_qty("Probiotikum")
    assert qty is None, qty
    assert unit is None, unit


def test_qty_unicode_fraction():
    assert parse_qty("½ Tisercin")[0] == 0.5


# ── parser: status ─────────────────────────────────────────────────────────

def test_negative_marker_gives_unknown_status():
    """Negatívny marker → 'neznamy', NIKDY nie 'podane'."""
    for txt in ("Tisercin v tomto čase nedostal",
                "Poobedné vitamíny vynechané",
                "liek nebol podaný včas, zabudla sesterička"):
        status, note = detect_status(txt, None)
        assert status == "neznamy", (txt, status)
        assert note == txt, (txt, note)


def test_negative_marker_in_note_only(db):
    """Marker stačí v note — status sa nesmie odvodiť len z value."""
    cat = catalog_of(db)
    rows, flags = parse_event("1/2 Tisercin", "zabudla podať sesterička", cat, source="migracia")
    assert all(r["status"] == "neznamy" for r in rows), rows
    assert rows[0]["status_note"], rows[0]


def test_plain_event_is_podane(db):
    cat = catalog_of(db)
    rows, _ = parse_event("3x Orfiril", None, cat, source="migracia")
    assert all(r["status"] == "podane" for r in rows)
    assert all(r["status_note"] is None for r in rows)


# ── parser: homoglyfy a raw_name ───────────────────────────────────────────

def test_homoglyph_matches_but_raw_name_kept(db):
    """Cyrilika sa normalizuje PRI PÁROVANÍ, raw_name zostáva pôvodný."""
    cat = catalog_of(db)
    rows, flags = parse_event("1/2 Tisercinу", None, cat, source="migracia")      # 'у' = U+0443
    assert rows[0]["catalog_id"] == 16, rows
    assert "у" in rows[0]["raw_name"], rows[0]["raw_name"]
    assert any("homoglyf" in f for f in flags), flags


def test_source_is_migracia(db):
    cat = catalog_of(db)
    rows, _ = parse_event("3x Orfiril", None, cat, source="migracia")
    assert all(r["source"] == "migracia" for r in rows)


def test_unknown_supplement_gets_null_catalog_id(db):
    """Doplnok mimo katalógu nesmie zablokovať rozklad — catalog_id = NULL."""
    cat = catalog_of(db)
    rows, _ = parse_event("2x Magnetit, 1x Karnozin", None, cat, source="migracia")
    assert len(rows) == 2
    assert all(r["catalog_id"] is None for r in rows), rows
    assert all(r["raw_name"] for r in rows), "raw_name je povinný"


def test_short_name_is_not_swallowed_as_dose(db):
    """Krátky názov (B6, C, Mg) je LIEK, nie dávka predchádzajúceho lieku.

    Regresia: pôvodné pravidlo "segment bez 3-písmenového slova = dávka"
    ticho zlúčilo '1× B6' s predchádzajúcim liekom a B6 zmizol.
    """
    cat = catalog_of(db)
    rows, _ = parse_event("2x Magnetit, 1x B6", None, cat, source="migracia")
    assert len(rows) == 2, [r["raw_name"] for r in rows]
    assert rows[1]["raw_name"] == "1x B6", rows[1]
    assert rows[1]["qty"] == 1.0

    rows2, _ = parse_event("1/2 Tisercin, 2x Magnetit, 1x Karnozin, 1x B6", None, cat, source="migracia")
    assert len(rows2) == 4, [r["raw_name"] for r in rows2]


def test_dose_only_segment_still_merges(db):
    """Segment bez akéhokoľvek písmena zostáva dávkou predošlého lieku."""
    from parse_med_events import is_dose_only
    assert is_dose_only("1/2") and is_dose_only("3x") and is_dose_only("600 mg")
    assert not is_dose_only("1x B6") and not is_dose_only("Mg") and not is_dose_only("1x D K3")


def test_short_variant_matches_only_whole_word(db):
    """2-znakový alias (B6) sa páruje len ako celé slovo, nie ako podreťazec.

    Regresia: load_catalog() krátke varianty zahadzoval → alias by sa zapísal
    do katalógu a ticho by nefungoval.
    """
    from parse_med_events import variant_matches
    assert variant_matches("b6", "1x b6")
    assert variant_matches("b6", "b6")
    assert not variant_matches("b6", "1x b60")
    assert not variant_matches("b6", "ab6")
    # Dôvod, prečo sa krátke aliasy nesmú párovať ako podreťazec: 'mg' by inak
    # chytalo hmotnosť. Ako celé slovo ho chytí — preto sa 'mg' ako alias
    # zámerne NEPRIDÁVA (viď DISPUTED v add_aliases.py).
    assert variant_matches("mg", "600 mg orfiril")
    # dlhý variant sa naďalej páruje ako podreťazec
    assert variant_matches("tisercin", "1/2 tisercinu")


# ── beh bez pytestu ────────────────────────────────────────────────────────

def _main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        needs_db = fn.__code__.co_argcount > 0
        try:
            if needs_db:
                with temp_db() as path:
                    fn(path)
            else:
                fn()
            print(f"PASS  {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} prešlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
