import math
import random

import cv2
import numpy as np

from facerec.antispoof import (
    ALL_CHALLENGES, BLINK, FAILED, PASSED, PENDING, TURN_LEFT, TURN_RIGHT,
    ChallengeLiveness, ScreenDetector, screen_score,
)
from facerec.config import Settings

S = Settings()
DT = 0.1  # 10 frames per second


def face(yaw: float = 0.0, open_eyes: bool = True, eye_h: float | None = None) -> np.ndarray:
    """68 synthetic landmarks with a chosen yaw ratio and eye state."""
    pts = np.zeros((68, 2))
    pts[0], pts[16] = (0, 0), (100, 0)
    pts[30] = (50 + 50 * yaw, 30)
    h = eye_h if eye_h is not None else (0.45 if open_eyes else 0.1)  # EAR = 4h/6: 0.30 open, 0.07 closed
    eye = np.array([(0, 0), (1, -h), (2, -h), (3, 0), (2, h), (1, h)], dtype=float)
    pts[36:42], pts[42:48] = eye + (10, 10), eye + (40, 10)
    return pts


class Clock:
    """Feeds frames at 10 fps. `rest` is the person's own frontal yaw (an off-axis camera reads != 0);
    `yaw` arguments are relative to it. The first second is the calibration, spent frontal."""

    def __init__(self, challenge: ChallengeLiveness, rest: float = 0.0, eye_h: float | None = None):
        self.c, self.t, self.rest, self.eye_h = challenge, 0.0, rest, eye_h
        self.run(S.challenge_calibration_frames * DT)

    def run(self, seconds: float, yaw: float = 0.0, open_eyes: bool = True) -> str:
        for _ in range(round(seconds / DT)):
            self.t += DT
            h = self.eye_h if open_eyes else 0.1
            self.c.update(face(self.rest + yaw, eye_h=h), self.t)
        return self.c.status


def test_blink_then_turn_passes():
    clock = Clock(ChallengeLiveness(S, sequence=(BLINK, TURN_LEFT)))
    assert clock.run(0.5) == PENDING
    clock.run(0.4, open_eyes=False)
    assert clock.run(0.3) == PENDING  # blink done, now waiting for the turn
    assert clock.run(0.5, yaw=-0.4) == PASSED


def test_a_single_frame_beyond_the_angle_is_not_a_turn():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_RIGHT,)))
    for _ in range(S.challenge_timeout_s.__int__() * 10):  # a phone wiggled: one frame past the angle, then back
        clock.run(DT, yaw=0.5)
        assert clock.run(DT * 3, yaw=0.0) == PENDING or clock.c.status == FAILED
    assert clock.c.status != PASSED


def test_a_turn_held_for_the_required_frames_passes():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_RIGHT,)))
    assert clock.run(DT * (S.challenge_hold_frames - 1), yaw=0.5) == PENDING
    assert clock.run(DT, yaw=0.5) == PASSED


def test_static_photo_times_out():
    clock = Clock(ChallengeLiveness(S, sequence=(BLINK, TURN_RIGHT)))
    assert clock.run(S.challenge_timeout_s + 1.0) == FAILED


def test_wrong_direction_does_not_pass():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_LEFT, BLINK)))
    assert clock.run(S.challenge_timeout_s + 1.0, yaw=0.5) == FAILED


def test_a_turn_must_be_followed_by_a_frontal_pose():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_LEFT, TURN_RIGHT)))
    clock.run(0.5, yaw=-0.4)  # first challenge done
    assert clock.run(2.0, yaw=0.4) == PENDING  # jumping straight to the other side does not count
    clock.run(0.5, yaw=0.0)
    assert clock.run(0.5, yaw=0.4) == PASSED


def test_an_earlier_blink_does_not_satisfy_a_later_blink_challenge():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_RIGHT, BLINK)))
    clock.run(0.4, open_eyes=False)  # blink during the first challenge
    clock.run(0.3)
    clock.run(0.5, yaw=0.4)
    clock.run(0.5)  # frontal again
    assert clock.run(3.0) == PENDING
    clock.run(0.4, open_eyes=False)
    assert clock.run(0.3) == PASSED


def test_sequences_are_random_and_default_to_turns_only():
    orders = {ChallengeLiveness(S, rng=random.Random(seed)).sequence for seed in range(200)}
    assert len(orders) == 2 ** S.challenge_count  # every left/right combination shows up
    assert all(len(o) == S.challenge_count and set(o) <= {TURN_LEFT, TURN_RIGHT} for o in orders)


def test_blink_can_be_switched_back_on_in_the_settings():
    from dataclasses import replace

    s = replace(S, challenge_types=ALL_CHALLENGES)
    orders = [ChallengeLiveness(s, rng=random.Random(seed)).sequence for seed in range(100)]
    assert any(BLINK in o for o in orders)


