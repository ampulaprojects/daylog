"""
Testy pravidiel režimu liekov — med_rules.days (Blok 2A).

Každý test si robí VLASTNÚ dočasnú DB (tempfile) — daylog.db ani produkcie
sa nedotýka. Logika v med_rules je čistá (bez DB), round-trip testy idú cez
database.update_medication() nad dočasnou DB.

POZADIE: riadok med_schedule id23 mal days='parny_datum'; editácia cez /meds
ju 2026-08-05 ticho prepísala na 'kazdy_den', lebo UI hodnotu nepoznalo
a server nič nevalidoval. Testy 22–28 sú regresia presne na túto chybu.

Spusti:   pytest tests/test_med_days.py -v
alebo:    python tests/test_med_days.py
"""
import os
import sys
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import database
from database import create_medication, update_medication, get_medications
from med_rules import (
    KAZDY_DEN, PRI_KRIZE, PARNY_DATUM, NEPARNY_DATUM,
    WD_KEYS, SIMPLE_DAYS, DAYS_LABELS,
    InvalidDays, normalize_days, is_due, format_days,
)

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn


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


def _seed(path):
    c = sqlite3.connect(path)
    for cid, name, active in ((23, 'Zinok', 1), (25, 'Vitamín D3K2', 1),
                              (99, 'Zlúčená historická položka', 0)):
        c.execute("INSERT INTO med_catalog (id, canonical_name, active, created_at, updated_at) "
                  "VALUES (?, ?, ?, '2026-01-01', '2026-01-01')", (cid, name, active))
    c.commit()
    c.close()


@pytest.fixture
def db():
    with temp_db() as path:
        yield path


def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception as e:
        raise AssertionError(f"čakal {exc.__name__}, dostal {type(e).__name__}: {e}")
    raise AssertionError(f"čakal {exc.__name__}, ale nič sa nevyhodilo")


def _row(med_id):
    for m in get_medications(include_inactive=True):
        if m["id"] == med_id:
            return m
    raise AssertionError(f"riadok {med_id} sa nenašiel")


# ── A. Čistá logika is_due() ───────────────────────────────────────────────

def test_01_parny_datum_na_parne_dni():
    for d in (4, 6, 8, 10, 30):
        assert is_due(PARNY_DATUM, date(2026, 8, d)) is True, d


def test_02_parny_datum_na_neparne_dni():
    for d in (5, 7, 9, 31):
        assert is_due(PARNY_DATUM, date(2026, 8, d)) is False, d


def test_03_neparny_datum_na_neparne_dni():
    for d in (5, 7, 9, 31):
        assert is_due(NEPARNY_DATUM, date(2026, 8, d)) is True, d


def test_04_neparny_datum_na_parne_dni():
    for d in (4, 6, 8, 30):
        assert is_due(NEPARNY_DATUM, date(2026, 8, d)) is False, d


def test_05_kazdy_den_vzdy_true():
    for d in range(1, 32):
        assert is_due(KAZDY_DEN, date(2026, 8, d)) is True, d


def test_06_pri_krize_vzdy_false():
    # PRN dávka nemá plánovaný deň → nikdy sa nesmie ohlásiť ako vynechaná.
    for d in range(1, 32):
        assert is_due(PRI_KRIZE, date(2026, 8, d)) is False, d


def test_07_konkretne_dni_v_tyzdni():
    # 2026-08-03 je pondelok → po, ut, str, stv, pi, so, ne = 3..9
    expect = {3: True, 4: False, 5: True, 6: False, 7: True, 8: False, 9: False}
    for day, want in expect.items():
        assert is_due("po,str,pi", date(2026, 8, day)) is want, day


def test_08_parny_a_neparny_su_doplnkove():
    for d in range(1, 32):
        dt = date(2026, 8, d)
        assert is_due(PARNY_DATUM, dt) != is_due(NEPARNY_DATUM, dt), d


# ── B. Prechod mesiaca ─────────────────────────────────────────────────────
# POZOR: testy 09 a 10 popisujú ZÁMER, nie chybu. "Párny dátum" je day % 2,
# teda deň v mesiaci bez kotvy. Po 31. nasleduje 1. → dva nepárne dni po sebe
# (7× do roka). Je to vedomé rozhodnutie: pravidlo, ktoré reálne platí, znie
# "pozri sa na dátum, je párny?", a softvér sa nesmie rozísť s človekom, ktorý
# drží dávkovač. NEOPRAVOVAŤ na kotvenú alternáciu — to by bola iná hodnota.

