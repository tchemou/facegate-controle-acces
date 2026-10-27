import time

import numpy as np
import pytest

from facerec.enrollment import HINTS, EnrollmentSession
from facerec.gallery import Gallery
from facerec.store import Store

rng = np.random.default_rng(5)


def face(center: float, n: int = 1) -> np.ndarray:
    return (np.full((n, 128), center) + rng.normal(0, 0.01, (n, 128))).astype(np.float32)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


# --- queue (store) -----------------------------------------------------------------------------


def test_a_request_is_claimed_once_and_in_order(store):
    first = store.request_enrollment("alice", "admin", "consent", total=5)
    claimed = store.claim_enrollment()
    assert claimed["id"] == first and claimed["status"] == "running" and claimed["total"] == 5
    assert store.claim_enrollment() is None  # nothing else pending: the service does not take it twice


def test_only_one_enrollment_at_a_time(store):
    store.request_enrollment("alice", "admin", "consent")
    with pytest.raises(ValueError, match="busy"):
        store.request_enrollment("bob", "admin", "consent")
    store.claim_enrollment()
    with pytest.raises(ValueError, match="busy"):  # still busy while running
        store.request_enrollment("bob", "admin", "consent")


def test_an_existing_name_cannot_be_enrolled_again(store):
    store.add_person("alice", face(0.0, 3), "sface")
    with pytest.raises(ValueError, match="name_exists"):
        store.request_enrollment("ALICE", "admin", "consent")


def test_progress_updates_and_finishing_frees_the_queue(store):
    eid = store.request_enrollment("alice", "admin", "consent")
    store.claim_enrollment()
    store.update_enrollment(eid, progress=7)
    assert store.get_enrollment(eid)["progress"] == 7
    store.update_enrollment(eid, status="done", message="ok")
    assert store.get_enrollment(eid)["status"] == "done"
    assert store.request_enrollment("bob", "admin", "consent")  # the next one can start


def test_a_cancelled_request_stays_cancelled(store):
    eid = store.request_enrollment("alice", "admin", "consent")
    store.claim_enrollment()
    assert store.cancel_enrollment(eid)
    store.update_enrollment(eid, status="done")  # the service reports late: ignored
    assert store.get_enrollment(eid)["status"] == "cancelled"
    assert not store.cancel_enrollment(eid)


def test_update_only_accepts_known_fields(store):
    eid = store.request_enrollment("alice", "admin", "consent")
    with pytest.raises(ValueError):
        store.update_enrollment(eid, name="hacked")
    with pytest.raises(ValueError):
        store.update_enrollment(eid)


def test_an_unclaimed_request_expires_instead_of_blocking_forever(store):
    eid = store.request_enrollment("alice", "admin", "consent")
    assert store.expire_enrollments(pending_after=3600) == 0
    with store._conn() as db:
        db.execute("UPDATE enrollments SET created_at = ? WHERE id = ?", (time.time() - 200, eid))
    assert store.expire_enrollments(pending_after=90) == 1
    assert store.get_enrollment(eid)["status"] == "failed"
    assert store.request_enrollment("bob", "admin", "consent")


# --- session (pure logic) --------------------------------------------------------------------------


def feed(session, now, faces=1, reasons=(), center=0.0):
    ok = session.observe(now, faces, reasons)
    if ok:
        session.add(face(center)[0], now)
    return ok


def test_samples_are_spaced_and_the_session_completes():
    s = EnrollmentSession("alice", total=3, min_interval_s=0.5)
    assert feed(s, 0.0) and not feed(s, 0.2) and feed(s, 0.6) and feed(s, 1.2)
    assert s.done and s.finished and s.progress == 3
    assert not feed(s, 2.0)  # no more samples once complete


@pytest.mark.parametrize("faces, reasons, hint", [
    (0, (), "no_face"), (2, (), "several_faces"),
    (1, ("blurry",), "blurry"), (1, ("not_frontal", "blurry"), "not_frontal"),
])
def test_bad_frames_give_a_hint_and_no_sample(faces, reasons, hint):
    s = EnrollmentSession("alice")
    assert not feed(s, 0.0, faces, reasons)
    assert s.hint == HINTS[hint] and s.progress == 0


def test_the_session_times_out():
    s = EnrollmentSession("alice", timeout_s=10)
    feed(s, 0.0, faces=0)
    assert not feed(s, 11.0, faces=0)
    assert s.failed and "Délai" in s.failed and s.finished


def test_a_session_that_completed_in_time_does_not_time_out():
    s = EnrollmentSession("alice", total=1, timeout_s=10)
    feed(s, 0.0)
    feed(s, 50.0, faces=0)
    assert s.done and s.failed is None


def test_a_face_already_enrolled_under_another_name_is_refused():
    gallery = Gallery("sface")
    gallery.enroll("alice", face(0.0, 5))
    gallery.enroll("bob", face(1.0, 5))
    twin = EnrollmentSession("alice2", total=4)
    for i in range(4):
        feed(twin, i, center=0.0)
    assert twin.fail_if_duplicate(gallery, threshold=0.5, min_margin=0.0)
    assert "alice" in twin.failed


def test_a_new_face_is_accepted():
    gallery = Gallery("sface")
    gallery.enroll("alice", face(0.0, 5))
    new = EnrollmentSession("carol", total=4)
    for i in range(4):
        feed(new, i, center=5.0)
    assert not new.fail_if_duplicate(gallery, threshold=0.5, min_margin=0.0) and new.failed is None


def test_an_empty_gallery_has_no_duplicates():
    s = EnrollmentSession("alice", total=2)
    feed(s, 0.0)
    feed(s, 1.0)
    assert s.check_duplicate(Gallery("sface"), 0.5, 0.0) is None
