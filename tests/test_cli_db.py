import numpy as np
import pytest

from facerec import cli, crypto
from facerec.crypto import CryptoError, VectorCipher
from facerec.gallery import Gallery
from facerec.store import Store

rng = np.random.default_rng(21)


def vectors(n=4) -> np.ndarray:
    return rng.normal(size=(n, 128)).astype(np.float32)


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    """These tests use key files (the conftest otherwise puts a key in the environment)."""
    monkeypatch.delenv(crypto.ENV_KEY, raising=False)


@pytest.fixture
def paths(tmp_path):
    return {"db": tmp_path / "f.db", "key": tmp_path / "keys" / "f.key", "tmp": tmp_path}


def run(*args):
    cli.main([str(a) for a in args])


# --- status and encrypt ------------------------------------------------------------------------------------


def test_status_reports_counts_and_the_key_source(paths, capsys):
    Store(paths["db"]).add_person("alice", vectors(3), "sface")
    run("db", "status", "--db", paths["db"], "--key-file", paths["key"])
    out = capsys.readouterr().out
    assert "chiffrées : 0" in out and "en clair : 3" in out and "Clé : aucune" in out and "facerec db encrypt" in out


def test_encrypt_creates_a_key_encrypts_and_is_idempotent(paths, capsys):
    data = vectors(3)
    Store(paths["db"]).add_person("alice", data, "sface")
    run("db", "encrypt", "--db", paths["db"], "--key-file", paths["key"])
    out = capsys.readouterr().out
    assert "clé de chiffrement a été créée" in out and "AILLEURS" in out and "3 empreinte(s) chiffrée(s)" in out
    assert paths["key"].exists()
    key, _ = crypto.load_key(paths["key"])
    assert Store(paths["db"], VectorCipher(key)).encryption_status() == {"encrypted": 3, "plain": 0}
    run("db", "encrypt", "--db", paths["db"], "--key-file", paths["key"])
    assert "déjà" in capsys.readouterr().out


def test_status_shows_the_environment_variable_as_the_source(paths, capsys, monkeypatch):
    monkeypatch.setenv(crypto.ENV_KEY, crypto.encode_key(crypto.new_key()))
    run("db", "status", "--db", paths["db"], "--key-file", paths["key"])
    assert crypto.ENV_KEY in capsys.readouterr().out


# --- the lost key must never be replaced silently --------------------------------------------------------------------


def test_a_lost_key_is_not_replaced_by_a_new_one(paths):
    key, _ = crypto.load_key(paths["key"], create=True)
    Store(paths["db"], VectorCipher(key)).add_person("alice", vectors(), "sface")
    paths["key"].unlink()  # the key is lost
    with pytest.raises(SystemExit) as stop:
        cli.open_store(paths["db"], paths["key"], create_key=True)
    assert "chiffrées" in str(stop.value) and "aucune clé" in str(stop.value)
    assert not paths["key"].exists()  # and no replacement key was written


def test_status_warns_that_the_recognition_cannot_start_without_the_key(paths, capsys):
    key, _ = crypto.load_key(paths["key"], create=True)
    Store(paths["db"], VectorCipher(key)).add_person("alice", vectors(), "sface")
    paths["key"].unlink()
    run("db", "status", "--db", paths["db"], "--key-file", paths["key"])
    assert "introuvable" in capsys.readouterr().out


def test_a_malformed_key_is_reported_not_ignored(paths):
    paths["key"].parent.mkdir(parents=True)
    paths["key"].write_text("garbage")
    with pytest.raises(CryptoError):
        cli.open_store(paths["db"], paths["key"], create_key=True)


def test_plaintext_embeddings_trigger_a_warning(paths, capsys):
    store = Store(paths["db"])
    store.add_person("alice", vectors(2), "sface")
    cli.warn_if_plaintext(store)
    assert "en clair" in capsys.readouterr().out
    cli.warn_if_plaintext(Store(paths["db"], VectorCipher(crypto.new_key())))
    assert "ne sont pas chiffrées" in capsys.readouterr().out
    Store(paths["db"], VectorCipher(crypto.new_key())).encrypt_existing()
    cli.warn_if_plaintext(Store(paths["db"], VectorCipher(crypto.new_key())))
    assert capsys.readouterr().out == ""


# --- people enter encrypted ---------------------------------------------------------------------------------------------------


def test_import_gallery_stores_encrypted_embeddings(paths):
    gallery = Gallery("sface")
    data = vectors(5)
    gallery.enroll("alice", data)
    gallery.save(paths["tmp"] / "g.npz")
    run("import-gallery", "--gallery", paths["tmp"] / "g.npz", "--db", paths["db"], "--key-file", paths["key"])
    key, _ = crypto.load_key(paths["key"])
    store = Store(paths["db"], VectorCipher(key))
    assert store.encryption_status() == {"encrypted": 5, "plain": 0}
    assert np.allclose(store.embeddings_by_person()["alice"], data)


# --- rotation and backup ---------------------------------------------------------------------------------------------------------


