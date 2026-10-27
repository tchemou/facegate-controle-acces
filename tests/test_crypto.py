import os
import sys

import numpy as np
import pytest

from facerec import crypto
from facerec.crypto import CryptoError, VectorCipher, aad_for, is_encrypted, load_key
from facerec.store import Store

rng = np.random.default_rng(9)


def vec(n=3) -> np.ndarray:
    return rng.normal(size=(n, 128)).astype(np.float32)


@pytest.fixture
def cipher():
    return VectorCipher(crypto.new_key())


# --- keys ---------------------------------------------------------------------------------------


def test_key_roundtrip_and_validation():
    key = crypto.new_key()
    assert crypto.decode_key(crypto.encode_key(key)) == key
    assert crypto.decode_key("  " + crypto.encode_key(key) + "\n") == key
    for bad in ("", "not base64 !!", crypto.encode_key(b"short"), crypto.encode_key(os.urandom(64))):
        with pytest.raises(CryptoError):
            crypto.decode_key(bad)
    with pytest.raises(CryptoError):
        VectorCipher(b"too short")


def test_the_environment_key_wins_over_the_key_file(tmp_path, monkeypatch):
    file_key, env_key = crypto.new_key(), crypto.new_key()
    path = tmp_path / "k"
    path.write_text(crypto.encode_key(file_key))
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    assert load_key(path)[0] == file_key
    monkeypatch.setenv(crypto.ENV_KEY, crypto.encode_key(env_key))
    key, source = load_key(path)
    assert key == env_key and crypto.ENV_KEY in source


def test_a_missing_key_is_only_created_when_asked_and_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    path = tmp_path / "sub" / "facegate.key"
    assert load_key(path) == (None, "")
    assert not path.exists()
    key, source = load_key(path, create=True)
    assert len(key) == 32 and path.exists() and "créé" in source
    assert load_key(path, create=True)[0] == key  # a second call reads it, it does not make a new one
    if sys.platform != "win32":
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_two_programs_starting_together_cannot_overwrite_each_others_key(tmp_path, monkeypatch):
    """Both see 'no key yet'; the second creation must fail instead of replacing the first key."""
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    path = tmp_path / "k"
    first, _ = load_key(path, create=True)
    monkeypatch.setattr(crypto.Path, "exists", lambda self: False)  # the second program checked before the first wrote
    with pytest.raises(FileExistsError):
        load_key(path, create=True)
    monkeypatch.undo()
    assert crypto.decode_key(path.read_text()) == first  # the original key is intact


def test_a_corrupt_key_file_is_an_error_not_a_silent_new_key(tmp_path, monkeypatch):
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)
    path = tmp_path / "k"
    path.write_text("garbage")
    with pytest.raises(CryptoError):
        load_key(path, create=True)
    assert path.read_text() == "garbage"


# --- cipher -------------------------------------------------------------------------------------------


def test_roundtrip_and_fresh_nonces(cipher):
    data, aad = vec(1).tobytes(), aad_for(1, "sface")
    a, b = cipher.encrypt(data, aad), cipher.encrypt(data, aad)
    assert a != b  # random nonce: the same embedding never looks the same twice
    assert cipher.decrypt(a, aad) == data and cipher.decrypt(b, aad) == data
    assert data not in a


def test_tampering_wrong_key_and_wrong_owner_are_detected(cipher):
    data, aad = vec(1).tobytes(), aad_for(1, "sface")
    blob = bytearray(cipher.encrypt(data, aad))
    blob[-5] ^= 1
    with pytest.raises(CryptoError):
        cipher.decrypt(bytes(blob), aad)
    good = cipher.encrypt(data, aad)
    with pytest.raises(CryptoError):
        VectorCipher(crypto.new_key()).decrypt(good, aad)
    with pytest.raises(CryptoError):
        cipher.decrypt(good, aad_for(2, "sface"))  # moved to another person
    with pytest.raises(CryptoError):
        cipher.decrypt(good, aad_for(1, "dlib"))  # relabelled with another model
    with pytest.raises(CryptoError):
        cipher.decrypt(data, aad)  # not encrypted at all


