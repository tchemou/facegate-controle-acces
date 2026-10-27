"""Time-based one-time passwords (RFC 6238) and recovery codes. Standard library only, plus `segno` for the QR."""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

import segno

STEP_S = 30
DIGITS = 6
WINDOW = 1  # accept the previous and the next code too: phone clocks drift a little
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)
RECOVERY_COUNT = 8


def new_secret() -> str:
    """160 random bits, base32 (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def _hotp(secret: str, counter: int, digits: int = DIGITS, algorithm=hashlib.sha1) -> str:
    key = base64.b32decode(secret.upper().replace(" ", ""), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), algorithm).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def code_at(secret: str, at: float | None = None) -> str:
    return _hotp(secret, int((time.time() if at is None else at) // STEP_S))


def verify(secret: str, code: str, at: float | None = None, last_step: int = 0) -> int | None:
    """The time step the code belongs to, or None. A step at or before `last_step` is refused: a code
    that was already used (or older) cannot be replayed, even inside its validity window."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    now_step = int((time.time() if at is None else at) // STEP_S)
    match = None
    for step in range(now_step - WINDOW, now_step + WINDOW + 1):
        if hmac.compare_digest(_hotp(secret, step), code) and step > last_step:  # no early exit: constant work
            match = step if match is None else match
    return match


def otpauth_uri(secret: str, account: str, issuer: str = "FaceGate") -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_S}"


def qr_svg(text: str, scale: int = 5, border: int = 2) -> str:
    """The QR code of `text` as inline SVG built only from numbers (no style attribute, no script).

    Dark modules are merged into horizontal runs, one path segment each."""
    matrix = segno.make(text, error="m").matrix
    size = len(matrix) + 2 * border
    path = []
    for y, row in enumerate(matrix):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                path.append(f"M{start + border} {y + border}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    side = size * scale
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="qr" viewBox="0 0 {size} {size}" width="{side}" '
        f'height="{side}" role="img" aria-label="QR code à scanner avec une application d\'authentification" '
        f'shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="#ffffff"/>'
        f'<path d="{"".join(path)}" fill="#000000"/></svg>'
    )


def new_recovery_codes(count: int = RECOVERY_COUNT) -> list[str]:
    """One-time codes for when the phone is lost: 10 characters (about 50 bits), shown once."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def normalize_recovery(code: str) -> str:
    return (code or "").strip().lower().replace(" ", "").replace("-", "")


def format_secret(secret: str) -> str:
    """Groups of four, easier to type when the QR cannot be scanned."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
