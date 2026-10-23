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
    """Frame geometry. Default (bands=False): FULL-BLEED 9:16 art, text placed on free background by segmentation.
    Opt-in bands=True (config comic.layout.bands): reserved bands: top band (narration) | art window | dialogue band |
    empty bottom safe zone and right strip."""

    def __init__(self, top_h: int = 330, art_h: int = 1170, dialogue_h: int = 190, right_safe: int = 120, margin: int = 60,
                 bands: bool = False):
        self.bands = bool(bands)
        self.right_safe, self.margin = int(right_safe), int(margin)
        if self.bands:
            self.top_h, self.art_h, self.dialogue_h = int(top_h), int(art_h), int(dialogue_h)
        else:
            self.top_h, self.art_h, self.dialogue_h = 0, H, 0
        self.art_y = self.top_h
        self.art_bottom = self.art_y + self.art_h
        self.dial_top = self.art_bottom
        self.dial_bottom = self.dial_top + self.dialogue_h
        self.text_right = W - self.right_safe
        self.canvas_w, self.canvas_h = int(W * 1.5), int(self.art_h * 1.5)  # zoompan canvas (1.5x the art window)

    @classmethod
    def from_cfg(cls, d: dict | None) -> "Layout":
        return cls(**{k: v for k, v in (d or {}).items() if k in ("top_h", "art_h", "dialogue_h", "right_safe", "margin", "bands")})


