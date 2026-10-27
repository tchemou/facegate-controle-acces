import importlib.util
import os
from pathlib import Path

import cv2
import dlib
import numpy as np

from .config import Settings


def models_dir() -> Path:
    """Locate the dlib model files without importing face_recognition_models."""
    override = os.environ.get("FACEREC_MODELS_DIR")
    if override:
        return Path(override)
    spec = importlib.util.find_spec("face_recognition_models")
    if spec is None or spec.origin is None:
        raise RuntimeError(
            "dlib models not found: install face-recognition-models or set FACEREC_MODELS_DIR"
        )
    return Path(spec.origin).parent / "models"


def onnx_path(name: str) -> Path:
    """OpenCV Zoo models live in <project>/models (see scripts/download_models.py)."""
    root = Path(os.environ.get("FACEREC_ONNX_DIR", Path(__file__).resolve().parents[2] / "models"))
    path = root / name
    if not path.exists():
        raise RuntimeError(f"{path} not found: run `python scripts/download_models.py`")
    return path


def shape_points(shape: dlib.full_object_detection) -> np.ndarray:
    return np.array([(p.x, p.y) for p in shape.parts()], dtype=np.float64)


def rect_box(rect: dlib.rectangle) -> tuple[int, int, int, int]:
    return rect.left(), rect.top(), rect.right(), rect.bottom()


def sface_row(points: np.ndarray) -> np.ndarray:
    """YuNet-style detection row built from dlib's 68 points, as SFace's aligner expects.

    Landmark order: right eye, left eye, nose tip, right and left mouth corner
    (right/left from the subject's point of view, i.e. image-left first).
    """
    row = np.zeros((1, 15), dtype=np.float32)
    row[0, 4:14] = np.concatenate([
        points[36:42].mean(axis=0), points[42:48].mean(axis=0),
        points[30], points[48], points[54],
    ])
    return row


class FaceEngine:
    """Detection (dlib HOG or OpenCV YuNet), 68 landmarks (dlib), embedding (dlib ResNet or SFace)."""

    def __init__(self, settings: Settings = Settings()):
        self._s = settings
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)  # silence harmless DNN warnings
        models = models_dir()
        self._hog = dlib.get_frontal_face_detector()
        self._predictor = dlib.shape_predictor(
            str(models / "shape_predictor_68_face_landmarks.dat")
        )
        self._yunet = None
        if settings.detector == "yunet":
            self._yunet = cv2.FaceDetectorYN.create(
                str(onnx_path("face_detection_yunet_2023mar.onnx")), "", (320, 320), 0.8, 0.3, 5000
            )
        self._dlib_encoder = self._sface = None
        if settings.embedder == "sface":
            self._sface = cv2.FaceRecognizerSF.create(
                str(onnx_path("face_recognition_sface_2021dec.onnx")), ""
            )
        else:
            self._dlib_encoder = dlib.face_recognition_model_v1(
                str(models / "dlib_face_recognition_resnet_model_v1.dat")
            )

    def detect(self, rgb: np.ndarray) -> list[dlib.rectangle]:
        if self._yunet is not None:
            return self._detect_yunet(rgb)
        scale = self._s.detect_scale
        small = rgb if scale == 1.0 else cv2.resize(rgb, None, fx=scale, fy=scale)
        rects = self._hog(small, self._s.detector_upsample)
        if scale == 1.0:
            return list(rects)
        inv = 1.0 / scale
        return [
            dlib.rectangle(
                int(r.left() * inv), int(r.top() * inv), int(r.right() * inv), int(r.bottom() * inv)
            )
            for r in rects
        ]

    def _detect_yunet(self, rgb: np.ndarray) -> list[dlib.rectangle]:
        h, w = rgb.shape[:2]
        self._yunet.setInputSize((w, h))
        _, faces = self._yunet.detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        if faces is None:
            return []
        return [
            dlib.rectangle(int(x), int(y), int(x + bw), int(y + bh))
            for x, y, bw, bh in faces[:, :4]
        ]

    def shape(self, rgb: np.ndarray, rect: dlib.rectangle) -> dlib.full_object_detection:
        return self._predictor(rgb, rect)

    def embed(self, rgb: np.ndarray, shape: dlib.full_object_detection) -> np.ndarray:
        if self._sface is not None:
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            aligned = self._sface.alignCrop(bgr, sface_row(shape_points(shape)))
            feat = self._sface.feature(aligned).ravel().astype(np.float32)
            return feat / (np.linalg.norm(feat) + 1e-9)  # unit length: Euclidean ~ cosine
        desc = self._dlib_encoder.compute_face_descriptor(rgb, shape, self._s.embedding_jitters)
        return np.asarray(desc, dtype=np.float32)
