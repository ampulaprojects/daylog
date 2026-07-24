"""
Testy roly "opatrovatelka" (obmedzený prístup).

Testujú sa ČISTÉ rozhodovacie funkcie (is_family, can_modify_entry z auth.py) a
validácia roly v manage_users.py cez argparse — žiadne HTTP (API testy zatiaľ
nemáme), žiadny zápis do DB (init_db aj handler sú pri teste zaslepené).

Spusti:   pytest tests/test_role_opatrovatelka.py -v
alebo:    python tests/test_role_opatrovatelka.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# auth.py overuje DAYLOG_SECRET pri importe — daj hermetickú hodnotu skôr, než
# sa načíta (lokálne ju inak dodá .env cez load_dotenv). Na tieto testy secret
# nepoužívame, ide len o to, aby import prešiel.
os.environ.setdefault("DAYLOG_SECRET", "x" * 40)

from auth import is_family, can_modify_entry, ROLE_OPATROVATELKA

try:
    import pytest
except ImportError:                                    # beh bez pytestu
    class pytest:                                      # noqa: N801
        @staticmethod
        def fixture(fn):
            return fn


# ── is_family ────────────────────────────────────────────────────────────────

def test_is_family_admin_and_user_true():
    assert is_family("admin") is True
    assert is_family("user") is True


def test_is_family_opatrovatelka_false():
    assert is_family(ROLE_OPATROVATELKA) is False
    assert is_family("opatrovatelka") is False


# ── can_modify_entry ─────────────────────────────────────────────────────────

def test_family_can_modify_any_entry():
    """Admin aj user (zdieľaný denník) môžu meniť aj cudzí záznam."""
    for role in ("admin", "user"):
        u = {"id": 1, "role": role}
        assert can_modify_entry(u, {"user_id": 999}) is True, role   # cudzí
        assert can_modify_entry(u, {"user_id": 1}) is True, role     # vlastný


def test_opatrovatelka_can_modify_only_own():
    u = {"id": 5, "role": ROLE_OPATROVATELKA}
    assert can_modify_entry(u, {"user_id": 5}) is True     # vlastný
    assert can_modify_entry(u, {"user_id": 6}) is False    # cudzí


# ── manage_users: validácia roly cez argparse (bez zápisu do DB) ─────────────

def test_manage_users_accepts_opatrovatelka():
    """add-user --role opatrovatelka prejde choices; handler aj init_db zaslepené."""
    import manage_users
    orig_init, orig_add = manage_users.init_db, manage_users.cmd_add_user
    captured = {}
    manage_users.init_db = lambda: None
    manage_users.cmd_add_user = lambda args: captured.update(
        username=args.username, role=args.role)
    old_argv = sys.argv
    try:
        sys.argv = ["manage_users.py", "add-user", "irina", "heslo123",
                    "--role", "opatrovatelka"]
        manage_users.main()
    finally:
        sys.argv = old_argv
        manage_users.init_db, manage_users.cmd_add_user = orig_init, orig_add
    assert captured.get("role") == "opatrovatelka", captured
    assert captured.get("username") == "irina", captured


def test_manage_users_rejects_unknown_role():
    """Neznáma rola musí byť argparse odmietnutá (SystemExit z choices)."""
    import manage_users
    orig_init = manage_users.init_db
    manage_users.init_db = lambda: None
    old_argv = sys.argv
    raised = False
    try:
        sys.argv = ["manage_users.py", "add-user", "x", "y", "--role", "kravata"]
        try:
            manage_users.main()
        except SystemExit:
            raised = True
    finally:
        sys.argv = old_argv
        manage_users.init_db = orig_init
    assert raised, "argparse mal odmietnuť neznámu rolu (choices)"


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
