"""Where do errors come from? Genuine-pair failure rate by pose, blur, brightness and size.

Reads results/hog_dlib/features_hog_dlib.npz written by `facerec evaluate`. A genuine pair "fails" when two
images of the same person are farther apart than the matching threshold.

Usage: python scripts/analyze_failures.py [--threshold 0.5] [--features results/hog_dlib/features_hog_dlib.npz]
"""

import argparse
from itertools import combinations
from pathlib import Path

import numpy as np


def bucket_table(title: str, key: np.ndarray, fail: np.ndarray, edges: list[float]) -> list[str]:
    lines = [f"### {title}", "", "| intervalle | paires | échec de reconnaissance |", "|---|---|---|"]
    bounds = [-np.inf, *edges, np.inf]
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        m = (key >= lo) & (key < hi)
        if m.sum() >= 20:
            lines.append(f"| [{lo:g}, {hi:g}[ | {m.sum()} | {fail[m].mean() * 100:.1f} % |")
    return lines + [""]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", type=Path, default=Path("results/hog_dlib/features_hog_dlib.npz"))
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--out", type=Path, default=Path("results/hog_dlib/failure_analysis.md"))
    args = ap.parse_args()

    with np.load(args.features, allow_pickle=False) as d:
        ids, emb = d["ids"], d["emb"]
        feats = {k: d[k] for k in ("blur", "brightness", "size", "yaw")}
    valid = ~np.isnan(emb).any(axis=1)

    rows = []
    for ident in dict.fromkeys(ids.tolist()):
        idx = np.flatnonzero((ids == ident) & valid)
        for i, j in combinations(idx, 2):
            rows.append((
                np.linalg.norm(emb[i] - emb[j]),
                max(abs(feats["yaw"][i]), abs(feats["yaw"][j])),
                min(feats["blur"][i], feats["blur"][j]),
                min(feats["size"][i], feats["size"][j]),
                feats["brightness"][i], feats["brightness"][j],
            ))
    a = np.array(rows)
    fail = a[:, 0] > args.threshold
    out = [
        "# Analyse des échecs (paires de la même personne)",
        "",
        f"{len(a)} paires, seuil {args.threshold}, échec global : {fail.mean() * 100:.1f} %",
        "",
    ]
    out += bucket_table("Pose : |lacet| de la pire image", a[:, 1], fail, [0.1, 0.2, 0.3, 0.4, 0.5])
    out += bucket_table("Netteté : score de la pire image", a[:, 2], fail, [15, 25, 40, 80, 150])
    out += bucket_table("Taille du visage (px) de la plus petite image", a[:, 3], fail, [70, 90, 110, 130])
    out += bucket_table("Écart de luminosité entre les deux images", np.abs(a[:, 4] - a[:, 5]), fail, [10, 20, 40, 60])
    args.out.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
