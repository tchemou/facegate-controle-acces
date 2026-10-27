"""End-to-end check on real faces (needs `python scripts/fetch_lfw.py`; skipped otherwise)."""

from pathlib import Path

import numpy as np
import pytest

from facerec.config import Settings
from facerec.engine import FaceEngine
from facerec.evaluation import extract, list_dataset
from facerec.gallery import Gallery
from facerec.smoothing import KNOWN, UNKNOWN, VoteSmoother

DATA = Path(__file__).parent.parent / "data" / "lfw"
pytestmark = pytest.mark.skipif(not DATA.exists(), reason="LFW subset not downloaded")


@pytest.fixture(scope="module")
def probes():
    settings = Settings()
    engine = FaceEngine(settings)
    dataset = list_dataset(DATA)
    names = [n for n, p in dataset.items() if len(p) >= 12][:3]
    assert len(names) == 3
    return {n: [extract(engine, n, p, settings) for p in dataset[n][:12]] for n in names}


def emb(items):
    return np.stack([p.embedding for p in items if p.embedding is not None])


def test_enrolled_person_is_recognised_and_stranger_rejected(probes):
    alice, bob, carol = list(probes)
    gallery = Gallery()
    gallery.enroll(alice, emb(probes[alice][:8]))
    gallery.enroll(bob, emb(probes[bob][:8]))

    s = Settings()
    smoother = VoteSmoother(s)
    for p in probes[alice][8:]:
        smoother.update(gallery.identify(p.embedding, s.threshold, s.margin).name)
    assert smoother.decision() == (KNOWN, alice)

    stranger = VoteSmoother(s)
    for p in probes[carol]:  # a stranger needs the whole window now: UNKNOWN is declared once the evidence is in
        stranger.update(gallery.identify(p.embedding, s.threshold, s.margin).name)
    assert stranger.decision()[0] == UNKNOWN