def test_09_prechod_mesiaca_dva_neparne_po_sebe():
    assert is_due(NEPARNY_DATUM, date(2026, 8, 31)) is True
    assert is_due(NEPARNY_DATUM, date(2026, 9, 1)) is True     # ZÁMER


def test_10_prechod_mesiaca_parne_ma_jednodnovu_dieru():
    assert is_due(PARNY_DATUM, date(2026, 8, 31)) is False
    assert is_due(PARNY_DATUM, date(2026, 9, 1)) is False      # ZÁMER


def test_11_februar_nepriestupny_prechod_hladko():
    assert is_due(PARNY_DATUM, date(2027, 2, 28)) is True
    assert is_due(NEPARNY_DATUM, date(2027, 3, 1)) is True


def test_12_februar_priestupny_dva_neparne_po_sebe():
    assert is_due(NEPARNY_DATUM, date(2028, 2, 29)) is True
    assert is_due(NEPARNY_DATUM, date(2028, 3, 1)) is True     # ZÁMER


def test_13_tridsatdnovy_mesiac_prechod_hladko():
    assert is_due(PARNY_DATUM, date(2026, 4, 30)) is True
    assert is_due(NEPARNY_DATUM, date(2026, 5, 1)) is True


# ── C. Odmietnutie neplatnej hodnoty ───────────────────────────────────────

def test_14_neznama_hodnota_odmietnuta():
    _raises(InvalidDays, normalize_days, "hocico")


def test_15_none_a_prazdne_su_chyba_nie_default():
    # KĽÚČOVÉ: chýbajúci vstup MUSÍ byť odlíšiteľný od platného "kazdy_den".
    _raises(InvalidDays, normalize_days, None)
    _raises(InvalidDays, normalize_days, "")


def test_16_biele_znaky_su_chyba():
    _raises(InvalidDays, normalize_days, "   ")


def test_17_ciastocne_platny_csv_odmietnuty():
    # 'po,xx' sa NESMIE ticho zredukovať na 'po'.
    _raises(InvalidDays, normalize_days, "po,xx")


def test_18_is_due_pri_neznamej_hodnote_vyhodi_nie_false():
    # False by znamenalo, že Fáza 2 liek s pokazeným pravidlom ticho vynechá.
    _raises(InvalidDays, is_due, "hocico", date(2026, 8, 5))


def test_19_csv_sa_normalizuje_na_kanonicke_poradie():
    assert normalize_days("ut,po") == "po,ut"
    assert normalize_days("ne,po,str") == "po,str,ne"
    assert normalize_days("po,po,ut") == "po,ut"      # deduplikácia
    assert normalize_days("  po , ut  ") == "po,ut"   # whitespace


def test_20_medbody_odmietne_neplatne_days():
    from pydantic import ValidationError
    from main import MedBody
    _raises(ValidationError, MedBody, name="X", days="hocico")
    _raises(ValidationError, MedBody, name="X", days="")


def test_21_medbody_bez_days_pouzije_default():
    # Spätná kompatibilita: klient, ktorý kľúč nepošle, sa správa ako doteraz.
    from main import MedBody
    assert MedBody(name="X").days == KAZDY_DEN


def test_21b_vsetky_simple_days_prejdu_validaciou():
    for v in SIMPLE_DAYS:
        assert normalize_days(v) == v
        assert v in DAYS_LABELS


# ── D. Regresia na chybu, kvôli ktorej blok vznikol ────────────────────────
# Round-trip: vytvor riadok, načítaj ho, ulož SPÄŤ NEZMENENÝ, over že prežil.
# Presne to urobil používateľ 2026-08-05 s riadkom id23 a hodnota sa stratila.

def _roundtrip(days_value, **extra):
    """Vytvorí riadok, uloží ho späť nezmenený a vráti (pred, po)."""
    mid = create_medication(name="Test", days=days_value, **extra)
    before = _row(mid)
    update_medication(
        med_id=mid, name=before["name"], kind=before["kind"], count=before["count"],
        dose=before["dose"], unit=before["unit"], time_type=before["time_type"],
        time_exact=before["time_exact"], time_value=before["time_value"],
        days=before["days"], note=before["note"], sort_order=before["sort_order"],
        catalog_id=before["catalog_id"],
    )
    return before, _row(mid)


def test_22_roundtrip_zachova_parny_datum(db):
    before, after = _roundtrip(PARNY_DATUM)
    assert before["days"] == PARNY_DATUM
    assert after["days"] == PARNY_DATUM, "days sa pri uložení bez zmeny stratilo"


