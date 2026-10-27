import numpy as np
import pytest

from facerec.config import Settings
from facerec.gallery import Gallery
from facerec.liveness import BlinkDetector, eye_aspect_ratio
from facerec.quality import assess, blur_score, yaw_ratio
from facerec.smoothing import KNOWN, PENDING, UNKNOWN, VoteSmoother
from facerec.tracking import Tracker

rng = np.random.default_rng(0)


def person(center: float, n: int = 5, spread: float = 0.01) -> np.ndarray:
    base = np.full(128, center, dtype=np.float32)
    return base + rng.normal(0, spread, (n, 128)).astype(np.float32)


# --- gallery -----------------------------------------------------------------


def test_identify_known_and_unknown():
    g = Gallery()
    g.enroll("alice", person(0.0))
    g.enroll("bob", person(0.1))
    near_alice = person(0.0, 1)[0]
    far = np.full(128, 5.0, dtype=np.float32)
    assert g.identify(near_alice, threshold=0.5, min_margin=0.04).name == "alice"
    assert g.identify(far, threshold=0.5, min_margin=0.04).name is None


def test_ambiguous_match_is_rejected_by_margin():
    g = Gallery()
    g.enroll("alice", person(0.0))
    g.enroll("bob", person(0.001))
    between = person(0.0005, 1)[0]
    assert g.identify(between, threshold=5.0, min_margin=0.5).name is None


def test_single_identity_has_infinite_margin():
    g = Gallery()
    g.enroll("alice", person(0.0))
    assert g.rank(person(0.0, 1)[0])[2] == float("inf")


def test_save_load_roundtrip_and_remove(tmp_path):
    g = Gallery()
    g.enroll("alice", person(0.0))
    g.enroll("bob", person(1.0, 3))
    path = tmp_path / "g.npz"
    g.save(path)
    loaded = Gallery.load(path)
    assert loaded.names == ["alice", "bob"]
    assert loaded.sample_count("bob") == 3
    assert loaded.remove("alice") and not loaded.remove("alice")


def test_gallery_stores_only_embeddings(tmp_path):
    g = Gallery()
    g.enroll("alice", person(0.0))
    g.save(tmp_path / "g.npz")
    with np.load(tmp_path / "g.npz", allow_pickle=False) as data:
        assert set(data.files) == {"labels", "embeddings", "embedder"}
        assert data["embeddings"].shape[1] == 128


def test_embedder_name_survives_roundtrip(tmp_path):
    g = Gallery("sface")
    g.enroll("alice", person(0.0))
    g.save(tmp_path / "g.npz")
    assert Gallery.load(tmp_path / "g.npz").embedder == "sface"


def test_enroll_rejects_wrong_dimension():
    with pytest.raises(ValueError):
        Gallery().enroll("x", np.zeros((2, 64)))


def test_load_missing_file_gives_empty_gallery(tmp_path):
    assert len(Gallery.load(tmp_path / "nope.npz")) == 0


# --- quality -----------------------------------------------------------------


def frontal_points() -> np.ndarray:
    pts = np.zeros((68, 2))
    pts[0], pts[16], pts[30] = (0, 50), (100, 50), (50, 60)
    return pts


def test_yaw_is_zero_when_frontal_and_large_when_turned():
    assert abs(yaw_ratio(frontal_points())) < 1e-6
    turned = frontal_points()
    turned[30] = (85, 60)
    assert yaw_ratio(turned) > 0.5


def test_blur_score_separates_sharp_from_blurry():
    import cv2

    sharp = (rng.random((200, 200)) * 255).astype(np.uint8)
    blurry = cv2.GaussianBlur(sharp, (21, 21), 8)
    assert blur_score(sharp) > 10 * blur_score(blurry)


def test_assess_flags_each_failure():
    s = Settings()
    gray = (rng.random((300, 300)) * 255).astype(np.uint8)
    ok = assess(gray, frontal_points(), (60, 60, 200, 200), s)
    assert ok.ok

    small = assess(gray, frontal_points(), (60, 60, 100, 100), s)
    assert "too_small" in small.reasons

    dark = assess(np.full((300, 300), 10, np.uint8), frontal_points(), (60, 60, 200, 200), s)
    assert "too_dark" in dark.reasons and "blurry" in dark.reasons

    turned = frontal_points()
    turned[30] = (90, 60)
    assert "not_frontal" in assess(gray, turned, (60, 60, 200, 200), s).reasons

    assert "cut_off" in assess(gray, frontal_points(), (-20, 60, 140, 200), s).reasons
    assert assess(gray, frontal_points(), (500, 500, 600, 600), s).reasons == ("out_of_frame",)


# --- liveness ----------------------------------------------------------------


def eye(opening: float) -> np.ndarray:
    return np.array([(0, 0), (1, opening), (2, opening), (3, 0), (2, -opening), (1, -opening)], float)


