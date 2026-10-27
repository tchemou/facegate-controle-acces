import asyncio
import json
import re
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from facerec.channel import STATE, LivePublisher, LiveReader
from facerec.store import Store
from facerec.web import app as webapp
from facerec.web import security
from facerec.web.app import CONSENT_TEXT, create_app, mjpeg_chunks

PASSWORD = "Correct-Horse-42"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.create_admin("admin", security.hash_password(PASSWORD))
    return s


def make_client(store, live_dir=None, **kw):
    c = TestClient(create_app(store.path, live_dir=live_dir, **kw), follow_redirects=False)
    assert c.post("/login", data={"username": "admin", "password": PASSWORD}).status_code == 303
    return c


@pytest.fixture
def client(store):
    return make_client(store)


@pytest.fixture
def live(tmp_path):
    folder = tmp_path / "live"
    pub = LivePublisher(folder, max_fps=1000)
    image = np.full((60, 80, 3), 120, np.uint8)
    cv2.circle(image, (40, 30), 15, (255, 255, 255), -1)
    pub.publish(image, {"running": True, "fps": 24.5, "zone": "lab", "mode": "yunet + sface",
                        "faces": [{"label": "Camille · accès autorisé", "tone": "ok", "state": "granted"}],
                        "door": {"open": True, "seconds_left": 3.0}, "enrollment": None})
    return folder, pub


def csrf(client):
    return re.search(r'name="csrf" value="([^"]+)"', client.get("/people").text).group(1)


# --- access ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/live", "/people/new", "/enroll/1"])
def test_pages_need_a_login(store, path):
    anonymous = TestClient(create_app(store.path), follow_redirects=False)
    assert anonymous.get(path).headers["location"] == "/login"


@pytest.mark.parametrize("path", ["/live/stream", "/api/live", "/api/enroll/1"])
def test_data_endpoints_need_a_login(store, live, path):
    # a short stream cap: if the protection were ever lost, the test must fail fast instead of hanging for an hour
    anonymous = TestClient(create_app(store.path, live_dir=live[0], stream_max_seconds=0.3), follow_redirects=False)
    assert anonymous.get(path).status_code == 401


def test_the_live_page_explains_how_to_enable_the_video_when_it_is_off(client):
    page = client.get("/live").text
    assert "n'est pas activée" in page and "--live-dir" in page and 'src="/live/stream"' not in page
    assert client.get("/live/stream").status_code == 404
    assert client.get("/api/live").json() == {"enabled": False, "online": False}


def test_the_live_page_shows_the_video_when_it_is_on(store, live):
    page = make_client(store, live[0]).get("/live").text
    assert 'src="/live/stream"' in page and "/static/live.js" in page


# --- the stream -------------------------------------------------------------------------------------------


