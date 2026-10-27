"""One row per presentation: a face from its arrival to its departure, judged on its outcome."""

import csv

import pytest

from facerec import attacks
from facerec.attacks import Presentation, ScenarioLog

from test_live_loop import FakeEngine, setup, store  # noqa: F401  (fixtures are used by name)


def rows(path):
    return list(csv.DictReader(path.open(encoding="utf-8")))


# --- the outcome of one presentation -------------------------------------------------------------------------------


def test_refused_then_recognised_is_one_acceptance_with_a_transient_refusal():
    """The pattern seen on the real camera: 'refused (unknown)', then 'granted' a second later, same person."""
    p = Presentation("reel")
    p.observe("denied", None, "unknown_person")
    p.observe("granted", "pagnol")
    assert p.final == ("granted", "pagnol", "") and p.earlier_denial == "unknown_person" and p.decided


def test_acceptance_is_sticky_an_attack_that_got_in_stays_in():
    p = Presentation("photo")
    p.observe("granted", "pagnol")
    p.observe("denied", "pagnol", "screen_detected")  # a later refusal does not undo the success
    assert p.final[0] == "granted" and p.earlier_denial == ""


def test_several_refusals_keep_the_last_reason():
    p = Presentation("photo")
    p.observe("denied", None, "unknown_person")
    p.observe("denied", "pagnol", "challenge_timeout:turn_left")
    assert p.final == ("denied", "pagnol", "challenge_timeout:turn_left") and p.earlier_denial == ""


def test_the_first_refusal_before_an_acceptance_is_the_one_remembered():
    p = Presentation("reel")
    p.observe("denied", None, "unknown_person")
    p.observe("denied", "pagnol", "screen_detected")
    p.observe("granted", "pagnol")
    assert p.earlier_denial == "screen_detected"  # the refusal immediately before the acceptance


def test_nothing_observed_means_undecided():
    assert not Presentation("reel").decided


# --- the rows -----------------------------------------------------------------------------------------------------------


def test_rows_for_decided_undecided_and_flickering_presentations(tmp_path):
    log = ScenarioLog(tmp_path / "a.csv")
    ok = Presentation("reel")
    ok.observe("granted", "pagnol")
    log.write_presentation(ok)
    silent = Presentation("reel")
    log.write_presentation(silent)  # seen long enough, never decided
    flicker = Presentation("reel")
    log.write_presentation(flicker, min_decided_frames_ok=False)  # seen for a few frames only: not an attempt
    assert [(r["decision"], r["name"]) for r in rows(tmp_path / "a.csv")] == [("granted", "pagnol"), ("no_decision", "")]


def test_a_decided_flicker_is_still_recorded(tmp_path):
    """A short appearance that DID get a decision (e.g. an attack accepted at once) must never be dropped."""
    log = ScenarioLog(tmp_path / "a.csv")
    p = Presentation("photo")
    p.observe("granted", "pagnol")
    log.write_presentation(p, min_decided_frames_ok=False)
    assert [r["decision"] for r in rows(tmp_path / "a.csv")] == ["granted"]


def test_the_scenario_is_the_one_announced_when_the_face_arrived(tmp_path):
    log = ScenarioLog(tmp_path / "a.csv")
    p = Presentation("video")
    p.observe("denied", None, "unknown_person")
    log.write_presentation(p)
    assert rows(tmp_path / "a.csv")[0]["scenario"] == "video"


# --- a file written by the earlier version -------------------------------------------------------------------------------------


def test_an_old_file_gets_the_new_column_and_stays_readable(tmp_path):
    path = tmp_path / "old.csv"
    path.write_text("time,scenario,decision,name,reason\n2026-10-26T18:11:28,reel,granted,pagnol,\n"
                    "2026-10-26T18:12:10,reel,denied,,unknown_person\n", encoding="utf-8")
    log = ScenarioLog(path)
    p = Presentation("reel")
    p.observe("denied", None, "unknown_person")
    p.observe("granted", "pagnol")
    log.write_presentation(p)
    data = rows(path)
    assert list(data[0].keys()) == attacks.FIELDS and len(data) == 3
    assert data[0]["earlier_denial"] == "" and data[2]["earlier_denial"] == "unknown_person"
    summary = {r.scenario: r for r in attacks.summarise(attacks.load([path]))}
    assert summary["reel"].attempts == 3 and summary["reel"].after_denial == 1


def test_a_current_file_is_left_alone(tmp_path):
    path = tmp_path / "a.csv"
    ScenarioLog(path).write("photo", "denied", None, "screen_detected")
    before = path.read_text(encoding="utf-8")
    ScenarioLog(path)
    assert path.read_text(encoding="utf-8") == before


# --- the report ---------------------------------------------------------------------------------------------------------------------


def test_the_report_mentions_transient_refusals_for_real_users():
    data = [{"scenario": "reel", "decision": "granted", "name": "", "reason": "", "earlier_denial": "unknown_person"}] * 3 + \
           [{"scenario": "reel", "decision": "granted", "name": "", "reason": "", "earlier_denial": ""}] * 7
    text = attacks.report(attacks.summarise(data))
    assert "3 des 10 accès ont été précédés d'un refus transitoire" in text and "BPCER 0 %" in text


def test_the_report_flags_an_attack_that_only_gave_way_after_a_first_refusal():
    data = [{"scenario": "video", "decision": "granted", "name": "", "reason": "", "earlier_denial": "challenge_timeout:turn_left"}] + \
           [{"scenario": "video", "decision": "denied", "name": "", "reason": "x", "earlier_denial": ""}] * 9
    text = attacks.report(attacks.summarise(data))
    assert "1 attaque(s) acceptée(s) après un premier refus" in text and "APCER 10 %" in text


# --- the camera loop ------------------------------------------------------------------------------------------------------------------


def test_refused_then_recognised_in_front_of_the_camera_is_logged_once(setup, tmp_path):
    FakeEngine.script["embedding"] = 9.0  # nobody the gallery knows
    log = tmp_path / "t.csv"

    def becomes_alice(n):
        if n == 9:
            FakeEngine.script["embedding"] = 0.0  # the camera finally sees the enrolled person

    setup(frames=40, scenario_log=ScenarioLog(log), scenario="reel", on_frame=becomes_alice)
    data = rows(log)
    assert [(r["scenario"], r["decision"], r["name"], r["earlier_denial"]) for r in data] == [
        ("reel", "granted", "alice", "unknown_person")]  # ONE presentation, not a refusal plus an acceptance


def test_two_visits_are_two_presentations(setup, tmp_path):
    log = tmp_path / "t.csv"

    def leaves_and_a_stranger_comes(n):
        if n == 20:
            FakeEngine.script["faces"] = 0  # the first person leaves
        if n == 40:
            FakeEngine.script.update(faces=1, embedding=9.0)  # a stranger arrives

    setup(frames=70, scenario_log=ScenarioLog(log), scenario="reel", on_frame=leaves_and_a_stranger_comes)
    assert [(r["decision"], r["reason"]) for r in rows(log)] == [("granted", ""), ("denied", "unknown_person")]


def test_a_face_still_present_when_the_session_ends_is_not_lost(setup, tmp_path):
    log = tmp_path / "t.csv"
    setup(frames=30, scenario_log=ScenarioLog(log), scenario="photo")
    assert [(r["scenario"], r["decision"]) for r in rows(log)] == [("photo", "granted")]  # flushed on exit, once
