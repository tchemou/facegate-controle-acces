"""Latency per pipeline stage on a 640x480 frame, for each detector/embedder combination.

Usage: python scripts/benchmark_speed.py [image] [--runs 40] [--out results/speed.md]
The test frame is a real face pasted on a 640x480 canvas at a typical webcam face size.
"""

import argparse
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from facerec.config import Settings
from facerec.engine import FaceEngine

COMBOS = [("hog", "dlib"), ("hog", "sface"), ("yunet", "sface"), ("yunet", "dlib")]


def frame_from(image: Path) -> np.ndarray:
    face = cv2.resize(cv2.imread(str(image)), (320, 320))
    canvas = np.full((480, 640, 3), 90, np.uint8)
    canvas[80:400, 160:480] = face
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)


def timed(fn, runs: int) -> float:
    fn()
    start = time.perf_counter()
    for _ in range(runs):
        fn()
    return (time.perf_counter() - start) / runs * 1000


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path, nargs="?")
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    image = args.image or next(Path("data/lfw").rglob("*.jpg"))
    rgb = frame_from(image)

    lines = [
        f"# Latence par étape (image 640x480, moyenne de {args.runs} passages, CPU)",
        "",
        "| Détecteur | Empreinte | Détection | 68 points | Empreinte | Total | Images/s |",
        "|---|---|---|---|---|---|---|",
    ]
    for detector, embedder in COMBOS:
        engine = FaceEngine(replace(Settings(), detector=detector, embedder=embedder))
        rects = engine.detect(rgb)
        if not rects:
            lines.append(f"| {detector} | {embedder} | aucun visage détecté | | | | |")
            continue
        rect = rects[0]
        shape = engine.shape(rgb, rect)
        t_det = timed(lambda: engine.detect(rgb), args.runs)
        t_shape = timed(lambda: engine.shape(rgb, rect), args.runs)
        t_emb = timed(lambda: engine.embed(rgb, shape), args.runs)
        total = t_det + t_shape + t_emb
        lines.append(
            f"| {detector} | {embedder} | {t_det:.1f} ms | {t_shape:.1f} ms | {t_emb:.1f} ms "
            f"| {total:.1f} ms | {1000 / total:.0f} |"
        )
    text = "\n".join(lines) + "\n"
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
