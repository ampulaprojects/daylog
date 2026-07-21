"""
Testy atomicity zápisu záznamu (Blok 2B): entry + eventy sa zapisujú v jednej
transakcii, editácia takisto. Pri chybe uprostred nesmie v DB zostať nič
rozrobené — ani neúplný nový záznam, ani záznam s vymazanými eventmi.

Každý test si robí VLASTNÚ dočasnú DB (tempfile) — daylog.db ani produkcie
sa nedotýka.

Spusti:   pytest tests/test_entry_atomicity.py -v
alebo:    python tests/test_entry_atomicity.py
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
from database import create_entry_with_events, update_entry_with_events, get_db

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn

USER_ID = 1

# Event, ktorý zaručene zhodí INSERT: dict sa nedá naviazať ako SQL parameter
# (sqlite3.InterfaceError). Simuluje zlyhanie zápisu N-tého eventu.
BAD_EVENT = {"event_time": "09:00", "event_type": "liek", "value": {"nedá": "sa"},
             "note": None, "catalog_id": None}


def _ev(value, time="08:00", etype="liek"):
    return {"event_time": time, "event_type": etype, "value": value,
            "note": None, "catalog_id": None}


def _seed(path):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
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


def counts():
    c = get_db()
    e = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    v = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    c.close()
    return e, v


def events_of(entry_id):
    c = get_db()
    rows = c.execute("SELECT value, event_time, confirmed FROM events "
                     "WHERE entry_id=? ORDER BY id", (entry_id,)).fetchall()
    c.close()
    return [tuple(r) for r in rows]


def _make_entry(values=("Orfiril", "Tisercin")):
    return create_entry_with_events(
        entry_date="2026-01-01", text="pôvodný text",
        events=[_ev(v) for v in values], user_id=USER_ID)


# ── potvrdenie nového záznamu ──────────────────────────────────────────────

def test_confirm_writes_entry_and_all_events(db):
    """Úspešné potvrdenie zapíše entry aj všetky eventy."""
    entry_id = _make_entry(("Orfiril", "Tisercin", "raňajky"))
    assert entry_id, entry_id
    assert counts() == (1, 3), counts()
    assert [v for v, _, _ in events_of(entry_id)] == ["Orfiril", "Tisercin", "raňajky"]
    # confirmed=0 — rovnaká sémantika ako pôvodné create_event()
    assert all(c == 0 for _, _, c in events_of(entry_id)), events_of(entry_id)


def test_confirm_rolls_back_entry_when_event_fails(db):
    """Zlyhanie 3. eventu → v DB nie je ani entry, ani predošlé 2 eventy."""
    events = [_ev("Orfiril"), _ev("Tisercin"), BAD_EVENT, _ev("Fevarin")]
    try:
        create_entry_with_events(entry_date="2026-01-01", text="pokus",
                                 events=events, user_id=USER_ID)
        raise AssertionError("zápis NEMAL prejsť — 3. event je neplatný")
    except AssertionError:
        raise
    except Exception:
        pass
    assert counts() == (0, 0), f"po rollbacku zostali dáta: {counts()}"


def test_confirm_rollback_keeps_older_entries(db):
    """Rollback zhodí len rozrobený zápis, staršie záznamy ostávajú."""
    first = _make_entry(("Orfiril",))
    try:
        create_entry_with_events(entry_date="2026-01-02", text="pokus",
                                 events=[_ev("ok"), BAD_EVENT], user_id=USER_ID)
        raise AssertionError("zápis NEMAL prejsť")
    except AssertionError:
        raise
    except Exception:
        pass
    assert counts() == (1, 1), counts()
    assert [v for v, _, _ in events_of(first)] == ["Orfiril"]


def test_confirm_without_events_ok(db):
    """Záznam bez eventov je legitímny — zapíše sa samotné entry."""
    entry_id = create_entry_with_events(entry_date="2026-01-01", text="len text",
                                        events=[], user_id=USER_ID)
    assert counts() == (1, 0), counts()
    assert events_of(entry_id) == []


# ── editácia záznamu ───────────────────────────────────────────────────────

def test_update_replaces_text_and_events(db):
    """Úspešná editácia prepíše text aj vymení eventy."""
    entry_id = _make_entry(("staré A", "staré B"))
    update_entry_with_events(entry_id, USER_ID, "nový text",
                             [_ev("nové A"), _ev("nové B"), _ev("nové C")],
                             entry_date="2026-02-02", entry_time="07:30")

    c = get_db()
    row = c.execute("SELECT text, entry_date, entry_time FROM entries WHERE id=?",
                    (entry_id,)).fetchone()
    c.close()
    assert tuple(row) == ("nový text", "2026-02-02", "07:30"), tuple(row)
    assert [v for v, _, _ in events_of(entry_id)] == ["nové A", "nové B", "nové C"]
    # editácia označuje eventy ako potvrdené (confirmed=1) — pôvodné správanie
    assert all(cf == 1 for _, _, cf in events_of(entry_id)), events_of(entry_id)


def test_update_failure_keeps_original_events(db):
    """Zlyhanie uprostred editácie → pôvodné eventy AJ text zostanú nedotknuté."""
    entry_id = _make_entry(("staré A", "staré B"))
    before = events_of(entry_id)

    try:
        update_entry_with_events(entry_id, USER_ID, "nový text",
                                 [_ev("nové A"), BAD_EVENT], entry_date="2026-02-02")
        raise AssertionError("editácia NEMALA prejsť — 2. event je neplatný")
    except AssertionError:
        raise
    except Exception:
        pass

    assert events_of(entry_id) == before, f"eventy sa zmenili: {events_of(entry_id)}"
    c = get_db()
    row = c.execute("SELECT text, entry_date FROM entries WHERE id=?", (entry_id,)).fetchone()
    c.close()
    assert tuple(row) == ("pôvodný text", "2026-01-01"), tuple(row)


def test_update_to_zero_events_ok(db):
    """Vymazanie všetkých eventov pri editácii je platná operácia."""
    entry_id = _make_entry(("staré A", "staré B"))
    update_entry_with_events(entry_id, USER_ID, "bez eventov", [])
    assert events_of(entry_id) == []
    assert counts() == (1, 0), counts()


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
