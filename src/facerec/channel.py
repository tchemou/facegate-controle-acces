"""Live channel between the recognition service (which owns the camera) and the web app.

The service publishes ONE annotated JPEG and a small JSON state into a folder, each overwritten
atomically; the web app reads them and relays them to signed-in administrators. Nothing is recorded:
there is never more than the latest frame on disk, and it is deleted when the service stops.
Opt-in only (`facerec run --live-dir ...`): a face image on disk is personal data.
"""

import json
import os
import time
from pathlib import Path

import cv2
import numpy as np

FRAME = "frame.jpg"
STATE = "state.json"
STALE_AFTER_S = 3.0  # no publish for this long: the service is considered stopped


def _atomic_write(path: Path, data: bytes) -> bool:
    """Write via a temp file and rename. False when a reader holds the target open (Windows): skip it."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, path)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        return False
    return True


class LivePublisher:
    """Used by the recognition loop: `publish(frame, state)` at most `max_fps` times a second."""

    def __init__(self, folder: Path | str, max_fps: float = 10.0, quality: int = 75):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._interval = 1.0 / max_fps
        self._quality = quality
        self._last = 0.0

    def publish(self, frame: np.ndarray, state: dict, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if now - self._last < self._interval:
            return False
        self._last = now
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._quality])
        if not ok:
            return False
        _atomic_write(self.folder / FRAME, jpeg.tobytes())
        _atomic_write(self.folder / STATE, json.dumps({**state, "updated_at": time.time()}).encode("utf-8"))
        return True

    def close(self) -> None:
        """Remove the last image and mark the service as stopped."""
        (self.folder / FRAME).unlink(missing_ok=True)
        _atomic_write(self.folder / STATE, json.dumps({"running": False, "updated_at": time.time()}).encode("utf-8"))


class LiveReader:
    """Used by the web app."""

    def __init__(self, folder: Path | str):
        self.folder = Path(folder)

    def state(self) -> dict:
        """The service state, with `online` false when it stopped or stopped publishing."""
        try:
            data = json.loads((self.folder / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"online": False}
        fresh = time.time() - float(data.get("updated_at", 0)) <= STALE_AFTER_S
        return {**data, "online": bool(fresh and data.get("running", True))}

    def frame(self) -> bytes | None:
        try:
            return (self.folder / FRAME).read_bytes()
        except OSError:
            return None
