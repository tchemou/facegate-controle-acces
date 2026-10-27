"""Open-set evaluation (NIST-style FNIR / FPIR) with and without the quality gate.

Protocol: identities are split in two halves. "Known" identities are enrolled with
`n_enroll` images and their remaining images are genuine probes. "Unknown" identities
are never enrolled and all their images are impostor probes. A probe is correct only
if it is accepted AND attributed to the right person.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .config import Settings  # noqa: E402
from .engine import FaceEngine, rect_box, shape_points  # noqa: E402
from .gallery import Gallery  # noqa: E402
from .quality import assess, judge  # noqa: E402

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
THRESHOLDS = np.round(np.arange(0.20, 1.60, 0.01), 2)  # covers dlib (~0.5) and SFace (~1.1)


@dataclass
class Probe:
    identity: str
    path: str
    embedding: np.ndarray | None  # None when no face was detected
    quality_ok: bool
    reasons: tuple[str, ...]
    blur: float = 0.0
    brightness: float = 0.0
    size: int = 0
    yaw: float = 0.0


def list_dataset(root: Path) -> dict[str, list[Path]]:
    """data/<identity>/<image> -> {identity: [paths]}"""
    dataset: dict[str, list[Path]] = {}
    for person in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        images = sorted(f for f in person.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
        if images:
            dataset[person.name] = images
    return dataset


def extract(engine: FaceEngine, identity: str, path: Path, settings: Settings) -> Probe:
    img = cv2.imread(str(path))
    if img is None:
        return Probe(identity, str(path), None, False, ("unreadable",))
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    rects = engine.detect(rgb)
    if not rects:
        return Probe(identity, str(path), None, False, ("no_face",))
    rect = max(rects, key=lambda r: r.width() * r.height())
    shape = engine.shape(rgb, rect)
    report = assess(gray, shape_points(shape), rect_box(rect), settings)
    return Probe(
        identity,
        str(path),
        engine.embed(rgb, shape),
        report.ok,
        report.reasons,
        report.blur,
        report.brightness,
        report.size,
        report.yaw,
    )


def extract_all(
    engine: FaceEngine, dataset: dict[str, list[Path]], settings: Settings, cache: Path | None
) -> dict[str, list[Probe]]:
    cached: dict[str, Probe] = {}
    if cache is not None and cache.exists():
        with np.load(cache, allow_pickle=False) as d:
            for i, p in enumerate(d["paths"].tolist()):
                emb = d["emb"][i]
                stored = tuple(r for r in str(d["reasons"][i]).split("|") if r)
                blur, bright = float(d["blur"][i]), float(d["brightness"][i])
                size, yaw = int(d["size"][i]), float(d["yaw"][i])
                detected = not np.isnan(emb).any()
                # Re-judge from raw measurements so threshold changes need no re-extraction
                reasons = (
                    judge(size, blur, bright, yaw, "cut_off" in stored, settings)
                    if detected and "out_of_frame" not in stored
                    else stored
                )
                cached[p] = Probe(
                    str(d["ids"][i]), p, emb if detected else None, detected and not reasons,
                    reasons, blur, bright, size, yaw,
                )
    out: dict[str, list[Probe]] = {}
    total = sum(len(v) for v in dataset.values())
    done = 0
    for identity, paths in dataset.items():
        out[identity] = []
        for path in paths:
            probe = cached.get(str(path)) or extract(engine, identity, path, settings)
            out[identity].append(probe)
            done += 1
            if done % 100 == 0:
                print(f"  features {done}/{total}", flush=True)
    if cache is not None:
        _save_cache(cache, out)
    return out


def _save_cache(cache: Path, probes: dict[str, list[Probe]]) -> None:
    flat = [p for v in probes.values() for p in v]
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache,
        paths=np.array([p.path for p in flat]),
        ids=np.array([p.identity for p in flat]),
        emb=np.array(
            [p.embedding if p.embedding is not None else np.full(128, np.nan) for p in flat],
            dtype=np.float32,
        ),
        ok=np.array([p.quality_ok for p in flat]),
        reasons=np.array(["|".join(p.reasons) for p in flat]),
        blur=np.array([p.blur for p in flat]),
        brightness=np.array([p.brightness for p in flat]),
        size=np.array([p.size for p in flat]),
        yaw=np.array([p.yaw for p in flat]),
    )


@dataclass
class Scores:
    genuine_best: list[str]
    genuine_true: list[str]
    genuine_dist: np.ndarray
    genuine_margin: np.ndarray
    impostor_dist: np.ndarray
    impostor_margin: np.ndarray
    n_known: int
    n_unknown: int
    genuine_total: int
    impostor_total: int


def score_protocol(
    probes: dict[str, list[Probe]], n_enroll: int, seed: int, use_quality: bool
) -> Scores:
    rng = np.random.default_rng(seed)
    ids = [i for i, v in probes.items() if len(v) >= n_enroll + 2]
    if len(ids) < 4:
        raise ValueError("need at least 4 identities with enough images")
    order = rng.permutation(len(ids))
    half = len(ids) // 2
    known = [ids[i] for i in order[:half]]
    unknown = [ids[i] for i in order[half:]]

    def usable(p: Probe) -> bool:
        return p.embedding is not None and (p.quality_ok or not use_quality)

    gallery = Gallery()
    genuine: list[tuple[str, Probe]] = []
    for ident in known:
        items = [probes[ident][i] for i in rng.permutation(len(probes[ident]))]
        enroll = [p for p in items if usable(p)][:n_enroll]
        if len(enroll) < n_enroll:
            continue
        gallery.enroll(ident, np.stack([p.embedding for p in enroll]))
        taken = {id(p) for p in enroll}
        genuine += [(ident, p) for p in items if id(p) not in taken]
    impostors = [p for ident in unknown for p in probes[ident]]

    g_best, g_true, g_d, g_m = [], [], [], []
    for ident, p in genuine:
        if usable(p):
            best, d, m = gallery.rank(p.embedding)
            g_best.append(best), g_true.append(ident), g_d.append(d), g_m.append(m)
    i_d, i_m = [], []
    for p in impostors:
        if usable(p):
            _, d, m = gallery.rank(p.embedding)
            i_d.append(d), i_m.append(m)

    return Scores(
        g_best, g_true, np.array(g_d), np.array(g_m), np.array(i_d), np.array(i_m),
        len(gallery), len(unknown), len(genuine), len(impostors),
    )


def rates(s: Scores, threshold: float, min_margin: float) -> tuple[float, float]:
    """FNIR (genuine not correctly identified) and FPIR (impostor accepted), over acquired probes."""
    accepted = (s.genuine_dist <= threshold) & (s.genuine_margin >= min_margin)
    correct = accepted & (np.array(s.genuine_best) == np.array(s.genuine_true))
    fnir = 1.0 - (correct.sum() / max(len(s.genuine_dist), 1))
    fpir = ((s.impostor_dist <= threshold) & (s.impostor_margin >= min_margin)).sum() / max(
        len(s.impostor_dist), 1
    )
    return float(fnir), float(fpir)


def sweep(s: Scores, min_margin: float, thresholds: np.ndarray) -> np.ndarray:
    return np.array([rates(s, t, min_margin) for t in thresholds])


def summarize(s: Scores, settings: Settings, thresholds: np.ndarray) -> dict:
    curve = sweep(s, settings.margin, thresholds)
    fnir, fpir = curve[:, 0], curve[:, 1]
    eer_i = int(np.argmin(np.abs(fnir - fpir)))
    low_fpir = np.where(fpir <= 0.01)[0]
    op_i = int(low_fpir[-1]) if len(low_fpir) else 0
    d_fnir, d_fpir = rates(s, settings.threshold, settings.margin)
    return {
        "identities_known": s.n_known,
        "identities_unknown": s.n_unknown,
        "genuine_probes": s.genuine_total,
        "impostor_probes": s.impostor_total,
        "not_acquired_genuine": s.genuine_total - len(s.genuine_dist),
        "not_acquired_impostor": s.impostor_total - len(s.impostor_dist),
        "eer": float((fnir[eer_i] + fpir[eer_i]) / 2),
        "eer_threshold": float(thresholds[eer_i]),
        "at_fpir_1pct": {
            "threshold": float(thresholds[op_i]),
            "fnir": float(fnir[op_i]),
            "fpir": float(fpir[op_i]),
        },
        "at_default_threshold": {
            "threshold": settings.threshold,
            "fnir": d_fnir,
            "fpir": d_fpir,
        },
        "_curve": curve,
    }


def quality_stats(probes: dict[str, list[Probe]]) -> dict:
    flat = [p for v in probes.values() for p in v]
    detected = [p for p in flat if p.embedding is not None]
    reasons: dict[str, int] = {}
    for p in flat:
        for r in p.reasons:
            reasons[r] = reasons.get(r, 0) + 1
    blur = np.array([p.blur for p in detected]) if detected else np.zeros(1)
    return {
        "images": len(flat),
        "face_detected": len(detected),
        "quality_ok": sum(p.quality_ok for p in flat),
        "rejection_reasons": reasons,
        "blur_quantiles_10_50_90": [float(q) for q in np.quantile(blur, [0.1, 0.5, 0.9])],
    }


def evaluate(
    root: Path, out_dir: Path, settings: Settings, n_enroll: int = 3, seeds: int = 5,
    cache: bool = True,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset = list_dataset(root)
    print(f"{len(dataset)} identities, {sum(map(len, dataset.values()))} images")
    engine = FaceEngine(settings)
    probes = extract_all(engine, dataset, settings, out_dir / f"features_{settings.detector}_{settings.embedder}.npz" if cache else None)

    thresholds = THRESHOLDS
    result: dict = {"settings": asdict(settings), "n_enroll": n_enroll, "seeds": seeds}
    result["dataset"] = quality_stats(probes)
    curves = {}
    for label, use_quality in (("without_quality_gate", False), ("with_quality_gate", True)):
        runs = [summarize(score_protocol(probes, n_enroll, s, use_quality), settings, thresholds)
                for s in range(seeds)]
        keys = ["eer", "eer_threshold"]
        agg = {k: [float(np.mean([r[k] for r in runs])), float(np.std([r[k] for r in runs]))]
               for k in keys}
        for op in ("at_fpir_1pct", "at_default_threshold"):
            agg[op] = {k: float(np.mean([r[op][k] for r in runs])) for k in ("threshold", "fnir", "fpir")}
        agg["not_acquired_genuine"] = float(np.mean([r["not_acquired_genuine"] for r in runs]))
        agg["genuine_probes"] = float(np.mean([r["genuine_probes"] for r in runs]))
        agg["impostor_probes"] = float(np.mean([r["impostor_probes"] for r in runs]))
        agg["identities_known"] = runs[0]["identities_known"]
        agg["identities_unknown"] = runs[0]["identities_unknown"]
        result[label] = agg
        curves[label] = np.mean([r["_curve"] for r in runs], axis=0)

    (out_dir / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _plot(curves, thresholds, out_dir / "det_curve.png")
    (out_dir / "report.md").write_text(_report(result), encoding="utf-8")
    return result


def _plot(curves: dict[str, np.ndarray], thresholds: np.ndarray, path: Path) -> None:
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    names = {"without_quality_gate": "sans filtre qualité", "with_quality_gate": "avec filtre qualité"}
    for label, c in curves.items():
        ax[0].plot(c[:, 1] * 100, c[:, 0] * 100, label=names[label])
        ax[1].plot(thresholds, c[:, 0] * 100, label=f"FNIR {names[label]}")
        ax[1].plot(thresholds, c[:, 1] * 100, "--", label=f"FPIR {names[label]}")
    ax[0].set(xlabel="FPIR (% d'inconnus acceptés)", ylabel="FNIR (% de personnes connues ratées)",
              title="Courbe DET (identification en ensemble ouvert)")
    ax[0].set_xlim(0, 30), ax[0].set_ylim(0, 100), ax[0].grid(alpha=0.3), ax[0].legend()
    ax[1].set(xlabel="Seuil de distance", ylabel="%", title="Erreurs selon le seuil")
    ax[1].grid(alpha=0.3), ax[1].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)


def _report(r: dict) -> str:
    def row(label: str, a: dict) -> str:
        return (
            f"| {label} | {a['eer'][0]*100:.1f} ± {a['eer'][1]*100:.1f} % "
            f"| {a['at_fpir_1pct']['fnir']*100:.1f} % (seuil {a['at_fpir_1pct']['threshold']:.2f}) "
            f"| {a['at_default_threshold']['fnir']*100:.1f} % / {a['at_default_threshold']['fpir']*100:.1f} % "
            f"| {a['not_acquired_genuine']:.0f} / {a['genuine_probes']:.0f} |"
        )

    d = r["dataset"]
    w, q = r["without_quality_gate"], r["with_quality_gate"]
    return "\n".join([
        "# Résultats d'évaluation",
        "",
        f"- Images : {d['images']}, visage détecté : {d['face_detected']}, qualité suffisante : {d['quality_ok']}",
        f"- Identités connues / inconnues par tirage : {w['identities_known']} / {w['identities_unknown']}",
        f"- Sondes légitimes ≈ {w['genuine_probes']:.0f}, sondes imposteurs ≈ {w['impostor_probes']:.0f}",
        f"- Inscription : {r['n_enroll']} images par personne, moyenne sur {r['seeds']} tirages aléatoires",
        f"- Motifs de rejet qualité : {d['rejection_reasons']}",
        "",
        "FNIR = personnes connues non identifiées correctement. FPIR = inconnus acceptés à tort.",
        "Taux calculés sur les images exploitables ; les images écartées sont comptées à part (« non acquises »).",
        "En direct, une image écartée n'est pas une erreur : le système attend une meilleure image de la vidéo.",
        "",
        "| Configuration | EER | FNIR à FPIR ≤ 1 % | FNIR / FPIR au seuil par défaut | Sondes non acquises |",
        "|---|---|---|---|---|",
        row("Sans filtre qualité", w),
        row("Avec filtre qualité", q),
        "",
        "![DET](det_curve.png)",
        "",
    ])
