"""Enrollment run by the recognition service when the web app queues a request.

Pure logic (no camera, no database): the live loop feeds it one frame at a time.
It collects well-spaced, good-quality embeddings from exactly one face, refuses a face that is
already enrolled under another name, and gives up after a timeout.
"""

from dataclasses import dataclass, field

import numpy as np

from .gallery import Gallery

HINTS = {
    "no_face": "Placez votre visage devant la caméra",
    "several_faces": "Une seule personne devant la caméra, s'il vous plaît",
    "too_small": "Approchez-vous",
    "blurry": "Image floue : restez immobile",
    "too_dark": "Pas assez de lumière",
    "overexposed": "Trop de lumière",
    "not_frontal": "Regardez la caméra",
    "cut_off": "Votre visage est coupé",
    "out_of_frame": "Votre visage est hors du cadre",
}


@dataclass
class EnrollmentSession:
    name: str
    total: int = 20
    min_interval_s: float = 0.4  # spacing between samples, so that the head moves a little
    timeout_s: float = 90.0
    embeddings: list = field(default_factory=list)
    started: float | None = None
    last_taken: float = -1e9
    hint: str = HINTS["no_face"]
    failed: str | None = None

    @property
    def progress(self) -> int:
        return len(self.embeddings)

    @property
    def done(self) -> bool:
        return self.progress >= self.total

    @property
    def finished(self) -> bool:
        return self.done or self.failed is not None

    def observe(self, now: float, faces: int, reasons: tuple[str, ...] = ()) -> bool:
        """Report what the frame shows; True when a sample may be taken from it."""
        if self.started is None:
            self.started = now
        if now - self.started > self.timeout_s and not self.done:
            self.failed = "Délai dépassé : l'inscription n'a pas pu être terminée"
            return False
        if faces == 0:
            self.hint = HINTS["no_face"]
        elif faces > 1:
            self.hint = HINTS["several_faces"]
        elif reasons:
            self.hint = HINTS.get(reasons[0], reasons[0])
        elif now - self.last_taken < self.min_interval_s:
            self.hint = "Bougez légèrement la tête"
        else:
            self.hint = "Parfait, bougez légèrement la tête"
            return not self.done
        return False

    def add(self, embedding: np.ndarray, now: float) -> None:
        self.embeddings.append(np.asarray(embedding, dtype=np.float32))
        self.last_taken = now

    def check_duplicate(self, gallery: Gallery, threshold: float, min_margin: float) -> str | None:
        """Name of an enrolled person this face matches (most samples), else None."""
        if not len(gallery) or not self.embeddings:
            return None
        names = [gallery.identify(e, threshold, min_margin).name for e in self.embeddings]
        known = [n for n in names if n is not None]
        if not known:
            return None
        best = max(set(known), key=known.count)
        return best if known.count(best) > len(names) / 2 else None

    def fail_if_duplicate(self, gallery: Gallery, threshold: float, min_margin: float) -> bool:
        other = self.check_duplicate(gallery, threshold, min_margin)
        if other is not None:
            self.failed = f"Ce visage est déjà inscrit sous le nom « {other} »"
        return other is not None
