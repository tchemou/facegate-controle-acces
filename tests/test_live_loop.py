"""The camera loop, end to end, with a scripted camera and a scripted face engine (no hardware, no models)."""

import json
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pytest

from facerec import live
from facerec.antispoof import FAILED, ChallengeLiveness
from facerec.channel import LivePublisher, LiveReader
from facerec.config import Settings
from facerec.door import Door
from facerec.enrollment import EnrollmentSession
from facerec.gallery import Gallery
from facerec.policy import AccessPolicy
from facerec.smoothing import KNOWN, PENDING, UNKNOWN
from facerec.store import Store

S = Settings()
MON_10H = datetime(2026, 10, 26, 10, 0)
rng = np.random.default_rng(11)


def vec(center: float) -> np.ndarray:
    return (np.full(128, center) + rng.normal(0, 0.005, 128)).astype(np.float32)


class FakeRect:
    """Same interface as dlib.rectangle: left(), top(), right(), bottom() are methods."""

    def __init__(self, left=250, top=120, right=390, bottom=300):
        self._b = (left, top, right, bottom)

    def left(self):
        return self._b[0]

    def top(self):
        return self._b[1]

    def right(self):
        return self._b[2]

    def bottom(self):
        return self._b[3]


def frontal_points() -> np.ndarray:
    pts = np.zeros((68, 2))
    pts[0], pts[16], pts[30] = (250, 200), (390, 200), (320, 220)  # nose in the middle: yaw 0
    eye = np.array([(0, 0), (2, -4), (6, -4), (8, 0), (6, 4), (2, 4)], float) * 2
    pts[36:42], pts[42:48] = eye + (270, 160), eye + (330, 160)
    return pts


class FakeShape:
    def __init__(self):
        self._pts = frontal_points()

    def parts(self):
        return [SimpleNamespace(x=x, y=y) for x, y in self._pts]


class FakeEngine:
    """Detects `faces` rectangles per frame and 'recognises' them as `embedding`."""

    script = {"faces": 1, "embedding": 0.0}

    def __init__(self, settings=None):
        pass

    def detect(self, rgb):
        width = self.script.get("width", 140)  # a narrow face fails the quality gate: no decision is ever reached
        return [FakeRect(250, 120, 250 + width, 300) for _ in range(self.script["faces"])]

    def shape(self, rgb, rect):
        return FakeShape()

    def embed(self, rgb, shape):
        return vec(self.script["embedding"])


class FakeCamera:
    def __init__(self, frames: int, on_frame=None):
        self.frames, self.n, self.on_frame, self.released = frames, 0, on_frame, False
        self._image = rng.integers(40, 220, (480, 640, 3)).astype(np.uint8)  # textured: passes blur/brightness

    def read(self):
        if self.n >= self.frames:
            return False, None
        self.n += 1
        if self.on_frame:
            self.on_frame(self.n)
        return True, self._image.copy()

    def release(self):
        self.released = True


class RecordingPublisher(LivePublisher):
    instances: list = []

    def __init__(self, folder, **kw):
        super().__init__(folder, max_fps=1000)
        self.states = []
        RecordingPublisher.instances.append(self)

    def publish(self, frame, state, now=None):
        self.states.append(state)
        return super().publish(frame, state, now)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    FakeEngine.script = {"faces": 1, "embedding": 0.0}
    RecordingPublisher.instances = []
    camera_holder = {}

    def run(frames=30, gallery_people=None, on_frame=None, **kwargs):
        gallery = Gallery("sface")
        for name, center in (gallery_people or {"alice": 0.0}).items():
            gallery.enroll(name, np.vstack([vec(center) for _ in range(5)]))
        camera = FakeCamera(frames, on_frame)
        camera_holder["camera"] = camera
        monkeypatch.setattr(live, "open_camera", lambda index: camera)
        monkeypatch.setattr(live, "FaceEngine", FakeEngine)
        monkeypatch.setattr(live, "LivePublisher", RecordingPublisher)
        monkeypatch.setattr(live, "ENROLL_POLL_S", 0.0)
        monkeypatch.setattr(live, "ENROLL_CANCEL_POLL_S", 0.0)
        monkeypatch.setattr(live, "EnrollmentSession", lambda name, total: EnrollmentSession(name, total, min_interval_s=0.0))
        live.run_live(gallery, S, show_window=False, **kwargs)
        return camera

    run.camera_holder = camera_holder
    return run


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


# --- the decision, in isolation ------------------------------------------------------------------


