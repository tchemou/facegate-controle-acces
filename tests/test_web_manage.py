import csv
import io
import re
import time
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facerec.policy import AccessPolicy
from facerec.store import Store
from facerec.web import security
from facerec.web.app import create_app

PASSWORD = "Correct-Horse-42"
MON_10H = datetime(2026, 10, 26, 10, 0)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.create_admin("admin", security.hash_password(PASSWORD))
    return s


@pytest.fixture
def client(store):
    c = TestClient(create_app(store.path), follow_redirects=False)
    assert c.post("/login", data={"username": "admin", "password": PASSWORD}).status_code == 303
    return c


def csrf(client) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/people").text).group(1)


def person(store, name="alice", n=3):
    return store.add_person(name, np.random.default_rng(len(name)).normal(size=(n, 128)), "sface", "consent")


def audit_actions(store) -> list[str]:
    """Administration actions, newest first, without the sign-in entries every test starts with."""
    return [a["action"] for a in store.list_audit() if a["action"] != "login"]


def flash_code(response) -> str:
    return response.headers["location"].split("m=")[1]


PAGES = ["/people", "/zones", "/events", "/events.csv", "/audit"]
ACTIONS = ["/people/1/toggle", "/people/1/delete", "/zones", "/zones/1/delete", "/rules", "/rules/1/delete"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_requires_login(store, path):
    anonymous = TestClient(create_app(store.path), follow_redirects=False)
    r = anonymous.get(path)
    assert r.status_code == 303 and r.headers["location"] == "/login"


@pytest.mark.parametrize("path", ACTIONS)
def test_every_action_requires_login_and_a_valid_csrf_token(store, client, path):
    person(store)
    store.add_zone("lab")
    anonymous = TestClient(create_app(store.path), follow_redirects=False)
    assert anonymous.post(path, data={"csrf": "x"}).status_code == 303  # not signed in
    revision = store.revision()
    body = {"csrf": "wrong", "name": "Z", "zone_id": 1, "person_id": 1, "mode": "always"}
    assert client.post(path, data=body).status_code == 403  # signed in, bad token
    assert store.revision() == revision and audit_actions(store) == []  # and nothing happened


def test_every_form_carries_the_csrf_token(store, client):
    person(store)
    store.add_zone("lab")
    for path in ("/people", "/zones"):
        html = client.get(path).text
        assert html.count("<form") == html.count('name="csrf"')


# --- people -------------------------------------------------------------------------------------


def test_deactivating_a_person_takes_effect_and_is_audited(store, client):
    pid = person(store)
    before = store.revision()
    assert flash_code(client.post(f"/people/{pid}/toggle", data={"csrf": csrf(client)})) == "person_updated"
    assert "alice" not in store.embeddings_by_person()
    assert store.revision() > before  # the recognition service reloads on this signal
    assert flash_code(client.post(f"/people/{pid}/toggle", data={"csrf": csrf(client)})) == "person_updated"
    assert "alice" in store.embeddings_by_person()
    assert audit_actions(store) == ["person_activated", "person_deactivated"]


def test_erasure_needs_the_typed_name_and_removes_everything(store, client):
    pid = person(store)
    zone = store.add_zone("lab")
    store.add_rule(zone, pid, always=True)
    token = csrf(client)
    assert flash_code(client.post(f"/people/{pid}/delete", data={"csrf": token})) == "confirm_mismatch"
    assert flash_code(client.post(f"/people/{pid}/delete", data={"csrf": token, "confirm": "bob"})) == "confirm_mismatch"
    assert store.get_person(pid) is not None
    assert flash_code(client.post(f"/people/{pid}/delete", data={"csrf": token, "confirm": " ALICE "})) == "person_deleted"
    assert store.get_person(pid) is None and store.list_rules() == []
    with store._conn() as db:
        assert db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0
    assert store.list_audit()[0]["action"] == "person_deleted"


def test_unknown_ids_are_handled(client):
    token = csrf(client)
    for path, extra in (("/people/999/toggle", {}), ("/people/999/delete", {"confirm": "x"}),
                        ("/zones/999/delete", {}), ("/rules/999/delete", {})):
        assert flash_code(client.post(path, data={"csrf": token, **extra})) == "not_found"


# --- zones and rules ---------------------------------------------------------------------------------


def test_zone_names_are_validated(store, client):
    token = csrf(client)
    assert flash_code(client.post("/zones", data={"csrf": token, "name": "Laboratoire"})) == "zone_created"
    assert flash_code(client.post("/zones", data={"csrf": token, "name": "laboratoire"})) == "zone_exists"
    for bad in ("", "   ", "<script>alert(1)</script>", "x" * 41, "a;DROP TABLE zones", "-start"):
        assert flash_code(client.post("/zones", data={"csrf": token, "name": bad})) == "zone_invalid", bad
    assert [z["name"] for z in store.list_zones()] == ["Laboratoire"]


def test_creating_rules_feeds_the_access_policy(store, client):
    pid, zone = person(store), store.add_zone("lab")
    token = csrf(client)
    ok = client.post("/rules", data={"csrf": token, "zone_id": zone, "person_id": pid, "mode": "schedule",
                                     "days": ["mon", "tue"], "start": "08:00", "end": "18:00"})
    assert flash_code(ok) == "rule_created"
    policy = AccessPolicy.from_dict(store.policy_dict())
    assert policy.check("alice", "lab", MON_10H)[0]
    assert not policy.check("alice", "lab", datetime(2026, 10, 31, 10))[0]  # a Saturday
    assert store.list_audit()[0]["action"] == "rule_created"


@pytest.mark.parametrize("fields", [
    {"mode": "schedule", "start": "08:00", "end": "18:00"},  # no day
    {"mode": "schedule", "days": ["funday"], "start": "08:00", "end": "18:00"},
    {"mode": "schedule", "days": ["mon"], "start": "08:00", "end": "08:00"},  # empty window
    {"mode": "schedule", "days": ["mon"], "start": "25:00", "end": "18:00"},
    {"mode": "schedule", "days": ["mon"], "start": "8:00", "end": "18:00"},
    {"mode": "schedule", "days": ["mon"], "start": "", "end": ""},
])
def test_invalid_rules_are_refused(store, client, fields):
    pid, zone = person(store), store.add_zone("lab")
    r = client.post("/rules", data={"csrf": csrf(client), "zone_id": zone, "person_id": pid, **fields})
    assert flash_code(r) == "rule_invalid" and store.list_rules() == []


def test_always_rule_ignores_schedule_fields_and_can_be_removed(store, client):
    pid, zone = person(store), store.add_zone("lab")
    token = csrf(client)
    client.post("/rules", data={"csrf": token, "zone_id": zone, "person_id": pid, "mode": "always",
                                "days": ["mon"], "start": "99:99", "end": "zz"})
    (rule,) = store.list_rules()
    assert rule["always"] and rule["start"] is None
    assert flash_code(client.post(f"/rules/{rule['id']}/delete", data={"csrf": token})) == "rule_deleted"
    assert store.list_rules() == []


def test_deleting_a_zone_removes_its_rules(store, client):
    pid, zone = person(store), store.add_zone("lab")
    store.add_rule(zone, pid, always=True)
    assert flash_code(client.post(f"/zones/{zone}/delete", data={"csrf": csrf(client)})) == "zone_deleted"
    assert store.list_rules() == [] and store.list_zones() == []


# --- journal ----------------------------------------------------------------------------------------------


@pytest.fixture
def events(store):
    now = time.time()
    store.add_event("granted", "alice", "lab", at=now - 5 * 86400)
    store.add_event("denied", "bob", "lab", "policy:outside_schedule", at=now - 2 * 86400)
    store.add_event("denied", None, "server", "unknown_person", at=now - 3600)
    for i in range(30):
        store.add_event("granted", "carol", "server", at=now - 60 * (i + 1))


def test_events_page_filters(client, events):
    assert "33 événements" in client.get("/events").text
    assert "1 événement correspondant" in client.get("/events", params={"person": "bob"}).text
    assert "31 événements" in client.get("/events", params={"zone": "server"}).text
    assert "1 événement correspondant" in client.get("/events", params={"decision": "denied", "zone": "lab"}).text
    denied = client.get("/events", params={"decision": "denied"}).text
    assert "2 événements correspondant" in denied
    since = datetime.fromtimestamp(time.time() - 3 * 86400).strftime("%Y-%m-%d")
    assert "32 événements" in client.get("/events", params={"from": since}).text


def test_events_date_range_is_inclusive_of_the_last_day(store, client):
    day = datetime(2026, 3, 10, 23, 30).timestamp()
    store.add_event("granted", "late", "lab", at=day)
    page = client.get("/events", params={"from": "2026-03-10", "to": "2026-03-10"}).text
    assert "1 événement correspondant" in page


def test_events_pagination_and_clamping(client, events):
    first = client.get("/events").text
    assert "Page 1 sur 2" in first and "Suivant" in first and "Précédent" not in first
    second = client.get("/events", params={"page": 2}).text
    assert "Page 2 sur 2" in second and "Précédent" in second
    assert "Page 2 sur 2" in client.get("/events", params={"page": 999}).text
    assert "Page 1 sur 2" in client.get("/events", params={"page": -4}).text


def test_invalid_filters_are_ignored_not_fatal(client, events):
    r = client.get("/events", params={"decision": "hacked", "from": "not-a-date", "to": "2026-99-99", "page": "x"})
    assert r.status_code in (200, 422)
    ok = client.get("/events", params={"decision": "hacked", "from": "not-a-date", "to": "2026-99-99"})
    assert ok.status_code == 200 and "33 événements" in ok.text


def test_csv_export_is_safe_filtered_and_audited(store, client):
    store.add_event("denied", "=HYPERLINK(\"http://evil\",\"x\")", "lab", "unknown_person")
    store.add_event("granted", "+SUM(1)", "lab")
    store.add_event("granted", "normal", "lab")
    r = client.get("/events.csv", params={"zone": "lab"})
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["date", "personne", "zone", "decision", "raison"]
    people = {row[1] for row in rows[1:]}
    assert "normal" in people
    assert all(not p.startswith(("=", "+", "-", "@")) for p in people)  # formulas are neutralised
    assert any(p.startswith("'=") for p in people)
    assert store.list_audit()[0]["action"] == "events_exported"
    only_granted = list(csv.reader(io.StringIO(client.get("/events.csv", params={"decision": "granted"}).text)))
    assert all(row[3] == "granted" for row in only_granted[1:])


# --- output safety -----------------------------------------------------------------------------------------


def test_the_flash_code_in_the_url_is_never_echoed(client):
    page = client.get("/people", params={"m": "<script>alert(1)</script>"}).text
    assert "alert(1)" not in page
    assert "Statut mis à jour" in client.get("/people", params={"m": "person_updated"}).text


def test_hostile_names_already_in_the_database_are_escaped_everywhere(store, client):
    evil = '<img src=x onerror=alert(1)>'
    pid = store.add_person(evil, np.zeros((1, 128)), "sface")
    zone = store.add_zone(evil)
    store.add_rule(zone, pid, always=True)
    store.add_event("granted", evil, evil)
    store.add_audit("admin", "person_deleted", evil)
    for path in ("/", "/people", "/zones", "/events", "/audit"):
        html = client.get(path).text
        assert evil not in html, path
        assert "&lt;img" in html, path


def test_audit_page_lists_actions_in_order(store, client):
    token = csrf(client)
    client.post("/zones", data={"csrf": token, "name": "lab"})
    client.post("/zones", data={"csrf": token, "name": "server"})
    page = client.get("/audit").text
    assert "Zone créée" in page
    assert page.index(">server<") < page.index(">lab<")  # newest first (matching table cells, not "aria-label")


def test_navigation_links_to_every_page(client):
    html = client.get("/").text
    for href in ("/people", "/zones", "/events", "/audit"):
        assert f'href="{href}"' in html