def test_rotate_key_writes_the_new_key_and_the_old_one_stops_working(paths, capsys):
    key, _ = crypto.load_key(paths["key"], create=True)
    data = vectors(3)
    Store(paths["db"], VectorCipher(key)).add_person("alice", data, "sface")
    new_file = paths["tmp"] / "new.key"
    run("db", "rotate-key", new_file, "--db", paths["db"], "--key-file", paths["key"])
    assert "3 empreinte(s) rechiffrée(s)" in capsys.readouterr().out
    new_key, _ = crypto.load_key(new_file)
    assert new_key != key
    assert np.allclose(Store(paths["db"], VectorCipher(new_key)).embeddings_by_person()["alice"], data)
    with pytest.raises(CryptoError):
        Store(paths["db"], VectorCipher(key)).load_gallery("sface")


def test_rotate_key_refuses_to_overwrite_a_file_and_needs_the_current_key(paths):
    key, _ = crypto.load_key(paths["key"], create=True)
    Store(paths["db"], VectorCipher(key)).add_person("alice", vectors(), "sface")
    existing = paths["tmp"] / "existing.key"
    existing.write_text("keep me")
    with pytest.raises(SystemExit):
        run("db", "rotate-key", existing, "--db", paths["db"], "--key-file", paths["key"])
    assert existing.read_text() == "keep me"
    paths["key"].unlink()
    with pytest.raises(SystemExit, match="clé actuelle"):
        run("db", "rotate-key", paths["tmp"] / "n.key", "--db", paths["db"], "--key-file", paths["key"])


def test_rotate_key_with_the_wrong_current_key_changes_nothing(paths):
    right = VectorCipher(crypto.new_key())
    Store(paths["db"], right).add_person("alice", vectors(), "sface")
    paths["key"].parent.mkdir(parents=True)
    paths["key"].write_text(crypto.encode_key(crypto.new_key()))  # a different key
    with pytest.raises(SystemExit, match="rien n'a changé"):
        run("db", "rotate-key", paths["tmp"] / "n.key", "--db", paths["db"], "--key-file", paths["key"])
    assert not (paths["tmp"] / "n.key").exists()
    Store(paths["db"], right).load_gallery("sface")  # still opens with the original key


def test_backup_command(paths, capsys):
    key, _ = crypto.load_key(paths["key"], create=True)
    data = vectors(2)
    Store(paths["db"], VectorCipher(key)).add_person("alice", data, "sface")
    target = paths["tmp"] / "bk" / "copy.db"
    run("db", "backup", target, "--db", paths["db"])
    assert "séparément" in capsys.readouterr().out
    assert np.allclose(Store(target, VectorCipher(key)).embeddings_by_person()["alice"], data)
    with pytest.raises(SystemExit):
        run("db", "backup", target, "--db", paths["db"])  # never overwrites


# --- retention and administrators from the command line -------------------------------------------------------------------


def test_db_purge_applies_the_configured_periods(paths, capsys):
    import time

    store = Store(paths["db"])
    store.set_retention_days(30, 365)
    store.add_event("granted", "old", "lab", at=time.time() - 40 * 86400)
    store.add_event("granted", "new", "lab")
    run("db", "purge", "--db", paths["db"])
    out = capsys.readouterr().out
    assert "accès 30 j" in out and "1 événement(s)" in out
    assert [e["person"] for e in store.list_events()] == ["new"]


def test_db_purge_reports_an_unlimited_period(paths, capsys):
    Store(paths["db"]).set_retention_days(0, 0)
    run("db", "purge", "--db", paths["db"])
    assert "sans limite" in capsys.readouterr().out


def test_the_service_start_purges_too(paths, monkeypatch):
    """`facerec run --db` applies the retention before it starts (it needs no camera to reach that point)."""
    import time

    key, _ = crypto.load_key(paths["key"], create=True)
    store = Store(paths["db"], VectorCipher(key))
    store.add_person("alice", vectors(), "sface")
    store.set_retention_days(30, 365)
    store.add_event("granted", "old", "lab", at=time.time() - 40 * 86400)

    class Reached(Exception):
        pass

    def stop_before_the_camera(*a, **k):
        raise Reached

    monkeypatch.setattr(cli, "run_live", stop_before_the_camera)
    with pytest.raises(Reached):
        run("run", "--db", paths["db"], "--key-file", paths["key"])
    assert store.list_events() == []


def test_admin_create_takes_a_role(paths, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "Correct-Horse-42")
    run("admin", "create", "camille", "--role", "manager", "--db", paths["db"])
    assert Store(paths["db"]).get_admin("camille")["role"] == "manager"
    assert "(manager)" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run("admin", "create", "x", "--role", "root", "--db", paths["db"])  # unknown role: refused by the parser


def test_admin_create_defaults_to_owner_and_refuses_weak_or_mismatched_passwords(paths, monkeypatch):
    answers = iter(["Correct-Horse-42", "Correct-Horse-42"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    run("admin", "create", "boss", "--db", paths["db"])
    assert Store(paths["db"]).get_admin("boss")["role"] == "owner"
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "weak")
    with pytest.raises(SystemExit, match="trop faible"):
        run("admin", "create", "other", "--db", paths["db"])
    answers = iter(["Correct-Horse-42", "Different-Horse-42"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit, match="correspondent pas"):
        run("admin", "create", "third", "--db", paths["db"])
    assert Store(paths["db"]).get_admin("other") is None and Store(paths["db"]).get_admin("third") is None
