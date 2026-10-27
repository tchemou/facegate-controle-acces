import time

import pytest
from fastapi.testclient import TestClient

from facerec.store import Store
from facerec.web import security
from facerec.web.app import create_app

PW = "Correct-Horse-42"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    for name, role in (("own", "owner"), ("man", "manager"), ("vie", "viewer")):
        s.create_admin(name, security.hash_password(PW), role)
    return s


def signed_in(store, name):
    client = TestClient(create_app(store.path), follow_redirects=False)
    assert client.post("/login", data={"username": name, "password": PW}).status_code == 303
    return client


def test_overview_counts_series_reasons_and_alerts(store):
    now = time.time()
    for i in range(3):
        store.add_event("granted", "Ana", "lab", at=now - 60 * i)
    store.add_event("denied", None, "lab", "screen_detected", at=now - 30)
    store.add_event("denied", None, "lab", "unknown", at=now - 20)
    o = store.overview("24h", now=now)
    assert len(o["starts"]) == len(o["granted"]) == len(o["denied"]) == 24
    assert (sum(o["granted"]), sum(o["denied"])) == (3, 2)
    assert o["reasons"] == {"screen_detected": 1, "unknown": 1} or o["reasons"] == {"unknown": 1, "screen_detected": 1}
    assert o["alerts"] == 1


def test_overview_compares_with_the_previous_period(store):
    now = time.time()
    store.add_event("granted", "Ana", "lab", at=now - 30 * 3600)
    assert store.overview("24h", now=now)["previous"]["granted"] == 1


def test_unknown_period_falls_back_to_24h(store):
    assert store.overview("nope")["period"] == "24h"


def test_heatmap_counts_by_weekday_and_hour(store):
    now = time.time()
    store.add_event("granted", "Ana", "lab", at=now - 10)
    h = store.heatmap(28, now=now)
    assert h["total"] == 1 and h["max"] == 1 and len(h["grid"]) == 7 and len(h["grid"][0]) == 24


def test_api_overview_requires_login(store):
    client = TestClient(create_app(store.path), follow_redirects=False)
    assert client.get("/api/overview").status_code == 401


def test_viewer_gets_no_posture_or_service_state(store):
    data = signed_in(store, "vie").get("/api/overview").json()
    assert "posture" not in data and "service" not in data and "events" in data


def test_manager_and_owner_get_posture(store):
    for name in ("man", "own"):
        data = signed_in(store, name).get("/api/overview?period=7j").json()
        assert 0 <= data["posture"]["score"] <= 100 and data["period"] == "7j"
        assert data["service"]["enabled"] is False


def test_navigation_only_shows_what_the_role_may_open(store):
    viewer = signed_in(store, "vie").get("/").text
    assert 'href="/people"' not in viewer and 'href="/admins"' not in viewer and 'href="/events"' in viewer
    owner = signed_in(store, "own").get("/").text
    assert 'href="/admins"' in owner and 'href="/compliance"' in owner
