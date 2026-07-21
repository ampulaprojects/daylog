"""
Testy validácie DAYLOG_SECRET (Blok 2A): appka musí odmietnuť štart, keď
secret chýba, je prázdny, príliš krátky, alebo je to starý fallback z gitu.

Testy sa nedotýkajú .env ani daylog.db — validáciu volajú priamo
(resolve_secret) a štart appky overujú v samostatnom procese s vlastným
prostredím (subprocess), nikdy nemenia prostredie tohto procesu natrvalo.

Spusti:   pytest tests/test_auth_secret.py -v
alebo:    python tests/test_auth_secret.py
"""
import os
import sys
import shutil
import tempfile
import subprocess

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from auth import resolve_secret, SecretConfigError, LEGACY_SECRET, MIN_SECRET_LENGTH

VALID = "x" * MIN_SECRET_LENGTH
LONG_VALID = "qF7-" * 20


def _rejects(value, must_contain=None):
    try:
        resolve_secret(value)
    except SecretConfigError as e:
        msg = str(e)
        assert "DAYLOG_SECRET" in msg, msg
        if must_contain:
            assert must_contain in msg, msg
        if value:
            assert value not in msg, "hláška vypísala samotnú hodnotu secretu!"
        return msg
    raise AssertionError(f"resolve_secret NEMAL prejsť pre {value!r}")


# ── validácia hodnoty ──────────────────────────────────────────────────────

def test_missing_secret_rejected():
    """Chýbajúci secret (premenná nie je nastavená) → odmietnuté."""
    _rejects(None, "nie je nastavený")


def test_empty_secret_rejected():
    """Prázdny reťazec aj samé medzery → odmietnuté."""
    _rejects("", "nie je nastavený")
    _rejects("   ", "nie je nastavený")


def test_short_secret_rejected():
    """Kratší než MIN_SECRET_LENGTH → odmietnuté."""
    msg = _rejects("x" * (MIN_SECRET_LENGTH - 1), "príliš krátky")
    assert str(MIN_SECRET_LENGTH) in msg, msg


def test_legacy_secret_rejected():
    """Starý fallback z gitu → vlastná hláška, nikdy nie je použiteľný."""
    _rejects(LEGACY_SECRET, "starú verejne známu hodnotu")


def test_valid_secret_passes():
    """Dostatočne dlhý vlastný secret prejde a vráti sa nezmenený."""
    assert resolve_secret(LONG_VALID) == LONG_VALID
    assert resolve_secret(VALID) == VALID


# ── fail-fast pri ŠTARTE (nie až pri prihlásení) ───────────────────────────

def _run(code, cwd, secret):
    env = {k: v for k, v in os.environ.items() if k != "DAYLOG_SECRET"}
    env["PYTHONIOENCODING"] = "utf-8"
    if secret is not None:
        env["DAYLOG_SECRET"] = secret
    return subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                          cwd=cwd, env=env, capture_output=True,
                          text=True, encoding="utf-8")


def test_auth_import_fails_when_secret_missing():
    """Bez DAYLOG_SECRET spadne už samotný import auth.py.

    Beží nad KÓPIOU auth.py v dočasnom adresári, aby projektový .env
    (ktorý secret nastavuje) test neovplyvnil — a aby sa .env nemenil.
    """
    d = tempfile.mkdtemp(prefix="daylog_auth_test_")
    try:
        shutil.copy(os.path.join(BASE, "auth.py"), os.path.join(d, "auth.py"))
        r = _run("import auth; print('STARTED')", d, None)
        assert r.returncode != 0, f"auth sa naimportoval bez secretu!\n{r.stdout}"
        assert "STARTED" not in r.stdout, r.stdout
        assert "SecretConfigError" in r.stderr, r.stderr
        assert "DAYLOG_SECRET nie je nastavený" in r.stderr, r.stderr
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_app_refuses_to_start_with_empty_secret():
    """Prázdny DAYLOG_SECRET zhodí celú appku pri ŠTARTE (import main.py),
    nie až pri prvom prihlásení."""
    r = _run("import main; print('STARTED')", BASE, "")
    assert r.returncode != 0, f"appka naštartovala s prázdnym secretom!\n{r.stdout}"
    assert "STARTED" not in r.stdout, r.stdout
    assert "SecretConfigError" in r.stderr, r.stderr


def test_app_starts_with_valid_secret():
    """S platným secretom sa main.py naimportuje bez chyby."""
    r = _run("import main; print('STARTED')", BASE, LONG_VALID)
    assert r.returncode == 0, r.stderr
    assert "STARTED" in r.stdout, r.stdout


# ── beh bez pytestu ────────────────────────────────────────────────────────

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