def track(screen=False, status="passed", failed_on=None, blinking=True):
    challenge = ChallengeLiveness(S, sequence=("turn_left",))
    challenge.status, challenge.failed_on = status, failed_on
    return SimpleNamespace(screen=SimpleNamespace(is_screen=screen), challenge=challenge,
                           blink=SimpleNamespace(is_live=lambda now: blinking))


def decide(state=KNOWN, name="alice", ok=True, reasons=(), policy=None, zone="lab", challenge=False, liveness=False, **tk):
    report = SimpleNamespace(ok=ok, reasons=reasons)
    return live.decide(track(**tk), report, state, name, 0.0, challenge=challenge, liveness=liveness,
                       policy=policy, zone=zone, when=MON_10H)


def test_decision_for_every_case():
    assert decide(state=PENDING, ok=False, reasons=("blurry",)).label == "Image floue"
    assert decide(state=PENDING).state == "pending"
    assert decide().state == "granted" and decide().tone == "ok"
    assert decide(state=UNKNOWN, name=None).reason == "unknown_person"
    assert decide(challenge=True, screen=True).reason == "screen_detected"
    failed = decide(challenge=True, status=FAILED, failed_on="turn_left")
    assert failed.state == "denied" and failed.reason == "challenge_timeout:turn_left"
    waiting = decide(challenge=True, status="pending")
    assert waiting.state == "known" and waiting.show_challenge  # identified, not yet allowed in
    assert decide(liveness=True, blinking=False).state == "known"
    assert decide(liveness=True, blinking=True).state == "granted"


def test_decision_applies_the_access_policy():
    policy = AccessPolicy.from_dict({"zones": {"lab": {"alice": "always"}}})
    assert decide(policy=policy).state == "granted"
    assert decide(policy=policy, name="bob").reason == "policy:not_authorized"
    assert decide(policy=policy, zone="vault").reason == "policy:unknown_zone"
    outside = AccessPolicy.from_dict({"zones": {"lab": {"alice": [{"days": ["sat"], "from": "08:00", "to": "09:00"}]}}})
    verdict = decide(policy=outside)
    assert verdict.state == "denied" and "horaires" in verdict.label


def test_a_screen_beats_a_passed_challenge():
    assert decide(challenge=True, screen=True, status="passed").state == "denied"


# --- the loop ---------------------------------------------------------------------------------------------


def test_a_recognised_person_is_granted_logged_and_the_door_opens(setup, store):
    door = Door(open_seconds=60)
    setup(frames=25, door=door, store=store, zone="lab")
    events = store.list_events()
    assert [(e["person"], e["decision"], e["zone"]) for e in events] == [("alice", "granted", "lab")]  # once, not per frame
    assert door.opened_count == 1


def test_an_unknown_face_is_denied_and_the_door_stays_shut(setup, store):
    FakeEngine.script["embedding"] = 9.0
    door = Door()
    setup(frames=25, door=door, store=store, zone="lab")
    (event,) = store.list_events()
    assert (event["decision"], event["reason"], event["person"]) == ("denied", "unknown_person", None)
    assert door.opened_count == 0


def test_a_policy_refusal_keeps_the_door_shut_and_says_why(setup, store):
    door = Door()
    policy = AccessPolicy.from_dict({"zones": {"lab": {}}})
    setup(frames=25, door=door, store=store, zone="lab", policy=policy)
    (event,) = store.list_events()
    assert event["reason"] == "policy:not_authorized" and door.opened_count == 0


def test_the_door_is_closed_when_the_loop_ends(setup):
    door = Door(open_seconds=60)
    setup(frames=25, door=door)
    assert door.opened_count == 1 and not door.is_open  # fail closed


def test_the_camera_is_released_even_when_the_loop_crashes(setup, monkeypatch):
    def boom(n):
        if n == 5:
            raise RuntimeError("camera unplugged")

    with pytest.raises(RuntimeError):
        setup(frames=30, on_frame=boom)
    assert setup.camera_holder["camera"].released


def test_the_live_view_gets_state_and_the_image_is_removed_at_the_end(setup, tmp_path):
    folder = tmp_path / "live"
    setup(frames=25, door=Door(open_seconds=60), zone="lab", live_dir=folder)
    states = RecordingPublisher.instances[0].states
    assert any(s["door"]["open"] for s in states) and states[-1]["zone"] == "lab"
    assert any(f["state"] == "granted" for s in states for f in s["faces"])
    assert not (folder / "frame.jpg").exists()  # no face image left behind
    assert LiveReader(folder).state()["online"] is False
    assert json.loads((folder / "state.json").read_text())["running"] is False


