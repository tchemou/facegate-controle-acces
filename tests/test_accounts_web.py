import re
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facerec.store import Store
from facerec.web import accounts, security, totp
from facerec.web.app import COOKIE, create_app

PW = "Correct-Horse-42"
NEW_PW = "Another-Strong-Pass-7"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.fixture
def app(store):
    return create_app(store.path)


def make(store, name, role="owner", password=PW, **kw):
    return store.create_admin(name, security.hash_password(password), role, **kw)


def signed_in(app, name, password=PW):
    client = TestClient(app, follow_redirects=False)
    assert client.post("/login", data={"username": name, "password": password}).status_code == 303
    return client


def csrf(client, path="/account"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def flash(response) -> str:
    return response.headers["location"].split("m=")[1]


def audit(store):
    return [a["action"] for a in store.list_audit() if a["action"] != "login"]


@pytest.fixture
def team(store, app):
    """One administrator of each role, all signed in."""
    for name, role in (("olivia", "owner"), ("manon", "manager"), ("victor", "viewer")):
        make(store, name, role)
    return {n: signed_in(app, n) for n in ("olivia", "manon", "victor")}


# --- who may open what -----------------------------------------------------------------------------------------

PAGES = {  # path: lowest role allowed
    "/": "viewer", "/events": "viewer", "/account": "viewer",
    "/people": "manager", "/people/new": "manager", "/zones": "manager", "/live": "manager", "/events.csv": "manager",
    "/audit": "owner", "/admins": "owner",
}
RANKS = {"viewer": 1, "manager": 2, "owner": 3}
ROLE_OF = {"olivia": "owner", "manon": "manager", "victor": "viewer"}


@pytest.mark.parametrize("path, lowest", PAGES.items())
@pytest.mark.parametrize("who", ["olivia", "manon", "victor"])
def test_page_permission_matrix(team, who, path, lowest):
    r = team[who].get(path)
    if RANKS[ROLE_OF[who]] >= RANKS[lowest]:
        assert r.status_code == 200, (who, path)
    else:
        assert r.status_code == 403 and "Accès refusé" in r.text, (who, path)  # a readable page, not raw JSON


@pytest.mark.parametrize("path, lowest", {
    "/api/stats": "viewer", "/api/events": "viewer", "/api/live": "manager", "/api/enroll/1": "manager",
    "/live/stream": "manager",
}.items())
@pytest.mark.parametrize("who", ["olivia", "manon", "victor"])
def test_api_permission_matrix(team, who, path, lowest):
    r = team[who].get(path)
    if RANKS[ROLE_OF[who]] < RANKS[lowest]:
        assert r.status_code == 403 and r.json() == {"detail": "Droits insuffisants"}
    else:
        assert r.status_code != 403, (who, path)


def test_a_lower_role_cannot_run_actions_and_nothing_changes(store, team):
    pid = store.add_person("alice", np.zeros((1, 128)), "sface")
    zone = store.add_zone("lab")
    revision, admins = store.revision(), len(store.list_admins())
    viewer, manager = team["victor"], team["manon"]
    token = lambda c, p="/": csrf(c, p)  # noqa: E731
    attempts = [
        (viewer, "/people/%d/toggle" % pid, {}), (viewer, "/people/%d/delete" % pid, {"confirm": "alice"}),
        (viewer, "/zones", {"name": "x"}), (viewer, "/zones/%d/delete" % zone, {}),
        (viewer, "/rules", {"zone_id": zone, "person_id": pid, "mode": "always"}),
        (viewer, "/people/enroll", {"name": "Bob", "consent": "yes"}),
        (manager, "/admins", {"username": "intruder", "role": "owner", "password": NEW_PW}),
        (manager, "/admins/1/role", {"role": "viewer"}), (manager, "/admins/1/active", {}),
        (manager, "/admins/1/password", {"password": NEW_PW}), (manager, "/admins/1/reset-2fa", {}),
    ]
    for client, path, data in attempts:
        assert client.post(path, data={"csrf": token(client, "/account"), **data}).status_code == 403, path
    assert store.revision() == revision and len(store.list_admins()) == admins
    assert store.get_person(pid)["active"] == 1 and len(store.list_zones()) == 1 and store.list_rules() == []


@pytest.mark.parametrize("who, hidden, shown", [
    ("victor", ["/people", "/zones", "/live", "/admins", "/audit"], ["/events", "/account"]),
    ("manon", ["/admins", "/audit"], ["/people", "/zones", "/live", "/events"]),
    ("olivia", [], ["/people", "/zones", "/live", "/events", "/admins", "/audit"]),
])
def test_the_menu_only_offers_what_the_role_allows(team, who, hidden, shown):
    html = team[who].get("/").text
    nav = html.split('aria-label="Navigation principale"')[1].split("</nav>")[0]
    for path in shown:
        assert f'href="{path}"' in html
    for path in hidden:
        assert f'href="{path}"' not in nav


def test_the_menu_shows_who_is_signed_in_and_their_role(team):
    html = team["manon"].get("/").text
    assert "manon" in html and "Gestionnaire" in html and 'href="/account"' in html


def test_a_role_change_applies_to_the_next_request_without_signing_in_again(store, team):
    assert team["manon"].get("/people").status_code == 200
    manon = store.get_admin("manon")
    assert team["olivia"].post(f"/admins/{manon['id']}/role", data={"csrf": csrf(team["olivia"]), "role": "viewer"}
                               ).status_code == 303
    assert team["manon"].get("/people").status_code == 403
    assert team["manon"].get("/").status_code == 200


# --- disabled accounts --------------------------------------------------------------------------------------------------


def test_deactivating_signs_the_person_out_and_blocks_new_sign_ins(store, app, team):
    victor = store.get_admin("victor")
    olivia = team["olivia"]
    assert olivia.post(f"/admins/{victor['id']}/active", data={"csrf": csrf(olivia)}).status_code == 303
    assert team["victor"].get("/").headers["location"] == "/login"  # the open session is gone
    again = TestClient(app, follow_redirects=False).post("/login", data={"username": "victor", "password": PW})
    assert again.status_code == 401 and COOKIE not in again.headers.get("set-cookie", "")
    olivia.post(f"/admins/{victor['id']}/active", data={"csrf": csrf(olivia)})  # reactivate
    assert signed_in(app, "victor").get("/").status_code == 200


def test_unknown_wrong_password_and_disabled_accounts_are_indistinguishable(store, app):
    make(store, "ghost")
    store.set_admin_active(store.get_admin("ghost")["id"], False)
    make(store, "real")
    answers = []
    for name, password in (("nobody", PW), ("real", "wrong-Password-1"), ("ghost", PW)):
        r = TestClient(app, follow_redirects=False).post("/login", data={"username": name, "password": password})
        answers.append((r.status_code, r.text))
    assert len(set(answers)) == 1 and answers[0][0] == 401  # same status, same page: no account enumeration


# --- forced password change -------------------------------------------------------------------------------------------------


def test_a_temporary_password_must_be_replaced_before_anything_else(store, app):
    make(store, "newbie", "manager", password="Temporary-Pass-1", must_change_password=True)
    c = signed_in(app, "newbie", "Temporary-Pass-1")
    for path in ("/", "/people", "/events", "/zones"):
        r = c.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/account?m=must_change", path
    assert c.get("/api/stats").status_code == 403  # the API says why too
    page = c.get("/account")
    assert page.status_code == 200 and "choisissez-en un nouveau" in page.text
    ok = c.post("/account/password", data={"csrf": csrf(c), "current_password": "Temporary-Pass-1",
                                           "new_password": NEW_PW, "confirm_password": NEW_PW})
    assert flash(ok) == "password_changed"
    assert c.get("/people").status_code == 200  # free to work now


def test_signing_out_is_always_possible_even_with_a_required_password_change(store, app):
    make(store, "newbie", "viewer", password="Temporary-Pass-1", must_change_password=True)
    c = signed_in(app, "newbie", "Temporary-Pass-1")
    assert c.post("/logout", data={"csrf": csrf(c)}).headers["location"] == "/login"


# --- changing my password -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fields, code", [
    ({"current_password": "wrong", "new_password": NEW_PW, "confirm_password": NEW_PW}, "bad_current"),
    ({"current_password": PW, "new_password": NEW_PW, "confirm_password": NEW_PW + "x"}, "password_mismatch"),
    ({"current_password": PW, "new_password": PW, "confirm_password": PW}, "same_password"),
    ({"current_password": PW, "new_password": "short1A", "confirm_password": "short1A"}, "weak_password"),
    ({"current_password": PW, "new_password": "alllowercase12345", "confirm_password": "alllowercase12345"}, "weak_password"),
])
def test_password_change_validation(store, app, fields, code):
    make(store, "anne")
    c = signed_in(app, "anne")
    assert flash(c.post("/account/password", data={"csrf": csrf(c), **fields})) == code
    assert security.verify_password(PW, store.get_admin("anne")["password_hash"])  # unchanged