def test_a_raw_embedding_is_never_mistaken_for_an_encrypted_one(cipher):
    raw = b"FG1" + bytes(509)  # a raw vector that happens to start with the marker
    assert len(raw) == crypto.RAW_VECTOR_LEN and not is_encrypted(raw)
    assert is_encrypted(cipher.encrypt(vec(1).tobytes(), b"x"))


# --- in the store ----------------------------------------------------------------------------------------


@pytest.fixture
def secured(tmp_path, cipher):
    return Store(tmp_path / "t.db", cipher)


def test_embeddings_roundtrip_and_never_hit_the_disk_in_the_clear(tmp_path, cipher):
    store, vectors = Store(tmp_path / "t.db", cipher), vec(4)
    store.add_person("alice", vectors, "sface")
    assert np.allclose(store.embeddings_by_person()["alice"], vectors)
    assert np.allclose(store.load_gallery("sface").embeddings_of("alice"), vectors)
    assert store.encryption_status() == {"encrypted": 4, "plain": 0}
    # not in the database file, nor in its write-ahead log
    disk = b"".join(p.read_bytes() for p in tmp_path.glob("t.db*"))
    assert all(v.tobytes() not in disk for v in vectors)


def test_without_the_key_the_database_is_unreadable_with_a_clear_message(tmp_path, cipher):
    Store(tmp_path / "t.db", cipher).add_person("alice", vec(), "sface")
    with pytest.raises(CryptoError, match="FACEGATE_KEY"):
        Store(tmp_path / "t.db").load_gallery("sface")
    with pytest.raises(CryptoError):
        Store(tmp_path / "t.db", VectorCipher(crypto.new_key())).load_gallery("sface")


def test_swapping_embeddings_between_two_people_is_detected(secured):
    a, b = secured.add_person("alice", vec(2), "sface"), secured.add_person("bob", vec(2), "sface")
    with secured._conn() as db:  # an attacker with write access to the file copies alice's template onto bob's row
        blob = db.execute("SELECT vector FROM embeddings WHERE person_id = ? LIMIT 1", (a,)).fetchone()[0]
        db.execute("UPDATE embeddings SET vector = ? WHERE person_id = ?", (blob, b))
    with pytest.raises(CryptoError):
        secured.load_gallery("sface")


def flip_a_byte(store, row_id=None, offset=40):
    """Change one byte of a stored embedding, the way a tampered file would."""
    with store._conn() as db:
        row = db.execute("SELECT id, vector FROM embeddings " + ("WHERE id = ?" if row_id else "ORDER BY id LIMIT 1"),
                         (row_id,) if row_id else ()).fetchone()
        blob = bytearray(row["vector"])
        blob[offset] ^= 0xFF
        db.execute("UPDATE embeddings SET vector = ? WHERE id = ?", (bytes(blob), row["id"]))
        return row["id"]


def test_a_tampered_embedding_stops_the_load_instead_of_dropping_the_person(secured):
    secured.add_person("alice", vec(2), "sface")
    flip_a_byte(secured)
    with pytest.raises(CryptoError):
        secured.load_gallery("sface")


def test_legacy_plaintext_rows_stay_readable_and_can_be_encrypted(tmp_path, cipher):
    legacy, vectors = Store(tmp_path / "t.db"), vec(3)
    legacy.add_person("alice", vectors, "sface")
    store = Store(tmp_path / "t.db", cipher)
    assert store.encryption_status() == {"encrypted": 0, "plain": 3}
    assert np.allclose(store.embeddings_by_person()["alice"], vectors)  # readable before migrating
    store.add_person("bob", vec(2), "sface")  # new rows are encrypted straight away
    assert store.encryption_status() == {"encrypted": 2, "plain": 3}
    assert store.encrypt_existing() == 3 and store.encrypt_existing() == 0  # idempotent
    assert store.encryption_status() == {"encrypted": 5, "plain": 0}
    assert np.allclose(store.embeddings_by_person()["alice"], vectors)