def test_the_stream_serves_multipart_jpeg(store, live):
    client = make_client(store, live[0], stream_max_seconds=0.4)
    r = client.get("/live/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert r.content.startswith(b"--frame\r\nContent-Type: image/jpeg")
    jpeg = r.content.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape == (60, 80, 3)
    assert r.content.count(b"--frame") == 1  # the same frame is not sent again and again


def test_the_number_of_simultaneous_streams_is_capped(store, live, monkeypatch):
    client = make_client(store, live[0], stream_max_seconds=0.2)
    monkeypatch.setattr(webapp, "MAX_STREAMS", 0)
    assert client.get("/live/stream").status_code == 429


def test_the_stream_counter_is_released_after_each_stream(store, live):
    client = make_client(store, live[0], stream_max_seconds=0.2)
    for _ in range(3):
        assert client.get("/live/stream").status_code == 200
    assert client.app.state.streams == 0


def test_mjpeg_chunks_send_only_new_frames_and_stop_when_the_client_leaves(live):
    folder, pub = live
    reader = LiveReader(folder)

    async def run():
        seen = {"n": 0}

        async def disconnected():
            seen["n"] += 1
            return seen["n"] > 6

        chunks = []
        async for chunk in mjpeg_chunks(reader, disconnected, interval=0.001, max_seconds=5):
            chunks.append(chunk)
            if len(chunks) == 1:  # a new frame appears: it must be sent once
                # explicit clock: right after the fixture's frame, the fps cap would drop it (coarse clock on
                # Windows before Python 3.13, sub-millisecond gap on fast Linux runners)
                assert pub.publish(np.full((60, 80, 3), 30, np.uint8), {}, now=time.monotonic() + 1)
        return chunks

    chunks = asyncio.run(run())
    assert len(chunks) == 2 and all(c.startswith(b"--frame") for c in chunks)


def test_mjpeg_chunks_stop_after_the_time_cap(live):
    async def never():
        return False

    async def run():
        return [c async for c in mjpeg_chunks(LiveReader(live[0]), never, interval=0.01, max_seconds=0.1)]

    started = time.monotonic()
    assert len(asyncio.run(run())) == 1 and time.monotonic() - started < 2


# --- the state API -------------------------------------------------------------------------------------------


def test_api_live_relays_the_service_state(store, live):
    state = make_client(store, live[0]).get("/api/live").json()
    assert state["online"] and state["fps"] == 24.5 and state["zone"] == "lab"
    assert state["door"] == {"open": True, "seconds_left": 3.0}
    assert state["faces"][0] == {"label": "Camille · accès autorisé", "tone": "ok", "state": "granted"}
    assert state["enrollment"] is None


def test_api_live_reports_a_stopped_service_as_offline(store, tmp_path):
    folder = tmp_path / "live"
    LivePublisher(folder).close()
    assert make_client(store, folder).get("/api/live").json()["online"] is False
    assert make_client(store, tmp_path / "never-started").get("/api/live").json()["online"] is False


def test_api_live_only_relays_known_simple_fields(store, live):
    folder, _ = live
    hostile = {
        "running": True, "updated_at": time.time(), "fps": 1, "zone": "z",
        "secret": "do not leak", "token": "x",
        "faces": [{"label": "<script>alert(1)</script>", "tone": "javascript:alert(1)", "state": "x", "extra": 1}] * 20,
        "door": "not a dict", "enrollment": {"name": "n", "progress": "7", "total": 20, "hint": "h", "debug": 1},
    }
    (folder / STATE).write_text(json.dumps(hostile))
    state = make_client(store, folder).get("/api/live").json()
    assert "secret" not in state and "token" not in state
    assert len(state["faces"]) == 5 and state["faces"][0]["tone"] == "muted" and "extra" not in state["faces"][0]
    assert state["door"] is None
    assert state["enrollment"] == {"name": "n", "progress": 7, "total": 20, "hint": "h"}


# --- enrollment ------------------------------------------------------------------------------------------------


def test_the_enrollment_form_shows_the_consent_text(client):
    page = client.get("/people/new").text
    assert "Consentement" in page and CONSENT_TEXT.split(",")[0][:30] in page and 'name="consent"' in page


def start(client, store, name="Camille Martin", consent="yes", **extra):
    return client.post("/people/enroll", data={"csrf": csrf(client), "name": name, "consent": consent, **extra})


def test_starting_an_enrollment_records_the_consent_and_redirects_to_the_progress_page(client, store):
    r = start(client, store)
    assert r.status_code == 303 and re.fullmatch(r"/enroll/\d+", r.headers["location"])
    row = store.get_enrollment(int(r.headers["location"].rsplit("/", 1)[1]))
    assert row["status"] == "pending" and row["name"] == "Camille Martin" and row["total"] == webapp.ENROLL_SAMPLES
    assert CONSENT_TEXT in row["consent_text"] and "admin" in row["consent_text"] and row["admin"] == "admin"
    assert store.list_audit()[0]["action"] == "enrollment_requested"


def test_no_consent_no_enrollment(client, store):
    for consent in ("", "no", "on", "YES"):
        r = start(client, store, consent=consent)
        assert r.headers["location"].endswith("m=enroll_no_consent")
    assert store.claim_enrollment() is None


@pytest.mark.parametrize("name", ["", "   ", "<b>x</b>", "x" * 41, "a;b", "-nope"])
def test_invalid_names_are_refused(client, store, name):
    assert start(client, store, name=name).headers["location"].endswith("m=enroll_invalid")
    assert store.claim_enrollment() is None


def test_one_enrollment_at_a_time_and_no_duplicate_names(client, store):
    assert start(client, store).status_code == 303
    assert start(client, store, name="Autre").headers["location"].endswith("m=enroll_busy")
    store.cancel_enrollment(1)
    store.add_person("Alice", np.zeros((1, 128)), "sface")
    assert start(client, store, name="alice").headers["location"].endswith("m=enroll_name_exists")


def test_enrollment_actions_need_the_csrf_token(client, store):
    assert client.post("/people/enroll", data={"csrf": "x", "name": "A", "consent": "yes"}).status_code == 403
    start(client, store)
    assert client.post("/enroll/1/cancel", data={"csrf": "x"}).status_code == 403
    assert store.get_enrollment(1)["status"] == "pending"


def test_the_progress_page_and_api(client, store):
    start(client, store)
    page = client.get("/enroll/1").text
    assert 'data-id="1"' in page and "Camille Martin" in page and "/static/enroll.js" in page
    store.claim_enrollment()
    store.update_enrollment(1, progress=6)
    assert client.get("/api/enroll/1").json() == {
        "id": 1, "name": "Camille Martin", "status": "running", "progress": 6,
        "total": webapp.ENROLL_SAMPLES, "message": "",
    }
    assert client.get("/api/enroll/99").status_code == 404
    assert client.get("/enroll/99").headers["location"].endswith("m=not_found")


def test_an_unclaimed_enrollment_is_failed_by_the_api_instead_of_waiting_forever(client, store):
    start(client, store)
    with store._conn() as db:
        db.execute("UPDATE enrollments SET created_at = ?", (time.time() - 500,))
    data = client.get("/api/enroll/1").json()
    assert data["status"] == "failed" and "injoignable" in data["message"]


def test_cancelling_an_enrollment(client, store):
    start(client, store)
    r = client.post("/enroll/1/cancel", data={"csrf": csrf(client)})
    assert r.headers["location"].endswith("m=enroll_cancelled")
    assert store.get_enrollment(1)["status"] == "cancelled"
    assert [a["action"] for a in store.list_audit()][:2] == ["enrollment_cancelled", "enrollment_requested"]


def test_a_hostile_name_already_in_the_database_is_escaped_on_the_progress_page(client, store):
    with store._conn() as db:
        db.execute("INSERT INTO enrollments (name, status, total, created_at, admin, consent_text) "
                   "VALUES ('<img src=x onerror=alert(1)>', 'running', 20, ?, 'admin', 'c')", (time.time(),))
    page = client.get("/enroll/1").text
    assert "<img src=x" not in page and "&lt;img" in page


# --- content security: the new pages must work under the strict CSP --------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/live", "/people", "/people/new", "/zones", "/events", "/audit"])
def test_pages_have_no_inline_script_or_style(store, live, path):
    html = make_client(store, live[0]).get(path).text
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "inline script"
    assert not re.search(r"\sstyle=", html), "inline style attribute"
    assert not re.search(r"\son[a-z]+=", html), "inline event handler"


@pytest.mark.parametrize("asset", ["app.js", "live.js", "enroll.js", "dashboard.js", "app.css", "favicon.svg"])
def test_static_assets_are_served(client, asset):
    r = client.get(f"/static/{asset}")
    assert r.status_code == 200 and len(r.content) > 100


def test_the_scripts_never_write_untrusted_data_as_html():
    from pathlib import Path

    for name in ("app.js", "live.js", "enroll.js", "dashboard.js"):
        source = (Path(webapp.__file__).parent / "static" / name).read_text(encoding="utf-8")
        source = re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", source)  # comments may mention what they forbid
        assert "innerHTML" not in source and "outerHTML" not in source and "insertAdjacentHTML" not in source, name
        assert "eval(" not in source and "document.write" not in source, name


def test_navigation_has_the_live_page(client):
    assert 'href="/live"' in client.get("/").text
