"""SIMULATED camera service, for demos and for trying the web app without a camera.

It publishes synthetic frames drawn with the real overlay (a grey oval stands in for a face), cycles
through the states a visitor would see, and performs enrollments queued in the web app with RANDOM
embeddings. Nothing it creates is a real face: do not use it for real access control.

    python scripts/simulate_service.py --db data/demo.db --live-dir data/demo_live --seconds 600
"""

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from facerec import overlay as ov
from facerec.channel import LivePublisher
from facerec.store import Store

W, H = 640, 480
BOX = (250, 120, 390, 300)


def background() -> np.ndarray:
    gradient = np.linspace(72, 40, H)[:, None, None] * np.ones((1, W, 3))
    frame = gradient.astype(np.uint8)
    frame[:, :, 0] = np.clip(frame[:, :, 0] + 12, 0, 255)
    cv2.ellipse(frame, (320, 205), (78, 98), 0, 0, 360, (150, 150, 165), -1, cv2.LINE_AA)  # stand-in for a face
    return frame


def scene(kind: str, t: float, door_left: float, zone: str):
    frame, faces = background(), []
    if kind == "waiting":
        pass
    elif kind == "analysing":
        ov.face_label(frame, BOX, "Analyse…", ov.WARN)
        faces = [("Analyse…", "warn", "pending")]
    elif kind == "challenge":
        ov.face_label(frame, BOX, "Camille Martin", ov.WARN)
        ov.instruction(frame, "Tournez la tête vers la gauche de l'écran", ov.ACCENT)
        ov.steps(frame, 320 - 22, 62, 1, 3, ov.ACCENT)
        ov.arrow(frame, (180, 210), -1, ov.ACCENT, pulse=0.5 + 0.5 * np.sin(t * 6))
        ov.ring(frame, (W - 44, 44), 24, 0.6, ov.ACCENT)
        faces = [("Camille Martin", "warn", "known")]
    elif kind == "granted":
        ov.face_label(frame, BOX, "Camille Martin · accès autorisé", ov.OK, 0.86)
        faces = [("Camille Martin · accès autorisé", "ok", "granted")]
    elif kind == "denied":
        ov.face_label(frame, BOX, "Inconnu · accès refusé", ov.BAD)
        faces = [("Inconnu · accès refusé", "bad", "denied")]
    ov.hud(frame, 24.3, "simulation · défi")
    ov.door_banner(frame, door_left > 0, door_left, 5, zone)
    return frame, [{"label": l, "tone": tone, "state": st} for l, tone, st in faces]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=Path("data/demo.db"))
    ap.add_argument("--live-dir", type=Path, default=Path("data/demo_live"))
    ap.add_argument("--zone", default="Laboratoire")
    ap.add_argument("--seconds", type=float, default=600)
    args = ap.parse_args()

    store, pub = Store(args.db), LivePublisher(args.live_dir, max_fps=10)
    story = [("waiting", 2.0), ("analysing", 1.5), ("challenge", 3.5), ("granted", 3.0), ("waiting", 2.0),
             ("analysing", 1.5), ("denied", 3.0)]
    end = time.monotonic() + args.seconds
    enrolling, started, door_until, cycle_t = None, 0.0, 0.0, 0.0
    try:
        while time.monotonic() < end:
            now = time.monotonic()
            store.expire_enrollments()
            if enrolling is None and (row := store.claim_enrollment()):
                enrolling, started = row, now
            if enrolling is not None:
                row = store.get_enrollment(enrolling["id"])
                if row["status"] != "running":
                    enrolling = None  # cancelled from the web app
                else:
                    progress = min(enrolling["total"], int((now - started) / 0.45))
                    store.update_enrollment(enrolling["id"], progress=progress)
                    frame = background()
                    ov.corner_brackets(frame, BOX, ov.ACCENT)
                    ov.enrollment_panel(frame, enrolling["name"], progress, enrolling["total"], "Parfait, bougez légèrement la tête")
                    ov.hud(frame, 24.3, "inscription (simulation)")
                    pub.publish(frame, {"running": True, "fps": 24.3, "zone": args.zone, "mode": "inscription", "faces": [],
                                        "door": {"open": False, "seconds_left": 0},
                                        "enrollment": {"id": enrolling["id"], "name": enrolling["name"], "progress": progress,
                                                       "total": enrolling["total"], "hint": "Parfait, bougez légèrement la tête"}}, now)
                    if progress >= enrolling["total"]:
                        rng = np.random.default_rng(int(now))
                        store.add_person(enrolling["name"], rng.normal(size=(enrolling["total"], 128)), "sface",
                                         enrolling["consent_text"])
                        store.add_audit(enrolling["admin"], "person_enrolled", enrolling["name"])
                        store.update_enrollment(enrolling["id"], status="done", progress=enrolling["total"],
                                                message="Inscription terminée (simulation)")
                        enrolling = None
                    time.sleep(0.1)
                    continue
            cycle_t = (now - (end - args.seconds)) % sum(d for _, d in story)
            kind, acc = "waiting", 0.0
            for name, duration in story:
                if cycle_t < acc + duration:
                    kind = name
                    break
                acc += duration
            if kind == "granted" and door_until < now:
                door_until = now + 5
            door_left = max(0.0, door_until - now)
            frame, faces = scene(kind, now, door_left, args.zone)
            pub.publish(frame, {"running": True, "fps": 24.3, "zone": args.zone, "mode": "simulation · défi", "faces": faces,
                                "door": {"open": door_left > 0, "seconds_left": round(door_left, 1)}, "enrollment": None}, now)
            time.sleep(0.1)
    finally:
        pub.close()


if __name__ == "__main__":
    main()