def test_changing_the_password_closes_the_other_sessions_and_the_old_password_stops_working(store, app):
    make(store, "anne")
    here, elsewhere = signed_in(app, "anne"), signed_in(app, "anne")
    assert flash(here.post("/account/password", data={"csrf": csrf(here), "current_password": PW,
                                                      "new_password": NEW_PW, "confirm_password": NEW_PW})) == "password_changed"
    assert here.get("/").status_code == 200 and elsewhere.get("/").headers["location"] == "/login"
    assert TestClient(app).post("/login", data={"username": "anne", "password": PW}, follow_redirects=False).status_code == 401
    assert signed_in(app, "anne", NEW_PW)
    assert "password_changed" in audit(store)


def test_account_forms_need_the_csrf_token(store, app):
    make(store, "anne")
    c = signed_in(app, "anne")
    for path, data in (("/account/password", {"current_password": PW, "new_password": NEW_PW, "confirm_password": NEW_PW}),
                       ("/account/2fa/start", {"password": PW}), ("/account/2fa/disable", {"password": PW}),
                       ("/account/2fa/recovery", {"password": PW}), ("/account/2fa/confirm", {"code": "123456"})):
        assert c.post(path, data={"csrf": "wrong", **data}).status_code == 403, path
    assert security.verify_password(PW, store.get_admin("anne")["password_hash"])


