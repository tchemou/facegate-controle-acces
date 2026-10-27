"""`python -m facerec.web`: the entry point of the Docker web image (no dlib, no camera)."""

import socket
import subprocess
import sys
from pathlib import Path

import pytest

from facerec.store import Store
from facerec.web import __main__ as entry

SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_options_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("FACEGATE_DB", "/data/x.db")
    monkeypatch.setenv("FACEGATE_HOST", "0.0.0.0")
    monkeypatch.setenv("FACEGATE_PORT", "9123")
    monkeypatch.setenv("FACEGATE_LIVE_DIR", "/data/live")
    monkeypatch.setenv("FACEGATE_SECURE_COOKIES", "1")
    args = entry.build_parser().parse_args([])
    assert (str(args.db), args.host, args.port, str(args.live_dir), args.secure_cookies) == (
        str(Path("/data/x.db")), "0.0.0.0", 9123, str(Path("/data/live")), True)


def test_the_safe_defaults(monkeypatch):
    for name in ("FACEGATE_DB", "FACEGATE_HOST", "FACEGATE_PORT", "FACEGATE_LIVE_DIR", "FACEGATE_SECURE_COOKIES"):
        monkeypatch.delenv(name, raising=False)
    args = entry.build_parser().parse_args([])
    assert args.host == "127.0.0.1" and args.port == 8000 and args.live_dir is None and args.secure_cookies is False


def test_command_line_beats_the_environment(monkeypatch):
    monkeypatch.setenv("FACEGATE_PORT", "9123")
    assert entry.build_parser().parse_args(["--port", "9999"]).port == 9999


def test_a_busy_port_is_reported_with_a_suggestion():
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(SystemExit) as stop:
            entry.check_port_free("127.0.0.1", port)
    assert str(port) in str(stop.value) and str(port + 1) in str(stop.value)


def test_a_free_port_passes():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    entry.check_port_free("127.0.0.1", port)


def test_create_admin_from_the_web_entry_point(tmp_path, monkeypatch, capsys):
    db = tmp_path / "t.db"
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "Correct-Horse-42")
    entry.main(["create-admin", "camille", "--db", str(db), "--role", "manager"])
    assert Store(db).get_admin("camille")["role"] == "manager" and "(manager)" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already exists"):
        entry.main(["create-admin", "camille", "--db", str(db)])


def test_create_admin_refuses_weak_and_mismatched_passwords(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "weak")
    with pytest.raises(SystemExit, match="too weak"):
        entry.main(["create-admin", "a", "--db", str(db)])
    answers = iter(["Correct-Horse-42", "Another-Horse-42"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit, match="do not match"):
        entry.main(["create-admin", "b", "--db", str(db)])
    assert Store(db).admin_count() == 0


def test_the_web_app_starts_in_a_process_where_dlib_and_pillow_cannot_be_imported(tmp_path):
    """What the Docker `web` image relies on: nothing in the web import chain needs dlib, Pillow or matplotlib."""
    code = (
        "import sys\n"
        "for blocked in ('dlib', 'PIL', 'matplotlib', 'sklearn', 'face_recognition_models'):\n"
        "    sys.modules[blocked] = None\n"
        f"sys.path.insert(0, {SRC!r})\n"
        "import facerec.web.__main__\n"
        "from facerec.web.app import create_app\n"
        f"create_app({str(tmp_path / 'p.db')!r})\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr[-500:]
