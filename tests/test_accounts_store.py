import sqlite3
import time

import pytest

from facerec.store import ROLES, Store
from facerec.web import security


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def admin(store, name="alice", role="owner", **kw):
    return store.create_admin(name, security.hash_password("Correct-Horse-42"), role, **kw)


# --- migration of a database created before roles existed ---------------------------------------------------


def test_an_old_database_is_migrated_and_its_admin_becomes_an_owner(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE admins (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, "
        "password_hash TEXT NOT NULL, created_at REAL NOT NULL);"
        "INSERT INTO admins (username, password_hash, created_at) VALUES ('pagnol', 'hash', 1.0);"
    )
    db.commit()
    db.close()
    store = Store(path)
    row = store.get_admin("pagnol")
    assert row["role"] == "owner" and row["active"] == 1 and row["totp_enabled"] == 0
    assert row["must_change_password"] == 0 and row["password_hash"] == "hash"  # nothing was lost
    assert store.active_owner_count() == 1
    Store(path)  # opening it again does not fail on the already-added columns


def test_a_new_database_has_the_columns_straight_away(store):
    row = store.get_admin_by_id(admin(store))
    assert {"role", "active", "must_change_password", "totp_secret", "totp_enabled"} <= set(row.keys())


# --- roles ----------------------------------------------------------------------------------------------------


def test_roles_are_validated(store):
    assert ROLES == ("viewer", "manager", "owner")
    with pytest.raises(ValueError):
        store.create_admin("x", "h", "root")
    aid = admin(store)
    with pytest.raises(ValueError):
        store.set_admin_role(aid, "superuser")
    store.set_admin_role(aid, "viewer")
    assert store.get_admin_by_id(aid)["role"] == "viewer"


def test_active_owner_count_ignores_other_roles_and_inactive_owners(store):
    a, b = admin(store, "a", "owner"), admin(store, "b", "owner")
    admin(store, "c", "manager")
    admin(store, "d", "viewer")
    assert store.active_owner_count() == 2
    store.set_admin_active(b, False)
    assert store.active_owner_count() == 1


def test_creation_records_who_created_the_account_and_the_forced_change(store):
    aid = admin(store, "b", "manager", must_change_password=True, created_by="a")
    row = store.get_admin_by_id(aid)
    assert row["must_change_password"] == 1 and row["created_by"] == "a" and row["role"] == "manager"


# --- sessions follow the account ---------------------------------------------------------------------------------


def test_the_session_carries_the_role_and_follows_role_changes_at_once(store):
    aid = admin(store, role="manager")
    store.create_session(aid, "tok", "csrf", 3600)
    assert store.get_session("tok")["role"] == "manager"
    store.set_admin_role(aid, "viewer")
    assert store.get_session("tok")["role"] == "viewer"  # no need to sign in again to lose a privilege


def test_deactivating_an_admin_ends_every_session_and_blocks_new_ones(store):
    aid = admin(store)
    store.create_session(aid, "t1", "c", 3600)
    store.create_session(aid, "t2", "c", 3600)
    store.create_pending_2fa(aid, "p", 300)
    store.set_admin_active(aid, False)
    assert store.get_session("t1") is None and store.get_session("t2") is None
    store.set_admin_active(aid, True)
    assert store.get_session("t1") is None  # reactivating does not bring old sessions back


def test_a_password_change_closes_the_other_sessions_only(store):
    aid = admin(store)
    store.create_session(aid, "here", "c", 3600)
    store.create_session(aid, "elsewhere", "c", 3600)
    store.set_admin_password(aid, "newhash", keep_token="here")
    assert store.get_session("here") and store.get_session("elsewhere") is None
    assert store.get_admin_by_id(aid)["password_hash"] == "newhash"
    store.set_admin_password(aid, "again", must_change=True)  # no token kept: everyone is signed out
    assert store.get_session("here") is None and store.get_admin_by_id(aid)["must_change_password"] == 1


# --- two-factor authentication ----------------------------------------------------------------------------------------


def test_enrolling_a_second_factor_needs_the_confirmation_step(store):
    aid = admin(store)
    store.set_totp_secret(aid, "SECRET")
    row = store.get_admin_by_id(aid)
    assert row["totp_secret"] == "SECRET" and row["totp_enabled"] == 0  # stored, not yet required
    store.enable_totp(aid, 123, ["h1", "h2"])
    row = store.get_admin_by_id(aid)
    assert row["totp_enabled"] == 1 and row["totp_last_step"] == 123
    assert len(store.unused_recovery_codes(aid)) == 2


def test_enabling_without_a_secret_does_nothing(store):
    aid = admin(store)
    store.enable_totp(aid, 5, [])
    assert store.get_admin_by_id(aid)["totp_enabled"] == 0


def test_restarting_the_enrollment_resets_the_state(store):
    aid = admin(store)
    store.set_totp_secret(aid, "A")
    store.enable_totp(aid, 9, ["h"])
    store.set_totp_secret(aid, "B")  # a new secret: the old factor is no longer in force until confirmed
    row = store.get_admin_by_id(aid)
    assert row["totp_secret"] == "B" and row["totp_enabled"] == 0 and row["totp_last_step"] == 0


def test_disabling_removes_the_secret_and_the_recovery_codes(store):
    aid = admin(store)
    store.set_totp_secret(aid, "S")
    store.enable_totp(aid, 1, ["h"])
    store.disable_totp(aid)
    row = store.get_admin_by_id(aid)
    assert row["totp_secret"] is None and row["totp_enabled"] == 0 and store.unused_recovery_codes(aid) == []


def test_a_totp_step_can_only_move_forward(store):
    aid = admin(store)
    store.set_totp_secret(aid, "S")
    store.enable_totp(aid, 10, [])
    assert store.update_totp_step(aid, 11)
    assert not store.update_totp_step(aid, 11)  # the same code twice (e.g. two racing requests)
    assert not store.update_totp_step(aid, 5)
    assert store.get_admin_by_id(aid)["totp_last_step"] == 11


def test_a_recovery_code_works_once(store):
    aid = admin(store)
    store.replace_recovery_codes(aid, ["h1", "h2", "h3"])
    first = store.unused_recovery_codes(aid)[0]["id"]
    assert store.use_recovery_code(first) and not store.use_recovery_code(first)
    assert len(store.unused_recovery_codes(aid)) == 2
    store.replace_recovery_codes(aid, ["n1"])  # regenerating invalidates every earlier code
    assert [r["code_hash"] for r in store.unused_recovery_codes(aid)] == ["n1"]


def test_the_pending_second_step_expires_and_needs_an_active_2fa_admin(store):
    aid = admin(store)
    store.set_totp_secret(aid, "S")
    store.enable_totp(aid, 1, [])
    store.create_pending_2fa(aid, "tok", 300)
    assert store.get_pending_2fa("tok")["admin_id"] == aid
    assert store.get_pending_2fa("unknown") is None
    store.create_pending_2fa(aid, "old", -1)
    assert store.get_pending_2fa("old") is None  # expired
    store.disable_totp(aid)
    assert store.get_pending_2fa("tok") is None  # the factor was turned off meanwhile
    store.delete_pending_2fa("tok")


def test_tokens_in_the_pending_table_are_hashed(store):
    aid = admin(store)
    store.create_pending_2fa(aid, "plain-token", 300)
    with store._conn() as db:
        assert "plain-token" not in [r[0] for r in db.execute("SELECT token_hash FROM pending_2fa")]