# --- second factor --------------------------------------------------------------------------------------------------------------


def enable_2fa(client, store, name):
    """Run the whole enrollment; returns (secret, recovery codes)."""
    assert client.post("/account/2fa/start", data={"csrf": csrf(client), "password": PW}).status_code == 303
    secret = store.get_admin(name)["totp_secret"]
    page = client.post("/account/2fa/confirm", data={"csrf": csrf(client, "/account/2fa"), "code": totp.code_at(secret)})
    assert page.status_code == 200
    return secret, re.findall(r"<code>([a-z2-9]{5}-[a-z2-9]{5})</code>", page.text)


def test_the_enrollment_shows_a_qr_and_the_secret_and_needs_the_password(store, app):
    make(store, "anne")
    c = signed_in(app, "anne")
    assert flash(c.post("/account/2fa/start", data={"csrf": csrf(c), "password": "wrong"})) == "bad_current"
    assert store.get_admin("anne")["totp_secret"] is None  # a hijacked session cannot add a factor
    c.post("/account/2fa/start", data={"csrf": csrf(c), "password": PW})
    page = c.get("/account/2fa").text
    secret = store.get_admin("anne")["totp_secret"]
    assert "<svg" in page and totp.format_secret(secret) in page and "Impossible de scanner" in page
    assert not re.search(r"<script(?![^>]*\bsrc=)", page) and not re.search(r"\sstyle=", page)


def test_a_wrong_code_does_not_enable_the_second_factor(store, app):
    make(store, "anne")
    c = signed_in(app, "anne")
    c.post("/account/2fa/start", data={"csrf": csrf(c), "password": PW})
    r = c.post("/account/2fa/confirm", data={"csrf": csrf(c, "/account/2fa"), "code": "000000"})
    assert flash(r) == "2fa_bad_code" and store.get_admin("anne")["totp_enabled"] == 0


