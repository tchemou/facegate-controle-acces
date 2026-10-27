"""Encryption of face embeddings at rest (AES-256-GCM).

What it protects: a copy of the database file (stolen backup, leaked disk image) is useless without the key.
What it does not: someone who has both the key and the database, or code running on the machine while the
service is up. Keep the key away from the database (environment variable, other disk, secrets manager).
Names, consent texts and the access log are NOT encrypted here: use full-disk encryption for those.
"""

import base64
import binascii
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"FG1"  # format marker + version
NONCE_LEN = 12
KEY_LEN = 32
RAW_VECTOR_LEN = 128 * 4  # an unencrypted embedding: 128 float32
ENV_KEY = "FACEGATE_KEY"


class CryptoError(Exception):
    """Wrong key, corrupted data, or an encrypted value read without a key."""


def new_key() -> bytes:
    return os.urandom(KEY_LEN)


def encode_key(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).decode("ascii")


def decode_key(text: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(text.strip().encode("ascii"))
    except (binascii.Error, ValueError, UnicodeEncodeError) as err:
        raise CryptoError("clé invalide : base64 attendu") from err
    if len(key) != KEY_LEN:
        raise CryptoError(f"clé invalide : {KEY_LEN} octets attendus, {len(key)} reçus")
    return key


def load_key(key_file: Path | None, create: bool = False) -> tuple[bytes | None, str]:
    """The key from FACEGATE_KEY, else from `key_file`. `create`: generate the file when neither exists.

    Returns (key, where it came from); key is None when absent and not created."""
    from_env = os.environ.get(ENV_KEY)
    if from_env:
        return decode_key(from_env), f"variable d'environnement {ENV_KEY}"
    if key_file is not None and Path(key_file).exists():
        return decode_key(Path(key_file).read_text(encoding="ascii")), f"fichier {key_file}"
    if create and key_file is not None:
        key_file = Path(key_file)
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key = new_key()
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # owner-only where the OS supports it
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(encode_key(key) + "\n")
        return key, f"fichier {key_file} (créé)"
    return None, ""


class VectorCipher:
    def __init__(self, key: bytes):
        if len(key) != KEY_LEN:
            raise CryptoError(f"clé de {KEY_LEN} octets requise")
        self._aead = AESGCM(key)

    def encrypt(self, data: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_LEN)  # a fresh random nonce per value: never reused under one key
        return MAGIC + nonce + self._aead.encrypt(nonce, data, aad)

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        if not is_encrypted(blob):
            raise CryptoError("valeur non chiffrée")
        nonce, body = blob[len(MAGIC):len(MAGIC) + NONCE_LEN], blob[len(MAGIC) + NONCE_LEN:]
        try:
            return self._aead.decrypt(nonce, body, aad)
        except InvalidTag as err:
            raise CryptoError("déchiffrement impossible : mauvaise clé, donnée altérée ou déplacée") from err


def is_encrypted(blob: bytes) -> bool:
    return len(blob) != RAW_VECTOR_LEN and blob.startswith(MAGIC)


def aad_for(person_id: int, model: str) -> bytes:
    """Binds a ciphertext to its owner and model: copying it to another person's row makes it fail to open."""
    return f"{person_id}:{model}".encode("utf-8")
