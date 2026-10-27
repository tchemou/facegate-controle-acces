import json
import logging
import re
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from facerec import crypto
from facerec.crypto import VectorCipher
from facerec.store import ERASED, Store
from facerec.web import security
from facerec.web.app import create_app

PW = "Correct-Horse-42"
DAY = 86400


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def person(store, name, n=3):
    return store.add_person(name, np.random.default_rng(len(name)).normal(size=(n, 128)), "sface", "consent text")


def disk(tmp_path) -> bytes:
    return b"".join(p.read_bytes() for p in tmp_path.glob("t.db*"))


# --- retention settings ---------------------------------------------------------------------------------------


def test_default_retention_and_changing_it(store):
    assert store.retention_days() == (90, 365)
    store.set_retention_days(30, 180)
    assert store.retention_days() == (30, 180)
    assert Store(store.path).retention_days() == (30, 180)  # persisted
    store.set_retention_days(0, 0)  # 0 = no limit
    assert store.retention_days() == (0, 0)


@pytest.mark.parametrize("bad", [(6, 100), (100, 6), (3651, 100), (-1, 100), (100, -5)])
def test_invalid_retention_is_refused_and_changes_nothing(store, bad):
    store.set_retention_days(30, 180)
    with pytest.raises(ValueError):
        store.set_retention_days(*bad)
    assert store.retention_days() == (30, 180)


def test_the_boundaries_are_accepted(store):
    store.set_retention_days(7, 3650)
    assert store.retention_days() == (7, 3650)


# --- the purge ------------------------------------------------------------------------------------------------------


def test_the_purge_deletes_only_what_is_older_than_the_periods(store):
    store.set_retention_days(30, 100)
    now = time.time()
    store.add_event("granted", "old", "lab", at=now - 31 * DAY)
    store.add_event("granted", "recent", "lab", at=now - 29 * DAY)
    with store._conn() as db:
        db.executemany("INSERT INTO audit (at, admin, action, detail) VALUES (?, 'a', 'x', ?)",
                       [(now - 101 * DAY, "old"), (now - 99 * DAY, "recent")])
    assert store.apply_retention() == (1, 1)
    assert [e["person"] for e in store.list_events()] == ["recent"]
    assert [a["detail"] for a in store.list_audit()] == ["recent"]
    assert store.apply_retention() == (0, 0)  # idempotent


def test_zero_means_nothing_is_ever_purged(store):
    store.set_retention_days(0, 0)
    store.add_event("granted", "ancient", "lab", at=time.time() - 5000 * DAY)
    assert store.apply_retention() == (0, 0) and len(store.list_events()) == 1


def test_finished_enrollment_requests_do_not_outlive_a_month_but_running_ones_stay(store):
    done = store.request_enrollment("alice", "admin", "c")
    store.claim_enrollment()
    store.update_enrollment(done, status="done")
    running = store.request_enrollment("bob", "admin", "c")
    store.claim_enrollment()
    with store._conn() as db:
        db.execute("UPDATE enrollments SET created_at = ?", (time.time() - 40 * DAY,))
    store.apply_retention()
    assert store.get_enrollment(done) is None and store.get_enrollment(running)["status"] == "running"


def test_the_overview(store):
    assert store.retention_overview() == {"events": 0, "oldest_event": None, "audit": 0, "oldest_audit": None}
    store.add_event("granted", "a", at=1000.0)
    store.add_audit("admin", "x")
    overview = store.retention_overview()
    assert overview["events"] == 1 and overview["oldest_event"] == 1000.0 and overview["audit"] == 1


# --- erasure ---------------------------------------------------------------------------------------------------------


