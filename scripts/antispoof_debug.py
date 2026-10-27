"""Live diagnostic: shows the measured yaw, screen score and challenge state frame by frame.

    python scripts/antispoof_debug.py            # q to quit; prints a summary at the end
"""

import time
from pathlib import Path

import cv2
import numpy as np

from facerec.antispoof import ChallengeLiveness, screen_score
from facerec.config import Settings
from facerec.engine import FaceEngine, rect_box, shape_points
from facerec.live import open_camera
from facerec.liveness import mean_ear
from facerec.quality import yaw_ratio


def main() -> None:
    s = Settings()
    engine, cap = FaceEngine(s), open_camera(0)
    challenge, scores, yaws, ears = ChallengeLiveness(s), [], [], []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            rgb, gray = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            rects = engine.detect(rgb)
            lines = ["aucun visage"]
            if len(rects) == 1:
                points = shape_points(engine.shape(rgb, rects[0]))
                left, top, right, bottom = rect_box(rects[0])
                face = gray[max(top, 0):bottom, max(left, 0):right]
                sc, yaw, ear = screen_score(face), yaw_ratio(points), mean_ear(points)
                scores.append(sc), yaws.append(yaw), ears.append(ear)
                challenge.update(points, time.monotonic())
                lines = [
                    f"defi: {challenge.prompt()}  [{challenge.status}]",
                    f"yaw {yaw:+.2f} (cible +/-{s.challenge_turn_yaw})   EAR {ear:.2f} (clignement < {s.blink_ear})",
                    f"score ecran {sc:5.1f} (seuil {s.screen_threshold})",
                ]
            for i, text in enumerate(lines):
                cv2.putText(frame, text, (10, 26 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("diagnostic anti-usurpation (q = quitter)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    if scores:
        q = lambda v: f"min {min(v):.2f} / mediane {np.median(v):.2f} / p95 {np.percentile(v, 95):.2f} / max {max(v):.2f}"
        summary = [
            f"{len(scores)} images avec visage",
            f"score ecran : {q(scores)}",
            f"yaw         : {q(yaws)}   (negatif = visage vers la GAUCHE de l'image)",
            f"EAR         : {q(ears)}",
            f"etat du defi : {challenge.status} | sequence : {challenge.sequence}",
        ]
        print("\n".join(summary))
        out = Path("logs/antispoof_debug.txt")  # read back without copy-paste
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
