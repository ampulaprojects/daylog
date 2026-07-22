"""
Testy dopĺňania aliasov do katalógu (Blok 3A.5).

Vlastná dočasná DB (tempfile) — daylog.db ani produkcie sa nedotýka.

Spusti:   pytest tests/test_add_aliases.py -v
alebo:    python tests/test_add_aliases.py
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
from add_aliases import (build_plan, apply_plan, check_collision, existing_aliases,
                         catalog_with_plan)
from parse_med_events import load_catalog, match_catalog

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn

CAT = (
    (8,  "Orfiril long", ["Orfiril", "Ofriril"]),
    (13, "Vitamin B6", []),
    (14, "Thiamin", []),
    (16, "TISERCIN", ["Tisercinu"]),
)


def _seed(path):
    c = sqlite3.connect(path)
    for cid, name, al in CAT:
        c.execute("INSERT INTO med_catalog (id, canonical_name, aliases, created_at, updated_at) "
                  "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')",
                  (cid, name, json.dumps(al, ensure_ascii=False)))
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


def read(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    cat = load_catalog(c)
    cur = existing_aliases(c)
    c.close()
    return cat, cur


def aliases_of(path, cid):
    c = sqlite3.connect(path)
    raw = c.execute("SELECT aliases FROM med_catalog WHERE id=?", (cid,)).fetchone()[0]
    c.close()
    return json.loads(raw)


# ── idempotencia ───────────────────────────────────────────────────────────

def test_apply_is_idempotent(db):
    """Dvakrát spustený --apply nesmie alias zdvojiť."""
    cat, cur = read(db)
    plan, _ = build_plan(cat, {13: ["B6"], 14: ["Tiamín"]}, cur)
    assert apply_plan(db, plan) == {13: 1, 14: 1}

    cat2, cur2 = read(db)
    plan2, skipped2 = build_plan(cat2, {13: ["B6"], 14: ["Tiamín"]}, cur2)
    assert plan2 == {}, plan2
    assert all("už existuje" in why for _, _, why in skipped2), skipped2

    # aj priame druhé apply toho istého plánu nič nepridá
    apply_plan(db, plan)
    assert aliases_of(db, 13) == ["B6"], aliases_of(db, 13)
    assert aliases_of(db, 14) == ["Tiamín"], aliases_of(db, 14)


def test_existing_alias_not_duplicated_case_insensitive(db):
    """'orfiril' sa nepridá, lebo 'Orfiril' už je (líši sa len veľkosťou písmen)."""
    cat, cur = read(db)
    plan, skipped = build_plan(cat, {8: ["orfiril", "OFRIRIL"]}, cur)
    assert plan == {}, plan
    assert len(skipped) == 2, skipped


def test_canonical_name_and_existing_aliases_untouched(db):
    """Pridávame, nikdy neprepisujeme."""
    before = aliases_of(db, 8)
    cat, cur = read(db)
    plan, _ = build_plan(cat, {8: ["Orifiril"]}, cur)
    apply_plan(db, plan)
    after = aliases_of(db, 8)
    assert after[:len(before)] == before, (before, after)
    assert "Orifiril" in after
    c = sqlite3.connect(db)
    name = c.execute("SELECT canonical_name FROM med_catalog WHERE id=8").fetchone()[0]
    c.close()
    assert name == "Orfiril long"


# ── alias sa prejaví v párovaní ────────────────────────────────────────────

def test_alias_changes_matching(db):
    """Pred pridaním sa tvar nespáruje, po pridaní áno."""
    cat, cur = read(db)
    assert match_catalog("1× B6", cat) is None
    assert match_catalog("3x Orifiril", cat) is None

    plan, _ = build_plan(cat, {13: ["B6"], 8: ["Orifiril"]}, cur)
    apply_plan(db, plan)

    cat2, _ = read(db)
    assert match_catalog("1× B6", cat2) == 13
    assert match_catalog("3x Orifiril", cat2) == 8
    # nespárovateľný doplnok zostáva nespárovaný
    assert match_catalog("2x Magnetit", cat2) is None


def test_catalog_with_plan_matches_without_writing(db):
    """Hypotetické párovanie v dry-run nesmie nič zapísať."""
    cat, cur = read(db)
    plan, _ = build_plan(cat, {13: ["B6"]}, cur)
    hypo = catalog_with_plan(cat, plan)
    assert match_catalog("1× B6", hypo) == 13
    assert aliases_of(db, 13) == [], "dry-run zapísal do DB!"


# ── kolízie ────────────────────────────────────────────────────────────────

def test_collision_is_rejected(db):
    """Alias, ktorý ukazuje na inú položku, sa nesmie pridať."""
    cat, cur = read(db)
    plan, skipped = build_plan(cat, {13: ["Tisercin"]}, cur)
    assert plan == {}, plan
    assert any("koliduje" in why for _, _, why in skipped), skipped


def test_collision_substring_both_directions(db):
    cat, _ = read(db)
    assert check_collision("Tisercinu", 13, cat), "presná zhoda s iným aliasom neodhalená"
    assert check_collision("Orfiril long navyše", 16, cat), "alias obsahujúci cudzí variant"
    assert check_collision("B6", 13, cat) is None


def test_unknown_catalog_item_is_skipped(db):
    cat, cur = read(db)
    plan, skipped = build_plan(cat, {999: ["Čokoľvek"]}, cur)
    assert plan == {}
    assert any("neexistuje" in why for _, _, why in skipped), skipped


def test_apply_rolls_back_on_error(db):
    """Chyba uprostred → nezapíše sa ani prvá položka (jedna transakcia)."""
    before13 = aliases_of(db, 13)
    try:
        apply_plan(db, {13: ["B6"], 999: ["Neexistuje"]})
        raise AssertionError("apply NEMAL prejsť — položka 999 neexistuje")
    except AssertionError:
        raise
    except Exception:
        pass
    assert aliases_of(db, 13) == before13, "rollback nezafungoval"


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
