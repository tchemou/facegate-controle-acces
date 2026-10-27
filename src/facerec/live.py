import csv
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import overlay as ov
from .antispoof import FAILED, PASSED
from .attacks import KEYS, MIN_FRAMES_FOR_AN_ATTEMPT, Presentation, ScenarioLog
from .channel import LivePublisher
from .config import Settings
from .door import Door
from .engine import FaceEngine, rect_box, shape_points
from .enrollment import EnrollmentSession
from .gallery import Gallery
from .policy import AccessPolicy, policy_from_store
from .quality import assess
from .smoothing import KNOWN, PENDING
from .store import Store
from .tracking import Tracker

TONES = {"ok": ov.OK, "bad": ov.BAD, "warn": ov.WARN, "muted": ov.MUTED, "accent": ov.ACCENT}

REASON_HINTS = {
    "too_small": "Approchez-vous",
    "blurry": "Image floue",
    "too_dark": "Trop sombre",
    "overexposed": "Trop lumineux",
    "not_frontal": "Regardez la caméra",
    "cut_off": "Visage coupé",
    "out_of_frame": "Visage hors cadre",
}
POLICY_HINTS = {
    "not_authorized": "non autorisé dans cette zone",
    "outside_schedule": "hors des horaires",
    "unknown_zone": "zone inconnue",
}

RELOAD_EVERY_S = 2.0  # how often the recognition loop checks whether the web app changed anything
ENROLL_POLL_S = 1.0  # how often it looks for an enrollment queued from the web app
ENROLL_CANCEL_POLL_S = 0.5


def reload_if_changed(
    store: Store, last_revision: int, embedder: str, reload_rules: bool, policy: AccessPolicy | None
) -> tuple[int, Gallery, AccessPolicy | None] | None:
    """Fresh (revision, gallery, policy) when the web app changed something since `last_revision`, else None.

    The policy is only replaced when it comes from the database: a --policy file stays in force.
    """
    revision = store.revision()
    if revision == last_revision:
        return None
    return revision, store.load_gallery(embedder), policy_from_store(store) if reload_rules else policy


def _backends() -> list[int]:
    if sys.platform == "win32":
        return [cv2.CAP_MSMF, cv2.CAP_DSHOW, cv2.CAP_ANY]
    return [cv2.CAP_ANY]


def open_camera(index: int, attempts: int = 3) -> cv2.VideoCapture:
    """Try each backend a few times: a camera often fails to open once, then works."""
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    for attempt in range(attempts):
        for backend in _backends():
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened() and cap.read()[0]:
                return cap
            cap.release()
        time.sleep(0.5)
    raise RuntimeError(
        f"cannot open camera {index}. Close any app using the webcam (Teams, Zoom, browser, "
        "Camera app), check Settings > Privacy > Camera, or try --camera 1. "
        "Run `facerec cameras` to see which indexes work."
    )


def list_cameras(max_index: int = 4) -> list[int]:
    found = []
    for index in range(max_index):
        try:
            open_camera(index, attempts=1).release()
            found.append(index)
        except RuntimeError:
            pass
    return found


def _confidence(distance: float, threshold: float) -> float:
    """1.0 for a clearly close match (<= 60% of the threshold), 0.0 at the threshold."""
    return float(np.clip((threshold - distance) / (0.4 * threshold), 0.0, 1.0))


@dataclass(frozen=True)
class Verdict:
    """What the system concludes about one face, independently of how it is drawn."""

    label: str
    tone: str  # ok | bad | warn | muted
    state: str  # pending | known | granted | denied
    reason: str = ""
    show_challenge: bool = False


def decide(
    track, report, state: str, name: str | None, now: float, *, challenge: bool, liveness: bool,
    policy: AccessPolicy | None, zone: str, when: datetime,
) -> Verdict:
    """The access decision for a tracked face (order matters: the first matching rule wins)."""
    if not report.ok and state == PENDING:
        return Verdict(REASON_HINTS.get(report.reasons[0], report.reasons[0]), "muted", "pending")
    if state == PENDING:
        return Verdict("Analyse…", "warn", "pending")
    if state == KNOWN and challenge and track.screen.is_screen:
        return Verdict("Écran détecté · accès refusé", "bad", "denied", "screen_detected")
    if state == KNOWN and challenge and track.challenge.status == FAILED:
        return Verdict("Vérification non réussie · accès refusé", "bad", "denied",
                       f"challenge_timeout:{track.challenge.failed_on}")
    if state == KNOWN and challenge and track.challenge.status != PASSED:
        return Verdict(str(name), "warn", "known", show_challenge=True)
    if state == KNOWN and liveness and not track.blink.is_live(now):
        return Verdict(f"{name} · clignez des yeux", "warn", "known")
    if state == KNOWN:
        allowed, why = policy.check(name, zone, when) if policy else (True, "")
        if allowed:
            return Verdict(f"{name} · accès autorisé", "ok", "granted")
        return Verdict(f"{name} · {POLICY_HINTS.get(why, why)}", "bad", "denied", f"policy:{why}")
    return Verdict("Inconnu · accès refusé", "bad", "denied", "unknown_person")


