"""Drawing for the camera window and the web live view: accents, status chips, challenge cues, door.

OpenCV's own text renderer has no accents, so text is rendered with Pillow into small BGRA sprites
(cached: a label that does not change costs one array blend per frame) and alpha-blended.
"""

import os
import sys
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# BGR
OK = (118, 211, 76)
BAD = (86, 86, 236)
WARN = (60, 170, 250)
ACCENT = (253, 139, 91)
MUTED = (170, 160, 150)
WHITE = (255, 255, 255)
INK = (32, 24, 18)

_FONT_CANDIDATES = {
    False: ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc", "LiberationSans-Regular.ttf"],
    True: ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Helvetica.ttc", "LiberationSans-Bold.ttf"],
}


def _font_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("FACEREC_FONT_DIR", "")) if os.environ.get("FACEREC_FONT_DIR") else None]
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
    dirs += [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/liberation"),
             Path("/System/Library/Fonts"), Path("/Library/Fonts")]
    return [d for d in dirs if d is not None]


@lru_cache(maxsize=16)
def _font(size: int, bold: bool) -> ImageFont.ImageFont:
    for directory in _font_dirs():
        for name in _FONT_CANDIDATES[bold]:
            if (directory / name).exists():
                return ImageFont.truetype(str(directory / name), size)
    return ImageFont.load_default(size)  # Pillow's bundled font: always available, accents included


@lru_cache(maxsize=256)
def text_sprite(text: str, size: int, color: tuple, bold: bool = False) -> np.ndarray:
    """BGRA image of `text` (tight to its ink), cached by content."""
    font = _font(size, bold)
    left, top, right, bottom = font.getbbox(text or " ")
    width, height = max(1, right - left + 2), max(1, bottom - top + 4)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).text((1 - left, 1 - top), text, font=font, fill=(color[2], color[1], color[0], 255))
    rgba = np.asarray(image)
    return np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])


def blit(frame: np.ndarray, sprite: np.ndarray, x: int, y: int) -> None:
    """Alpha-blend a BGRA sprite onto `frame` at (x, y); anything outside the frame is clipped."""
    h, w = sprite.shape[:2]
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, fw), min(y + h, fh)
    if x0 >= x1 or y0 >= y1:
        return
    part = sprite[y0 - y:y1 - y, x0 - x:x1 - x]
    alpha = part[:, :, 3:4].astype(np.float32) / 255.0
    region = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (part[:, :, :3] * alpha + region * (1 - alpha)).astype(np.uint8)


def put_text(frame, text: str, x: int, y: int, size: int = 16, color=WHITE, bold: bool = False) -> int:
    """Draw text with its top-left at (x, y). Returns its width in pixels."""
    sprite = text_sprite(text, size, tuple(color), bold)
    blit(frame, sprite, x, y)
    return sprite.shape[1]


def text_width(text: str, size: int = 16, bold: bool = False) -> int:
    return text_sprite(text, size, WHITE, bold).shape[1]


def fit_text(text: str, max_width: int, size: int = 16, bold: bool = False) -> str:
    """Shorten with an ellipsis so that the text fits in `max_width`."""
    if text_width(text, size, bold) <= max_width:
        return text
    while len(text) > 1 and text_width(text + "…", size, bold) > max_width:
        text = text[:-1]
    return text + "…"