def test_23_roundtrip_zachova_neparny_datum(db):
    _, after = _roundtrip(NEPARNY_DATUM)
    assert after["days"] == NEPARNY_DATUM


def test_24_roundtrip_zachova_csv_dni(db):
    _, after = _roundtrip("po,str,pi")
    assert after["days"] == "po,str,pi"


def test_25_roundtrip_zachova_pri_krize(db):
    _, after = _roundtrip(PRI_KRIZE)
    assert after["days"] == PRI_KRIZE


def test_26_roundtrip_zachova_poznamku(db):
    _, after = _roundtrip(PARNY_DATUM, note="každý druhý deň — párny dátum")
    assert after["note"] == "každý druhý deň — párny dátum"


def test_26b_roundtrip_zachova_time_type(db):
    # time_type nemá widget; UI ho posiela len kvôli round-tripu. Bez toho by
    # ho update_medication() pri každom edite prepísal na NULL.
    _, after = _roundtrip(KAZDY_DEN, time_type="presny")
    assert after["time_type"] == "presny", "time_type sa pri edite vynuloval"


def test_26c_roundtrip_zachova_catalog_id_aj_pri_neaktivnej_polozke(db):
    # Položka 99 je active=0 → nie je v dropdowne /catalog/list. UI ju musí
    # doplniť, inak by select spadol na prvú možnosť a väzba by sa ticho zrušila.
    _, after = _roundtrip(KAZDY_DEN, catalog_id=99)
    assert after["catalog_id"] == 99, "väzba na neaktívnu položku katalógu sa stratila"
    assert after["catalog_name"] == "Zlúčená historická položka"


def test_27_migracia_nastavi_days_a_zachova_note(db):
    # Simulácia migračného SQL z migrate_med_days.sql. Zinok je tam zatiaľ
    # zakomentovaný (čaká na overenie), ale logika sa testuje pre obe hodnoty.
    zinok = create_medication(name="Zinok", days=KAZDY_DEN, catalog_id=23,
                              note="každý druhý deň — nepárny dátum")
    d3k2 = create_medication(name="Vitamín D3K2", days=KAZDY_DEN, catalog_id=25,
                             note="každý druhý deň — párny dátum")
    c = sqlite3.connect(database.DB_PATH)
    c.execute("UPDATE med_schedule SET days='neparny_datum', updated_at='2026-08-05T20:00:00' "
              "WHERE id=? AND name='Zinok' AND days='kazdy_den'", (zinok,))
    c.execute("UPDATE med_schedule SET days='parny_datum', updated_at='2026-08-05T20:00:00' "
              "WHERE id=? AND name='Vitamín D3K2' AND days='kazdy_den'", (d3k2,))
    c.commit()
    c.close()
    assert _row(zinok)["days"] == NEPARNY_DATUM
    assert _row(d3k2)["days"] == PARNY_DATUM
    assert _row(zinok)["note"] == "každý druhý deň — nepárny dátum"
    assert _row(d3k2)["note"] == "každý druhý deň — párny dátum"


def test_28_migracia_je_idempotentna(db):
    # Guard `AND days='kazdy_den'` musí druhý beh zastaviť na 0 riadkoch.
    d3k2 = create_medication(name="Vitamín D3K2", days=KAZDY_DEN, catalog_id=25)
    c = sqlite3.connect(database.DB_PATH)
    sql = ("UPDATE med_schedule SET days='parny_datum', updated_at='2026-08-05T20:00:00' "
           "WHERE id=? AND name='Vitamín D3K2' AND days='kazdy_den'")
    first = c.execute(sql, (d3k2,)).rowcount
    second = c.execute(sql, (d3k2,)).rowcount
    c.commit()
    c.close()
    assert first == 1, "prvý beh mal zmeniť 1 riadok"
    assert second == 0, "druhý beh nesmie zmeniť nič"
    assert _row(d3k2)["days"] == PARNY_DATUM


# ── E. format_days — neznámu hodnotu nezahadzuje ───────────────────────────

def test_29_format_days_neznamu_hodnotu_zobrazi():
    assert "hocico" in format_days("hocico")
    assert format_days(PARNY_DATUM) == "každý párny dátum"
    assert format_days(NEPARNY_DATUM) == "každý nepárny dátum"
    assert format_days("po,str") == "Po, St"


def test_30_is_due_odmietne_iny_typ_nez_date():
    _raises(TypeError, is_due, KAZDY_DEN, "2026-08-05")


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
