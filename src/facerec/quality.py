from dataclasses import dataclass

import cv2
import numpy as np

from .config import Settings

_REF = 112


@dataclass(frozen=True)
class QualityReport:
    ok: bool
    reasons: tuple[str, ...]
    size: int
    blur: float
    brightness: float
    yaw: float


def blur_score(gray_face: np.ndarray) -> float:
    """Variance of the Laplacian on a fixed-size crop, so scores are comparable across distances."""
    ref = cv2.resize(gray_face, (_REF, _REF), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(ref, cv2.CV_64F).var())


def yaw_ratio(points: np.ndarray) -> float:
    """0 for a frontal face, towards +/-1 as the head turns (nose vs jaw extremities)."""
    nose_x = points[30, 0]
    left = abs(nose_x - points[0, 0])
    right = abs(points[16, 0] - nose_x)
    return float((left - right) / (left + right + 1e-6))


def judge(
    size: int, blur: float, brightness: float, yaw: float, cut_off: bool, settings: Settings
) -> tuple[str, ...]:
    """Apply the quality rules to already-measured values."""
    reasons: list[str] = []
    if size < settings.min_face_px:
        reasons.append("too_small")
    if blur < settings.min_blur:
        reasons.append("blurry")
    if brightness < settings.min_brightness:
        reasons.append("too_dark")
    elif brightness > settings.max_brightness:
        reasons.append("overexposed")
    if abs(yaw) > settings.max_yaw:
        reasons.append("not_frontal")
    if cut_off:
        reasons.append("cut_off")
    return tuple(reasons)


def assess(
    gray: np.ndarray,
    points: np.ndarray,
    box: tuple[int, int, int, int],
    settings: Settings = Settings(),
) -> QualityReport:
    h, w = gray.shape[:2]
    left, top, right, bottom = box
    size = right - left
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(right, w), min(bottom, h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return QualityReport(False, ("out_of_frame",), size, 0.0, 0.0, 0.0)

    crop = gray[y0:y1, x0:x1]
    blur, brightness, yaw = blur_score(crop), float(crop.mean()), yaw_ratio(points)
    cut_off = left < 0 or top < 0 or right > w or bottom > h
    reasons = judge(size, blur, brightness, yaw, cut_off, settings)
    return QualityReport(not reasons, reasons, size, blur, brightness, yaw)
