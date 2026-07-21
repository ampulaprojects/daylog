import os

import bcrypt
from dotenv import load_dotenv
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

# .env načítavame tu, lebo auth sa importuje skôr než llm.py (ktorý tiež volá
# load_dotenv). Bez toho by lokálny .env pri štarte ešte nebol k dispozícii.
load_dotenv()

SESSION_MAX_AGE = 30 * 24 * 3600
SESSION_COOKIE_NAME = "session"

MIN_SECRET_LENGTH = 32
# Starý fallback, ktorý bol v gite — nesmie sa vrátiť do používania.
LEGACY_SECRET = "daylog-dev-secret-2026"

_HOWTO = (
    "Nastav DAYLOG_SECRET na náhodnú hodnotu s aspoň "
    f"{MIN_SECRET_LENGTH} znakmi:\n"
    "  - lokálne: riadok DAYLOG_SECRET=... v súbore .env v koreni projektu\n"
    "  - na VPS: systemd drop-in /etc/systemd/system/daylog.service.d/env.conf, "
    "potom systemctl daemon-reload && systemctl restart daylog\n"
    "Novú hodnotu vygeneruješ cez: "
    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
)


class SecretConfigError(RuntimeError):
    """Aplikácia nemá použiteľný DAYLOG_SECRET — štart sa odmieta."""


def resolve_secret(value):
    """Overí hodnotu DAYLOG_SECRET. Vráti ju, alebo vyhodí SecretConfigError.

    Samotnú hodnotu nikdy nevypisujeme do hlášky ani do logu.
    """
    if value is None or value.strip() == "":
        raise SecretConfigError(
            "DAYLOG_SECRET nie je nastavený (chýba alebo je prázdny). "
            "Bez neho sa dá sfalšovať session cookie, preto aplikácia neštartuje.\n"
            + _HOWTO
        )
    if value == LEGACY_SECRET:
        raise SecretConfigError(
            "DAYLOG_SECRET má starú verejne známu hodnotu, ktorá bola v gite. "
            "Túto hodnotu nemožno použiť — ktokoľvek s prístupom k repozitáru "
            "by si podpísal vlastnú session cookie.\n" + _HOWTO
        )
    if len(value) < MIN_SECRET_LENGTH:
        raise SecretConfigError(
            f"DAYLOG_SECRET je príliš krátky ({len(value)} znakov, "
            f"minimum je {MIN_SECRET_LENGTH}).\n" + _HOWTO
        )
    return value


# Overuje sa pri importe → aplikácia spadne pri ŠTARTE, nie až pri prihlásení.
SECRET_KEY = resolve_secret(os.environ.get("DAYLOG_SECRET"))

# secure=True je default; vypnúť sa dá len vedome pre lokálny http://localhost.
COOKIE_SECURE = os.environ.get("DAYLOG_INSECURE_COOKIE") != "1"

_serializer = URLSafeTimedSerializer(SECRET_KEY)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_session_token(user_id: int) -> str:
    return _serializer.dumps(user_id)


def decode_session_token(token: str):
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


# ── session cookie (jedno miesto pre všetky atribúty) ───────────────────────

def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME, token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )
