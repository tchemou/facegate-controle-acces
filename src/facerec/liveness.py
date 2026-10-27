import numpy as np

from .config import Settings

RIGHT_EYE = slice(36, 42)
LEFT_EYE = slice(42, 48)


def eye_aspect_ratio(eye: np.ndarray) -> float:
    """Soukupova & Cech (2016): ratio of eye opening to eye width, from 6 landmarks."""
    vertical = np.linalg.norm(eye[1] - eye[5]) + np.linalg.norm(eye[2] - eye[4])
    horizontal = np.linalg.norm(eye[0] - eye[3])
    return float(vertical / (2.0 * horizontal + 1e-6))


def mean_ear(points: np.ndarray) -> float:
    return (eye_aspect_ratio(points[LEFT_EYE]) + eye_aspect_ratio(points[RIGHT_EYE])) / 2.0


class BlinkDetector:
    """Detects a blink (eyes closed for a few frames then open) to reject a static photo.

    It does not stop a video replay or a mask: it is one layer, not a guarantee.
    """

    def __init__(self, settings: Settings = Settings()):
        self._s = settings
        self._closed = 0
        self._last_blink: float | None = None

    def update(self, points: np.ndarray, t: float) -> None:
        if mean_ear(points) < self._s.blink_ear:
            self._closed += 1
            return
        if self._closed >= self._s.blink_min_frames:
            self._last_blink = t
        self._closed = 0

    @property
    def last_blink(self) -> float | None:
        return self._last_blink

    def is_live(self, t: float) -> bool:
        return self._last_blink is not None and t - self._last_blink <= self._s.blink_validity_s
