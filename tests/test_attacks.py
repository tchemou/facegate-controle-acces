import csv
import math

import pytest

from facerec import attacks, cli
from facerec.attacks import ScenarioLog, wilson
from facerec.config import Settings
from facerec.tracking import Tracker

# the fixtures and fakes of the camera-loop tests (a scripted camera and face engine)
from test_live_loop import FakeEngine, setup, store  # noqa: F401  (fixtures are used by name)


# --- statistics ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("successes, n, low, high", [
    (0, 20, 0.0, 0.1611), (20, 20, 0.8389, 1.0), (5, 10, 0.2366, 0.7634), (1, 20, 0.0089, 0.2361), (0, 10, 0.0, 0.2775),
])
def test_wilson_matches_reference_values(successes, n, low, high):
    got_low, got_high = wilson(successes, n)
    assert got_low == pytest.approx(low, abs=0.002) and got_high == pytest.approx(high, abs=0.002)


def test_zero_successes_never_means_a_zero_rate():
    for n in (5, 10, 20, 50, 100):
        assert wilson(0, n)[1] > 0
    assert wilson(0, 100)[1] < wilson(0, 10)[1]  # more trials, a tighter bound


def test_wilson_edge_cases():
    assert wilson(0, 0) == (0.0, 1.0)
    low, high = wilson(3, 7)
    assert 0 <= low < 3 / 7 < high <= 1
    assert wilson(7, 7)[1] == 1.0 and wilson(0, 7)[0] == 0.0


# --- the log -----------------------------------------------------------------------------------------------------


def test_log_rows_and_a_single_header(tmp_path):
    log = ScenarioLog(tmp_path / "sub" / "a.csv")
    log.write("photo", "denied", None, "screen_detected")
    log.write("reel", "granted", "alice")
    rows = list(csv.DictReader((tmp_path / "sub" / "a.csv").open(encoding="utf-8")))
    assert [(r["scenario"], r["decision"], r["name"], r["reason"]) for r in rows] == [
        ("photo", "denied", "", "screen_detected"), ("reel", "granted", "alice", "")]
    ScenarioLog(tmp_path / "sub" / "a.csv").write("video", "denied", None)  # reopening does not repeat the header
    assert (tmp_path / "sub" / "a.csv").read_text(encoding="utf-8").count("time,scenario") == 1


def test_an_unknown_scenario_is_refused(tmp_path):
    with pytest.raises(ValueError):
        ScenarioLog(tmp_path / "a.csv").write("alien", "denied", None)


def test_keys_map_to_real_scenarios_and_reel_is_first():
    assert attacks.KEYS["1"] == attacks.BONA_FIDE and set(attacks.KEYS.values()) == set(attacks.SCENARIOS)


# --- summary and report ----------------------------------------------------------------------------------------


def rows(*items):
    return [{"scenario": s, "decision": d, "name": "", "reason": r} for s, d, r in items]


def test_apcer_counts_accepted_attacks_and_bpcer_counts_rejected_or_silent_real_users():
    data = rows(*[("reel", "granted", "")] * 17, ("reel", "denied", "challenge_timeout:turn_left"), ("reel", "denied", "x"),
                ("reel", "no_decision", "no_decision"),
                *[("photo", "denied", "challenge_timeout:turn_left")] * 9, ("photo", "granted", ""))
    by = {r.scenario: r for r in attacks.summarise(data)}
    assert (by["reel"].attempts, by["reel"].errors) == (20, 3) and by["reel"].rate == pytest.approx(0.15)
    assert (by["photo"].attempts, by["photo"].errors, by["photo"].granted) == (10, 1, 1) and by["photo"].rate == 0.1
    assert by["photo"].reasons == {"challenge_timeout:turn_left": 9}
    assert by["reel"].reasons["no_decision"] == 1


