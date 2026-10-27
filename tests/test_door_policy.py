import json
import sys
import types
from datetime import datetime

import pytest

from facerec.door import Door, GpioDoor, make_door
from facerec.policy import NOT_AUTHORIZED, OK, OUTSIDE_SCHEDULE, UNKNOWN_ZONE, AccessPolicy

MON_10H = datetime(2026, 10, 26, 10, 0)  # a Monday
MON_22H = datetime(2026, 10, 26, 22, 0)
TUE_2H = datetime(2026, 10, 27, 2, 0)
SAT_10H = datetime(2026, 10, 31, 10, 0)

POLICY = AccessPolicy.from_dict({
    "zones": {
        "lab": {
            "alice": "always",
            "pagnol": [{"days": ["mon", "tue", "wed", "thu", "fri"], "from": "08:00", "to": "19:00"}],
            "gardien": [{"days": ["mon"], "from": "22:00", "to": "06:00"}],  # overnight, starts Monday
        },
    }
})


# --- door -------------------------------------------------------------------------


def test_door_opens_then_closes_after_the_delay():
    door = Door(open_seconds=5)
    assert not door.is_open
    door.open(now=100.0)
    door.update(104.9)
    assert door.is_open and door.seconds_left(104.0) == pytest.approx(1.0)
    door.update(105.0)
    assert not door.is_open and door.seconds_left(105.0) == 0.0


def test_a_new_grant_extends_the_opening_and_is_counted_once():
    door = Door(open_seconds=5)
    door.open(100.0)
    door.open(103.0)
    door.update(106.0)
    assert door.is_open  # extended to 108
    door.update(108.0)
    assert not door.is_open
    assert door.opened_count == 1
    door.open(120.0)
    assert door.opened_count == 2


def test_close_is_immediate_and_safe_to_repeat():
    door = Door()
    door.open(0.0)
    door.close()
    door.close()
    assert not door.is_open


def test_make_door():
    assert make_door("none", 5) is None
    assert isinstance(make_door("sim", 3), Door)
    with pytest.raises(RuntimeError):
        make_door("gpio", 5)  # no pin
    with pytest.raises(RuntimeError):
        make_door("magic", 5)


@pytest.fixture
def fake_gpiozero(monkeypatch):
    calls = []

    class OutputDevice:
        def __init__(self, pin, active_high=True, initial_value=False):
            calls.append(("init", pin, active_high, initial_value))

        def on(self):
            calls.append("on")

        def off(self):
            calls.append("off")

    module = types.ModuleType("gpiozero")
    module.OutputDevice = OutputDevice
    monkeypatch.setitem(sys.modules, "gpiozero", module)
    return calls


def test_gpio_door_drives_the_pin_and_starts_locked(fake_gpiozero):
    door = GpioDoor(pin=17, open_seconds=2, active_high=False)
    assert fake_gpiozero == [("init", 17, False, False), "off"]  # locked at startup
    door.open(10.0)
    door.update(11.9)  # still within the 2 s opening: no change
    door.update(12.0)  # time is up: relay released
    door.close()  # explicit close always releases the relay
    assert fake_gpiozero[2:] == ["on", "off", "off"]


def test_gpio_door_without_gpiozero_gives_a_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "gpiozero", None)  # makes `import gpiozero` fail
    with pytest.raises(RuntimeError, match="gpiozero"):
        GpioDoor(pin=17)


# --- policy -----------------------------------------------------------------------


def test_always_and_scheduled_access():
    assert POLICY.check("alice", "lab", SAT_10H) == (True, OK)
    assert POLICY.check("pagnol", "lab", MON_10H) == (True, OK)


def test_outside_the_schedule_is_refused():
    assert POLICY.check("pagnol", "lab", SAT_10H) == (False, OUTSIDE_SCHEDULE)
    assert POLICY.check("pagnol", "lab", MON_22H) == (False, OUTSIDE_SCHEDULE)


def test_default_deny_for_unlisted_people_zones_and_unknown_faces():
    assert POLICY.check("bob", "lab", MON_10H) == (False, NOT_AUTHORIZED)
    assert POLICY.check(None, "lab", MON_10H) == (False, NOT_AUTHORIZED)
    assert POLICY.check("alice", "server", MON_10H) == (False, UNKNOWN_ZONE)


def test_the_end_of_the_window_is_exclusive():
    assert POLICY.check("pagnol", "lab", datetime(2026, 10, 26, 19, 0)) == (False, OUTSIDE_SCHEDULE)
    assert POLICY.check("pagnol", "lab", datetime(2026, 10, 26, 18, 59)) == (True, OK)


def test_overnight_window_belongs_to_the_day_it_starts():
    assert POLICY.check("gardien", "lab", MON_22H)[0]  # Monday 22:00
    assert POLICY.check("gardien", "lab", TUE_2H)[0]  # Tuesday 02:00, still Monday's shift
    assert not POLICY.check("gardien", "lab", MON_10H)[0]
    assert not POLICY.check("gardien", "lab", datetime(2026, 10, 26, 2, 0))[0]  # Monday 02:00: Sunday's shift


def test_bad_day_name_is_rejected_when_loading():
    with pytest.raises(ValueError, match="funday"):
        AccessPolicy.from_dict({"zones": {"lab": {"a": [{"days": ["funday"], "from": "08:00", "to": "09:00"}]}}})


def test_policy_loads_from_a_json_file(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"zones": {"lab": {"alice": "always"}}}), encoding="utf-8")
    assert AccessPolicy.load(path).check("alice", "lab", MON_10H) == (True, OK)