class AccessLog:
    """Decision log: timestamp, decision and name. No image and no embedding are written."""

    def __init__(self, path: Path | None, store: Store | None = None, zone: str = ""):
        self._path, self._store, self._zone = path, store, zone
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, decision: str, name: str | None, reason: str = "") -> None:
        if self._store is not None:
            self._store.add_event(decision, name, self._zone, reason)
        if self._path is None:
            return
        self._migrate()
        new = not self._path.exists()
        with self._path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new:
                writer.writerow(["time", "decision", "name", "reason"])
            writer.writerow([datetime.now().isoformat(timespec="seconds"), decision, name or "", reason])

    def _migrate(self) -> None:
        """Add the `reason` column to a log written before it existed."""
        if not self._path.exists():
            return
        lines = self._path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0] == "time,decision,name":
            rows = [lines[0] + ",reason"] + [line + "," for line in lines[1:]]
            self._path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def draw_face(frame, box, verdict: Verdict, track, now: float, confidence: float | None, challenge: bool) -> None:
    """Brackets, status chip and, while a challenge runs, the arrow, steps and countdown ring."""
    ov.face_label(frame, box, verdict.label, TONES[verdict.tone], confidence)
    if not (challenge and verdict.show_challenge):
        return
    view = track.challenge.view(now)
    left, top, right, bottom = box
    ov.instruction(frame, view.text, ov.ACCENT)
    ov.steps(frame, frame.shape[1] // 2 - 11 * (view.total - 1), 62, view.step, view.total, ov.ACCENT)
    if view.direction:
        side = left - 60 if view.direction < 0 else right + 60
        pulse = 0.5 + 0.5 * np.sin(now * 6)
        ov.arrow(frame, (int(np.clip(side, 40, frame.shape[1] - 40)), (top + bottom) // 2), view.direction,
                 ov.ACCENT, pulse=pulse)
    if view.seconds_left > 0:
        center = (frame.shape[1] - 44, 44)
        ov.ring(frame, center, 24, view.fraction_left, ov.ACCENT)
        text = f"{view.seconds_left:.0f}"
        ov.put_text(frame, text, center[0] - ov.text_width(text, 16, True) // 2, center[1] - 11, 16, ov.WHITE, bold=True)


class _Enrollment:
    """An enrollment taken from the web app's queue, and the bookkeeping to report it."""

    def __init__(self, row, session: EnrollmentSession):
        self.row, self.session, self.last_cancel_check = row, session, 0.0


def _finish_enrollment(store: Store, active: _Enrollment, settings: Settings) -> None:
    """Persist a completed enrollment, or report why it failed. Nobody is enrolled twice."""
    session, row = active.session, active.row
    if session.done and session.failed is None:
        everyone = store.load_gallery(settings.embedder, only_active=False)  # deactivated people count too
        session.fail_if_duplicate(everyone, settings.threshold, settings.margin)
    if session.failed is not None:
        store.update_enrollment(row["id"], status="failed", message=session.failed, progress=session.progress)
        return
    try:
        store.add_person(row["name"], np.vstack(session.embeddings), settings.embedder, row["consent_text"])
    except Exception as err:  # e.g. the same name was created meanwhile
        store.update_enrollment(row["id"], status="failed", message=f"Enregistrement impossible : {err}")
        return
    store.add_audit(row["admin"], "person_enrolled", row["name"])
    store.update_enrollment(row["id"], status="done", progress=session.total, message="Inscription terminée")


def run_live(
    gallery: Gallery,
    settings: Settings,
    camera: int = 0,
    liveness: bool = False,
    log_path: Path | None = None,
    challenge: bool = False,
    door: Door | None = None,
    policy: AccessPolicy | None = None,
    zone: str = "default",
    store: Store | None = None,
    live_reload: bool = False,
    reload_rules: bool = False,
    live_dir: Path | None = None,
    show_window: bool = True,
    scenario_log: ScenarioLog | None = None,
    scenario: str = "reel",
) -> None:
    """`live_reload`: the gallery comes from `store` and is reloaded when the web app changes it
    (`reload_rules`: the access rules too, otherwise a --policy file or no policy stays in force).
    `live_dir`: publish the annotated frame and state there for the web app's live view (opt-in).
    With a store, an enrollment queued in the web app is performed here, since this loop owns the camera.
    `scenario_log`: attack-test mode. Every decision (and every face that left without one) is written with the
    scenario announced by the operator (keys 1-5 in the window, or `scenario` at the start); see attacks.py."""
    if not len(gallery):
        raise RuntimeError("gallery is empty: enroll someone first (facerec enroll NAME)")
    engine, tracker, log = FaceEngine(settings), Tracker(settings), AccessLog(log_path, store, zone)
    publisher = LivePublisher(live_dir) if live_dir is not None else None
    last_revision = store.revision() if live_reload and store is not None else 0
    last_check = last_enroll_poll = time.monotonic()
    enrolling: _Enrollment | None = None
    mode = f"{settings.detector} + {settings.embedder}" + (" · défi" if challenge else "")
    cap = open_camera(camera)
    frame_idx, fps, last = 0, 0.0, time.monotonic()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            now = time.monotonic()
            fps = 0.9 * fps + 0.1 / max(now - last, 1e-6)
            last = now
            if live_reload and store is not None and now - last_check >= RELOAD_EVERY_S:
                last_check = now
                changed = reload_if_changed(store, last_revision, settings.embedder, reload_rules, policy)
                if changed is not None:  # an admin changed people, zones or rules in the web app
                    last_revision, gallery, policy = changed
                    print(f"Base rechargée : {len(gallery)} personne(s) active(s).")
            if store is not None and enrolling is None and now - last_enroll_poll >= ENROLL_POLL_S:
                last_enroll_poll = now
                store.expire_enrollments()
                row = store.claim_enrollment()
                if row is not None:
                    enrolling = _Enrollment(row, EnrollmentSession(row["name"], total=row["total"]))
                    print(f"Inscription de {row['name']} demandée depuis l'interface web.")

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            rects = engine.detect(rgb)
            faces_state: list[dict] = []

            if enrolling is not None:
                mode = "inscription"
                if now - enrolling.last_cancel_check >= ENROLL_CANCEL_POLL_S:
                    enrolling.last_cancel_check = now
                    current = store.get_enrollment(enrolling.row["id"])
                    if current is None or current["status"] != "running":  # cancelled from the web app
                        enrolling = None
                        mode = f"{settings.detector} + {settings.embedder}" + (" · défi" if challenge else "")
            if enrolling is not None:
                session = enrolling.session
                reasons, shape = (), None
                if len(rects) == 1:
                    shape = engine.shape(rgb, rects[0])
                    reasons = assess(gray, shape_points(shape), rect_box(rects[0]), settings).reasons
                if session.observe(now, len(rects), reasons) and shape is not None:
                    session.add(engine.embed(rgb, shape), now)
                    store.update_enrollment(enrolling.row["id"], progress=session.progress)
                for rect in rects:
                    ov.corner_brackets(frame, rect_box(rect), ov.ACCENT if not reasons else ov.WARN)
                ov.enrollment_panel(frame, enrolling.row["name"], session.progress, session.total, session.hint)
                faces_state.append({"label": session.hint, "tone": "accent", "state": "enrolling"})
                if session.finished:
                    _finish_enrollment(store, enrolling, settings)
                    enrolling = None
                    tracker = Tracker(settings)  # forget the faces seen before: votes start afresh
                    if live_reload:  # the new person is still in front of the camera: recognise them right away
                        last_revision = store.revision()
                        gallery = store.load_gallery(settings.embedder)
                        if reload_rules:
                            policy = policy_from_store(store)
                    mode = f"{settings.detector} + {settings.embedder}" + (" · défi" if challenge else "")
            else:
                centers = [((r.left() + r.right()) / 2, (r.top() + r.bottom()) / 2) for r in rects]
                tracks = tracker.update(centers)
                if scenario_log is not None:  # a face that has left: its presentation is over, log its outcome once
                    for gone in tracker.dropped:
                        presentation = gone.extra.get("presentation")
                        if presentation is not None:
                            scenario_log.write_presentation(presentation, gone.seen >= MIN_FRAMES_FOR_AN_ATTEMPT)
                for rect, track in zip(rects, tracks):
                    if scenario_log is not None and "presentation" not in track.extra:
                        track.extra["presentation"] = Presentation(scenario)  # labelled as announced on arrival
                    shape = engine.shape(rgb, rect)
                    points = shape_points(shape)
                    track.blink.update(points, now)
                    report = assess(gray, points, rect_box(rect), settings)
                    if report.ok and frame_idx % settings.process_every == 0:
                        if len(gallery):
                            match = gallery.identify(engine.embed(rgb, shape), settings.threshold, settings.margin)
                            track.smoother.update(match.name)
                            track.extra["distance"] = match.distance
                        else:  # everyone was deactivated or deleted while running: nobody is recognised
                            track.smoother.update(None)
                    if challenge:
                        left, top, right, bottom = rect_box(rect)
                        face = gray[max(top, 0):bottom, max(left, 0):right]
                        if min(face.shape[:2]) >= 40:
                            track.screen.update(face)
                    state, name = track.smoother.decision()
                    if challenge and state == KNOWN:  # the challenge only starts once identity is settled
                        track.challenge.update(points, now)

                    verdict = decide(track, report, state, name, now, challenge=challenge, liveness=liveness,
                                     policy=policy, zone=zone, when=datetime.now())
                    if verdict.state in ("granted", "denied") and track.last_state != verdict.state:
                        log.write(verdict.state, name, verdict.reason)
                        if scenario_log is not None:
                            track.extra["presentation"].observe(verdict.state, name, verdict.reason)
                        if verdict.state == "granted" and door is not None:
                            door.open(now)
                    if verdict.state in ("granted", "denied"):
                        track.last_state = verdict.state
                    conf = None
                    if verdict.state in ("known", "granted") and "distance" in track.extra:
                        conf = _confidence(track.extra["distance"], settings.threshold)
                    draw_face(frame, rect_box(rect), verdict, track, now, conf, challenge)
                    faces_state.append({"label": verdict.label, "tone": verdict.tone, "state": verdict.state})

            ov.hud(frame, fps, mode if scenario_log is None else f"{mode} · TEST : {scenario}")
            if scenario_log is not None:
                hint = "1 réel · 2 photo · 3 vidéo · 4 imposteur · 5 masque"
                ov.put_text(frame, hint, frame.shape[1] - ov.text_width(hint, 13) - 14, frame.shape[0] - 26, 13, ov.MUTED)
            if door is not None:
                door.update(now)
                ov.door_banner(frame, door.is_open, door.seconds_left(now), door.open_seconds, zone)
            if publisher is not None:
                publisher.publish(frame, {
                    "running": True, "fps": round(fps, 1), "zone": zone, "mode": mode, "faces": faces_state,
                    "door": None if door is None else {"open": door.is_open, "seconds_left": round(door.seconds_left(now), 1)},
                    "enrollment": None if enrolling is None else {
                        "id": enrolling.row["id"], "name": enrolling.row["name"],
                        "progress": enrolling.session.progress, "total": enrolling.session.total,
                        "hint": enrolling.session.hint,
                    },
                }, now)
            if show_window:
                cv2.imshow("FaceGate - q pour quitter", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if scenario_log is not None and chr(key) in KEYS:
                    scenario = KEYS[chr(key)]  # the operator announces what they are about to present
            frame_idx += 1
    finally:
        if door is not None:
            door.close()  # fail closed: never leave a lock energised
        if enrolling is not None:  # stopped in the middle: do not leave the web page waiting
            store.update_enrollment(enrolling.row["id"], status="failed", message="Service caméra arrêté")
        if scenario_log is not None:  # the operator quit while faces were still in front of the camera
            for alive in tracker.alive:
                presentation = alive.extra.get("presentation")
                if presentation is not None:
                    scenario_log.write_presentation(presentation, alive.seen >= MIN_FRAMES_FOR_AN_ATTEMPT)
        if publisher is not None:
            publisher.close()  # deletes the last image
        cap.release()
        cv2.destroyAllWindows()


def capture_samples(
    camera: int, settings: Settings, count: int, on_sample, title: str, min_interval: float = 0.4
) -> int:
    """Grab `count` frames with a good-quality face, spaced out so poses vary."""
    engine = FaceEngine(settings)
    cap = open_camera(camera)
    taken, last_taken = 0, 0.0
    try:
        while taken < count:
            ok, frame = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            rects = engine.detect(rgb)
            hint, tone = "Aucun visage", "muted"
            if len(rects) == 1:
                rect = rects[0]
                shape = engine.shape(rgb, rect)
                report = assess(gray, shape_points(shape), rect_box(rect), settings)
                if report.ok:
                    hint, tone = "Parfait, bougez légèrement la tête", "ok"
                    if time.monotonic() - last_taken >= min_interval:
                        on_sample(frame, rgb, shape, engine)
                        taken, last_taken = taken + 1, time.monotonic()
                else:
                    hint, tone = REASON_HINTS.get(report.reasons[0], report.reasons[0]), "muted"
                ov.face_label(frame, rect_box(rect), hint, TONES[tone])
            elif len(rects) > 1:
                ov.instruction(frame, "Une seule personne, s'il vous plaît", ov.BAD)
            ov.enrollment_panel(frame, title, taken, count, hint)
            cv2.imshow("FaceGate", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    return taken
