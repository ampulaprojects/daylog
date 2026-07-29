"""
Testy parsovania LLM odpovede a detekcie odseknutia (koniec tichého zlyhania).

Bez volania API a bez siete — testujú sa čisté funkcie _parse_llm_json a
check_stop_reason priamo. Import llm.py nevyžaduje ANTHROPIC_API_KEY (klient sa
vytvára lenivo až pri reálnom volaní).

Spusti:   pytest tests/test_llm_parsing.py -v
alebo:    python tests/test_llm_parsing.py
"""
import logging
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from llm import _parse_llm_json, check_stop_reason, LLMApiError

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn


def _run_capturing_warnings(fn):
    """Spusti fn() a zachyť WARNING+ záznamy z loggera 'uvicorn.error'."""
    logger = logging.getLogger("uvicorn.error")
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _H()
    old_level = logger.level
    logger.addHandler(h)
    logger.setLevel(logging.WARNING)
    try:
        result = fn()
    finally:
        logger.removeHandler(h)
        logger.setLevel(old_level)
    return result, records


# ── _parse_llm_json: 4 úrovne ────────────────────────────────────────────────

def test_valid_json_direct():
    """Úroveň 1: platný JSON sa naparsuje priamo."""
    raw = '{"cleaned_text":"ok","events":[{"event_type":"liek","value":"Orfiril"}]}'
    r = _parse_llm_json(raw, "fallback")
    assert isinstance(r, dict), r
    assert r["events"][0]["value"] == "Orfiril", r


def test_empty_events_is_valid_not_fallback():
    """Legitímny 'text bez udalostí' = platné events:[] — NESMIE ísť do fallbacku
    ani logovať warning (rozlíšenie od odseknutia)."""
    raw = '{"cleaned_text":"nič sa nedialo","events":[]}'
    (r, records) = _run_capturing_warnings(lambda: _parse_llm_json(raw, "fallback"))
    assert r == {"cleaned_text": "nič sa nedialo", "events": []}, r
    assert not records, f"nemal padnúť warning pre platné prázdne events: {records}"


def test_json_in_code_fence():
    """Úroveň 2: JSON obalený v ```-fence."""
    raw = '```json\n{"cleaned_text":"ok","events":[]}\n```'
    r = _parse_llm_json(raw, "fallback")
    assert r == {"cleaned_text": "ok", "events": []}, r


def test_json_embedded_in_text():
    """Úroveň 3: JSON blok uprostred iného textu."""
    raw = 'Tu je výsledok: {"cleaned_text":"ok","events":[{"event_type":"nalada","value":"dobrá"}]} hotovo'
    r = _parse_llm_json(raw, "fallback")
    assert isinstance(r, dict), r
    assert r["events"][0]["event_type"] == "nalada", r


def test_truncated_json_returns_empty_and_warns():
    """Úroveň 4: odseknutý JSON (pole events bez uzatváracej zátvorky) → prázdne
    eventy + WARNING v logu. Toto je práve tichý stav, ktorý teraz nie je tichý."""
    raw = ('{"cleaned_text":"dlhy denny zaznam bez konca",'
           '"events":[{"event_type":"liek","value":"Orfiril"},'
           '{"event_type":"liek","value":"Tiserc')
    (r, records) = _run_capturing_warnings(lambda: _parse_llm_json(raw, "fallback text"))
    assert r == {"cleaned_text": "fallback text", "events": []}, r
    assert any(rec.levelno == logging.WARNING for rec in records), \
        "chýba WARNING o prepadnutom parse"


def test_empty_and_none_do_not_crash():
    """Prázdny text aj None → vráti fallback, nespadne (json.loads(None) by inak
    hodil TypeError)."""
    assert _parse_llm_json("", "fb") == {"cleaned_text": "fb", "events": []}
    assert _parse_llm_json(None, "fb") == {"cleaned_text": "fb", "events": []}


# ── check_stop_reason ────────────────────────────────────────────────────────

def test_stop_reason_max_tokens_raises():
    try:
        check_stop_reason("max_tokens")
    except LLMApiError as e:
        assert "odseknut" in str(e).lower(), str(e)
    else:
        raise AssertionError("check_stop_reason('max_tokens') mal vyhodiť LLMApiError")


def test_stop_reason_ok_values_pass():
    """end_turn, tool_use aj None nesmú vyhodiť nič."""
    assert check_stop_reason("end_turn") is None
    assert check_stop_reason("tool_use") is None
    assert check_stop_reason("stop_sequence") is None
    assert check_stop_reason(None) is None


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