def test_erasing_a_person_anonymises_every_trace_of_their_name(store):
    done = store.request_enrollment("Camille Martin", "admin", "c")  # the enrollment that created the person
    store.claim_enrollment()
    store.update_enrollment(done, status="done")
    pid, other = person(store, "Camille Martin"), person(store, "Karim Benali")
    store.add_event("granted", "Camille Martin", "lab")
    store.add_event("denied", "camille martin", "lab", "policy:outside_schedule")  # a different case
    store.add_event("granted", "Karim Benali", "lab")
    store.add_audit("admin", "person_enrolled", "Camille Martin")
    store.add_audit("admin", "rule_created", "Camille Martin → Laboratoire (toujours)")
    store.add_audit("admin", "rule_created", "Karim Benali → Laboratoire (toujours)")

    assert store.delete_person(pid)
    assert sorted(e["person"] for e in store.list_events()) == sorted([ERASED, ERASED, "Karim Benali"])
    details = [a["detail"] for a in store.list_audit()]
    assert f"{ERASED} → Laboratoire (toujours)" in details and "Karim Benali → Laboratoire (toujours)" in details
    assert ERASED in details and not any("Camille" in d for d in details)
    assert store.get_enrollment(done) is None
    assert store.get_person(other)["name"] == "Karim Benali"


def test_a_decision_without_its_name_still_counts_in_the_statistics(store):
    pid = person(store, "Camille")
    for _ in range(3):
        store.add_event("granted", "Camille", "lab")
    store.delete_person(pid)
    assert store.stats(since=0)["granted"] == 3  # the log keeps its decisions, only the name goes


def test_erasing_a_short_name_leaves_longer_names_alone(store):
    """Erasing "Ann" must not damage the entries of "Anna" or "Joann"."""
    ann, _ = person(store, "Ann"), person(store, "Anna")
    store.add_event("granted", "Ann", "lab")
    store.add_event("granted", "Anna", "lab")
    store.add_audit("admin", "rule_created", "Anna → lab (toujours)")
    store.add_audit("admin", "rule_created", "Ann → lab, puis Anna → vault")
    store.delete_person(ann)
    assert sorted(e["person"] for e in store.list_events()) == sorted([ERASED, "Anna"])
    details = [a["detail"] for a in store.list_audit()]
    assert "Anna → lab (toujours)" in details
    assert f"{ERASED} → lab, puis Anna → vault" in details


def test_names_with_punctuation_and_underscores_are_erased_exactly(store):
    pid = person(store, "J. O'Neil_Smith")
    other = person(store, "J. O'NeilxSmith")  # `_` must not behave like a wildcard
    store.add_audit("admin", "person_enrolled", "J. O'Neil_Smith")
    store.add_audit("admin", "person_enrolled", "J. O'NeilxSmith")
    store.delete_person(pid)
    assert sorted(a["detail"] for a in store.list_audit()) == sorted([ERASED, "J. O'NeilxSmith"])
    assert store.get_person(other) is not None


def test_the_name_is_really_gone_from_the_database_files(tmp_path):
    store = Store(tmp_path / "t.db")
    pid = person(store, "Zoé Unique-Nom")
    store.add_event("granted", "Zoé Unique-Nom", "lab")
    store.add_audit("admin", "person_enrolled", "Zoé Unique-Nom")
    assert "Zoé Unique-Nom".encode() in disk(tmp_path)  # sanity: it was on disk
    store.delete_person(pid)
    assert "Zoé Unique-Nom".encode() not in disk(tmp_path)  # overwritten, not just unlinked


def test_erasing_an_unknown_person(store):
    assert store.delete_person(999) is False


# --- export (right of access) -------------------------------------------------------------------------------------------


