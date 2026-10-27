from collections import Counter, deque

from .config import Settings

PENDING = "pending"
KNOWN = "known"
UNKNOWN = "unknown"


class VoteSmoother:
    """Majority vote over the last frames: one bad frame can no longer flip the decision."""

    def __init__(self, settings: Settings = Settings()):
        self._s = settings
        self._votes: deque[str | None] = deque(maxlen=settings.vote_window)

    def update(self, name: str | None) -> None:
        self._votes.append(name)

    def decision(self) -> tuple[str, str | None]:
        """KNOWN as soon as `min_votes` frames agree on a person; UNKNOWN only once the evidence is in.

        Asymmetric on purpose. A legitimate person walking up gives a few blurred or turned frames first, and
        branding them "unknown" on the first four would refuse (and log) someone who is recognised a second later.
        The door only opens on KNOWN, so waiting longer to say UNKNOWN costs nothing in security."""
        n = len(self._votes)
        if n < self._s.min_votes:
            return PENDING, None
        label, count = Counter(self._votes).most_common(1)[0]
        if label is not None and count / n >= self._s.vote_ratio:
            return KNOWN, label
        if n < min(self._s.unknown_min_votes, self._s.vote_window):
            return PENDING, None  # no consensus yet: keep watching rather than refuse on a few bad frames
        recent = list(self._votes)[-self._s.min_votes:]
        recent_label, recent_count = Counter(recent).most_common(1)[0]
        if recent_label is not None and recent_count / len(recent) >= self._s.vote_ratio:
            return PENDING, None  # the latest frames already agree on someone: the older bad ones are about to leave
        return UNKNOWN, None  # the window is full and nobody is recognised, even lately: refuse rather than guess