def test_scenarios_without_attempts_are_left_out_and_foreign_rows_ignored(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("time,scenario,decision,name,reason\nt,photo,denied,,screen\nt,alien,granted,,\nt,video,,,\n", encoding="utf-8")
    summary = attacks.summarise(attacks.load([path]))
    assert [r.scenario for r in summary] == ["photo"] and summary[0].attempts == 1


def test_several_files_are_combined(tmp_path):
    for name in ("a", "b"):
        ScenarioLog(tmp_path / f"{name}.csv").write("photo", "denied", None, "screen_detected")
    assert attacks.summarise(attacks.load([tmp_path / "a.csv", tmp_path / "b.csv"]))[0].attempts == 2


def test_the_report_states_rates_intervals_and_limits():
    data = rows(*[("reel", "granted", "")] * 19, ("reel", "denied", "x"),
                *[("photo", "denied", "screen_detected")] * 20, *[("video", "denied", "challenge_timeout:turn_left")] * 19,
                ("video", "granted", ""))
    text = attacks.report(attacks.summarise(data))
    assert "BPCER 5 %" in text and "APCER 0 %" in text and "APCER 5 %" in text
    assert "aucune attaque acceptée sur 20" in text and "jusqu'à **16 %**" in text  # 0/20 is NOT "0 %"
    assert "1 acceptée(s) sur 40" in text and "Limites de cette mesure" in text and "Ce n'est pas une certification" in text
    assert "screen_detected ×20" in text


def test_the_report_never_claims_a_perfect_result_for_few_attempts():
    text = attacks.report(attacks.summarise(rows(*[("photo", "denied", "x")] * 3)))
    assert "jusqu'à **56 %**" in text  # three refusals prove very little


def test_building_a_report_from_an_empty_file_is_an_error(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("time,scenario,decision,name,reason\n", encoding="utf-8")
    with pytest.raises(ValueError):
        attacks.build_report([path])


def test_percent_formatting():
    assert attacks.percent(0.0) == "0 %" and attacks.percent(0.155) == "16 %" and attacks.percent(math.nan) == "n/d"


# --- tracker support for "no decision" accounting ------------------------------------------------------------------------


def test_tracker_counts_frames_and_reports_faces_that_left():
    settings = Settings()
    tracker = Tracker(settings)
    for _ in range(5):
        (track,) = tracker.update([(100.0, 100.0)])
    assert track.seen == 5 and tracker.dropped == []
    for _ in range(settings.track_max_missed):
        tracker.update([])
        assert tracker.dropped == []  # still within the tolerance
    tracker.update([])
    assert tracker.dropped == [track]  # reported once, on the frame it is dropped
    tracker.update([])
    assert tracker.dropped == []


# --- the camera loop in test mode -----------------------------------------------------------------------------------------------


def logged(path):
    return [(r["scenario"], r["decision"], r["name"], r["reason"]) for r in csv.DictReader(path.open(encoding="utf-8"))]


def test_each_decision_is_logged_once_with_the_announced_scenario(setup, tmp_path):
    log = tmp_path / "t.csv"
    setup(frames=30, scenario_log=ScenarioLog(log), scenario="reel")
    assert logged(log) == [("reel", "granted", "alice", "")]  # once, not once per frame


def test_an_unknown_face_is_logged_as_a_refused_attack(setup, tmp_path):
    FakeEngine.script["embedding"] = 9.0
    log = tmp_path / "t.csv"
    setup(frames=30, scenario_log=ScenarioLog(log), scenario="imposteur")
    assert logged(log) == [("imposteur", "denied", "", "unknown_person")]


def test_a_face_that_leaves_without_any_decision_is_counted(setup, tmp_path):
    FakeEngine.script["width"] = 40  # too small: the quality gate never lets a decision through
    log = tmp_path / "t.csv"

    def leaves(n):
        if n == 16:
            FakeEngine.script["faces"] = 0

    setup(frames=40, scenario_log=ScenarioLog(log), scenario="reel", on_frame=leaves)
    assert logged(log) == [("reel", "no_decision", "", "no_decision")]


def test_a_flicker_is_not_an_attempt(setup, tmp_path):
    FakeEngine.script["width"] = 40
    log = tmp_path / "t.csv"

    def flicker(n):
        if n == 3:
            FakeEngine.script["faces"] = 0

    setup(frames=30, scenario_log=ScenarioLog(log), scenario="reel", on_frame=flicker)
    assert not log.exists() or logged(log) == []


def test_a_face_that_was_decided_does_not_also_count_as_no_decision(setup, tmp_path):
    log = tmp_path / "t.csv"

    def leaves(n):
        if n == 20:
            FakeEngine.script["faces"] = 0

    setup(frames=45, scenario_log=ScenarioLog(log), scenario="reel", on_frame=leaves)
    assert [d for _, d, _, _ in logged(log)] == ["granted"]


def test_the_test_mode_off_writes_nothing(setup, tmp_path):
    setup(frames=30)
    assert list(tmp_path.glob("*.csv")) == []


# --- command line -----------------------------------------------------------------------------------------------------------------


def test_attack_testing_is_refused_with_a_real_lock(tmp_path):
    args = cli.build_parser().parse_args(["run", "--db", str(tmp_path / "x.db"), "--door", "gpio", "--gpio-pin", "17",
                                          "--attack-log", str(tmp_path / "a.csv")])
    with pytest.raises(SystemExit, match="jamais avec une vraie gâche"):
        cli.cmd_run(args)


def test_the_report_command_prints_and_writes(tmp_path, capsys):
    log = ScenarioLog(tmp_path / "a.csv")
    for _ in range(4):
        log.write("photo", "denied", None, "screen_detected")
    cli.main(["attack-report", str(tmp_path / "a.csv"), "--out", str(tmp_path / "out" / "r.md")])
    out = capsys.readouterr().out
    assert "Tests d'attaques de présentation" in out and "Rapport écrit" in out
    assert (tmp_path / "out" / "r.md").read_text(encoding="utf-8").startswith("# Tests d'attaques")


def test_the_report_command_explains_its_failures(tmp_path):
    with pytest.raises(SystemExit, match="Rapport impossible"):
        cli.main(["attack-report", str(tmp_path / "missing.csv")])
    empty = tmp_path / "e.csv"
    empty.write_text("time,scenario,decision,name,reason\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="aucun essai"):
        cli.main(["attack-report", str(empty)])