def test_the_export_holds_what_is_known_about_the_person_and_no_one_else(store):
    pid, other = person(store, "Camille Martin", 4), person(store, "Karim Benali")
    zone = store.add_zone("Laboratoire")
    store.add_rule(zone, pid, days=["mon", "tue"], start="08:00", end="18:00")
    store.add_rule(zone, other, always=True)
    store.add_event("granted", "Camille Martin", "Laboratoire")
    store.add_event("denied", "Camille Martin", "Laboratoire", "policy:outside_schedule")
    store.add_event("granted", "Karim Benali", "Laboratoire")
    store.add_audit("admin", "person_enrolled", "Camille Martin")
    store.add_audit("admin", "person_enrolled", "Karim Benali")
    data = store.export_person(pid)
    assert data["person"]["name"] == "Camille Martin" and data["person"]["consent_text"] == "consent text"
    assert data["biometric_templates"] == [{"model": "sface", "count": 4}]
    assert data["access_rules"] == [{"zone": "Laboratoire", "always": False, "days": ["mon", "tue"],
                                     "from": "08:00", "to": "18:00"}]
    assert [e["decision"] for e in data["access_events"]] == ["denied", "granted"]  # newest first
    assert [a["action"] for a in data["administration_log"]] == ["person_enrolled"]
    assert "Karim" not in json.dumps(data)


def test_the_export_never_contains_the_embedding_values(store):
    vectors = np.random.default_rng(1).normal(size=(2, 128)).astype(np.float32)
    pid = store.add_person("Camille", vectors, "sface", "c")
    text = json.dumps(store.export_person(pid))
    assert not any(f"{v:.4f}"[:6] in text for v in vectors[0][:20])


def test_the_export_does_not_mix_up_similar_names(store):
    ann, anna = person(store, "Ann"), person(store, "Anna")
    store.add_event("granted", "Anna", "lab")
    store.add_audit("admin", "rule_created", "Anna → lab")
    export = store.export_person(ann)
    assert export["access_events"] == [] and export["administration_log"] == []  # Anna's data is not Ann's


def test_exporting_an_unknown_person(store):
    assert store.export_person(999) is None


# --- the web pages -------------------------------------------------------------------------------------------------------------


def make(store, name, role):
    store.create_admin(name, security.hash_password(PW), role)


@pytest.fixture
def app(store):
    return create_app(store.path)


def signed_in(app, name):
    c = TestClient(app, follow_redirects=False)
    assert c.post("/login", data={"username": name, "password": PW}).status_code == 303
    return c


def csrf(client, path="/compliance"):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


@pytest.fixture
def team(store, app):
    for name, role in (("olivia", "owner"), ("manon", "manager"), ("victor", "viewer")):
        make(store, name, role)
    return {n: signed_in(app, n) for n in ("olivia", "manon", "victor")}


def test_compliance_pages_are_for_owners_only(store, app, team):
    pid = person(store, "Camille")
    for who in ("manon", "victor"):
        assert team[who].get("/compliance").status_code == 403
        assert team[who].get(f"/people/{pid}/export.json").status_code == 403
    assert team["olivia"].get("/compliance").status_code == 200
    anonymous = TestClient(app, follow_redirects=False)
    assert anonymous.get("/compliance").headers["location"] == "/login"
    assert anonymous.get(f"/people/{pid}/export.json").headers["location"] == "/login"
    assert 'href="/compliance"' in team["olivia"].get("/").text and 'href="/compliance"' not in team["manon"].get("/").text


def test_the_retention_form_saves_validates_and_is_audited(store, team):
    olivia = team["olivia"]
    ok = olivia.post("/compliance/retention", data={"csrf": csrf(olivia), "events_days": "45", "audit_days": "400"})
    assert ok.headers["location"].endswith("m=retention_saved") and store.retention_days() == (45, 400)
    assert "retention_changed" in [a["action"] for a in store.list_audit()]
    for events, audit in ("3", "100"), ("100", "9999"), ("-4", "100"):
        bad = olivia.post("/compliance/retention", data={"csrf": csrf(olivia), "events_days": events, "audit_days": audit})
        assert bad.headers["location"].endswith("m=retention_invalid")
    assert store.retention_days() == (45, 400)  # unchanged by the invalid attempts
    page = olivia.get("/compliance").text
    assert 'value="45"' in page and 'value="400"' in page


