"""Anti-spoofing: an active random challenge plus a passive screen-artifact check.

Two layers, because neither is enough alone:

* ``ChallengeLiveness``: the user must perform a random sequence of actions (blink, turn the head
  towards the left or right of the image). A pre-recorded video cannot anticipate the order, a
  static photo cannot move. It does not stop a live attacker or a real-time deepfake.
* ``screen_score``: periodic high-frequency energy in the face crop, typical of the pixel grid of
  a phone or monitor filmed by the camera (moire). It is a heuristic: it can miss a good replay
  and it is only measured on simulated attacks (see results/antispoof.md).
"""

import math
import random
from dataclasses import dataclass, field, replace

import cv2
import numpy as np

from .config import Settings
from .liveness import BlinkDetector, mean_ear
from .quality import yaw_ratio

BLINK, TURN_LEFT, TURN_RIGHT = "blink", "turn_left", "turn_right"
ALL_CHALLENGES = (BLINK, TURN_LEFT, TURN_RIGHT)

PENDING, PASSED, FAILED = "pending", "passed", "failed"

# Prompts name the side of the *image*, which is unambiguous whether or not the preview is mirrored.
PROMPTS = {
    BLINK: "clignez des yeux",
    TURN_LEFT: "tournez la tete vers la gauche de l'ecran",
    TURN_RIGHT: "tournez la tete vers la droite de l'ecran",
}


# What the camera window shows for each challenge: text and arrow direction on the image (-1 left, +1 right)
CUES = {
    BLINK: ("Clignez des yeux", 0),
    TURN_LEFT: ("Tournez la tête vers la gauche de l'écran", -1),
    TURN_RIGHT: ("Tournez la tête vers la droite de l'écran", +1),
}


@dataclass(frozen=True)
class ChallengeView:
    text: str
    direction: int  # -1 / +1 arrow towards that side of the image, 0 none
    step: int  # challenges already done
    total: int
    seconds_left: float
    fraction_left: float


