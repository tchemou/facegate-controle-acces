from dataclasses import dataclass
from pathlib import Path

import numpy as np

_K_NEAREST = 3


@dataclass(frozen=True)
class Match:
    name: str | None  # None when rejected as unknown
    best: str | None  # closest enrolled identity, even if rejected
    distance: float
    margin: float  # gap to the second closest identity


class Gallery:
    """Enrolled identities stored as 128-d embeddings only: no image is ever kept."""

    def __init__(self, embedder: str = "dlib") -> None:
        self.embedder = embedder  # embeddings from different models are not comparable
        self._emb: dict[str, np.ndarray] = {}

    @property
    def names(self) -> list[str]:
        return sorted(self._emb)

    def __len__(self) -> int:
        return len(self._emb)

    def sample_count(self, name: str) -> int:
        return len(self._emb[name])

    def embeddings_of(self, name: str) -> np.ndarray:
        return self._emb[name].copy()

    def enroll(self, name: str, embeddings: np.ndarray) -> None:
        arr = np.atleast_2d(np.asarray(embeddings, dtype=np.float32))
        if arr.shape[1] != 128:
            raise ValueError(f"expected 128-d embeddings, got {arr.shape[1]}")
        if name in self._emb:
            arr = np.vstack([self._emb[name], arr])
        self._emb[name] = arr

    def remove(self, name: str) -> bool:
        return self._emb.pop(name, None) is not None

    def _person_distance(self, name: str, emb: np.ndarray) -> float:
        d = np.sort(np.linalg.norm(self._emb[name] - emb, axis=1))
        return float(d[: min(_K_NEAREST, len(d))].mean())

    def rank(self, emb: np.ndarray) -> tuple[str, float, float]:
        """Closest identity, its distance, and the margin to the runner-up (inf if alone)."""
        if not self._emb:
            raise ValueError("gallery is empty")
        scored = sorted((self._person_distance(n, emb), n) for n in self._emb)
        best_d, best_n = scored[0]
        margin = scored[1][0] - best_d if len(scored) > 1 else float("inf")
        return best_n, best_d, margin

    def identify(self, emb: np.ndarray, threshold: float, min_margin: float) -> Match:
        best, dist, margin = self.rank(emb)
        accepted = dist <= threshold and margin >= min_margin
        return Match(best if accepted else None, best, dist, margin)

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        labels = np.array([n for n in self.names for _ in range(len(self._emb[n]))])
        stacked = (
            np.vstack([self._emb[n] for n in self.names])
            if self._emb
            else np.zeros((0, 128), np.float32)
        )
        np.savez_compressed(path, labels=labels, embeddings=stacked, embedder=np.array(self.embedder))

    @classmethod
    def load(cls, path: Path) -> "Gallery":
        path = Path(path)
        if not path.exists():
            return cls()
        with np.load(path, allow_pickle=False) as data:
            labels, emb = data["labels"], data["embeddings"]
            gallery = cls(str(data["embedder"]) if "embedder" in data.files else "dlib")
        for name in dict.fromkeys(labels.tolist()):
            gallery.enroll(name, emb[labels == name])
        return gallery