def test_nothing_is_published_without_live_dir(setup, tmp_path):
    setup(frames=10)
    assert RecordingPublisher.instances == []


# --- enrollment from the web app ------------------------------------------------------------------------


def test_an_enrollment_queued_in_the_web_app_is_performed(setup, store):
    FakeEngine.script["embedding"] = 5.0  # a new face
    eid = store.request_enrollment("carol", "admin", "Je consens.", total=4)
    door = Door(open_seconds=60)
    setup(frames=40, store=store, zone="lab", door=door, live_reload=True)
    enrollment = store.get_enrollment(eid)
    assert enrollment["status"] == "done" and enrollment["progress"] == 4
    (carol,) = [p for p in store.list_people() if p["name"] == "carol"]
    assert carol["samples"] == 4 and carol["consent_at"] is not None
    assert store.embeddings_by_person()["carol"].shape == (4, 128)
    assert store.list_audit()[0]["action"] == "person_enrolled" and store.list_audit()[0]["admin"] == "admin"
    # nobody is let in (or logged) while enrolling; afterwards carol is recognised at once, not "unknown"
    assert [(e["person"], e["decision"]) for e in store.list_events()] == [("carol", "granted")]
    assert door.opened_count == 1


def test_nobody_is_logged_or_let_in_during_the_enrollment_itself(setup, store):
    FakeEngine.script["embedding"] = 5.0
    store.request_enrollment("carol", "admin", "consent", total=500)  # never finishes within the frames
    door = Door()
    setup(frames=30, store=store, door=door, live_reload=True)
    assert store.list_events() == [] and door.opened_count == 0


def test_enrolling_a_face_already_known_is_refused(setup, store):
    store.add_person("alice", np.vstack([vec(0.0) for _ in range(5)]), "sface")
    eid = store.request_enrollment("alice-bis", "admin", "consent", total=4)
    FakeEngine.script["embedding"] = 0.0  # the same face as alice
    setup(frames=40, store=store, gallery_people={"alice": 0.0})
    enrollment = store.get_enrollment(eid)
    assert enrollment["status"] == "failed" and "alice" in enrollment["message"]
    assert [p["name"] for p in store.list_people()] == ["alice"]


def test_a_deactivated_person_still_counts_as_a_duplicate(setup, store):
    pid = store.add_person("alice", np.vstack([vec(0.0) for _ in range(5)]), "sface")
    store.set_person_active(pid, False)
    eid = store.request_enrollment("alice-bis", "admin", "consent", total=4)
    FakeEngine.script["embedding"] = 0.0
    setup(frames=40, store=store, gallery_people={"bob": 7.0})
    assert store.get_enrollment(eid)["status"] == "failed"


def test_a_cancelled_enrollment_stops_and_saves_nothing(setup, store):
    FakeEngine.script["embedding"] = 5.0
    eid = store.request_enrollment("carol", "admin", "consent", total=30)
    setup(frames=40, store=store, on_frame=lambda n: store.cancel_enrollment(eid) if n == 8 else None)
    assert store.get_enrollment(eid)["status"] == "cancelled"
    assert [p["name"] for p in store.list_people()] == []


def test_stopping_the_service_mid_enrollment_does_not_leave_the_web_page_waiting(setup, store):
    FakeEngine.script["embedding"] = 5.0
    eid = store.request_enrollment("carol", "admin", "consent", total=50)
    setup(frames=12, store=store)  # the camera ends before the 50 samples
    enrollment = store.get_enrollment(eid)
    assert enrollment["status"] == "failed" and "arrêté" in enrollment["message"]
    assert store.list_people() == []


def test_no_face_in_front_of_the_camera_enrolls_nobody(setup, store):
    FakeEngine.script.update(faces=0, embedding=5.0)
    eid = store.request_enrollment("carol", "admin", "consent", total=3)
    setup(frames=15, store=store)
    assert store.get_enrollment(eid)["progress"] == 0 and store.list_people() == []


def test_enrollment_progress_is_published_for_the_progress_bar(setup, store, tmp_path):
    FakeEngine.script["embedding"] = 5.0
    store.request_enrollment("carol", "admin", "consent", total=4)
    setup(frames=30, store=store, live_dir=tmp_path / "live")
    seen = [s["enrollment"] for s in RecordingPublisher.instances[0].states if s["enrollment"]]
    assert seen and seen[0]["name"] == "carol" and seen[0]["total"] == 4
    assert max(e["progress"] for e in seen) >= 3
    assert any(s["mode"] == "inscription" for s in RecordingPublisher.instances[0].states)
