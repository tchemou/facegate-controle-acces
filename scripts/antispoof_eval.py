"""Calibrate and measure the passive screen detector.

Real faces: LFW crops. Attacks: *simulated* screen replays (pixel grid + camera resampling),
because no real replay footage is available. Results from this script are therefore an upper
bound on what a real attack would score: confirm on real recordings before trusting them.

    python scripts/antispoof_eval.py data/lfw --out results/antispoof.md
"""

import argparse
import random
from pathlib import Path

import cv2
import numpy as np

from facerec.antispoof import screen_score
from facerec.config import Settings
from facerec.engine import FaceEngine, rect_box


def face_crop_gray(engine: FaceEngine, bgr: np.ndarray) -> np.ndarray | None:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rects = engine.detect(rgb)
    if len(rects) != 1:
        return None
    left, top, right, bottom = rect_box(rects[0])
    h, w = bgr.shape[:2]
    crop = bgr[max(top, 0):min(bottom, h), max(left, 0):min(right, w)]
    return cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if min(crop.shape[:2]) >= 40 else None


def simulate_screen_replay(bgr: np.ndarray, rng: random.Random, depth: float = 0.80) -> np.ndarray:
    """Face shown on a screen then filmed: subpixel grid, resampling at an odd ratio, slight tilt."""
    pitch = rng.choice([3, 4, 5])
    scale = rng.uniform(1.6, 2.4)
    big = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    h, w = big.shape[:2]
    grid = np.ones((h, w, 1), np.float32)
    grid[::pitch, :] *= depth  # depth: 0.80 = visible grid, 0.97 = fine high-resolution screen
    grid[:, ::pitch] *= depth
    shown = np.clip(big.astype(np.float32) * grid, 0, 255)
    angle = rng.uniform(-3, 3)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, rng.uniform(0.45, 0.6))
    filmed = cv2.warpAffine(shown, m, (w, h), flags=cv2.INTER_LINEAR)
    filmed += np.random.default_rng(rng.randrange(1 << 30)).normal(0, 2.0, filmed.shape)
    return np.clip(filmed, 0, 255).astype(np.uint8)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--out", default="results/antispoof.md")
    ap.add_argument("--per-person", type=int, default=4)
    args = ap.parse_args()

    engine, rng = FaceEngine(Settings()), random.Random(0)
    levels = {"visible (0,80)": 0.80, "discret (0,92)": 0.92, "ecran HD (0,97)": 0.97}
    real, fakes = [], {name: [] for name in levels}
    for person in sorted(Path(args.data).iterdir()):
        for path in sorted(person.glob("*.jpg"))[: args.per_person]:
            bgr = cv2.imread(str(path))
            attacks = [(real, bgr)] + [(fakes[n], simulate_screen_replay(bgr, rng, d)) for n, d in levels.items()]
            for label, image in attacks:
                crop = face_crop_gray(engine, image)
                if crop is not None:
                    label.append(screen_score(crop))
    real, fakes = np.array(real), {n: np.array(v) for n, v in fakes.items()}

    threshold = float(np.percentile(real, 99))
    lines = [
        "# Anti-spoofing: detection passive d'ecran (evaluation simulee)",
        "",
        f"Visages reels (LFW): {len(real)} images. Attaques ecran **simulees** (3 niveaux de grille).",
        "",
        "| | min | mediane | p95 | p99 | max |",
        "|---|---|---|---|---|---|",
    ]
    for name, v in [("reels", real), *[(f"ecran {n}", v) for n, v in fakes.items()]]:
        lines.append(f"| {name} | {v.min():.1f} | {np.median(v):.1f} | {np.percentile(v, 95):.1f} | "
                     f"{np.percentile(v, 99):.1f} | {v.max():.1f} |")
    lines += [
        "",
        f"Seuil retenu (99e percentile des reels): **{threshold:.1f}** (faux rejet par image ~1 %).",
        f"- Faux rejets (reels > seuil): {np.mean(real > threshold) * 100:.1f} %",
        *[f"- Ecran simule {n}: {np.mean(v > threshold) * 100:.1f} % detecte" for n, v in fakes.items()],
        "",
        "Limites : attaque simulee (pas de vraie captation), visages LFW 250 px et non webcam ;",
        "a re-mesurer avec de vrais enregistrements avant toute conclusion de securite.",
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
