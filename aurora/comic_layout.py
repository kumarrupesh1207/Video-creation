"""Where text goes on a comic panel: keep speech bubbles / narration boxes OFF the characters' faces.

Faces: nagadomi's lbpcascade_animeface (MIT, https://github.com/nagadomi/lbpcascade_animeface; assets/cascades/),
then OpenCV's frontal-face Haar cascade, then an edge-density "subject" box. Boxes are mapped through the Ken-Burns
move (zoom/pan over the whole clip) into final 1080x1920 frame coordinates, so the free area stays free while it moves.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANIME_CASCADE = ROOT / "assets" / "cascades" / "lbpcascade_animeface.xml"
W, H = 1080, 1920
CANVAS_W, CANVAS_H = 1620, 2880  # the image is cover-scaled to this before zoompan (aurora/comic.py render_clip)
MARGIN = 40  # safe margin to the frame edge, px
Box = tuple[float, float, float, float]  # x0, y0, x1, y1


def detect_faces(image_path: Path) -> list[Box]:
    """Face boxes in IMAGE pixel coordinates: anime cascade -> frontal Haar -> edge-density subject box."""
    import cv2

    img = cv2.imread(str(image_path))
    if img is None:
        return []
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    ih, iw = gray.shape
    min_side = max(24, int(min(iw, ih) * 0.05))
    for xml in (ANIME_CASCADE, Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"):
        if not xml.exists():
            continue
        cascade = cv2.CascadeClassifier(str(xml))
        if cascade.empty():
            continue
        found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(min_side, min_side))
        if len(found):
            return [(float(x), float(y), float(x + w), float(y + h)) for x, y, w, h in found]
    return [_subject_box(img)]  # nothing detected: avoid the busiest (most edge-dense) region instead


def _subject_box(img) -> Box:
    import cv2
    import numpy as np

    ih, iw = img.shape[:2]
    edges = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0), 60, 160)
    gh, gw = 24, 14
    cells = cv2.resize(edges.astype("float32"), (gw, gh), interpolation=cv2.INTER_AREA)
    ys, xs = np.where(cells >= np.percentile(cells, 75))
    if len(xs) == 0:
        return (iw * 0.25, ih * 0.2, iw * 0.75, ih * 0.6)
    return (xs.min() / gw * iw, ys.min() / gh * ih, (xs.max() + 1) / gw * iw, (ys.max() + 1) / gh * ih)


def to_canvas(box: Box, img_w: int, img_h: int) -> Box:
    """Image coords -> the cover-scaled, centre-cropped CANVAS_W x CANVAS_H canvas that zoompan moves over."""
    s = max(CANVAS_W / img_w, CANVAS_H / img_h)
    ox, oy = (img_w * s - CANVAS_W) / 2, (img_h * s - CANVAS_H) / 2
    return (box[0] * s - ox, box[1] * s - oy, box[2] * s - ox, box[3] * s - oy)


def window(motion: str, t: float) -> tuple[float, float, float]:
    """Visible canvas window at progress t (0..1) for each Ken-Burns move: (x0, y0, zoom). Mirrors comic._zoompan."""
    if motion == "zoom_in":
        z = min(1.0 + 0.12 * t, 1.12)
    elif motion == "zoom_out":
        z = max(1.12 - 0.12 * t, 1.0)
    elif motion == "punch":
        z = 1.225 + 0.05 * t  # after the first ~0.4 s punch; the start (z=1.0) is brief, the held range is what matters
    else:
        z = 1.12
    ww, wh = CANVAS_W / z, CANVAS_H / z
    x0, y0 = (CANVAS_W - ww) / 2, (CANVAS_H - wh) / 2
    if motion == "pan_left":
        x0 = (CANVAS_W - ww) * t
    elif motion == "pan_right":
        x0 = (CANVAS_W - ww) * (1 - t)
    return x0, y0, z


def to_frame(box_canvas: Box, motion: str, t: float) -> Box:
    x0, y0, z = window(motion, t)
    k = z * W / CANVAS_W
    return ((box_canvas[0] - x0) * k, (box_canvas[1] - y0) * k, (box_canvas[2] - x0) * k, (box_canvas[3] - y0) * k)


def faces_over_motion(faces_img: list[Box], img_w: int, img_h: int, motion: str, samples=(0.0, 0.25, 0.5, 0.75, 1.0)) -> list[Box]:
    """Every face box in FINAL frame coordinates at several moments of the move, merged per face into its union box."""
    out = []
    for f in faces_img:
        cb = to_canvas(f, img_w, img_h)
        frames = [to_frame(cb, motion, t) for t in samples]
        out.append((min(b[0] for b in frames), min(b[1] for b in frames), max(b[2] for b in frames), max(b[3] for b in frames)))
    return out


def overlap(a: Box, b: Box) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _grow(b: Box, m: float) -> Box:
    return (b[0] - m, b[1] - m, b[2] + m, b[3] + m)


def place_narration(nw: int, nh: int, faces: list[Box], x: int = 60, top: int = 70, bottom_gap: int = 90) -> tuple[int, int]:
    """Top edge by default; the bottom edge when that overlaps faces less."""
    top_box = (x, top, x + nw, top + nh)
    y_bot = H - bottom_gap - nh
    bot_box = (x, y_bot, x + nw, y_bot + nh)
    cost = lambda b: sum(overlap(_grow(b, 10), f) for f in faces)  # noqa: E731
    return (x, top) if cost(top_box) <= cost(bot_box) else (x, y_bot)


def place_bubble(bw: int, bh: int, faces: list[Box], blocked: list[Box] = (), tail: int = 100, step: int = 30) -> tuple[int, int, tuple[int, int] | None]:
    """Best top-left (x, y) for a bubble of bw x bh (+ tail below): zero overlap with faces and blocked boxes when possible,
    >= MARGIN from the frame edge, preferring the top corners and a position near the speaker. Returns (x, y, tail_target)."""
    face_margin = 30
    avoid = [_grow(f, face_margin) for f in faces]
    best, best_score = None, float("inf")
    for y in range(MARGIN, H - MARGIN - bh - tail + 1, step):
        for x in range(MARGIN, W - MARGIN - bw + 1, step):
            box = (x, y, x + bw, y + bh + tail)
            ov = sum(overlap(box, a) for a in avoid) + sum(overlap(box, _grow(b, 12)) for b in blocked)
            cx = x + bw / 2
            corner = min(abs(cx - MARGIN), abs(cx - (W - MARGIN)))  # prefer top-left / top-right
            near = min((abs(cx - (f[0] + f[2]) / 2) + abs(y + bh - (f[1] + f[3]) / 2) for f in faces), default=0)
            score = ov * 1000 + y * 0.6 + corner * 0.15 + near * 0.05
            if score < best_score:
                best, best_score = (x, y), score
    x, y = best or (MARGIN, MARGIN)
    target = None
    if faces:  # tail points at the speaker's mouth: lower-middle of the nearest face, stopping at the face edge
        f = min(faces, key=lambda f: abs((f[0] + f[2]) / 2 - (x + bw / 2)) + abs((f[1] + f[3]) / 2 - (y + bh)))
        target = (int((f[0] + f[2]) / 2), int(f[1] + (f[3] - f[1]) * 0.78))
    return int(x), int(y), target


class Layout:
    """Reserved bands of the 1080x1920 frame. Text lives ONLY in the top/dialogue bands, never on the art:
    top band (narration) | art window (image + Ken Burns) | dialogue band (speech bubble) | empty bottom safe zone
    (platform UI) and an empty right strip. Sizes come from config comic.layout."""

    def __init__(self, top_h: int = 330, art_h: int = 1170, dialogue_h: int = 190, right_safe: int = 120, margin: int = 60):
        self.top_h, self.art_h, self.dialogue_h, self.right_safe, self.margin = (int(top_h), int(art_h), int(dialogue_h),
                                                                                  int(right_safe), int(margin))
        self.art_y = self.top_h
        self.art_bottom = self.art_y + self.art_h
        self.dial_top = self.art_bottom
        self.dial_bottom = self.dial_top + self.dialogue_h  # everything below stays empty (Shorts/Reels UI covers it)
        self.text_right = W - self.right_safe
        self.canvas_w, self.canvas_h = int(W * 1.5), int(self.art_h * 1.5)  # zoompan canvas (1.5x the art window)

    @classmethod
    def from_cfg(cls, d: dict | None) -> "Layout":
        return cls(**{k: v for k, v in (d or {}).items() if k in ("top_h", "art_h", "dialogue_h", "right_safe", "margin")})


def speaker_x(faces_img: list[Box], img_w: int, img_h: int, win_w: int = W, win_h: int = 1170) -> int | None:
    """x (in the art window) of the largest detected face, for the bubble tail side; None when nothing was detected."""
    if not faces_img:
        return None
    f = max(faces_img, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    s = max(win_w / img_w, win_h / img_h)  # cover-fit into the art window
    ox = (img_w * s - win_w) / 2
    return int(min(max((f[0] + f[2]) / 2 * s - ox, 120), win_w - 120))