def disk_bytes(tmp_path) -> bytes:
    return b"".join(p.read_bytes() for p in tmp_path.glob("t.db*"))  # the file and its write-ahead log


def test_migrating_leaves_no_plaintext_copy_behind(tmp_path, cipher):
    """SQLite keeps replaced values in free pages and in the log: the migration must scrub them."""
    legacy, vectors = Store(tmp_path / "t.db"), vec(6)
    legacy.add_person("alice", vectors, "sface")
    assert all(v.tobytes() in disk_bytes(tmp_path) for v in vectors)  # sanity: it really was in the clear
    Store(tmp_path / "t.db", cipher).encrypt_existing()
    assert all(v.tobytes() not in disk_bytes(tmp_path) for v in vectors)


def test_deleting_a_person_overwrites_their_embeddings_on_disk(tmp_path):
    store, vectors = Store(tmp_path / "t.db"), vec(6)
    pid = store.add_person("alice", vectors, "sface")
    store.delete_person(pid)
    store._scrub()
    assert all(v.tobytes() not in disk_bytes(tmp_path) for v in vectors)


def test_rotating_the_key_leaves_nothing_decryptable_with_the_old_one(tmp_path, cipher):
    store = Store(tmp_path / "t.db", cipher)
    store.add_person("alice", vec(3), "sface")
    with store._conn() as db:
        old_blobs = [r[0] for r in db.execute("SELECT vector FROM embeddings")]
    store.rotate_key(VectorCipher(crypto.new_key()))
    disk = disk_bytes(tmp_path)
    assert all(blob not in disk for blob in old_blobs)


def test_encrypting_needs_a_key(tmp_path):
    with pytest.raises(CryptoError):
        Store(tmp_path / "t.db").encrypt_existing()


def test_key_rotation_re_encrypts_everything(tmp_path, cipher):
    store, vectors = Store(tmp_path / "t.db", cipher), vec(3)
    store.add_person("alice", vectors, "sface")
    store.add_person("bob", vec(2), "sface")
    new = VectorCipher(crypto.new_key())
    assert store.rotate_key(new) == 5
    assert np.allclose(Store(tmp_path / "t.db", new).embeddings_by_person()["alice"], vectors)
    with pytest.raises(CryptoError):
        Store(tmp_path / "t.db", cipher).load_gallery("sface")  # the old key no longer opens anything


def test_a_failed_rotation_changes_nothing(tmp_path, cipher):
    store = Store(tmp_path / "t.db", cipher)
    store.add_person("alice", vec(3), "sface")
    store.add_person("bob", vec(3), "sface")
    with store._conn() as db:
        last = db.execute("SELECT MAX(id) FROM embeddings").fetchone()[0]
    flip_a_byte(store, row_id=last)  # the last row is corrupt, so the rotation fails half-way
    with pytest.raises(CryptoError):
        store.rotate_key(VectorCipher(crypto.new_key()))
    with store._conn() as db:  # rows that came before it were not left re-encrypted under the new key
        rows = db.execute("SELECT person_id, model, vector FROM embeddings WHERE id < ?", (last,)).fetchall()
    assert rows
    for row in rows:
        cipher.decrypt(row["vector"], aad_for(row["person_id"], row["model"]))
    assert store.cipher is cipher  # and the store keeps using the old key


def test_a_backup_is_a_consistent_copy_that_stays_encrypted(tmp_path, cipher):
    store, vectors = Store(tmp_path / "t.db", cipher), vec(3)
    store.add_person("alice", vectors, "sface")
    copy = store.backup_to(tmp_path / "backups" / "copy.db")
    assert np.allclose(Store(copy, cipher).embeddings_by_person()["alice"], vectors)
    assert vectors[0].tobytes() not in copy.read_bytes()
    with pytest.raises(FileExistsError):
        store.backup_to(copy)  # never overwrites a previous backup