def rounded_rect(frame, x0, y0, x1, y1, color, alpha: float = 1.0, radius: int = 10) -> None:
    """Filled, optionally translucent, rounded rectangle (clipped to the frame)."""
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    cx0, cy0, cx1, cy1 = max(x0, 0), max(y0, 0), min(x1, fw), min(y1, fh)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    r = max(0, min(radius, (x1 - x0) // 2, (y1 - y0) // 2))
    cv2.rectangle(mask, (r, 0), (x1 - x0 - r - 1, y1 - y0 - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (x1 - x0 - 1, y1 - y0 - r - 1), 255, -1)
    for cx, cy in ((r, r), (x1 - x0 - r - 1, r), (r, y1 - y0 - r - 1), (x1 - x0 - r - 1, y1 - y0 - r - 1)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    mask = mask[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0].astype(np.float32)[:, :, None] / 255.0 * alpha
    region = frame[cy0:cy1, cx0:cx1].astype(np.float32)
    frame[cy0:cy1, cx0:cx1] = (np.array(color, np.float32) * mask + region * (1 - mask)).astype(np.uint8)


def corner_brackets(frame, box, color, thickness: int = 3) -> None:
    left, top, right, bottom = box
    arm = max(14, (right - left) // 5)
    for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1), (left, bottom, 1, -1), (right, bottom, -1, -1)):
        cv2.line(frame, (x, y), (x + dx * arm, y), color, thickness, cv2.LINE_AA)
        cv2.line(frame, (x, y), (x, y + dy * arm), color, thickness, cv2.LINE_AA)


def chip(frame, x: int, y: int, text: str, color, max_width: int | None = None, size: int = 15) -> tuple[int, int]:
    """Rounded status pill with white bold text. Returns its (width, height)."""
    if max_width is not None:
        text = fit_text(text, max_width - 22, size, bold=True)
    sprite = text_sprite(text, size, WHITE, True)
    w, h = sprite.shape[1] + 22, sprite.shape[0] + 10
    rounded_rect(frame, x, y, x + w, y + h, color, 0.92, radius=h // 2)
    blit(frame, sprite, x + 11, y + 5)
    return w, h


def face_label(
    frame, box, text: str, color, confidence: float | None = None, reserved_bottom: int = 72, reserved_top: int = 70,
) -> tuple[int, int, int, int]:
    """Status chip (plus an optional confidence meter) centred on the face, kept clear of the HUD
    (top) and the door banner (bottom): below the face when there is room, else above it.
    Returns the (x, y, width, height) it occupies."""
    frame_h, frame_w = frame.shape[:2]
    left, top, right, bottom = box
    size = 15
    text = fit_text(text, frame_w - 48, size, bold=True)
    sprite = text_sprite(text, size, WHITE, True)
    w, h = sprite.shape[1] + 22, sprite.shape[0] + 10
    extra = 14 if confidence is not None else 0
    x = int(np.clip((left + right) // 2 - w // 2, 12, max(12, frame_w - w - 12)))
    below = bottom + 10
    if below + h + extra <= frame_h - reserved_bottom:
        y = below
    elif top - h - extra - 10 >= reserved_top:
        y = top - h - extra - 10
    else:  # no clean spot: tuck it inside the lower part of the box
        y = int(np.clip(bottom - h - extra - 8, reserved_top, max(reserved_top, frame_h - reserved_bottom - h - extra)))
    corner_brackets(frame, box, color)
    chip(frame, x, y, text, color)
    if confidence is not None:
        meter(frame, x, y + h + 6, w, confidence, color)
    return x, y, w, h + extra


def instruction(frame, text: str, color, y: int = 16) -> None:
    """Large chip centred at the top of the frame (clear of the HUD): what the person must do now."""
    frame_w = frame.shape[1]
    text = fit_text(text, frame_w - 240, 17, bold=True)  # between the HUD (left) and the countdown ring (right)
    sprite = text_sprite(text, 17, WHITE, True)
    chip(frame, (frame_w - sprite.shape[1] - 22) // 2, y, text, color, size=17)


def meter(frame, x: int, y: int, width: int, fraction: float, color, height: int = 6) -> None:
    """Thin rounded progress bar."""
    rounded_rect(frame, x, y, x + width, y + height, (60, 52, 46), 0.85, radius=height // 2)
    filled = int(width * float(np.clip(fraction, 0.0, 1.0)))
    if filled >= height:
        rounded_rect(frame, x, y, x + filled, y + height, color, 1.0, radius=height // 2)


def ring(frame, center: tuple[int, int], radius: int, fraction: float, color, thickness: int = 5) -> None:
    """Circular countdown/progress: a faint full circle and an arc covering `fraction` of it."""
    cv2.circle(frame, center, radius, (70, 62, 56), thickness, cv2.LINE_AA)
    fraction = float(np.clip(fraction, 0.0, 1.0))
    if fraction > 0:
        cv2.ellipse(frame, center, (radius, radius), -90, 0, 360 * fraction, color, thickness, cv2.LINE_AA)


def arrow(frame, center: tuple[int, int], direction: int, color, size: int = 34, pulse: float = 0.0) -> None:
    """Large chevron arrow pointing left (-1) or right (+1); `pulse` in [0, 1] slides it a little."""
    cx, cy = center
    cx += int(direction * pulse * 10)
    pts = np.array([(cx - direction * size // 2, cy - size), (cx + direction * size // 2, cy),
                    (cx - direction * size // 2, cy + size)], np.int32)
    overlay = frame.copy()
    cv2.polylines(overlay, [pts], False, color, 9, cv2.LINE_AA)
    cv2.addWeighted(overlay, 0.9, frame, 0.1, 0, frame)


def steps(frame, x: int, y: int, current: int, total: int, color) -> None:
    """Dots for a multi-step challenge: done = filled, current = ring, upcoming = dim."""
    for i in range(total):
        c = (x + i * 22, y)
        if i < current:
            cv2.circle(frame, c, 7, color, -1, cv2.LINE_AA)
        elif i == current:
            cv2.circle(frame, c, 7, color, 2, cv2.LINE_AA)
        else:
            cv2.circle(frame, c, 7, (90, 82, 76), 2, cv2.LINE_AA)


def door_banner(frame, is_open: bool, seconds_left: float, total_seconds: float, zone: str = "") -> None:
    """Bottom banner: a closed or open lock, the zone, and a ring emptying while the door is open."""
    h, w = frame.shape[:2]
    color = OK if is_open else (96, 88, 82)
    rounded_rect(frame, 12, h - 58, 12 + 270, h - 12, (28, 22, 18), 0.78, radius=14)
    center = (12 + 30, h - 35)
    ring(frame, center, 15, (seconds_left / max(total_seconds, 1e-6)) if is_open else 1.0, color, 4)
    # padlock: body + shackle (open = shackle lifted to one side)
    cv2.rectangle(frame, (center[0] - 6, center[1] - 1), (center[0] + 6, center[1] + 7), color, -1, cv2.LINE_AA)
    if is_open:
        cv2.ellipse(frame, (center[0] + 2, center[1] - 3), (5, 5), 0, 180, 360, color, 2, cv2.LINE_AA)
    else:
        cv2.ellipse(frame, (center[0], center[1] - 1), (5, 5), 0, 180, 360, color, 2, cv2.LINE_AA)
    label = f"Porte ouverte · {seconds_left:.0f} s" if is_open else "Porte fermée"
    put_text(frame, label, 12 + 58, h - 52, 17, WHITE, bold=True)
    if zone:
        put_text(frame, fit_text(zone, 200, 13), 12 + 58, h - 30, 13, MUTED)


def hud(frame, fps: float, mode: str) -> None:
    """Top-left translucent status panel."""
    text = f"{fps:4.1f} img/s"
    w = max(text_width(text, 14, True), text_width(mode, 12)) + 24
    rounded_rect(frame, 10, 10, 10 + w, 62, (28, 22, 18), 0.7, radius=12)
    put_text(frame, text, 22, 16, 14, WHITE, bold=True)
    put_text(frame, mode, 22, 38, 12, MUTED)


def enrollment_panel(frame, name: str, progress: int, total: int, hint: str) -> None:
    """Bottom card shown while someone is being enrolled."""
    h, w = frame.shape[:2]
    top = h - 96
    rounded_rect(frame, 12, top, w - 12, h - 12, (28, 22, 18), 0.82, radius=16)
    put_text(frame, fit_text(f"Inscription de {name}", w - 190, 18, True), 28, top + 12, 18, WHITE, bold=True)
    put_text(frame, fit_text(hint, w - 190, 15), 28, top + 40, 15, MUTED)
    meter(frame, 28, top + 68, w - 56, progress / max(total, 1), ACCENT, 8)
    label = f"{progress}/{total}"
    put_text(frame, label, w - 28 - text_width(label, 18, True), top + 12, 18, ACCENT, bold=True)
