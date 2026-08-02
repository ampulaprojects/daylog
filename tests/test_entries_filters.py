"""
Testy server-side filtrov záznamov (dátum, typ eventu, triedenie) + súhry s
count_entries a stránkovaním. Testuje DB vrstvu (get_entries / count_entries /
_entries_where), kde filtre žijú; HTTP endpoint sa neskúša (API testy nemáme).

Hlavné riziká, ktoré tu overujeme:
  - EXISTS vs JOIN: záznam s viacerými eventmi toho istého typu sa vráti RAZ
  - count_entries musí dať to isté číslo ako get_entries bez limitu (inak
    total/has_more klame)
  - e.id DESC chvost drží stabilitu stránkovania v OBOCH režimoch triedenia

Vlastná dočasná DB (tempfile) — produkcie ani daylog.db sa nedotýka.

Spusti:   pytest tests/test_entries_filters.py -v
alebo:    python tests/test_entries_filters.py
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
from database import get_entries, count_entries

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn


# rows: zoznam dictov {entry_date, created_at, entry_time, types:[...], text}
# id sa priradí automaticky 1..N v poradí vloženia.
def _seed(path, rows):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    for i, r in enumerate(rows, start=1):
        c.execute("INSERT INTO entries (user_id, created_at, entry_date, entry_time, text) "
                  "VALUES (1, ?, ?, ?, ?)",
                  (r.get("created_at", f"2026-01-01T00:00:{i:02d}"),
                   r["entry_date"], r.get("entry_time"), r.get("text", f"z{i}")))
        eid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        for t in r.get("types", []):
            c.execute("INSERT INTO events (entry_id, user_id, event_type, value, created_at) "
                      "VALUES (?, 1, ?, ?, '2026-01-01')", (eid, t, t))
    c.commit()
    c.close()


@contextmanager
def temp_db(rows):
    d = tempfile.mkdtemp(prefix="daylog_test_")
    path = os.path.join(d, "test.db")
    old = database.DB_PATH
    database.DB_PATH = path
    try:
        database.init_db()
        _seed(path, rows)
        yield path
    finally:
        database.DB_PATH = old
        shutil.rmtree(d, ignore_errors=True)


def ids(**kw):
    kw.setdefault("limit", 10000)
    return [e["id"] for e in get_entries(**kw)]


# Hlavná fixtúra: dátumy, created_at (líši sa od entry_date), typy.
FIX = [
    {"entry_date": "2026-06-25", "created_at": "2026-08-01T10:00:00", "types": ["spatok"]},        # id1
    {"entry_date": "2026-07-01", "created_at": "2026-07-01T09:00:00", "types": ["liek", "liek"]},  # id2 (dup typ)
    {"entry_date": "2026-07-05", "created_at": "2026-07-10T09:00:00", "types": ["nalada"]},        # id3
    {"entry_date": "2026-07-10", "created_at": "2026-07-02T09:00:00", "types": ["liek", "jedlo"]}, # id4 (multi typ)
    {"entry_date": "2026-07-20", "created_at": "2026-07-20T09:00:00", "types": []},                # id5 (bez eventov)
    {"entry_date": "2026-07-31", "created_at": "2026-06-01T09:00:00", "types": ["liek"]},          # id6
]


# ── 1. dátumový filter, INKLUZÍVNE hranice ───────────────────────────────────

def test_date_range_inclusive():
    with temp_db(FIX):
        # 07-01..07-10 → id2 (07-01, hranica), id3 (07-05), id4 (07-10, hranica)
        assert set(ids(date_from="2026-07-01", date_to="2026-07-10")) == {2, 3, 4}
        # krajné dni musia byť vnútri
        assert 2 in ids(date_from="2026-07-01")          # od-hranica inkluzívna
        assert 4 in ids(date_to="2026-07-10")            # do-hranica inkluzívna
        assert set(ids(date_from="2026-07-31")) == {6}
        assert set(ids(date_to="2026-06-25")) == {1}


# ── 2. typ eventu: EXISTS → žiadny duplikát ──────────────────────────────────

def test_type_filter_no_duplicate():
    with temp_db(FIX):
        got = ids(types=["liek"])
        # liek majú id2 (dvakrát liek!), id4, id6
        assert set(got) == {2, 4, 6}, got
        assert len(got) == len(set(got)), f"duplikát (EXISTS zlyhal): {got}"


# ── 3. viac typov = OR ───────────────────────────────────────────────────────

def test_multiple_types_or():
    with temp_db(FIX):
        assert set(ids(types=["liek", "nalada"])) == {2, 3, 4, 6}
        assert set(ids(types=["jedlo", "spatok"])) == {1, 4}


# ── 4. count_entries == počet riadkov bez limitu (každá kombinácia) ──────────

def test_count_matches_rows_all_combos():
    with temp_db(FIX):
        combos = [
            {},
            {"date_from": "2026-07-01", "date_to": "2026-07-10"},
            {"types": ["liek"]},
            {"search": "z"},                       # všetky texty obsahujú 'z'
            {"date_from": "2026-07-01", "date_to": "2026-07-31",
             "types": ["liek"], "search": "z"},
        ]
        for kw in combos:
            n_rows = len(ids(**kw))
            n_count = count_entries(**kw)
            assert n_count == n_rows, f"count {n_count} != rows {n_rows} pre {kw}"


# ── 5. stránkovanie nad filtrom: bez prekryvu, súčet = total ─────────────────

def test_paging_within_filter():
    with temp_db(FIX):
        kw = {"types": ["liek"]}                    # 3 záznamy: id2,4,6
        total = count_entries(**kw)
        assert total == 3
        p0 = ids(limit=2, offset=0, **kw)
        p1 = ids(limit=2, offset=2, **kw)
        assert len(p0) == 2 and len(p1) == 1
        assert set(p0) & set(p1) == set(), "strany sa prekrývajú"
        assert set(p0) | set(p1) == {2, 4, 6}
        assert len(p0) + len(p1) == total


# ── 6. sort event vs created; oba stabilné pri identických hodnotách ─────────

def test_sort_event_vs_created():
    with temp_db(FIX):
        ev = ids(sort="event")
        cr = ids(sort="created")
        assert ev == [6, 5, 4, 3, 2, 1], ev          # podľa entry_date DESC
        assert cr == [1, 5, 3, 4, 2, 6], cr          # podľa created_at DESC
        assert ev != cr, "triedenia sa musia líšiť tam, kde sa dátumy líšia"


def test_sort_stable_on_identical_values():
    """Tri záznamy s IDENTICKÝM entry_date, entry_time aj created_at → oba
    režimy triedenia musia dať deterministické poradie (id DESC chvost)."""
    same = [
        {"entry_date": "2026-07-01", "entry_time": "08:00", "created_at": "2026-07-01T08:00:00"},
        {"entry_date": "2026-07-01", "entry_time": "08:00", "created_at": "2026-07-01T08:00:00"},
        {"entry_date": "2026-07-01", "entry_time": "08:00", "created_at": "2026-07-01T08:00:00"},
    ]
    with temp_db(same):
        assert ids(sort="event") == [3, 2, 1]
        assert ids(sort="created") == [3, 2, 1]


# ── 7. neznámy sort / prázdne types → ako bez filtra, žiadny pád ─────────────

def test_unknown_sort_and_empty_types():
    with temp_db(FIX):
        assert ids(sort="bogus") == ids(sort="event")   # neznámy → default
        assert ids(types=[]) == ids()                   # prázdny zoznam = bez filtra
        assert ids(types=None) == ids()


# ── beh bez pytestu ──────────────────────────────────────────────────────────

def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} prešlo")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
