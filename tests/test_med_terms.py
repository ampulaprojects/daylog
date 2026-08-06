"""
Testy nešpecifikovaného lieku a prekladu záznamu (Blok 2B).

Každý test si robí VLASTNÚ dočasnú DB (tempfile) — daylog.db ani produkcie
sa nedotýka. Detekcia v med_terms je čistá logika bez DB; zápisové testy idú
cez database.create_entry_with_events() / update_entry_with_events().

POZADIE: opatrovateľka píše po rusky a lieky často bez názvu ("12:15-лекарство").
LLM to preloží na "liek", parser vyrobí riadok event_meds s catalog_id = NULL
a taký riadok sa nedá priradiť k slotu režimu. Zároveň sa do bloku vošla oprava
staršej chyby: preklad prepisoval entries.text a originál sa nenávratne strácal.

Spusti:   pytest tests/test_med_terms.py -v
alebo:    python tests/test_med_terms.py
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
                      get_entry, get_entries)
from med_terms import (
    GENERIC_MED_TERMS, is_generic, normalize_term, normalize_status,
    InvalidTranslationStatus, TR_NEPOTREBNY, TR_HOTOVY, TR_ZLYHAL,
)

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn


# katalógové položky použité v testoch (id, názov, aliasy, aktívna)
CAT = ((19, "B-komplex", '[]', 1),
       (23, "Zinok", '[]', 1),
       (24, "Vitamín C", '["vitamin c"]', 1),
       (8,  "Orfiril long", '["Orfiril"]', 1),
       (99, "Historická položka", '[]', 0))


def _seed(path):
    c = sqlite3.connect(path)
    c.execute("INSERT INTO users (id, username, hashed_password, role, created_at) "
              "VALUES (1, 'test', 'x', 'user', '2026-01-01')")
    for cid, name, aliases, active in CAT:
        c.execute("INSERT INTO med_catalog (id, canonical_name, aliases, active, "
                  "created_at, updated_at) VALUES (?,?,?,?,'2026-01-01','2026-01-01')",
                  (cid, name, aliases, active))
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


def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception as e:
        raise AssertionError(f"čakal {exc.__name__}, dostal {type(e).__name__}: {e}")
    raise AssertionError(f"čakal {exc.__name__}, ale nič sa nevyhodilo")


def _med_rows(entry_id=None):
    c = database.get_db()
    rows = [dict(r) for r in c.execute(
        "SELECT m.* FROM event_meds m JOIN events e ON m.event_id=e.id "
        "WHERE (? IS NULL OR e.entry_id=?) ORDER BY m.id", (entry_id, entry_id))]
    c.close()
    return rows


def _make_entry(value, specified=None, text="test", **kw):
    """Vytvorí záznam s jedným liekovým eventom a vráti (entry_id, riadky)."""
    ev = {"event_time": "12:15", "event_type": "liek", "value": value,
          "note": None, "catalog_id": None, "specified_catalog_ids": specified}
    eid = create_entry_with_events(entry_date="2026-08-06", text=text,
                                   events=[ev], user_id=1, **kw)
    return eid, _med_rows(eid)


# ── A. Detekcia všeobecných výrazov ────────────────────────────────────────

def test_01_slovenske_vyrazy():
    for t in ("liek", "lieky", "vitamíny", "podanie lieku", "lieky podľa zoznamu"):
        assert is_generic(t) is True, t


def test_02_velke_pismena():
    for t in ("LIEK", "Vitamíny", "VITAMÍNY", "Lieky Podľa Zoznamu"):
        assert is_generic(t) is True, t


def test_03_bez_diakritiky():
    for t in ("vitaminy", "lieky podla zoznamu", "VITAMINY"):
        assert is_generic(t) is True, t


def test_04_ruske_tvary():
    for t in ("лекарство", "лекарства", "таблетки", "витамины", "капли"):
        assert is_generic(t) is True, t


def test_05_ruske_velke_pismena():
    for t in ("ЛЕКАРСТВО", "Витамины", "Таблетки"):
        assert is_generic(t) is True, t


def test_06_okolite_medzery():
    for t in ("  liek  ", "\tvitamíny\n", "podanie    lieku"):
        assert is_generic(t) is True, repr(t)


def test_07_magm_nie_je_genericky():
    # KĽÚČOVÉ: "Magm" nemá catalog_id, ale je to PREKLEP konkrétneho lieku.
    # Kritérium je zhoda so zoznamom, NIE absencia catalog_id.
    assert is_generic("Magm") is False


def test_08_ostatne_neparovane_nie_su_genericke():
    for t in ("Mg", "C", "Vit. C", "1× Magnézi", "karнозín"):
        assert is_generic(t) is False, t


def test_09_realne_nazvy_nie_su_genericke():
    for t in ("Orfiril", "Tisercin", "Zinok", "Fevarin", "B-komplex"):
        assert is_generic(t) is False, t


def test_10_prazdne_hodnoty_nespadnu():
    for t in (None, "", "   ", "\t"):
        assert is_generic(t) is False, repr(t)


def test_11_dlha_veta_sa_nezhoduje():
    # Vedomé rozhodnutie: fakticky je to všeobecný výraz, ale ako celok
    # v zozname nie je a podreťazcové párovanie sem nepustíme.
    assert is_generic("Ranné vitamíny podané až na obed") is False


def test_12_zoznam_je_v_normalizovanej_podobe():
    for t in GENERIC_MED_TERMS:
        assert normalize_term(t) == t, f"{t!r} nie je normalizované"


# ── B. Doplnenie katalógových položiek ─────────────────────────────────────

def test_13_tri_polozky_daju_tri_riadky(db):
    _eid, rows = _make_entry("vitamíny", specified=[19, 24, 23])
    assert len(rows) == 3


def test_14_raw_name_zostane_povodny(db):
    # AUDIT TRAIL: z dát musí navždy vidno, že tam pôvodne stálo "vitamíny".
    _eid, rows = _make_entry("vitamíny", specified=[19, 24, 23])
    assert {r["raw_name"] for r in rows} == {"vitamíny"}


def test_15_catalog_id_sedi_na_vyber(db):
    _eid, rows = _make_entry("vitamíny", specified=[19, 24, 23])
    assert sorted(r["catalog_id"] for r in rows) == [19, 23, 24]


def test_16_specified_by_user_je_1(db):
    _eid, rows = _make_entry("vitamíny", specified=[19, 24])
    assert all(r["specified_by_user"] == 1 for r in rows)


def test_17_jedna_polozka_da_jeden_riadok(db):
    _eid, rows = _make_entry("liek", specified=[8])
    assert len(rows) == 1
    assert rows[0]["catalog_id"] == 8
    assert rows[0]["raw_name"] == "liek"


def test_18_neznamy_catalog_id_je_odmietnuty():
    from fastapi import HTTPException
    from main import _validate_specified_ids
    with temp_db():
        _raises(HTTPException, _validate_specified_ids,
                [{"specified_catalog_ids": [19, 12345]}])


def test_19_neaktivna_polozka_je_odmietnuta():
    from fastapi import HTTPException
    from main import _validate_specified_ids
    with temp_db():
        # 99 je active=0 → v ponuke nie je, takže ju nesmie prijať ani server
        _raises(HTTPException, _validate_specified_ids,
                [{"specified_catalog_ids": [99]}])
        # kontrola opačným smerom: aktívne prejdú
        _validate_specified_ids([{"specified_catalog_ids": [19, 23]}])


# ── C. Preskočenie výzvy ───────────────────────────────────────────────────

def test_20_prazdny_vyber_necha_riadok_nesparovany(db):
    _eid, rows = _make_entry("liek", specified=[])
    assert len(rows) == 1
    assert rows[0]["catalog_id"] is None
    assert rows[0]["raw_name"] == "liek"


def test_21_none_sa_sprava_ako_pred_blokom(db):
    _eid, rows = _make_entry("liek", specified=None)
    assert len(rows) == 1
    assert rows[0]["catalog_id"] is None


def test_22_preskocene_ma_specified_by_user_0(db):
    _eid, rows = _make_entry("liek", specified=None)
    assert rows[0]["specified_by_user"] == 0


def test_23_negenericky_event_ignoruje_specifikaciu(db):
    # "1× Orfiril" sa napáruje na catalog_id 8 a generický nie je → upresnenie
    # sa nesmie použiť (a _apply_specified_meds to zaloguje ako varovanie).
    _eid, rows = _make_entry("1× Orfiril", specified=[19, 23])
    assert len(rows) == 1
    assert rows[0]["raw_name"] != "vitamíny"
    assert rows[0]["specified_by_user"] == 0
    assert 19 not in [r["catalog_id"] for r in rows]


# ── D. Preklad ─────────────────────────────────────────────────────────────

def test_24_ruskym_zaznamom_prezije_original(db):
    ru = "12:15-лекарство. 12:45-покакал."
    eid, _ = _make_entry("liek", text=ru,
                         text_sk="12:15 – liek. 12:45 – pokakal.",
                         source_lang="ru", translation_status=TR_HOTOVY)
    e = get_entry(eid)
    assert e["text"] == ru, "originál bol prepísaný prekladom"
    assert e["text_sk"] == "12:15 – liek. 12:45 – pokakal."
    assert e["translation_status"] == TR_HOTOVY


def test_25_slovensky_zaznam_nepotrebuje_preklad(db):
    eid, _ = _make_entry("liek", text="12:15 liek",
                         text_sk=None, source_lang="sk",
                         translation_status=TR_NEPOTREBNY)
    e = get_entry(eid)
    assert e["translation_status"] == TR_NEPOTREBNY
    assert e["text_sk"] is None


def test_26_chybajuci_preklad_je_zlyhanie(db):
    eid, _ = _make_entry("liek", text="лекарство", text_sk=None,
                         source_lang="ru", translation_status=TR_ZLYHAL)
    e = get_entry(eid)
    assert e["translation_status"] == TR_ZLYHAL
    assert e["text_sk"] is None


def test_27_stary_zaznam_bez_prekladu_sa_nacita(db):
    eid, _ = _make_entry("liek", text="starý záznam")
    e = get_entry(eid)
    assert e["translation_status"] is None
    assert e["text_sk"] is None
    assert e["text"] == "starý záznam"
    assert any(x["id"] == eid for x in get_entries())     # vykreslí sa bez chyby


def test_28_neplatny_stav_prekladu_je_odmietnuty():
    _raises(InvalidTranslationStatus, normalize_status, "hocico")
    _raises(InvalidTranslationStatus, normalize_status, "HOTOVY")
    assert normalize_status(None) is None
    assert normalize_status("") is None
    assert normalize_status(TR_ZLYHAL) == TR_ZLYHAL


def test_29_nepotrebny_a_zlyhal_su_rozlisitelne(db):
    # JADRO POŽIADAVKY: obidva majú text_sk = NULL, takže sa NESMÚ rozlišovať
    # podľa neho — na to je práve translation_status.
    a, _ = _make_entry("liek", text="sk text", source_lang="sk",
                       translation_status=TR_NEPOTREBNY)
    b, _ = _make_entry("liek", text="ru text", source_lang="ru",
                       translation_status=TR_ZLYHAL)
    ea, eb = get_entry(a), get_entry(b)
    assert ea["text_sk"] is None and eb["text_sk"] is None
    assert ea["translation_status"] != eb["translation_status"]


def test_29b_odvodenie_stavu_v_llm(db):
    from llm import _translation_status
    assert _translation_status("sk", None) == TR_NEPOTREBNY
    assert _translation_status("sk", "čokoľvek") == TR_NEPOTREBNY
    assert _translation_status("ru", "preklad") == TR_HOTOVY
    assert _translation_status("ru", None) == TR_ZLYHAL
    assert _translation_status(None, None) is None       # nevieme ≠ zlyhalo
    assert _translation_status("", "x") is None


# ── E. Regresia na editáciu — mína z Bloku 2A ──────────────────────────────

def test_30_edit_bez_zmeny_zachova_doplnene_lieky(db):
    """KĽÚČOVÝ TEST. update_entry_with_events() maže a znova vytvára všetky
    event_meds. Bez prenosu specified_catalog_ids by prvá editácia zmazala
    všetky doplnené lieky — presne ako days v Bloku 2A."""
    eid, before = _make_entry("vitamíny", specified=[19, 24])
    assert len(before) == 2

    ev = {"event_time": "12:15", "event_type": "liek", "value": "vitamíny",
          "note": None, "catalog_id": None, "specified_catalog_ids": [19, 24]}
    update_entry_with_events(eid, 1, "test", [ev])

    after = _med_rows(eid)
    assert len(after) == 2, "doplnené lieky sa pri editácii stratili"
    assert sorted(r["catalog_id"] for r in after) == [19, 24]
    assert all(r["specified_by_user"] == 1 for r in after)


def test_31_edit_zachova_raw_name(db):
    eid, _ = _make_entry("vitamíny", specified=[19, 24])
    ev = {"event_time": "12:15", "event_type": "liek", "value": "vitamíny",
          "note": None, "catalog_id": None, "specified_catalog_ids": [19, 24]}
    update_entry_with_events(eid, 1, "test", [ev])
    assert {r["raw_name"] for r in _med_rows(eid)} == {"vitamíny"}


def test_32_zmena_textu_vynuluje_preklad(db):
    """Text sa zmenil → starý preklad mu už nezodpovedá. Radšej žiadny preklad
    než preklad nesúhlasiaci s textom."""
    eid, _ = _make_entry("liek", text="лекарство", text_sk="liek",
                         source_lang="ru", translation_status=TR_HOTOVY)
    assert get_entry(eid)["text_sk"] == "liek"

    ev = {"event_time": "12:15", "event_type": "liek", "value": "liek",
          "note": None, "catalog_id": None}
    update_entry_with_events(eid, 1, "iný text", [ev])

    e = get_entry(eid)
    assert e["text"] == "iný text"
    assert e["text_sk"] is None
    assert e["translation_status"] is None
    assert e["source_lang"] is None


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