def test_enabling_shows_eight_recovery_codes_once_and_stores_only_hashes(store, app):
    make(store, "anne")
    c = signed_in(app, "anne")
    secret, codes = enable_2fa(c, store, "anne")
    assert len(codes) == 8 and store.get_admin("anne")["totp_enabled"] == 1 and "2fa_enabled" in audit(store)
    with store._conn() as db:
        stored = [r[0] for r in db.execute("SELECT code_hash FROM recovery_codes")]
    assert len(stored) == 8 and not any(code.replace("-", "") in "".join(stored) for code in codes)
    assert "j'ai noté" not in c.get("/account").text.lower() and not any(code in c.get("/account").text for code in codes)


def login_start(app, name, password=PW):
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"username": name, "password": password})
    return c, r


def test_with_a_second_factor_the_password_alone_opens_nothing(store, app):
    make(store, "anne")
    secret, _ = enable_2fa(signed_in(app, "anne"), store, "anne")
    c, r = login_start(app, "anne")
    assert r.status_code == 303 and r.headers["location"] == "/login/2fa"
    assert COOKIE not in r.headers["set-cookie"] and accounts.PENDING_COOKIE in r.headers["set-cookie"]
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert c.get("/").headers["location"] == "/login"  # the half-signed-in browser is not signed in
    assert c.get("/people").status_code == 303


def test_the_right_code_completes_the_sign_in_and_a_code_cannot_be_replayed(store, app):
    make(store, "anne")
    secret, _ = enable_2fa(signed_in(app, "anne"), store, "anne")
    c, _ = login_start(app, "anne")
    code = totp.code_at(secret, time.time() + 30)  # a later step than the one used to enable it
    done = c.post("/login/2fa", data={"code": code})
    assert done.status_code == 303 and done.headers["location"] == "/" and c.get("/").status_code == 200
    assert accounts.PENDING_COOKIE in done.headers["set-cookie"]  # and the pending cookie is cleared
    replay, _ = login_start(app, "anne")
    assert replay.post("/login/2fa", data={"code": code}).status_code == 401  # the same code, again
    assert replay.get("/").status_code == 303


def test_a_wrong_code_is_refused_without_a_session(store, app):
    make(store, "anne")
    enable_2fa(signed_in(app, "anne"), store, "anne")
    c, _ = login_start(app, "anne")
    r = c.post("/login/2fa", data={"code": "000000"})
    assert r.status_code == 401 and "Code incorrect" in r.text and c.get("/").status_code == 303


def test_the_second_step_is_throttled_and_the_pending_token_dies(store, app):
    make(store, "anne")
    secret, _ = enable_2fa(signed_in(app, "anne"), store, "anne")
    c, _ = login_start(app, "anne")
    for _ in range(security.MAX_FAILURES):
        assert c.post("/login/2fa", data={"code": "111111"}).status_code == 401
    good = totp.code_at(secret, time.time() + 30)
    blocked = c.post("/login/2fa", data={"code": good})  # even the right code is refused now
    assert blocked.status_code == 429 and c.get("/").status_code == 303
    assert c.post("/login/2fa", data={"code": good}).headers["location"] == "/login"  # the pending step is gone


def test_a_recovery_code_works_exactly_once(store, app):
    make(store, "anne")
    _, codes = enable_2fa(signed_in(app, "anne"), store, "anne")
    c, _ = login_start(app, "anne")
    assert c.post("/login/2fa", data={"code": codes[0].upper()}).status_code == 303  # case and dash are forgiven
    assert c.get("/").status_code == 200
    c2, _ = login_start(app, "anne")
    assert c2.post("/login/2fa", data={"code": codes[0]}).status_code == 401
    c3, _ = login_start(app, "anne")
    assert c3.post("/login/2fa", data={"code": codes[1]}).status_code == 303  # another one is still good
    assert "recovery_code_used" in audit(store)