@dataclass
class ChallengeLiveness:
    """Random challenge-response liveness for one tracked face.

    Drive it with ``update(points, t)`` on every frame. ``status`` ends at ``passed`` once every
    challenge was met in order, or ``failed`` if one timed out. A turn must be followed by a
    return to a frontal pose before the next challenge, so one wiggle cannot satisfy two.
    """

    settings: Settings = field(default_factory=Settings)
    rng: random.Random = field(default_factory=random.Random)
    sequence: tuple[str, ...] = ()
    status: str = PENDING
    failed_on: str | None = None  # the challenge that timed out, for the access log
    index: int = 0
    _started: float | None = None
    _needs_frontal: bool = False
    _held: int = 0  # consecutive frames the current turn has been held
    _blink: BlinkDetector = field(init=False)
    _calibration: list = field(default_factory=list)
    _yaw0: float | None = None  # the person's own resting yaw, measured before the first challenge

    def __post_init__(self) -> None:
        if not self.sequence:
            s = self.settings
            self.sequence = tuple(self.rng.choices(s.challenge_types, k=s.challenge_count))
        self._blink = BlinkDetector(self.settings)

    @property
    def baseline_yaw(self) -> float | None:
        return self._yaw0

    def _calibrate(self, points: np.ndarray, t: float) -> None:
        """Measure the resting pose and eye opening, so every camera and seat position works.

        Turns are judged relative to the resting yaw (a camera placed off-axis reads a frontal face
        as 0.4, not 0), and a blink is an eye closing to ~75 % of this person's own open value.
        """
        s = self.settings
        self._calibration.append((yaw_ratio(points), mean_ear(points)))
        if len(self._calibration) < s.challenge_calibration_frames:
            return
        yaws, ears = zip(*self._calibration)
        self._yaw0 = float(np.median(yaws))
        blink_ear = float(np.clip(s.challenge_blink_ratio * np.median(ears), 0.15, 0.28))
        self._blink = BlinkDetector(replace(s, blink_ear=blink_ear))
        self._started = t

    @property
    def current(self) -> str | None:
        return self.sequence[self.index] if self.status == PENDING else None

    def remaining(self, t: float) -> float:
        """Seconds left for the current challenge (the clock waits while the user returns frontal)."""
        if self._started is None or self._needs_frontal:
            return self.settings.challenge_timeout_s
        return max(0.0, self.settings.challenge_timeout_s - (t - self._started))

    def prompt(self, t: float | None = None) -> str:
        if self.status != PENDING:
            return "verification reussie" if self.status == PASSED else "verification echouee"
        if self._yaw0 is None:
            return "regardez la camera"
        if self._needs_frontal:
            return "revenez de face"
        text = f"{PROMPTS[self.current]} ({self.index + 1}/{len(self.sequence)})"
        return text if t is None else f"{text} - {math.ceil(self.remaining(t))} s"

    def view(self, t: float) -> "ChallengeView":
        """What to show the person right now (text, arrow direction, step, countdown)."""
        total = len(self.sequence)
        step = min(self.index, total)
        if self.status != PENDING:
            return ChallengeView("", 0, step, total, 0.0, 0.0)
        if self._yaw0 is None:
            return ChallengeView("Regardez la caméra", 0, step, total, 0.0, 0.0)
        if self._needs_frontal:
            return ChallengeView("Revenez de face", 0, step, total, 0.0, 0.0)
        left = self.remaining(t)
        text, direction = CUES[self.current]
        return ChallengeView(text, direction, step, total, left, left / self.settings.challenge_timeout_s)

    def update(self, points: np.ndarray, t: float) -> str:
        if self.status != PENDING:
            return self.status
        if self._yaw0 is None:
            self._calibrate(points, t)
            return self.status
        self._blink.update(points, t)
        yaw = yaw_ratio(points) - self._yaw0  # relative to the person's own frontal pose
        s = self.settings

        if self._needs_frontal:
            if abs(yaw) <= s.challenge_frontal_yaw:
                self._needs_frontal = False
                self._started = t  # the clock for the next challenge starts once frontal
        elif self._done(self.current, yaw, t):
            self.index += 1
            self._held = 0
            self._started = t
            self._needs_frontal = self.current_was_turn()
            if self.index >= len(self.sequence):
                self.status = PASSED
        elif t - self._started > s.challenge_timeout_s:
            self.failed_on = self.current
            self.status = FAILED
        return self.status

    def current_was_turn(self) -> bool:
        return self.sequence[self.index - 1] in (TURN_LEFT, TURN_RIGHT)

    def _done(self, challenge: str, yaw: float, t: float) -> bool:
        s = self.settings
        if challenge == BLINK:  # only a blink made after this challenge started counts
            last = self._blink.last_blink
            return last is not None and last >= self._started
        # yaw_ratio > 0: the nose is closer to the right jaw end, i.e. the face points to image-right
        turned = yaw >= s.challenge_turn_yaw if challenge == TURN_RIGHT else yaw <= -s.challenge_turn_yaw
        self._held = self._held + 1 if turned else 0
        return self._held >= s.challenge_hold_frames


def screen_score(gray_face: np.ndarray, size: int = 128) -> float:
    """Strength of periodic high-frequency peaks in the face crop (higher = more screen-like).

    Takes the spectrum of the crop, keeps the band above the first quarter of the frequencies and
    returns the ratio between its strongest peak and its median. A natural face has a smooth,
    decaying spectrum; the pixel grid of a screen adds sharp isolated peaks.
    """
    face = cv2.resize(gray_face, (size, size), interpolation=cv2.INTER_AREA).astype(np.float64)
    face -= face.mean()
    face *= np.outer(np.hanning(size), np.hanning(size))  # window: avoids edge leakage
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(face)))
    yy, xx = np.indices(spectrum.shape)
    radius = np.hypot(yy - size / 2, xx - size / 2)
    band = spectrum[(radius > size / 4) & (radius < size / 2)]
    return float(band.max() / (np.median(band) + 1e-9))


class ScreenDetector:
    """Smooths ``screen_score`` over the last frames: one noisy frame must not block someone."""

    def __init__(self, settings: Settings = Settings()):
        self._s = settings
        self._recent: list[bool] = []

    def update(self, gray_face: np.ndarray) -> None:
        self._recent.append(screen_score(gray_face) > self._s.screen_threshold)
        del self._recent[: -self._s.screen_window]

    @property
    def is_screen(self) -> bool:
        w = self._s.screen_window
        return len(self._recent) >= w and sum(self._recent) / w >= self._s.screen_ratio
