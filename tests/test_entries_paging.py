"""
Testy stránkovania zoznamu záznamov (offset) — jadro funkcie "Načítať staršie".

Testuje DB vrstvu (get_entries s offset, count_entries), kde stránkovanie žije;
HTTP endpoint sa neskúša (API testy zatiaľ nemáme). Kľúčová je STABILITA: pri
zhodnom entry_date aj entry_time rozhoduje e.id DESC — bez neho by sa cez offset
záznamy opakovali alebo vypadávali. Seed preto dáva VŠETKÝM záznamom rovnaký
dátum aj čas, nech je tie-breaker naozaj vystavený.

Vlastná dočasná DB (tempfile) — produkcie ani daylog.db sa nedotýka.

Spusti:   pytest tests/test_entries_paging.py -v
alebo:    python tests/test_entries_paging.py
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

PAGE = 50


def _seed(path, n):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    # VŠETKY rovnaký entry_date aj entry_time → poradie drží len e.id DESC
    for i in range(n):
        c.execute("INSERT INTO entries (user_id, created_at, entry_date, entry_time, text) "
                  "VALUES (1, ?, '2026-01-01', '08:00', ?)",
                  (f"2026-01-01T00:00:00.{i:06d}", f"zaznam {i}"))
    c.commit()
    c.close()


@contextmanager
def temp_db(n):
    d = tempfile.mkdtemp(prefix="daylog_test_")
    path = os.path.join(d, "test.db")
    old = database.DB_PATH
    database.DB_PATH = path
    try:
        database.init_db()
        _seed(path, n)
        yield path
    finally:
        database.DB_PATH = old
        shutil.rmtree(d, ignore_errors=True)


def page_ids(offset, limit=PAGE, search=None):
    return [e["id"] for e in get_entries(search=search, limit=limit, offset=offset)]


def has_more(offset, page_len, search=None):
    """Rovnaký výpočet ako endpoint: offset + načítané < total."""
    return offset + page_len < count_entries(search=search)


# ── 1. veľkosti strán ────────────────────────────────────────────────────────

def test_page_sizes_50_50_20():
    with temp_db(120):
        assert len(page_ids(0)) == 50
        assert len(page_ids(50)) == 50
        assert len(page_ids(100)) == 20


# ── 2. žiadny duplikát, žiadny výpadok ───────────────────────────────────────

def test_no_duplicates_no_gaps():
    with temp_db(120):
        allids = page_ids(0) + page_ids(50) + page_ids(100)
        assert len(allids) == 120, len(allids)
        assert len(set(allids)) == 120, "objavil sa duplikát"
        assert set(allids) == set(range(1, 121)), "chýba niektorý záznam"


# ── 3. stabilita pri IDENTICKOM entry_date + entry_time ──────────────────────

def test_stable_paging_identical_timestamps():
    """Jadro opravy: všetky záznamy majú rovnaký dátum aj čas → poradie drží
    len e.id DESC. Stránkovanie musí byť deterministické a úplné."""
    with temp_db(120):
        first = page_ids(0) + page_ids(50) + page_ids(100)
        second = page_ids(0) + page_ids(50) + page_ids(100)
        assert first == second, "poradie nie je deterministické medzi behmi"
        assert len(set(first)) == 120, "duplikát cez offset"
        assert set(first) == set(range(1, 121)), "výpadok cez offset"
        # id DESC: prvá strana = najvyššie id (najnovšie), posledná = najnižšie
        assert page_ids(0)[0] == 120
        assert page_ids(100)[-1] == 1


# ── 4. offset za koncom → prázdno, nie chyba ─────────────────────────────────

def test_offset_past_end_is_empty():
    with temp_db(120):
        assert page_ids(200) == []
        assert page_ids(120) == []


# ── 5. total / has_more na prvej, prostrednej a poslednej strane ─────────────

def test_total_and_has_more():
    with temp_db(120):
        assert count_entries() == 120
        assert has_more(0, len(page_ids(0))) is True      # 0+50 < 120
        assert has_more(50, len(page_ids(50))) is True     # 50+50 < 120
        assert has_more(100, len(page_ids(100))) is False  # 100+20 == 120


# ── 6. offset=0 == volanie bez offsetu (spätná kompatibilita) ────────────────

def test_offset_zero_equals_default():
    with temp_db(120):
        default_ids = [e["id"] for e in get_entries(limit=PAGE)]
        assert page_ids(0) == default_ids


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