def face_with_eyes(opening: float) -> np.ndarray:
    pts = np.zeros((68, 2))
    pts[36:42] = eye(opening)
    pts[42:48] = eye(opening)
    return pts


def test_eye_aspect_ratio_drops_when_closed():
    assert eye_aspect_ratio(eye(0.5)) > 0.25 > eye_aspect_ratio(eye(0.05))


def test_blink_detected_then_expires():
    d = BlinkDetector(Settings(blink_validity_s=2.0))
    for t in (0.0, 0.1):
        d.update(face_with_eyes(0.5), t)
    assert not d.is_live(0.1)
    for t in (0.2, 0.3, 0.4):
        d.update(face_with_eyes(0.05), t)
    d.update(face_with_eyes(0.5), 0.5)
    assert d.is_live(0.5)
    assert not d.is_live(3.0)


def test_single_closed_frame_is_not_a_blink():
    d = BlinkDetector(Settings(blink_min_frames=2))
    d.update(face_with_eyes(0.05), 0.0)
    d.update(face_with_eyes(0.5), 0.1)
    assert not d.is_live(0.1)


# --- smoothing and tracking ---------------------------------------------------


def test_vote_waits_then_decides():
    v = VoteSmoother(Settings(min_votes=4))
    for _ in range(3):
        v.update("alice")
    assert v.decision()[0] == PENDING
    v.update("alice")
    assert v.decision() == (KNOWN, "alice")


def test_one_wrong_frame_does_not_flip_decision():
    v = VoteSmoother(Settings())
    for name in ["alice"] * 6 + ["bob"]:
        v.update(name)
    assert v.decision() == (KNOWN, "alice")


def test_no_consensus_means_unknown():
    v = VoteSmoother(Settings(vote_window=6, min_votes=4))
    for name in ["alice", "bob", "carol", "alice", "bob", "carol"]:
        v.update(name)
    assert v.decision() == (UNKNOWN, None)


def test_unknown_majority_is_unknown_once_the_evidence_is_in():
    v = VoteSmoother(Settings())
    for n, name in enumerate([None] * 7, start=1):
        v.update(name)
        # never "unknown" on the first frames: a person walking up is blurred or turned at first
        assert v.decision()[0] == (UNKNOWN if n >= 7 else PENDING)
    assert v.decision() == (UNKNOWN, None)


def test_a_few_bad_frames_do_not_make_a_stranger_of_a_legitimate_person():
    """The defect seen on the real camera: "refused (unknown)" one second before "granted" for the same person."""
    v = VoteSmoother(Settings())
    states = []
    for name in [None, None, None, "alice", "alice", "alice", "alice", "alice", "alice"]:  # a slow start
        v.update(name)
        states.append(v.decision()[0])
    assert UNKNOWN not in states  # never declared unknown on the way
    assert states[-1] == KNOWN and v.decision() == (KNOWN, "alice")


def test_unknown_needs_the_latest_frames_to_agree_on_nobody_too():
    v = VoteSmoother(Settings())
    for name in [None, None, None, "alice", "alice", "alice", "alice"]:  # 4/7 overall, but the last four say alice
        v.update(name)
    assert v.decision()[0] == PENDING
    v.update("alice")
    assert v.decision() == (KNOWN, "alice")


def test_a_real_stranger_is_still_refused():
    v = VoteSmoother(Settings())
    for name in ["bob", None, "carol", None, "bob", None, "dave", None]:  # no consensus, not even lately
        v.update(name)
    assert v.decision() == (UNKNOWN, None)


def test_accepting_is_as_quick_as_before():
    v = VoteSmoother(Settings())
    for name in ["alice"] * 3:
        v.update(name)
    assert v.decision()[0] == PENDING
    v.update("alice")
    assert v.decision() == (KNOWN, "alice")  # four frames, as ever


def test_the_unknown_threshold_cannot_exceed_the_window():
    v = VoteSmoother(Settings(vote_window=5, unknown_min_votes=99))
    for _ in range(5):
        v.update(None)
    assert v.decision() == (UNKNOWN, None)  # otherwise nobody could ever be refused


def test_tracker_keeps_identity_across_frames_and_separates_faces():
    t = Tracker(Settings(track_max_dist=50))
    a1, b1 = t.update([(100, 100), (400, 100)])
    a2, b2 = t.update([(110, 105), (395, 100)])
    assert (a1.id, b1.id) == (a2.id, b2.id) and a1.id != b1.id
    new = t.update([(110, 105), (395, 100), (250, 300)])[2]
    assert new.id not in (a1.id, b1.id)


def test_tracker_drops_lost_tracks():
    s = Settings(track_max_missed=2)
    t = Tracker(s)
    first = t.update([(100, 100)])[0]
    for _ in range(4):
        t.update([])
    assert t.update([(100, 100)])[0].id != first.id
