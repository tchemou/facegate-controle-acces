import re
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facerec.store import Store
from facerec.web import security
from facerec.web.app import COOKIE, create_app

PASSWORD = "Correct-Horse-42"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.create_admin("admin", security.hash_password(PASSWORD))
    return s


@pytest.fixture
def client(store):
    return TestClient(create_app(store.path), follow_redirects=False, base_url="http://testserver")


def login(client, password=PASSWORD, username="admin"):
    return client.post("/login", data={"username": username, "password": password})


# --- passwords ------------------------------------------------------------------------


def test_password_hash_roundtrip_and_salt():
    h1, h2 = security.hash_password("Abcdefghij12"), security.hash_password("Abcdefghij12")
    assert h1 != h2  # random salt
    assert security.verify_password("Abcdefghij12", h1)
    assert not security.verify_password("abcdefghij12", h1)
    assert not security.verify_password("x", "not-a-hash")
    assert "Abcdefghij12" not in h1


@pytest.mark.parametrize("weak", ["court1A", "alllowercase123", "ALLUPPERCASE123", "NoDigitsHereAtAll"])
def test_weak_passwords_are_refused(weak):
    assert security.check_password_strength(weak)


def test_a_strong_password_is_accepted():
    assert security.check_password_strength(PASSWORD) is None


# --- sign-in --------------------------------------------------------------------------


def test_dashboard_requires_login(client):
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/api/events").status_code == 401
    assert client.get("/api/stats").status_code == 401


def test_login_sets_a_hardened_cookie_and_opens_the_dashboard(client):
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    page = client.get("/")
    assert page.status_code == 200 and "Tableau de bord" in page.text


def test_wrong_password_and_unknown_user_look_the_same(client):
    a = login(client, "wrong-password-1A")
    b = login(client, "wrong-password-1A", username="nobody")
    assert a.status_code == b.status_code == 401
    assert a.text == b.text  # no user enumeration
    assert COOKIE not in a.headers.get("set-cookie", "")


def test_login_is_throttled_after_repeated_failures(client):
    for _ in range(security.MAX_FAILURES):
        assert login(client, "bad-password-1A").status_code == 401
    blocked = login(client, PASSWORD)  # even the right password is refused while locked
    assert blocked.status_code == 429


def test_a_successful_login_resets_the_failure_counter(client):
    for _ in range(security.MAX_FAILURES - 1):
        login(client, "bad-password-1A")
    assert login(client).status_code == 303
    client.cookies.clear()
    for _ in range(security.MAX_FAILURES - 1):
        assert login(client, "bad-password-1A").status_code == 401


def test_secure_cookie_flag_when_behind_https(store):
    c = TestClient(create_app(store.path, secure_cookies=True), follow_redirects=False)
    assert "secure" in login(c).headers["set-cookie"].lower()


# --- logout, CSRF, origin -------------------------------------------------------------


def csrf_of(client):
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/").text).group(1)


def test_logout_needs_the_csrf_token_and_ends_the_session(client):
    login(client)
    assert client.post("/logout", data={"csrf": "wrong"}).status_code == 403
    assert client.get("/").status_code == 200  # still signed in
    assert client.post("/logout", data={"csrf": csrf_of(client)}).status_code == 303
    assert client.get("/").status_code == 303  # session is gone server-side


