import pytest

from facerec import crypto
from facerec.web import security


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch):
    """Tests hash hundreds of passwords: use a small scrypt cost *for new hashes in tests only*.

    Production keeps N=2^15 (the hash string records its own parameters, so verification is unaffected)."""
    monkeypatch.setattr(security, "_N", 2**10)


@pytest.fixture(autouse=True)
def isolated_key(monkeypatch):
    """Tests must never read, create or overwrite the real key file in `data/`.

    A random key in the environment takes precedence over any key file; tests that exercise key files
    (test_crypto) remove it explicitly and point at a temporary path."""
    monkeypatch.setenv(crypto.ENV_KEY, crypto.encode_key(crypto.new_key()))