def speaker_x(faces_img: list[Box], img_w: int, img_h: int, win_w: int = W, win_h: int = 1170) -> int | None:
    """x (in the art window) of the largest detected face, for the bubble tail side; None when nothing was detected."""
    if not faces_img:
        return None
    f = max(faces_img, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    s = max(win_w / img_w, win_h / img_h)  # cover-fit into the art window
    ox = (img_w * s - win_w) / 2
    return int(min(max((f[0] + f[2]) / 2 * s - ox, 120), win_w - 120))


def subject_top_fraction(image_path: Path) -> float:
    """Where the subject starts (0 = top edge, 1 = bottom): first row band with real edge/texture energy. Used when no face
    is found (hooded / masked / dark subjects) so the head or hood is kept in the crop instead of a centre crop."""
    import cv2
    import numpy as np

    img = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    img = cv2.resize(img, (200, int(200 * img.shape[0] / img.shape[1])))
    rows = cv2.Canny(cv2.GaussianBlur(img, (5, 5), 0), 50, 140).mean(axis=1)
    rows = np.convolve(rows, np.ones(7) / 7, mode="same")
    hot = np.where(rows >= max(rows.max() * 0.35, 1e-6))[0]
    return float(hot[0] / len(rows)) if len(hot) else 0.0


def art_crop(image_path: Path, out_path: Path, win_w: int = W, win_h: int = 1170) -> Path:
    """Crop `image_path` to the art window's aspect, choosing the window by SUBJECT (head/hood in frame), not the centre:
    top of the face box minus headroom when a face is found, else the subject's top edge. No-op copy when the aspect already fits."""
    from PIL import Image

    with Image.open(image_path) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        want = win_w / win_h
        if abs(iw / ih - want) / want < 0.03:
            im.save(out_path)
            return out_path
        if iw / ih < want:  # taller than the window (the usual 9:16 image): choose the vertical window
            ch = int(round(iw / want))
            faces = detect_faces(Path(image_path))
            if faces:
                top = min(f[1] for f in faces)
                bottom = max(f[3] for f in faces)
                y0 = top - 0.28 * ch  # headroom above the face
                if bottom - top > 0.6 * ch:  # very large face: just keep the top part
                    y0 = top - 0.1 * ch
            else:
                y0 = subject_top_fraction(Path(image_path)) * ih - 0.05 * ch
            y0 = int(min(max(y0, 0), ih - ch))
            box = (0, y0, iw, y0 + ch)
        else:  # wider than the window: centre horizontally (rare)
            cw = int(round(ih * want))
            x0 = (iw - cw) // 2
            box = (x0, 0, x0 + cw, ih)
        im.crop(box).save(out_path)
    return out_path


# ------------------------------------------------------------------ segmentation-based placement (full-bleed art)
CELL = 10  # occupancy grid cell in frame pixels -> 108 x 192
SAFE = (40, 40, 950, 1574)  # x0, y0, x1, y1: keep text out of the right ~12% and bottom ~18% (platform UI) and the edges
_session: dict = {}


def foreground_mask(image_path: Path):
    """Character foreground mask (float32 HxW 0..1) via rembg's `isnet-anime` (CPU; model downloaded once by rembg).
    None when rembg/the model is unavailable: callers then fall back to face boxes only."""
    try:
        import numpy as np
        from PIL import Image
        from rembg import new_session, remove

        if "s" not in _session:
            _session["s"] = new_session("isnet-anime")
        with Image.open(image_path) as im:
            m = remove(im.convert("RGB"), session=_session["s"], only_mask=True)
        return np.asarray(m, dtype="float32") / 255.0
    except Exception:  # noqa: BLE001 - segmentation is an enhancement, never a requirement
        return None


def occupancy_over_motion(mask, faces_img: list[Box], img_w: int, img_h: int, motion: str,
                          samples=(0.0, 0.25, 0.5, 0.75, 1.0)):
    """Union, over the Ken-Burns move, of 'something important is here' in the final frame, as a (192 x 108) 0/1 grid:
    the character mask (if any) warped into frame coordinates at several moments, plus every face box."""
    import cv2
    import numpy as np

    gw, gh = W // CELL, H // CELL
    occ = np.zeros((gh, gw), np.float32)
    sc = max(CANVAS_W / img_w, CANVAS_H / img_h)
    ox, oy = (img_w * sc - CANVAS_W) / 2, (img_h * sc - CANVAS_H) / 2
    soft = cv2.blur(mask.astype("float32"), (11, 11)) if mask is not None else None
    for t in samples:
        x0, y0, z = window(motion, t)
        k = z * W / CANVAS_W
        if soft is not None:
            a = sc * k / CELL
            M = np.float32([[a, 0, (-ox - x0) * k / CELL], [0, a, (-oy - y0) * k / CELL]])
            layer = cv2.warpAffine(soft, M, (gw, gh), flags=cv2.INTER_LINEAR, borderValue=0)
            occ = np.maximum(occ, (layer > 0.35).astype(np.float32))
        for f in faces_img:
            fb = to_frame(to_canvas(f, img_w, img_h), motion, t)
            x1, y1, x2, y2 = (int(max(fb[0] / CELL - 1, 0)), int(max(fb[1] / CELL - 1, 0)),
                              int(min(fb[2] / CELL + 1, gw)), int(min(fb[3] / CELL + 1, gh)))
            if x2 > x1 and y2 > y1:
                occ[y1:y2, x1:x2] = 1.0
    return cv2.dilate(occ, np.ones((3, 3), np.uint8))  # 1-cell (10 px) breathing room around everything


def head_band(occ):
    """Rows of the grid covering the top ~35% of the occupied area (head / upper body): overlapping these is penalised hardest."""
    import numpy as np

    rows = np.where(occ.max(axis=1) > 0)[0]
    out = np.zeros_like(occ)
    if len(rows):
        top, h = rows[0], rows[-1] - rows[0] + 1
        out[top: top + max(2, int(h * 0.35))] = occ[top: top + max(2, int(h * 0.35))]
    return out


def head_point(occ, faces_frame: list[Box] | None = None) -> tuple[int, int] | None:
    """Where the speaker's head is, in frame px: the largest face box when there is one, else the centroid of the head band."""
    import numpy as np

    if faces_frame:
        f = max(faces_frame, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        return int((f[0] + f[2]) / 2), int(f[1] + (f[3] - f[1]) * 0.7)
    hb = head_band(occ)
    ys, xs = np.where(hb > 0)
    if len(xs) == 0:
        return None
    return int(xs.mean() * CELL), int(ys.mean() * CELL)


def best_spot(occ, w_px: int, h_px: int, blocked: list[Box] = (), safe=SAFE) -> tuple[int, int, float]:
    """Top-left (x, y) px inside the safe area minimising overlap with occupied cells (head/upper body counts 3x).
    Zero overlap wins; ties prefer the top, then the sides. Returns (x, y, cost) with cost = occupied fraction of the box (head/upper body weighted 3x)."""
    import cv2
    import numpy as np

    o = occ.copy()
    for b in blocked:  # another text box counts as occupied
        o[max(int(b[1] // CELL), 0): int(b[3] // CELL) + 1, max(int(b[0] // CELL), 0): int(b[2] // CELL) + 1] = 1.0
    ii, hh = cv2.integral(o), cv2.integral(head_band(occ))
    gh, gw = o.shape
    bw, bh = max(1, -(-w_px // CELL)), max(1, -(-h_px // CELL))
    cx0, cy0, cx1, cy1 = safe[0] // CELL, safe[1] // CELL, safe[2] // CELL, safe[3] // CELL
    best, best_key = (cx0 * CELL, cy0 * CELL, 1.0), (2, 9.9, 0.0)
    area = float(bw * bh)
    for cy in range(cy0, max(cy0, cy1 - bh) + 1, 2):
        for cx in range(cx0, max(cx0, cx1 - bw) + 1, 2):
            s = ii[cy + bh, cx + bw] - ii[cy, cx + bw] - ii[cy + bh, cx] + ii[cy, cx]
            hs = hh[cy + bh, cx + bw] - hh[cy, cx + bw] - hh[cy + bh, cx] + hh[cy, cx]
            cost = (s + 3.0 * hs) / area
            pref = cy * 0.01 + min(cx - cx0, cx1 - bw - cx) * 0.004  # top first, then the sides
            key = (0, 0.0, pref) if cost <= 0.0 else (1, cost, pref)
            if key < best_key:
                best_key, best = key, (cx * CELL, cy * CELL, float(cost))
    return best
