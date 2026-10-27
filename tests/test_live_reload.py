from datetime import datetime

import numpy as np
import pytest

from facerec import cli
from facerec.live import AccessLog, reload_if_changed
from facerec.policy import AccessPolicy, policy_from_store
from facerec.store import Store

MON_10H = datetime(2026, 10, 26, 10, 0)
rng = np.random.default_rng(3)


def vectors(center: float, n: int = 5) -> np.ndarray:
    return (np.full(128, center) + rng.normal(0, 0.01, (n, 128))).astype(np.float32)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def test_nothing_changed_means_no_reload(store):
    assert reload_if_changed(store, store.revision(), "sface", True, None) is None


def test_deactivating_a_person_removes_them_from_the_loaded_gallery(store):
    alice = store.add_person("alice", vectors(0.0), "sface")
    store.add_person("bob", vectors(1.0), "sface")
    revision = store.revision()
    store.set_person_active(alice, False)
    new_revision, gallery, _ = reload_if_changed(store, revision, "sface", False, None)
    assert new_revision > revision and gallery.names == ["bob"]
    assert gallery.identify(vectors(0.0, 1)[0], threshold=0.5, min_margin=0.0).name is None  # alice no longer matches


def test_the_gallery_only_holds_embeddings_of_the_running_model(store):
    store.add_person("alice", vectors(0.0), "sface")
    store.add_person("old", vectors(1.0), "dlib")  # enrolled with another model: not comparable
    assert store.load_gallery("sface").names == ["alice"]
    assert store.load_gallery("dlib").names == ["old"]


def test_rules_are_reloaded_only_when_they_come_from_the_database(store):
    pid = store.add_person("alice", vectors(0.0), "sface")
    revision = store.revision()
    zone = store.add_zone("lab")
    store.add_rule(zone, pid, always=True)
    file_policy = AccessPolicy.from_dict({"zones": {"lab": {}}})

    _, _, from_db = reload_if_changed(store, revision, "sface", True, None)
    assert from_db.check("alice", "lab", MON_10H)[0]
    _, _, kept = reload_if_changed(store, revision, "sface", False, file_policy)
    assert kept is file_policy and not kept.check("alice", "lab", MON_10H)[0]


def test_no_zone_means_no_policy_instead_of_locking_everybody_out(store):
    store.add_person("alice", vectors(0.0), "sface")
    assert policy_from_store(store) is None
    store.add_zone("lab")
    assert policy_from_store(store) is not None  # as soon as a zone exists, default deny applies


def test_the_loaded_gallery_recognises_the_enrolled_person(store):
    store.add_person("alice", vectors(0.0), "sface")
    store.add_person("bob", vectors(1.0), "sface")
    gallery = store.load_gallery("sface")
    assert gallery.identify(vectors(0.0, 1)[0], threshold=0.5, min_margin=0.0).name == "alice"
    assert gallery.identify(np.full(128, 9.0, dtype=np.float32), threshold=0.5, min_margin=0.0).name is None


def test_access_events_reach_the_database_with_their_zone_and_reason(store):
    log = AccessLog(None, store, "lab")
    log.write("denied", "bob", "policy:outside_schedule")
    log.write("granted", "alice")
    events = store.list_events()
    assert [(e["person"], e["zone"], e["decision"], e["reason"]) for e in events] == [
        ("alice", "lab", "granted", ""), ("bob", "lab", "denied", "policy:outside_schedule"),
    ]


def test_run_with_an_empty_database_explains_what_to_do(tmp_path):
    args = cli.build_parser().parse_args(["run", "--db", str(tmp_path / "empty.db")])
    with pytest.raises(SystemExit) as stop:
        cli.cmd_run(args)
    assert "import-gallery" in str(stop.value)
