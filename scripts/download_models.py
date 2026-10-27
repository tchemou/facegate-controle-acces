"""Download the OpenCV Zoo ONNX models (YuNet detector, SFace recognizer) into models/.

Files are verified against pinned SHA-256 hashes so a changed upstream file is never loaded silently.
"""

import hashlib
import urllib.request
from pathlib import Path

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "models"
    target.mkdir(exist_ok=True)
    for name, (url, digest) in MODELS.items():
        path = target / name
        if path.exists() and sha256(path) == digest:
            print(f"ok      {name}")
            continue
        print(f"download {name}")
        urllib.request.urlretrieve(url, path)
        if sha256(path) != digest:
            path.unlink()
            raise SystemExit(f"hash mismatch for {name}: file removed")


if __name__ == "__main__":
    main()
