import base64
import hashlib
import re

import pytest

from facerec.web import totp

# RFC 6238, appendix B: the shared secrets are the ASCII strings "1234567890" repeated
SHA1_SECRET = base64.b32encode(b"12345678901234567890").decode()
SHA256_SECRET = base64.b32encode(b"12345678901234567890123456789012").decode()
SHA512_SECRET = base64.b32encode(b"1234567890123456789012345678901234567890123456789012345678901234").decode()

RFC_VECTORS = [  # (time, 8-digit code) for SHA-1
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130"),
]


@pytest.mark.parametrize("at, expected", RFC_VECTORS)
def test_rfc_6238_sha1_vectors(at, expected):
    assert totp._hotp(SHA1_SECRET, at // 30, digits=8) == expected


def test_rfc_6238_other_hashes():
    assert totp._hotp(SHA256_SECRET, 59 // 30, digits=8, algorithm=hashlib.sha256) == "46119246"
    assert totp._hotp(SHA512_SECRET, 59 // 30, digits=8, algorithm=hashlib.sha512) == "90693936"


def test_six_digit_codes_are_the_last_six_of_the_rfc_codes():
    assert totp.code_at(SHA1_SECRET, at=59) == "287082"
    assert len(totp.code_at(totp.new_secret())) == 6


def test_secrets_are_random_valid_base32():
    a, b = totp.new_secret(), totp.new_secret()
    assert a != b and len(a) == 32 and re.fullmatch(r"[A-Z2-7]+", a)
    assert len(base64.b32decode(a)) == 20  # 160 bits


# --- verification ---------------------------------------------------------------------------------


def test_the_current_and_neighbouring_codes_are_accepted_but_not_older_ones():
    secret, now = SHA1_SECRET, 1_000_000_000
    step = now // 30
    assert totp.verify(secret, totp.code_at(secret, now), now) == step
    assert totp.verify(secret, totp.code_at(secret, now - 30), now) == step - 1  # phone slightly behind
    assert totp.verify(secret, totp.code_at(secret, now + 30), now) == step + 1  # phone slightly ahead
    assert totp.verify(secret, totp.code_at(secret, now - 90), now) is None
    assert totp.verify(secret, totp.code_at(secret, now + 90), now) is None


def test_a_used_code_cannot_be_replayed():
    secret, now = SHA1_SECRET, 1_000_000_000
    code = totp.code_at(secret, now)
    step = totp.verify(secret, code, now)
    assert totp.verify(secret, code, now, last_step=step) is None  # same code again
    assert totp.verify(secret, totp.code_at(secret, now - 30), now, last_step=step) is None  # an older code
    assert totp.verify(secret, totp.code_at(secret, now + 30), now, last_step=step) == step + 1  # a newer one is fine


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12 34 5", None, "000000"])
def test_malformed_or_wrong_codes_are_refused(bad):
    assert totp.verify(SHA1_SECRET, bad, 1_000_000_000) is None


def test_spaces_in_a_typed_code_are_tolerated():
    code = totp.code_at(SHA1_SECRET, 1_000_000_000)
    assert totp.verify(SHA1_SECRET, f" {code[:3]} {code[3:]} ", 1_000_000_000) is not None


# --- provisioning ----------------------------------------------------------------------------------


def test_otpauth_uri_carries_what_an_authenticator_needs():
    uri = totp.otpauth_uri("ABCDEFGH", "anne@example.org")
    assert uri.startswith("otpauth://totp/FaceGate%3Aanne%40example.org?")
    assert "secret=ABCDEFGH" in uri and "issuer=FaceGate" in uri and "digits=6" in uri and "period=30" in uri


def test_otpauth_uri_escapes_hostile_account_names():
    uri = totp.otpauth_uri("ABCDEFGH", "a b&secret=evil#x")
    assert "secret=evil" not in uri.split("?")[0] and "&secret=evil" not in uri.replace("secret=ABCDEFGH", "")
    assert " " not in uri


def test_the_qr_is_inline_svg_without_style_or_script():
    svg = totp.qr_svg(totp.otpauth_uri(totp.new_secret(), "anne"))
    assert svg.startswith("<svg") and svg.endswith("</svg>") and "<path" in svg
    assert " style=" not in svg and "<script" not in svg and "onload" not in svg and "javascript:" not in svg


def test_the_qr_encodes_the_uri():
    import segno

    uri = totp.otpauth_uri("ABCDEFGHIJKLMNOP", "anne")
    svg = totp.qr_svg(uri, border=0)
    matrix = segno.make(uri, error="m").matrix
    dark = sum(sum(1 for v in row if v) for row in matrix)
    drawn = sum(int(w) for w in re.findall(r"h(\d+)v1", svg))  # each run is `h<n>v1`
    assert drawn == dark  # every dark module is in the picture, no more, no fewer


# --- recovery codes ---------------------------------------------------------------------------------------


def test_recovery_codes_are_unique_readable_and_numerous():
    codes = totp.new_recovery_codes()
    assert len(codes) == totp.RECOVERY_COUNT and len(set(codes)) == len(codes)
    for code in codes:
        assert re.fullmatch(r"[a-z2-9]{5}-[a-z2-9]{5}", code) and not set(code) & set("01loi")


def test_recovery_codes_are_typed_loosely():
    assert totp.normalize_recovery(" ABCDE-fghjk ") == "abcdefghjk"
    assert totp.normalize_recovery("abcde fghjk") == "abcdefghjk"
    assert totp.normalize_recovery(None) == ""


def test_secret_formatting_for_manual_entry():
    assert totp.format_secret("ABCDEFGHIJKLMNOP") == "ABCD EFGH IJKL MNOP"