def test_purge_now_deletes_and_records_how_much(store, team):
    olivia = team["olivia"]
    store.set_retention_days(30, 365)
    store.add_event("granted", "old", "lab", at=time.time() - 40 * DAY)
    store.add_event("granted", "new", "lab")
    r = olivia.post("/compliance/purge", data={"csrf": csrf(olivia)})
    assert r.headers["location"].endswith("m=purge_done")
    assert [e["person"] for e in store.list_events()] == ["new"]
    purge = [a for a in store.list_audit() if a["action"] == "purge_run"][0]
    assert purge["detail"].startswith("1 accès")


def test_the_page_shows_the_data_and_the_encryption_state(store, team):
    cipher = VectorCipher(crypto.new_key())
    Store(store.path, cipher).add_person("secured", np.zeros((3, 128)), "sface")
    page = team["olivia"].get("/compliance").text
    assert "Toutes chiffrées" in page and "3 empreintes" in page
    store.add_person("clear", np.zeros((2, 128)), "sface")  # no cipher: stored in the clear
    page = team["olivia"].get("/compliance").text
    assert "2 en clair" in page and "facerec db encrypt" in page


def test_the_export_download(store, team):
    pid = person(store, "Camille Martin")
    store.add_event("granted", "Camille Martin", "lab")
    r = team["olivia"].get(f"/people/{pid}/export.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.headers["content-disposition"] == f'attachment; filename="export-{pid}.json"'  # an id, never the name
    data = r.json()
    assert data["person"]["name"] == "Camille Martin" and len(data["access_events"]) == 1
    assert "Camille Martin" in r.text  # real accents and spaces, not escaped
    exported = [a for a in store.list_audit() if a["action"] == "person_exported"]
    assert exported and exported[0]["admin"] == "olivia" and exported[0]["detail"] == "Camille Martin"
    assert team["olivia"].get("/people/999/export.json").status_code == 404


def test_a_hostile_name_cannot_inject_into_the_download_headers(store, team):
    evil = 'x"\r\nSet-Cookie: pwned=1'
    with store._conn() as db:
        pid = db.execute("INSERT INTO people (name, created_at) VALUES (?, ?)", (evil, time.time())).lastrowid
    r = team["olivia"].get(f"/people/{pid}/export.json")
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert r.headers["content-disposition"] == f'attachment; filename="export-{pid}.json"'


def test_deleting_a_person_in_the_interface_anonymises_the_history(store, team):
    pid = person(store, "Camille Martin")
    store.add_event("granted", "Camille Martin", "lab")
    olivia = team["manon"]
    r = olivia.post(f"/people/{pid}/delete", data={"csrf": csrf(olivia, "/people"), "confirm": "Camille Martin"})
    assert r.headers["location"].endswith("m=person_deleted")
    assert [e["person"] for e in store.list_events()] == [ERASED]
    assert not any("Camille" in (a["detail"] or "") for a in store.list_audit())


# --- the automatic purge -----------------------------------------------------------------------------------------------------------


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_the_purge_runs_when_the_web_app_starts(store):
    store.set_retention_days(30, 365)
    store.add_event("granted", "old", "lab", at=time.time() - 40 * DAY)
    store.add_event("granted", "new", "lab")
    with TestClient(create_app(store.path, purge_interval_s=3600)):  # entering the context starts the app
        assert wait_for(lambda: [e["person"] for e in store.list_events()] == ["new"])


def test_the_purge_repeats_on_its_interval(store):
    store.set_retention_days(30, 365)
    with TestClient(create_app(store.path, purge_interval_s=0.1)):
        time.sleep(0.3)
        store.add_event("granted", "late-old", "lab", at=time.time() - 40 * DAY)  # appears after the first purge
        assert wait_for(lambda: store.list_events() == [])


def test_a_failing_purge_is_logged_and_does_not_stop_the_web_app(store, caplog, monkeypatch):
    app = create_app(store.path, purge_interval_s=3600)

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(app.state.store, "apply_retention", boom)
    with caplog.at_level(logging.ERROR, logger="facegate"):
        with TestClient(app) as client:
            assert wait_for(lambda: any("retention purge failed" in r.message for r in caplog.records))
            assert client.get("/healthz").text == "ok"  # still serving
