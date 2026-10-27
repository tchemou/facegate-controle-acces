from dataclasses import dataclass

# Distances are not comparable across models, so each has its own operating point
# (set from the EER threshold on the calibration set: see results/).
DEFAULT_THRESHOLD = {"dlib": 0.5, "sface": 1.1}
DEFAULT_MARGIN = {"dlib": 0.04, "sface": 0.0}


@dataclass(frozen=True)
class Settings:
    # Models: detector "hog" | "yunet", embedder "dlib" | "sface"
    detector: str = "yunet"
    embedder: str = "sface"

    # Detection
    detector_upsample: int = 0
    detect_scale: float = 1.0
    embedding_jitters: int = 1

    # Quality gate (a frame failing any rule is never used for a decision)
    min_face_px: int = 80
    min_blur: float = 40.0
    min_brightness: float = 50.0
    max_brightness: float = 215.0
    max_yaw: float = 0.6

    # Matching: Euclidean distance between 128-d embeddings. None = default of the embedder.
    match_threshold: float | None = None
    min_margin: float | None = None

    # Temporal voting
    vote_window: int = 7
    vote_ratio: float = 0.6
    min_votes: int = 4  # frames needed to ACCEPT a person
    unknown_min_votes: int = 7  # frames needed to declare someone UNKNOWN (capped by vote_window)
    process_every: int = 1

    # Blink liveness
    blink_ear: float = 0.21
    blink_min_frames: int = 2
    blink_validity_s: float = 5.0

    # Random challenge liveness (blink / turn left / turn right, in random order)
    # Blink is available ("blink") but off by default: it was the least reliable gesture in live tests.
    # Turns can repeat, so 3 turns give 8 possible sequences instead of 2.
    challenge_types: tuple = ("turn_left", "turn_right")
    challenge_count: int = 3
    challenge_timeout_s: float = 8.0
    challenge_turn_yaw: float = 0.25
    challenge_hold_frames: int = 3  # a turn must be held this many consecutive frames: one jittery frame or a wiggled phone is not a turn
    challenge_frontal_yaw: float = 0.12
    challenge_calibration_frames: int = 10  # frames used to measure the resting pose (~0.5 s at 20 fps)
    challenge_blink_ratio: float = 0.75  # blink = eye opening below this share of the person's own

    # Passive screen detection (moire peaks); threshold calibrated by scripts/antispoof_eval.py
    screen_threshold: float = 34.5
    screen_window: int = 10
    screen_ratio: float = 0.6

    # Tracking
    track_max_dist: float = 90.0
    track_max_missed: int = 10

    @property
    def threshold(self) -> float:
        return self.match_threshold if self.match_threshold is not None else DEFAULT_THRESHOLD[self.embedder]

    @property
    def margin(self) -> float:
        return self.min_margin if self.min_margin is not None else DEFAULT_MARGIN[self.embedder]