def test_a_recovery_code_is_not_a_password_and_a_password_is_not_a_code(store, app):
    make(store, "anne")
    _, codes = enable_2fa(signed_in(app, "anne"), store, "anne")
    assert login_start(app, "anne", codes[0])[1].status_code == 401
    c, _ = login_start(app, "anne")
    assert c.post("/login/2fa", data={"code": PW}).status_code == 401


def test_an_expired_or_unknown_pending_step_goes_back_to_the_start(store, app):
    make(store, "anne")
    secret, _ = enable_2fa(signed_in(app, "anne"), store, "anne")
    fresh = TestClient(app, follow_redirects=False)
    assert fresh.get("/login/2fa").headers["location"] == "/login"
    assert fresh.post("/login/2fa", data={"code": "123456"}).headers["location"] == "/login"
    c, _ = login_start(app, "anne")
    with store._conn() as db:
        db.execute("UPDATE pending_2fa SET expires_at = 0")
    assert c.get("/login/2fa").headers["location"] == "/login"


def test_turning_the_factor_off_elsewhere_voids_a_pending_step(store, app):
    make(store, "anne")
    secret, _ = enable_2fa(signed_in(app, "anne"), store, "anne")
    c, _ = login_start(app, "anne")
    store.disable_totp(store.get_admin("anne")["id"])
    assert c.post("/login/2fa", data={"code": totp.code_at(secret, time.time() + 30)}).headers["location"] == "/login"


def test_regenerating_recovery_codes_invalidates_the_old_ones(store, app):
    make(store, "anne")
    c0 = signed_in(app, "anne")
    _, old = enable_2fa(c0, store, "anne")
    assert flash(c0.post("/account/2fa/recovery", data={"csrf": csrf(c0), "password": "wrong"})) == "bad_current"
    page = c0.post("/account/2fa/recovery", data={"csrf": csrf(c0), "password": PW})
    new = re.findall(r"<code>([a-z2-9]{5}-[a-z2-9]{5})</code>", page.text)
    assert len(new) == 8 and not set(new) & set(old) and "recovery_regenerated" in audit(store)
    c, _ = login_start(app, "anne")
    assert c.post("/login/2fa", data={"code": old[0]}).status_code == 401
    c, _ = login_start(app, "anne")
    assert c.post("/login/2fa", data={"code": new[0]}).status_code == 303


def test_disabling_the_factor_needs_the_password(store, app):
    make(store, "anne")
    c = signed_in(app, "anne")
    enable_2fa(c, store, "anne")
    assert flash(c.post("/account/2fa/disable", data={"csrf": csrf(c), "password": "wrong"})) == "bad_current"
    assert store.get_admin("anne")["totp_enabled"] == 1
    assert flash(c.post("/account/2fa/disable", data={"csrf": csrf(c), "password": PW})) == "2fa_disabled"
    assert store.get_admin("anne")["totp_enabled"] == 0 and "2fa_disabled" in audit(store)
    assert login_start(app, "anne")[1].headers["location"] == "/"  # password alone again


# --- administrator management ----------------------------------------------------------------------------------------------------


def test_creating_an_administrator(store, app, team):
    olivia = team["olivia"]
    r = olivia.post("/admins", data={"csrf": csrf(olivia), "username": "camille.martin", "role": "manager", "password": NEW_PW})
    assert flash(r) == "admin_created"
    row = store.get_admin("camille.martin")
    assert row["role"] == "manager" and row["must_change_password"] == 1 and row["created_by"] == "olivia"
    assert "admin_created" in audit(store) and signed_in(app, "camille.martin", NEW_PW)


