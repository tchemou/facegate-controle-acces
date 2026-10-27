"""Password hashing, tokens and login throttling (standard library only: no extra crypto dependency)."""

import hashlib
import hmac
import secrets

# scrypt, parameters above the OWASP minimum (N=2^15, r=8, p=1 needs ~32 MiB: allow it explicitly)
_N, _R, _P = 2**15, 8, 1
_MAXMEM = 64 * 1024 * 1024
MIN_PASSWORD_LENGTH = 12

MAX_FAILURES = 5
FAILURE_WINDOW_S = 15 * 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=_MAXMEM, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
            maxmem=_MAXMEM, dklen=len(expected) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


# Verified against when the username does not exist, so that a wrong user and a wrong password
# take the same time and the response does not reveal which accounts exist.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_unknown_user(password: str) -> bool:
    verify_password(password, _DUMMY_HASH)
    return False


def check_password_strength(password: str) -> str | None:
    """Returns a message when the password is too weak, None when acceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"au moins {MIN_PASSWORD_LENGTH} caracteres"
    if password.lower() == password or password.upper() == password:
        return "melangez majuscules et minuscules"
    if not any(c.isdigit() for c in password):
        return "ajoutez au moins un chiffre"
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
