"""Render the camera overlay on synthetic frames to look at it without a camera.

    python scripts/overlay_preview.py out.png
"""

import sys

import cv2
import numpy as np

from facerec import overlay as ov


def background(w=480, h=360) -> np.ndarray:
    y = np.linspace(70, 40, h)[:, None, None] * np.ones((1, w, 3))
    frame = y.astype(np.uint8)
    frame[:, :, 0] = np.clip(frame[:, :, 0] + 12, 0, 255)
    cv2.ellipse(frame, (w // 2, h // 2 - 10), (62, 82), 0, 0, 360, (150, 150, 165), -1, cv2.LINE_AA)  # a stand-in face
    return frame


def scenes() -> list[tuple[str, np.ndarray]]:
    box = (178, 78, 302, 262)
    out = []

    f = background()
    ov.face_label(f, box, "Camille Martin · accès autorisé", ov.OK, 0.82)
    ov.hud(f, 24.6, "yunet + sface · défi")
    ov.door_banner(f, True, 3.4, 5, "Laboratoire")
    out.append(("autorise", f))

    f = background()
    ov.face_label(f, box, "Inconnu · accès refusé", ov.BAD)
    ov.hud(f, 24.9, "yunet + sface · défi")
    ov.door_banner(f, False, 0, 5, "Laboratoire")
    out.append(("refuse", f))

    f = background()
    ov.face_label(f, box, "Camille Martin", ov.WARN)
    ov.arrow(f, (95, 170), -1, ov.ACCENT, pulse=0.6)
    ov.instruction(f, "Tournez la tête vers la gauche de l'écran (2/3)", ov.ACCENT)
    ov.steps(f, 218, 62, 1, 3, ov.ACCENT)
    ov.ring(f, (430, 50), 26, 0.62, ov.ACCENT)
    ov.put_text(f, "5 s", 419, 39, 17, ov.WHITE, bold=True)
    ov.hud(f, 24.2, "yunet + sface · défi")
    ov.door_banner(f, False, 0, 5, "Laboratoire")
    out.append(("defi", f))

    f = background()
    ov.face_label(f, box, "Vérification de présence non réussie", ov.BAD)
    ov.hud(f, 23.8, "yunet + sface · défi")
    out.append(("echec", f))

    f = background()
    ov.corner_brackets(f, box, ov.ACCENT)
    ov.enrollment_panel(f, "Inès Lefèvre", 13, 20, "Parfait, bougez légèrement la tête")
    ov.hud(f, 24.0, "inscription")
    out.append(("inscription", f))

    f = background()
    ov.hud(f, 25.0, "yunet + sface")
    ov.door_banner(f, False, 0, 5, "Salle serveurs")
    out.append(("attente", f))
    return out


if __name__ == "__main__":
    tiles = [cv2.resize(f, (480, 360)) for _, f in scenes()]
    rows = [np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)]
    cv2.imwrite(sys.argv[1] if len(sys.argv) > 1 else "overlay_preview.png", np.vstack(rows))
