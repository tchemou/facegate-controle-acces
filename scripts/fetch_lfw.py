"""Export a subset of the public LFW dataset to data/lfw/<person>/<n>.jpg for reproducible evaluation.

Usage: python scripts/fetch_lfw.py [--min-faces 10] [--max-per-person 20] [--max-people 80] [--offset 0]
"""

import argparse
from pathlib import Path

import cv2
import numpy as np
from sklearn.datasets import fetch_lfw_people


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-faces", type=int, default=10)
    ap.add_argument("--max-per-person", type=int, default=20)
    ap.add_argument("--max-people", type=int, default=80)
    ap.add_argument("--offset", type=int, default=0, help="skip this many people (disjoint validation set)")
    ap.add_argument("--out", type=Path, default=Path("data/lfw"))
    args = ap.parse_args()

    lfw = fetch_lfw_people(
        min_faces_per_person=args.min_faces,
        color=True,
        resize=1.0,
        slice_=(slice(0, 250), slice(0, 250)),
    )
    scale = 255.0 if lfw.images.max() <= 1.0 else 1.0
    rng = np.random.default_rng(0)
    people = rng.permutation(len(lfw.target_names))[args.offset : args.offset + args.max_people]

    written = 0
    for pid in people:
        idx = np.flatnonzero(lfw.target == pid)[: args.max_per_person]
        folder = args.out / lfw.target_names[pid].replace(" ", "_")
        folder.mkdir(parents=True, exist_ok=True)
        for n, i in enumerate(idx):
            img = np.clip(lfw.images[i] * scale, 0, 255).astype(np.uint8)
            cv2.imwrite(str(folder / f"{n:03d}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            written += 1
    print(f"{written} images, {len(people)} people -> {args.out}")


if __name__ == "__main__":
    main()
