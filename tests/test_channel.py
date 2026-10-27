import json
import time

import cv2
import numpy as np

from facerec.channel import FRAME, STALE_AFTER_S, STATE, LivePublisher, LiveReader


def frame(value: int = 90) -> np.ndarray:
    img = np.full((120, 160, 3), value, np.uint8)
    cv2.circle(img, (80, 60), 30, (255, 255, 255), -1)
    return img


def test_a_published_frame_is_readable_and_decodes(tmp_path):
    pub, reader = LivePublisher(tmp_path), LiveReader(tmp_path)
    assert reader.frame() is None and reader.state() == {"online": False}
    assert pub.publish(frame(), {"fps": 9.5, "door": {"open": False}})
    decoded = cv2.imdecode(np.frombuffer(reader.frame(), np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (120, 160, 3)
    state = reader.state()
    assert state["online"] and state["fps"] == 9.5 and state["door"] == {"open": False}


def test_publishing_is_rate_limited(tmp_path):
    pub = LivePublisher(tmp_path, max_fps=10)
    assert pub.publish(frame(), {}, now=100.0)
    assert not pub.publish(frame(), {}, now=100.05)  # too soon
    assert pub.publish(frame(), {}, now=100.11)


def test_only_the_latest_frame_is_kept(tmp_path):
    pub, reader = LivePublisher(tmp_path, max_fps=1000), LiveReader(tmp_path)
    for i, value in enumerate((20, 120, 220)):
        pub.publish(frame(value), {"n": i}, now=float(i))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [FRAME, STATE]  # no history, no leftover temp files
    assert reader.state()["n"] == 2
    assert cv2.imdecode(np.frombuffer(reader.frame(), np.uint8), cv2.IMREAD_GRAYSCALE)[5, 5] > 150


def test_the_service_counts_as_offline_when_it_stops_publishing(tmp_path):
    pub, reader = LivePublisher(tmp_path), LiveReader(tmp_path)
    pub.publish(frame(), {})
    data = json.loads((tmp_path / STATE).read_text())
    data["updated_at"] = time.time() - STALE_AFTER_S - 1
    (tmp_path / STATE).write_text(json.dumps(data))
    assert reader.state()["online"] is False


def test_closing_deletes_the_image_and_reports_offline(tmp_path):
    pub, reader = LivePublisher(tmp_path), LiveReader(tmp_path)
    pub.publish(frame(), {})
    pub.close()
    assert reader.frame() is None  # no face image is left on disk
    assert reader.state()["online"] is False


def test_a_corrupt_state_file_is_not_fatal(tmp_path):
    (tmp_path / STATE).write_text("{not json")
    assert LiveReader(tmp_path).state() == {"online": False}


def test_a_reader_holding_the_file_does_not_crash_the_publisher(tmp_path, monkeypatch):
    pub = LivePublisher(tmp_path, max_fps=1000)
    pub.publish(frame(), {}, now=0.0)

    def locked(src, dst):
        raise PermissionError

    monkeypatch.setattr("facerec.channel.os.replace", locked)
    assert pub.publish(frame(), {}, now=1.0)  # skips the frame silently
    assert not list(tmp_path.glob("*.tmp"))
