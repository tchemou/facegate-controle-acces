"""Which design choices actually reduce errors? Re-scores cached features under several configurations.

Usage: python scripts/ablation.py data/lfw --features results/hog_dlib/features_hog_dlib.npz [--seeds 8]
"""

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np

from facerec.config import Settings
from facerec.evaluation import THRESHOLDS, extract_all, list_dataset, score_protocol, summarize

# (label, n_enroll, quality gate, min_margin)
CONFIGS = [
    ("Référence naïve : 1 image d'inscription, sans filtre, sans marge", 1, False, 0.0),
    ("+ 3 images d'inscription", 3, False, 0.0),
    ("+ 8 images d'inscription", 8, False, 0.0),
    ("+ filtre qualité (8 images)", 8, True, 0.0),
    ("+ marge entre identités (8 images, filtre)", 8, True, 0.04),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", type=Path)
    ap.add_argument("--features", type=Path, required=True)
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    settings = Settings()
    probes = extract_all(None, list_dataset(args.data), settings, args.features)  # type: ignore[arg-type]

    lines = [
        f"# Ablation sur {args.data} ({args.seeds} tirages)",
        "",
        "| Configuration | EER | FNIR à FPIR ≤ 1 % | Images écartées |",
        "|---|---|---|---|",
    ]
    for label, n_enroll, quality, margin in CONFIGS:
        s = replace(settings, min_margin=margin)
        runs, dropped = [], []
        for seed in range(args.seeds):
            scores = score_protocol(probes, n_enroll, seed, quality)
            runs.append(summarize(scores, s, THRESHOLDS))
            dropped.append(1 - len(scores.genuine_dist) / max(scores.genuine_total, 1))
        eer = np.array([r["eer"] for r in runs]) * 100
        fnir = np.array([r["at_fpir_1pct"]["fnir"] for r in runs]) * 100
        lines.append(
            f"| {label} | {eer.mean():.1f} ± {eer.std():.1f} % | {fnir.mean():.1f} ± {fnir.std():.1f} % "
            f"| {np.mean(dropped) * 100:.0f} % |"
        )
    text = "\n".join(lines) + "\n"
    print(text.encode("ascii", "replace").decode())
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
