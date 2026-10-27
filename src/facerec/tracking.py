import math
from dataclasses import dataclass, field
from itertools import count

from .antispoof import ChallengeLiveness, ScreenDetector
from .config import Settings
from .liveness import BlinkDetector
from .smoothing import VoteSmoother


@dataclass
class Track:
    id: int
    center: tuple[float, float]
    smoother: VoteSmoother
    blink: BlinkDetector
    challenge: ChallengeLiveness
    screen: ScreenDetector
    missed: int = 0
    last_state: str = ""
    seen: int = 0  # frames in which this face was detected
    extra: dict = field(default_factory=dict)


class Tracker:
    """Greedy nearest-centroid tracker, enough to keep one vote history per face."""

    def __init__(self, settings: Settings = Settings()):
        self._s = settings
        self._tracks: list[Track] = []
        self._ids = count(1)
        self.dropped: list[Track] = []  # faces that left during the last `update` (for "no decision" accounting)

    @property
    def alive(self) -> list[Track]:
        """The faces currently tracked (their presentation is not over yet)."""
        return list(self._tracks)

    def update(self, centers: list[tuple[float, float]]) -> list[Track]:
        pairs = sorted(
            (math.dist(t.center, c), ti, ci)
            for ti, t in enumerate(self._tracks)
            for ci, c in enumerate(centers)
        )
        assigned: dict[int, Track] = {}
        used_tracks: set[int] = set()
        for dist, ti, ci in pairs:
            if dist > self._s.track_max_dist or ti in used_tracks or ci in assigned:
                continue
            track = self._tracks[ti]
            track.center, track.missed = centers[ci], 0
            track.seen += 1
            assigned[ci] = track
            used_tracks.add(ti)

        for ti, track in enumerate(self._tracks):
            if ti not in used_tracks:
                track.missed += 1

        for ci, center in enumerate(centers):
            if ci not in assigned:
                track = Track(
                    next(self._ids), center, VoteSmoother(self._s), BlinkDetector(self._s),
                    ChallengeLiveness(self._s), ScreenDetector(self._s), seen=1,
                )
                self._tracks.append(track)
                assigned[ci] = track

        self.dropped = [t for t in self._tracks if t.missed > self._s.track_max_missed]
        self._tracks = [t for t in self._tracks if t.missed <= self._s.track_max_missed]
        return [assigned[ci] for ci in range(len(centers))]