@pytest.mark.parametrize("fields, code", [
    ({"username": "ab", "role": "viewer", "password": NEW_PW}, "admin_invalid"),
    ({"username": "x" * 33, "role": "viewer", "password": NEW_PW}, "admin_invalid"),
    ({"username": "bad name", "role": "viewer", "password": NEW_PW}, "admin_invalid"),
    ({"username": "<b>x</b>", "role": "viewer", "password": NEW_PW}, "admin_invalid"),
    ({"username": "fine.name", "role": "root", "password": NEW_PW}, "admin_invalid"),
    ({"username": "fine.name", "role": "", "password": NEW_PW}, "admin_invalid"),
    ({"username": "fine.name", "role": "viewer", "password": "weak"}, "weak_password"),
    ({"username": "OLIVIA", "role": "viewer", "password": NEW_PW}, "admin_exists"),
])
def test_creation_is_validated(store, team, fields, code):
    olivia, before = team["olivia"], len(store.list_admins())
    assert flash(olivia.post("/admins", data={"csrf": csrf(olivia), **fields})) == code
    assert len(store.list_admins()) == before


def test_owners_cannot_act_on_their_own_account(store, team):
    olivia, me = team["olivia"], store.get_admin("olivia")["id"]
    token = csrf(olivia)
    for path, data in ((f"/admins/{me}/role", {"role": "viewer"}), (f"/admins/{me}/active", {}),
                       (f"/admins/{me}/password", {"password": NEW_PW}), (f"/admins/{me}/reset-2fa", {})):
        assert flash(olivia.post(path, data={"csrf": token, **data})) == "admin_self", path
    row = store.get_admin("olivia")
    assert row["role"] == "owner" and row["active"] == 1


def test_the_last_active_owner_can_never_be_removed(store, app, team, monkeypatch):
    other = make(store, "second", "owner")
    olivia = team["olivia"]
    monkeypatch.setattr(app.state.store, "active_owner_count", lambda: 1)  # as if the other owners had gone meanwhile
    assert flash(olivia.post(f"/admins/{other}/role", data={"csrf": csrf(olivia), "role": "viewer"})) == "admin_last_owner"
    assert flash(olivia.post(f"/admins/{other}/active", data={"csrf": csrf(olivia)})) == "admin_last_owner"
    row = store.get_admin("second")
    assert row["role"] == "owner" and row["active"] == 1


def test_owners_can_demote_and_deactivate_each_other_while_another_owner_remains(store, team):
    other = make(store, "second", "owner")
    olivia = team["olivia"]
    assert flash(olivia.post(f"/admins/{other}/role", data={"csrf": csrf(olivia), "role": "manager"})) == "admin_updated"
    assert store.active_owner_count() == 1  # only olivia left: she is the one that must stay


def test_a_password_reset_forces_a_change_and_signs_the_person_out(store, app, team):
    olivia, manon = team["olivia"], store.get_admin("manon")
    assert flash(olivia.post(f"/admins/{manon['id']}/password", data={"csrf": csrf(olivia), "password": "weak"})) == "weak_password"
    assert flash(olivia.post(f"/admins/{manon['id']}/password", data={"csrf": csrf(olivia), "password": NEW_PW})) == "admin_password_reset"
    assert team["manon"].get("/").headers["location"] == "/login"  # signed out at once
    c = signed_in(app, "manon", NEW_PW)
    assert c.get("/people").headers["location"] == "/account?m=must_change"
    assert "admin_password_reset" in audit(store)


def test_an_owner_can_reset_someone_elses_second_factor(store, app, team):
    manon = store.get_admin("manon")
    enable_2fa(team["manon"], store, "manon")
    olivia = team["olivia"]
    assert flash(olivia.post(f"/admins/{manon['id']}/reset-2fa", data={"csrf": csrf(olivia)})) == "admin_2fa_reset"
    assert store.get_admin("manon")["totp_enabled"] == 0 and store.unused_recovery_codes(manon["id"]) == []
    assert "admin_2fa_reset" in audit(store)


def test_unknown_administrator_ids(team):
    olivia = team["olivia"]
    for path, data in (("/admins/999/role", {"role": "viewer"}), ("/admins/999/active", {}),
                       ("/admins/999/password", {"password": NEW_PW}), ("/admins/999/reset-2fa", {})):
        assert flash(olivia.post(path, data={"csrf": csrf(olivia), **data})) == "not_found", path