def test_a_cross_site_origin_is_refused(client):
    r = client.post("/login", data={"username": "admin", "password": PASSWORD}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert COOKIE not in r.headers.get("set-cookie", "")


def test_a_null_origin_is_refused_and_the_same_origin_is_accepted(client):
    """Origin `null` comes from sandboxed or data: pages, which an attacker can create."""
    null = client.post("/login", data={"username": "admin", "password": PASSWORD}, headers={"origin": "null"})
    assert null.status_code == 403
    same = client.post("/login", data={"username": "admin", "password": PASSWORD},
                       headers={"origin": "http://testserver", "host": "testserver"})
    assert same.status_code == 303


def test_referrer_policy_lets_browsers_send_the_origin_on_same_site_forms(client):
    """Regression: `no-referrer` made Chrome send `Origin: null`, locking real users out of sign-in."""
    assert client.get("/login").headers["referrer-policy"] == "same-origin"


def test_an_expired_session_is_rejected(store):
    app = create_app(store.path, session_ttl=0.05)
    c = TestClient(app, follow_redirects=False)
    login(c)
    time.sleep(0.1)
    assert c.get("/").status_code == 303


def test_session_tokens_are_stored_hashed(client, store):
    r = login(client)
    token = re.search(rf"{COOKIE}=([^;]+)", r.headers["set-cookie"]).group(1)
    with store._conn() as db:
        stored = [row["token_hash"] for row in db.execute("SELECT token_hash FROM sessions")]
    assert stored and token not in stored


# --- headers and exposure -------------------------------------------------------------


def test_security_headers_are_present(client):
    r = client.get("/login")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_api_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_no_admin_yet_shows_how_to_create_one(tmp_path):
    c = TestClient(create_app(tmp_path / "empty.db"), follow_redirects=False)
    assert "facerec admin create" in c.get("/login").text


# --- data --------------------------------------------------------------------------------


def test_dashboard_and_api_show_events(client, store):
    store.add_event("granted", "alice", "lab")
    store.add_event("denied", None, "lab", "unknown_person")
    store.add_event("denied", "bob", "lab", "challenge_timeout:turn_left")
    login(client)
    page = client.get("/").text
    assert "Personne inconnue" in page and "Vérification de présence non réussie" in page
    stats = client.get("/api/stats").json()
    assert stats["granted"] == 1 and stats["denied"] == 2
    events = client.get("/api/events", params={"decision": "denied"}).json()
    assert {e["person"] for e in events} == {None, "bob"}


def test_html_in_data_is_escaped(client, store):
    store.add_event("granted", "<script>alert(1)</script>", "lab")
    login(client)
    page = client.get("/").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_api_limits_are_clamped(client, store):
    login(client)
    assert client.get("/api/events", params={"limit": 10**9}).status_code == 200
    assert client.get("/api/stats", params={"hours": -5}).status_code == 200


# --- store ------------------------------------------------------------------------------------


def test_people_are_stored_as_embeddings_and_erasable(store):
    vectors = np.random.default_rng(0).normal(size=(3, 128)).astype(np.float32)
    pid = store.add_person("alice", vectors, "sface", "consent text")
    got = store.embeddings_by_person()["alice"]
    assert got.shape == (3, 128) and np.allclose(got, vectors)
    assert store.list_people()[0]["consent_at"] is not None
    store.set_person_active(pid, False)
    assert "alice" not in store.embeddings_by_person()
    assert store.delete_person(pid) and not store.delete_person(pid)
    assert store.list_people() == []
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0  # erased with the person


def test_policy_dict_from_the_database_feeds_the_access_policy(store):
    from datetime import datetime

    from facerec.policy import AccessPolicy

    alice = store.add_person("alice", np.zeros((1, 128)), "sface")
    bob = store.add_person("bob", np.zeros((1, 128)), "sface")
    lab = store.add_zone("lab")
    store.add_rule(lab, alice, always=True)
    store.add_rule(lab, bob, days=["mon"], start="08:00", end="18:00")
    policy = AccessPolicy.from_dict(store.policy_dict())
    monday_10h, saturday = datetime(2026, 10, 26, 10), datetime(2026, 10, 31, 10)
    assert policy.check("alice", "lab", saturday)[0]
    assert policy.check("bob", "lab", monday_10h)[0] and not policy.check("bob", "lab", saturday)[0]
    store.set_person_active(bob, False)  # a deactivated person loses access immediately
    assert not AccessPolicy.from_dict(store.policy_dict()).check("bob", "lab", monday_10h)[0]


def test_event_retention_purge(store):
    store.add_event("granted", "a", at=time.time() - 40 * 86400)
    store.add_event("granted", "b")
    assert store.purge_events_older_than(30) == 1
    assert [e["person"] for e in store.list_events()] == ["b"]