def test_the_same_turn_twice_in_a_row_needs_a_return_to_frontal():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_LEFT, TURN_LEFT)))
    clock.run(0.5, yaw=-0.4)
    assert clock.run(2.0, yaw=-0.4) == PENDING  # holding the turn does not count twice
    clock.run(0.5)
    assert clock.run(0.5, yaw=-0.4) == PASSED


# --- passive screen detection --------------------------------------------------


def smooth_face(seed: int = 0) -> np.ndarray:
    noise = np.random.default_rng(seed).normal(128, 30, (160, 160)).astype(np.float32)
    return cv2.GaussianBlur(noise, (0, 0), 6).clip(0, 255).astype(np.uint8)


def gridded(img: np.ndarray, pitch: int = 3) -> np.ndarray:
    out = img.astype(np.float32).copy()
    out[::pitch, :] *= 0.8
    out[:, ::pitch] *= 0.8
    return out.astype(np.uint8)


def test_pixel_grid_scores_higher_than_a_smooth_face():
    assert screen_score(gridded(smooth_face())) > S.screen_threshold > screen_score(smooth_face())


def test_screen_detector_needs_a_stable_majority_of_frames():
    det = ScreenDetector(S)
    for i in range(S.screen_window):
        det.update(smooth_face(i))
    assert not det.is_screen
    for _ in range(S.screen_window):
        det.update(gridded(smooth_face()))
    assert det.is_screen


def test_screen_detector_does_not_decide_before_the_window_is_full():
    det = ScreenDetector(S)
    for _ in range(S.screen_window - 1):
        det.update(gridded(smooth_face()))
    assert not det.is_screen


# --- countdown, failure reason and access log -----------------------------------


def test_prompt_shows_a_countdown_and_asks_to_return_frontal():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_LEFT, BLINK)))
    clock.run(2.0)
    # the clock starts once calibration is done: 2.0 s elapsed, 6.0 s left
    assert abs(clock.c.remaining(clock.t) - (S.challenge_timeout_s - 2.0)) < 1e-6
    assert clock.c.prompt(clock.t).endswith(f"- {math.ceil(S.challenge_timeout_s - 2.0)} s")
    clock.run(0.3, yaw=-0.4)  # turn done: the next challenge waits for a frontal pose
    assert clock.c.prompt(clock.t) == "revenez de face"
    assert clock.c.remaining(clock.t) == S.challenge_timeout_s


def test_failure_records_which_challenge_timed_out():
    clock = Clock(ChallengeLiveness(S, sequence=(BLINK, TURN_LEFT)))
    assert clock.run(S.challenge_timeout_s + 1.0) == FAILED
    assert clock.c.failed_on == BLINK


def test_access_log_gets_a_reason_column_and_old_logs_are_migrated(tmp_path):
    from facerec.live import AccessLog

    path = tmp_path / "acces.csv"
    path.write_text("time,decision,name\n2026-01-01T10:00:00,denied,alice\n", encoding="utf-8")
    AccessLog(path).write("denied", "bob", "screen_detected")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "time,decision,name,reason"
    assert lines[1] == "2026-01-01T10:00:00,denied,alice,"
    assert lines[2].endswith(",denied,bob,screen_detected")


# --- calibration: an off-axis camera must not break the challenges --------------


def test_prompt_asks_to_look_at_the_camera_during_calibration():
    c = ChallengeLiveness(S, sequence=(BLINK, TURN_LEFT))
    c.update(face(), 0.1)
    assert c.prompt(0.1) == "regardez la camera"
    assert c.baseline_yaw is None


def test_turns_are_judged_from_the_persons_own_resting_pose():
    """Resting yaw 0.45 (camera off to one side): a 0.4 turn to the left must still pass."""
    for rest in (0.45, -0.45):
        clock = Clock(ChallengeLiveness(S, sequence=(TURN_LEFT, TURN_RIGHT)), rest=rest)
        assert abs(clock.c.baseline_yaw - rest) < 0.01
        assert clock.run(0.5, yaw=-0.4) == PENDING
        clock.run(0.5)  # back to the resting pose counts as frontal
        assert clock.run(0.5, yaw=0.4) == PASSED


def test_resting_at_an_offset_is_not_a_turn():
    clock = Clock(ChallengeLiveness(S, sequence=(TURN_RIGHT, BLINK)), rest=0.45)
    assert clock.run(S.challenge_timeout_s + 1.0) == FAILED  # a photo stays at its own resting pose


def test_blink_threshold_follows_the_persons_own_eye_opening():
    """Wide-open eyes (EAR 0.38) that only drop to 0.26 in a blink: the old fixed 0.21 never fired."""
    clock = Clock(ChallengeLiveness(S, sequence=(BLINK, TURN_LEFT)), eye_h=0.57)
    for _ in range(3):
        clock.t += DT
        clock.c.update(face(eye_h=0.39), clock.t)  # EAR 0.26
    clock.t += DT
    clock.c.update(face(eye_h=0.57), clock.t)
    assert clock.c.index == 1