def test_the_admin_page_lists_everyone_with_their_state(store, team):
    html = team["olivia"].get("/admins").text
    for name in ("olivia", "manon", "victor"):
        assert name in html
    assert "Propriétaire" in html and "Gestionnaire" in html and "Lecteur" in html


def test_hostile_names_are_escaped_in_every_new_page(store, app):
    evil = "<img src=x onerror=alert(1)>"
    make(store, "boss")
    with store._conn() as db:
        db.execute("INSERT INTO admins (username, password_hash, created_at, role) VALUES (?, 'x', 1, 'viewer')", (evil,))
    store.add_audit(evil, "admin_created", evil)
    c = signed_in(app, "boss")
    for path in ("/admins", "/audit", "/account"):
        html = c.get(path).text
        assert evil not in html, path


def test_the_new_pages_work_under_the_strict_csp(store, app, team):
    for path in ("/account", "/admins", "/forbidden-probe"):
        client = team["olivia"]
        html = client.get(path).text
        assert not re.search(r"<script(?![^>]*\bsrc=)", html) and not re.search(r"\sstyle=", html)
    html = team["victor"].get("/people").text  # the 403 page too
    assert "Accès refusé" in html and not re.search(r"\sstyle=", html)


def test_the_login_audit_trail_records_sign_ins(store, app):
    make(store, "anne")
    signed_in(app, "anne")
    assert [a["action"] for a in store.list_audit()] == ["login"] and store.list_audit()[0]["admin"] == "anne"


# --- gaps found by mutation testing -------------------------------------------------------------------------------------------

EVERY_FORM_FIELD = {  # enough fields for any form to validate, so that only the CSRF token can be what is refused
    "csrf": "wrong", "name": "x", "username": "someone", "role": "viewer", "password": NEW_PW, "code": "123456",
    "confirm": "x", "zone_id": "1", "person_id": "1", "mode": "always", "current_password": PW,
    "new_password": NEW_PW, "confirm_password": NEW_PW, "consent": "yes", "days": "mon", "start": "08:00", "end": "09:00",
    "events_days": "90", "audit_days": "365",
}
PUBLIC_POSTS = {"/login", "/login/2fa"}  # no session yet: protected by throttling and the Origin check instead


def test_every_state_changing_route_refuses_a_wrong_csrf_token(store, app, team):
    """Walk the application's own routes: a POST route added later without a CSRF check fails this test."""
    olivia = team["olivia"]
    make(store, "target", "manager")
    snapshot = lambda: (  # noqa: E731
        [tuple(r) for r in store.list_admins()], store.revision(), len(store.list_zones()),
        [a["action"] for a in store.list_audit() if a["action"] != "login"],
    )
    before, checked = snapshot(), []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "POST" not in methods or route.path in PUBLIC_POSTS:
            continue
        path = re.sub(r"\{[^}]+\}", "1", route.path)
        response = olivia.post(path, data=EVERY_FORM_FIELD)
        assert response.status_code == 403, f"{route.path}: expected 403 for a wrong CSRF token, got {response.status_code}"
        checked.append(route.path)
    assert snapshot() == before  # and nothing changed anywhere
    assert {"/admins", "/admins/{admin_id}/role", "/admins/{admin_id}/active", "/admins/{admin_id}/password",
            "/admins/{admin_id}/reset-2fa", "/account/password", "/people/enroll", "/zones", "/logout",
            "/compliance/retention", "/compliance/purge"} <= set(checked)


def test_a_session_of_an_account_disabled_by_any_means_stops_working(store, app):
    """Defence in depth: even if the sessions were not deleted, a disabled account must not stay signed in."""
    make(store, "anne")
    c = signed_in(app, "anne")
    assert c.get("/").status_code == 200
    with store._conn() as db:  # disabled directly, bypassing `set_admin_active` (which would also delete the sessions)
        db.execute("UPDATE admins SET active = 0 WHERE username = 'anne'")
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1  # the session row is still there
    assert c.get("/").headers["location"] == "/login"
    assert c.get("/api/stats").status_code == 401
