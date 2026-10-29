"""Motion-comic reel: panel images + per-panel voice -> 9:16 1080x1920 video (free: images only, no video model).

    make_comic_reel(channel, premise, out_dir)  # research -> images -> render -> validate -> sidecar -> series state

Each panel lasts its narration audio + 0.4 s, with Ken-Burns motion (punch zoom for action panels), a white panel
border, paper grain, a narration box and an optional speech bubble. Panels join with xfade page-turn transitions.
Cover card first, "To be continued... Part N+1" end card last. Voice: per-panel Edge TTS; music: ducked MusicGen.
"""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from aurora import comic_layout, research
from aurora.audio import generate_music, mix_audio, synthesize_voiceover
from aurora.costs import COST_DIR, credits_today, log_credits
from aurora.monitoring.media_validator import _ffprobe, validate_video

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS, XF, PAD = 1080, 1920, 24, 0.4, 0.4


@contextmanager
def frame(w: int, h: int):
    """Render at another frame size (e.g. `with comic.frame(1920, 1080):` for 16:9 episodes); restores the portrait defaults."""
    global W, H
    old = (W, H)
    prev = comic_layout.set_frame(w, h)
    W, H = int(w), int(h)
    try:
        yield
    finally:
        W, H = old
        comic_layout.restore_frame(prev)
COVER_S, END_S = 2.4, 3.0
MAX_NARRATION_WORDS = 12  # keeps a 6-panel reel at ~28-34 s
PANEL_MIN_S = 4.4  # hold at least this long per panel (voice is never slowed; the rest is a pause)
NO_TEXT_NEG = "text, letters, words, speech bubble, caption, watermark, signature, logo, subtitles"
TRANSITIONS = ["slideleft", "wipeleft", "circleopen", "slideright", "wiperight", "smoothleft"]
MOTIONS = ["zoom_in", "pan_left", "zoom_out", "pan_right"]
FONT_DIR = Path("C:/Windows/Fonts")
LATIN_FONTS = ["comicbd.ttf", "arialbd.ttf", "segoeuib.ttf"]
DEVA_FONTS = ["NirmalaB.ttf", "Nirmala.ttf"]


def _font(size: int, text: str = "") -> ImageFont.FreeTypeFont:
    names = DEVA_FONTS if re.search(r"[\u0900-\u097F]", text) else LATIN_FONTS
    for n in names:
        if (FONT_DIR / n).exists():
            return ImageFont.truetype(str(FONT_DIR / n), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _balanced(draw, text, font, max_w) -> list[str]:
    """Greedy wrap, then try slightly narrower widths so the last line is never a lonely single word."""
    lines = _wrap(draw, text, font, max_w)
    if len(lines) >= 2 and len(lines[-1].split()) == 1:
        for w in range(int(max_w * 0.95), int(max_w * 0.5), -15):
            alt = _wrap(draw, text, font, w)
            if len(alt) == len(lines) and len(alt[-1].split()) >= 2:
                return alt
    return lines


def fit_text(draw, text: str, max_w: int, max_h: int, hi: int, lo: int):
    """Largest font (hi..lo) whose wrapped, balanced text fits max_w x max_h. If even `lo` does not fit, the text is
    shortened with an ellipsis (never cut off at an edge). Returns (font, lines, line_height)."""
    words = text.split()
    while True:
        cur = " ".join(words) + ("…" if len(words) < len(text.split()) else "")
        for size in range(hi, lo - 1, -2):
            f = _font(size, cur)
            lines = _balanced(draw, cur, f, max_w)
            lh = int(size * 1.28)
            if len(lines) * lh <= max_h and all(draw.textlength(l, font=f) <= max_w for l in lines):
                return f, lines, lh
        if len(words) <= 2:
            return f, lines, lh
        words = words[:-1]


def _overlay_bands(path: Path, narration: str = "", bubble: str = "", title: str = "", subtitle: str = "", border: bool = True,
                   speaker_x: int | None = None, layout: "comic_layout.Layout | None" = None) -> Path:
    """Transparent 1080x1920 PNG with text ONLY in the reserved bands: narration box (top band), speech bubble (dialogue
    band, tail pointing up into the art toward speaker_x), or title/subtitle cards (top / dialogue band). The art window,
    the bottom safe zone and the right strip stay free of text."""
    L = layout or comic_layout.Layout()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if border:
        d.rectangle([6, L.art_y, W - 6, L.art_bottom - 1], outline=(255, 255, 255, 255), width=6)
    xl, xr = L.margin, L.text_right
    if narration:
        pad = 28
        f, lines, lh = fit_text(d, narration, xr - xl - 2 * pad, L.top_h - 2 * 40 - 2 * pad, 54, 32)
        tw = int(max(d.textlength(l, font=f) for l in lines))
        bw, bh = tw + 2 * pad, len(lines) * lh + 2 * pad
        x0, y0 = xl, (L.top_h - bh) // 2
        d.rectangle([x0, y0, x0 + bw, y0 + bh], fill=(255, 243, 196, 255), outline=(20, 20, 20, 255), width=5)
        for k, l in enumerate(lines):
            d.text((x0 + pad, y0 + pad + k * lh), l, font=f, fill=(20, 20, 20, 255))
    if bubble:
        px, py = 50, 22
        f, lines, lh = fit_text(d, bubble, xr - xl - 2 * px, L.dialogue_h - 2 * py - 10, 52, 30)
        tw = int(max(d.textlength(l, font=f) for l in lines))
        bw, bh = tw + 2 * px, len(lines) * lh + 2 * py
        x0 = xl + (xr - xl - bw) // 2
        y0 = L.dial_top + (L.dialogue_h - bh) // 2 + 6
        sx = min(max(speaker_x if speaker_x is not None else W // 2, x0 + 70), x0 + bw - 70)  # tail base stays on the bubble
        tip_x = min(max(speaker_x if speaker_x is not None else W // 2, xl + 10), xr - 10)
        tip = (tip_x, L.art_bottom - 75)  # up into the art window, toward the speaker's side
        tail = [(sx - 26, y0 + 4), tip, (sx + 26, y0 + 4)]
        d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=min(bh // 2, 60), fill=(255, 255, 255, 255),
                            outline=(20, 20, 20, 255), width=6)
        d.polygon(tail, fill=(255, 255, 255, 255))
        d.line([tail[0], tail[1], tail[2]], fill=(20, 20, 20, 255), width=6)
        d.line([(sx - 22, y0 + 3), (sx + 22, y0 + 3)], fill=(255, 255, 255, 255), width=9)  # open the bubble outline under the tail
        for k, l in enumerate(lines):
            d.text((x0 + (bw - d.textlength(l, font=f)) / 2, y0 + py + k * lh), l, font=f, fill=(20, 20, 20, 255))
    if title:  # cover / end card: big title in the top band, subtitle in the dialogue band
        f, lines, lh = fit_text(d, title, xr - xl, L.top_h - 2 * 40, 110, 56)
        y = (L.top_h - len(lines) * lh) // 2
        for l in lines:
            d.text((xl + (xr - xl - d.textlength(l, font=f)) / 2, y), l, font=f, fill=(255, 255, 255, 255),
                   stroke_width=7, stroke_fill=(0, 0, 0, 255))
            y += lh
        if subtitle:
            f2, lines2, lh2 = fit_text(d, subtitle, xr - xl, L.dialogue_h - 40, 70, 36)
            y = L.dial_top + (L.dialogue_h - len(lines2) * lh2) // 2
            for l in lines2:
                d.text((xl + (xr - xl - d.textlength(l, font=f2)) / 2, y), l, font=f2, fill=(255, 214, 80, 255),
                       stroke_width=6, stroke_fill=(0, 0, 0, 255))
                y += lh2
    img.save(path)
    return path


CLEAN_COST = 0.05  # a placement costlier than this (any head/other-box overlap, or >5% body) triggers the fallback layout


def _place_text(d, text: str, occ, blocked, hi: int, lo: int, widths, pad_x: int, pad_y: int, hz=None, safe=None):
    """Try font sizes hi..lo and wrap widths (wide -> narrow boxes) and keep the best-scoring placement: overlap with the
    characters (head/upper body weighted) + a small penalty per font step and per narrowing, so readable text beats a tiny
    box squeezed into a gap. Text is never cut off: fonts shrink first, then boxes get narrower."""
    best = None
    for si, size in enumerate(range(hi, lo - 1, -4)):
        f = _font(size, text)
        for wi, max_w in enumerate(widths):
            lines = _balanced(d, text, f, max_w)
            lh = int(size * 1.28)
            tw = int(max(d.textlength(l, font=f) for l in lines))
            bw, bh = tw + 2 * pad_x, len(lines) * lh + 2 * pad_y
            sf = safe or comic_layout.SAFE
            if bw > sf[2] - sf[0] or bh > sf[3] - sf[1]:
                continue
            x, y, cost = comic_layout.best_spot(occ, bw, bh, blocked, head=hz, **({'safe': safe} if safe else {}))
            score = cost + 0.03 * si + 0.01 * wi
            if best is None or score < best[0]:
                best = (score, cost, f, lines, lh, x, y, bw, bh)
    return best[2:], best[1]


def _dashed_rrect(d, box, r, fill, width=5, dash=18, gap=12):
    """Dashed rounded-rectangle outline (whisper bubbles)."""
    import math

    x0, y0, x1, y1 = box
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts += [(cx + r * math.cos(math.radians(a0 + k * 6)), cy + r * math.sin(math.radians(a0 + k * 6))) for k in range(16)]
    pts.append(pts[0])
    run, on = 0.0, True
    for p, q in zip(pts, pts[1:]):
        seg = math.dist(p, q)
        if on:
            d.line([p, q], fill=fill, width=width)
        run += seg
        if run >= (dash if on else gap):
            run, on = 0.0, not on


def _bubble_body(d, box, style: str) -> None:
    """speech: round white bubble; shout: spiky burst; whisper: dashed outline."""
    import math

    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if style == "shout":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pts = []
        for k in range(32):
            a = k * 2 * math.pi / 32
            f = 1.0 if k % 2 == 0 else 0.84
            pts.append((cx + math.cos(a) * (bw / 2 + 34) * f, cy + math.sin(a) * (bh / 2 + 30) * f))
        d.polygon(pts, fill=(255, 255, 255, 255), outline=(20, 20, 20, 255), width=7)
    elif style == "whisper":
        r = min(bh // 2, 56)
        d.rounded_rectangle(list(box), radius=r, fill=(250, 250, 250, 255))
        _dashed_rrect(d, box, r, (60, 60, 60, 255))
    else:
        d.rounded_rectangle(list(box), radius=min(bh // 2, 56), fill=(255, 255, 255, 255), outline=(20, 20, 20, 255), width=6)


def _overlay_free(path: Path, narration: str, bubble: str, title: str, subtitle: str, border: bool, occ, head, L, head_zone=None,
                  bubble_style: str = "speech", narr_style: str = "caption") -> Path:
    """Full-bleed art: narration box and speech bubble go where the characters are NOT (occupancy from the segmentation mask
    + faces over the whole Ken-Burns move), inside the platform-UI safe zones; the tail points at the speaker's head."""
    import numpy as np

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if border:
        d.rectangle([18, 18, W - 18, H - 18], outline=(255, 255, 255, 255), width=9)
    if occ is None:
        occ = np.zeros((H // comic_layout.CELL, W // comic_layout.CELL), np.float32)
    S = comic_layout.SAFE
    blocked, narr_box = [], None
    if narration:
        (f, lines, lh, x0, y0, bw, bh), cost = _place_text(d, narration, occ, blocked, 54, 30, (1100, 880, 700, 560, 440, 340), 28, 22, head_zone)
        if cost > CLEAN_COST:  # no clean region: slim semi-transparent strip pinned to the very top edge, at most 2 lines
            for size in range(46, 29, -2):
                f = _font(size, narration)
                lines, lh = _balanced(d, narration, f, comic_layout.SAFE[2] - 60), int(size * 1.28)
                if len(lines) <= 2:
                    break
            bh = len(lines) * lh + 20
            narr_box = (0, 0, W, bh)
            d.rectangle(list(narr_box), fill=(255, 243, 196, 215))
            for k, l in enumerate(lines):
                d.text((40, 10 + k * lh), l, font=f, fill=(20, 20, 20, 255))
        else:
            narr_box = (x0, y0, x0 + bw, y0 + bh)
            verse = narr_style == "verse"  # parchment box with a double rule; captions keep the yellow box
            d.rectangle(list(narr_box), fill=(232, 208, 160, 255) if verse else (255, 243, 196, 255), outline=(110, 70, 30, 255) if verse else (20, 20, 20, 255), width=5)
            if verse:
                d.rectangle([x0 + 9, y0 + 9, x0 + bw - 9, y0 + bh - 9], outline=(110, 70, 30, 255), width=2)
            for k, l in enumerate(lines):
                d.text((x0 + 28, y0 + 22 + k * lh), l, font=f, fill=(60, 35, 10, 255) if verse else (20, 20, 20, 255))
        blocked.append(narr_box)
    if bubble:
        placed = _place_text(d, bubble, occ, blocked, 54, 30, (900, 760, 600, 480, 380, 300), 46, 28, head_zone)
        (f, lines, lh, x0, y0, bw, bh), cost = placed
        if cost > CLEAN_COST:
            S0 = comic_layout.SAFE
            if head:  # (a) the side opposite the head's x, at the head's height, only if it is clean background there
                side = (540, 40, S0[2], S0[3]) if head[0] < 540 else (S0[0], 40, 540, S0[3])
                near = (side[0], max(head[1] - 160, 40), side[2], min(head[1] + 260, S0[3]))
                alt = _place_text(d, bubble, occ, blocked, 54, 30, (480, 380, 300), 46, 28, head_zone, safe=near)
                if alt[1] <= 0.0:
                    placed = alt
            if placed[1] > CLEAN_COST:  # (b) just above the platform-UI safe zone at the bottom (lower robe, never the head)
                placed = _place_text(d, bubble, occ, blocked, 54, 30, (760, 600, 480, 380, 300), 46, 28, head_zone,
                                     safe=(S0[0], S0[3] - 230, S0[2], S0[3]))
            (f, lines, lh, x0, y0, bw, bh), cost = placed
        # tail: from the bubble edge toward the speaker's head, stopping before it reaches the character
        cx, cy = x0 + bw / 2, y0 + bh / 2
        tail = None
        if head:
            dx, dy = head[0] - cx, head[1] - cy
            dist = max((dx * dx + dy * dy) ** 0.5, 1.0)
            ux, uy = dx / dist, dy / dist
            edge = 1.0 / (((ux / (bw / 2)) ** 2 + (uy / (bh / 2)) ** 2) ** 0.5)
            base = (cx + ux * (edge - 8), cy + uy * (edge - 8))
            L_t = 0.0
            while L_t < 110:
                px, py = base[0] + ux * (L_t + 8), base[1] + uy * (L_t + 8)
                gx, gy = int(px // comic_layout.CELL), int(py // comic_layout.CELL)
                if not (0 <= gx < occ.shape[1] and 0 <= gy < occ.shape[0]) or occ[gy, gx] > 0.5 or L_t + 8 >= dist - edge:
                    break
                L_t += 8
            L_t = max(L_t, 34.0) if dist - edge > 40 else 0.0
            if L_t:
                tip = (base[0] + ux * L_t, base[1] + uy * L_t)
                nx, ny = -uy * 24, ux * 24
                tail = [(base[0] + nx, base[1] + ny), tip, (base[0] - nx, base[1] - ny)]
        _bubble_body(d, (x0, y0, x0 + bw, y0 + bh), bubble_style)
        if tail:
            d.polygon(tail, fill=(255, 255, 255, 255))
            d.line([tail[0], tail[1], tail[2]], fill=(20, 20, 20, 255), width=6)
            d.line([tail[0], tail[2]], fill=(255, 255, 255, 255), width=9)
        for k, l in enumerate(lines):
            d.text((x0 + (bw - d.textlength(l, font=f)) / 2, y0 + 28 + k * lh), l, font=f,
                   fill=(70, 70, 70, 255) if bubble_style == "whisper" else (20, 20, 20, 255))
    if title:  # cover / end card over the (dimmed) art, centred in the safe area
        f, lines, lh = fit_text(d, title, S[2] - S[0] - 20, 400, 110, 56)
        y = int(H * 0.36)
        for l in lines:
            d.text((S[0] + (S[2] - S[0] - d.textlength(l, font=f)) / 2, y), l, font=f, fill=(255, 255, 255, 255), stroke_width=7, stroke_fill=(0, 0, 0, 255))
            y += lh
        if subtitle:
            f2, lines2, lh2 = fit_text(d, subtitle, S[2] - S[0] - 20, 200, 70, 36)
            for l in lines2:
                d.text((S[0] + (S[2] - S[0] - d.textlength(l, font=f2)) / 2, y + 30), l, font=f2, fill=(255, 214, 80, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255))
                y += lh2
    img.save(path)
    return path


def make_overlay(path: Path, narration: str = "", bubble: str = "", title: str = "", subtitle: str = "", border: bool = True,
                 speaker_x: int | None = None, layout: "comic_layout.Layout | None" = None, occ=None, head=None, head_zone=None,
                 bubble_style: str = "speech", narr_style: str = "caption") -> Path:
    """Text overlay PNG. Default: full-bleed art with segmentation-aware placement (`occ`, `head`); layout.bands=True keeps
    the reserved-band layout."""
    L = layout or comic_layout.Layout()
    if L.bands:
        return _overlay_bands(path, narration, bubble, title, subtitle, border, speaker_x, L)
    return _overlay_free(path, narration, bubble, title, subtitle, border, occ, head, L, head_zone, bubble_style, narr_style)


RETENTION_DEFAULTS = {"hook_first": False, "hook_s": 1.5, "title_s": 1.5, "previously": True, "previously_s": 1.2, "punch_zoom": True}


def retention_cfg(raw: dict | None) -> dict:
    """Series-pack `retention:` block merged over the defaults (hook_first is opt-in per series)."""
    return {**RETENTION_DEFAULTS, **{k: v for k, v in (raw or {}).items() if k in RETENTION_DEFAULTS}}


def pick_hook_panel(comic: dict) -> int:
    """0-based index of the panel that opens the Short: research's `hook_panel` (1-based) when valid, else the action+sfx panel, any action
    panel, any sfx panel, else the last-but-one panel (the build-up to the cliffhanger)."""
    panels = comic["panels"]
    hp = int(comic.get("hook_panel") or 0)
    if 1 <= hp <= len(panels):
        return hp - 1
    for test in (lambda p: p.get("action") and p.get("sfx"), lambda p: p.get("action"), lambda p: p.get("sfx")):
        for i, p in enumerate(panels):
            if test(p):
                return i
    return max(0, len(panels) - 2)


def pick_hook_line(comic: dict, idx: int, words: int = 8) -> str:
    """The strongest short line for the opening frame: research's `hook_line`, else the panel's speech, sfx, last spoken line or narration."""
    p = comic["panels"][idx]
    lines = p.get("lines") or []
    text = comic.get("hook_line") or p.get("bubble") or p.get("sfx") or (lines[-1]["text"] if lines else "") or p.get("narration") or ""
    return trim_words(" ".join(text.split()), words)


def make_hook_overlay(path: Path, tag: str = "", line: str = "", label: str = "", recap: str = "") -> Path:
    """Opening-frame text: a small series/part tag pinned to the top safe edge, a big punchy line in the lower third (hook), or a
    'Previously…' label + recap line at the top (recap card). Layout independent (stays inside comic_layout.SAFE)."""
    S = comic_layout.SAFE
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if tag:
        f = _font(40, tag)
        tw = int(d.textlength(tag, font=f))
        x0 = S[0] + 10
        d.rounded_rectangle([x0, S[1] + 10, x0 + tw + 44, S[1] + 82], radius=18, fill=(0, 0, 0, 170))
        d.text((x0 + 22, S[1] + 18), tag, font=f, fill=(255, 214, 80, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255))
    if line:
        f, lines, lh = fit_text(d, line.upper(), S[2] - S[0] - 20, 420, 118, 62)
        y = int(H * 0.60)
        for l in lines:
            d.text((S[0] + (S[2] - S[0] - d.textlength(l, font=f)) / 2, y), l, font=f, fill=(255, 255, 255, 255), stroke_width=9, stroke_fill=(0, 0, 0, 255))
            y += lh
    if recap:
        fl = _font(52, label)
        d.text((S[0] + 10, S[1] + 40), label, font=fl, fill=(255, 214, 80, 255), stroke_width=5, stroke_fill=(0, 0, 0, 255))
        f, lines, lh = fit_text(d, recap, S[2] - S[0] - 20, 520, 76, 40)
        y = S[1] + 120
        for l in lines:
            d.text((S[0] + 10, y), l, font=f, fill=(255, 255, 255, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255))
            y += lh
    img.save(path)
    return path


def _zoompan(motion: str, frames: int) -> str:
    c = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return {
        "zoom_in": f"z='min(1.0+0.12*on/{frames},1.12)':{c}",
        "zoom_out": f"z='max(1.12-0.12*on/{frames},1.0)':{c}",
        "pan_left": f"z=1.12:x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'",
        "pan_right": f"z=1.12:x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'",
        "punch": f"z='if(lt(on,9),1.0+0.025*on,1.225+0.05*on/{frames})':{c}",
    }[motion]


def _run(cmd: list[str], timeout: int = 900) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + " | ".join(proc.stderr.strip().splitlines()[-4:]))


def render_clip(ffmpeg: str, image: Path, overlay: Path, seconds: float, motion: str, out: Path, dim: bool = False,
                layout: "comic_layout.Layout | None" = None, extra=()) -> Path:
    """Image + Ken Burns inside the art window only; dark bands elsewhere (text is drawn there by the overlay)."""
    L = layout or comic_layout.Layout()
    frames = max(2, round(seconds * FPS))
    vf = (f"[0:v]scale={L.canvas_w}:{L.canvas_h}:force_original_aspect_ratio=increase,crop={L.canvas_w}:{L.canvas_h},"
          f"zoompan={_zoompan(motion, frames)}:d={frames}:s={W}x{L.art_h}:fps={FPS},"
          f"noise=alls=7:allf=t,eq=saturation=1.08{',eq=brightness=-0.28' if dim else ''}[art];"
          f"color=c=0x15151f:s={W}x{H}:r={FPS}[bg];[bg][art]overlay=0:{L.art_y}:shortest=1[v];"
          f"[v][1:v]overlay=0:0[o0]")
    ins, last = [], "o0"
    for k, (png, a, b) in enumerate(extra):  # extra overlays (manga: tone / speed lines always on; bubbles only while spoken)
        ins += ["-loop", "1", "-i", str(png)]
        en = "" if a is None else f":enable='between(t,{a:.2f},{b:.2f})'"
        vf += f";[{last}][{k + 2}:v]overlay=0:0{en}[o{k + 1}]"
        last = f"o{k + 1}"
    vf += f";[{last}]format=yuv420p[o]"
    _run([ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(image), "-loop", "1", "-i", str(overlay), *ins, "-filter_complex", vf,
          "-map", "[o]", "-frames:v", str(frames), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(out)])
    return out


_DANGLING = {"a", "an", "the", "in", "of", "and", "to", "with", "as", "at", "on", "for", "but", "or", "by", "from", "into", "its", "his", "her", "their"}


def trim_words(text: str, n: int = MAX_NARRATION_WORDS) -> str:
    """Keep the narration short (reels stay ~30 s) without cutting it mid-phrase: a complete sentence up to n+1 words is kept;
    longer text is cut at the last sentence/clause end inside n words, else at n words minus any dangling word (a, the, in...)."""
    words = text.split()
    if len(words) <= n + 1:
        return text.strip()
    cut = " ".join(words[:n])
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(", "))
    if m > len(cut) // 2:
        return cut[:m + 1].rstrip(",;: ") + ("" if cut[m] in ".!?" else ".")
    ws = cut.split()
    while len(ws) > 3 and ws[-1].lower().strip(",.;:") in _DANGLING:
        ws.pop()
    out = " ".join(ws).rstrip(",;: ")
    return out + ("" if out.endswith((".", "!", "?")) else ".")


def has_drawn_bubble(path: Path) -> bool:
    """Cheap safety net (no OCR installed): a big near-white, rounded region with several small dark marks inside
    (letters) is a speech bubble the image model drew by itself."""
    import cv2
    import numpy as np

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    img = cv2.resize(img, (400, int(400 * img.shape[0] / img.shape[1])))
    area_total = img.shape[0] * img.shape[1]
    white = cv2.morphologyEx((img > 195).astype("uint8") * 255, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white, 8)
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if not (0.004 * area_total < w * h < 0.25 * area_total) or area / float(w * h) < 0.55:
            continue
        roi = img[y:y + h, x:x + w]
        mask = (labels[y:y + h, x:x + w] == k).astype("uint8")
        cnts, _ = cv2.findContours(mask * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, cnts, -1, 1, -1)  # the bubble shape with its letters filled in
        interior = cv2.erode(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))  # drop the border / outside corners
        dark = ((roi < 110) & (interior > 0)).astype("uint8")
        m, _, st, _ = cv2.connectedComponentsWithStats(dark, 8)
        marks = [s for s in st[1:] if 3 <= s[4] <= 0.02 * area_total and s[2] < w * 0.35]
        if len(marks) >= 4:
            return True
    return False


def _duration(path: Path) -> float:
    return float(_ffprobe(Path(path))["format"]["duration"])


def render_comic(ffmpeg: str, comic: dict, images: list[Path], out_mp4: Path, work: Path, voice: str = "en-US-AvaNeural",
                 music: bool = True, language: str = "English", layout_aware: bool = True, layout: dict | None = None,
                 cover: bool = True, end_card="default", music_text: str = "epic anime orchestral battle hero",
                 max_words: int | None = None, cover_label: str = "PART", voices: dict | None = None,
                 retention: dict | None = None) -> dict:
    """Panels (+images) -> final mp4. Returns {"seconds", "voice_notes", "music", "panel_times"}.
    cover=False drops the title card; end_card=None drops the end card, a (title, subtitle) tuple customises it."""
    work.mkdir(parents=True, exist_ok=True)
    panels, series = comic["panels"], comic["series"]
    part = series["part"]
    L = comic_layout.Layout.from_cfg(layout)
    clips: list[tuple[Path, float]] = []  # (clip, seconds)
    voices_t: list[tuple[Path | None, float]] = []  # (voice file, start seconds)
    notes, t, panel_times = [], 0.0, []

    def fitted(img, name):  # bands mode only: art-window-shaped crop (full-bleed uses the whole image)
        if not L.bands:
            return Path(img)
        try:
            return comic_layout.art_crop(Path(img), work / f"art_{name}.png", W, L.art_h)
        except Exception as exc:  # noqa: BLE001 - never block a render on the crop
            notes.append(f"art crop skipped for {name}: {str(exc)[:60]}")
            return Path(img)

    rc = retention_cfg(retention)
    intro = None
    if rc["hook_first"] and cover:  # retention opening: most dramatic panel + punch-zoom + small title tag, then (Part 2+) a one-line recap
        hi = pick_hook_panel(comic)
        hline = pick_hook_line(comic, hi)
        hs = min(float(rc["hook_s"]), 1.5)
        tag = f"{series['title']} · {cover_label} {part}"
        hov = make_hook_overlay(work / "ov_hook.png", tag=tag[:60], line=hline)
        clips.append((render_clip(ffmpeg, fitted(images[hi], "hook"), hov, hs, "punch" if rc["punch_zoom"] else "zoom_in", work / "clip_hook.mp4", layout=L), hs))
        voices_t.append((None, 0.0))
        t += hs - XF
        intro = {"hook_panel": hi + 1, "hook_line": hline, "hook_s": hs}
        prev = " ".join(str(comic.get("previously") or "").split())
        if rc["previously"] and part >= 2 and prev:
            rs = min(float(rc["previously_s"]), 1.2)
            rov = make_hook_overlay(work / "ov_recap.png", label="Previously…", recap=trim_words(prev, 16))
            clips.append((render_clip(ffmpeg, fitted(images[0], "recap"), rov, rs, "zoom_in", work / "clip_recap.mp4", dim=True, layout=L), rs))
            voices_t.append((None, 0.0))
            t += rs - XF
            intro["previously_s"] = rs
    elif cover:
        cover_clip = render_clip(ffmpeg, fitted(images[0], "cover"), make_overlay(work / "ov_cover.png", title=series["title"], subtitle=f"{cover_label} {part}",
                                                                                   narration=comic.get("cover_note", ""), layout=L),
                                 COVER_S, "zoom_in", work / "clip_cover.mp4", dim=True, layout=L)
        clips.append((cover_clip, COVER_S))
        voices_t.append((None, 0.0))
        t += COVER_S - XF
    for i, (p, img) in enumerate(zip(panels, images)):
        p = {**p, "narration": trim_words(p["narration"], max_words or MAX_NARRATION_WORDS)}
        mlines = p.get("lines") or []
        if mlines:  # manga: every line is spoken by its own character voice; panel time = lines + 0.4 s
            from aurora import manga

            la = manga.panel_audio(ffmpeg, mlines, work, i, voices or manga.DEFAULT_VOICES)
            notes += la["notes"]
            vfile, vdur, secs = None, 0.0, la["secs"]
        else:
            vfile, note = synthesize_voiceover(p["narration"], work / f"voice_{i}.wav",
                                               options={"voice_engine": "edge", "voice": voice, "language": "hi" if language.lower().startswith("hi") else "en"})
            notes.append(note)
            vdur = _duration(vfile) if vfile else 0.0
            secs = max((vdur if vfile else 3.0) + PAD, PANEL_MIN_S)
        motion = "punch" if p.get("action") else MOTIONS[i % len(MOTIONS)]
        sx, occ, head, head_zone = None, None, None, None
        faces_img, iw, ih = [], 0, 0
        if layout_aware and not L.bands:  # full-bleed: where are the characters over the whole move? (mask + faces)
            try:
                with Image.open(img) as im0:
                    iw, ih = im0.size
                faces_img = comic_layout.detect_faces(Path(img))
                occ, head_zone = comic_layout.occupancy_and_head(comic_layout.foreground_mask(Path(img)), faces_img, iw, ih, motion)
                head = comic_layout.head_point(occ, comic_layout.faces_over_motion(faces_img, iw, ih, motion) if faces_img else None, head_zone)
            except Exception as exc:  # noqa: BLE001 - placement is best effort; defaults still keep text in the safe zones
                notes.append(f"panel {i}: placement analysis skipped ({str(exc)[:60]})")
        if layout_aware and L.bands and p.get("bubble"):  # face detection only picks the bubble tail's side; if it misses -> centre
            try:
                with Image.open(img) as im0:
                    iw, ih = im0.size
                sx = comic_layout.speaker_x(comic_layout.detect_faces(Path(img)), iw, ih, W, L.art_h)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"panel {i}: face detection skipped ({str(exc)[:60]})")
        if mlines:
            from aurora import manga

            extra = [(manga.tone_png(work / f"tone_{i}.png", W, H, bool(p.get("action"))), None, None)]
            for k, it in enumerate(la["items"]):
                ln = it["line"]
                spk_head = manga.speaker_head(ln["speaker"], p, faces_img, iw, ih, motion, occ, head_zone) if ln["kind"] in ("speech", "shout", "whisper") else None
                boxed = ln["kind"] in ("verse", "caption")
                bpng = make_overlay(work / f"ov_{i}_{k}.png", narration=ln["text"] if boxed else "", bubble="" if boxed else ln["text"], border=False,
                                    layout=L, occ=occ, head=spk_head, head_zone=head_zone,
                                    bubble_style=ln["kind"] if ln["kind"] in ("shout", "whisper") else "speech", narr_style=ln["kind"])
                nxt = la["items"][k + 1]["start"] if k + 1 < len(la["items"]) else secs
                extra.append((bpng, it["start"], max(it["end"] + 0.3, min(nxt, it["end"] + 0.3))))
            ov = make_overlay(work / f"ov_{i}.png", layout=L, occ=occ, head=head, head_zone=head_zone)  # panel border only
            clips.append((render_clip(ffmpeg, fitted(img, str(i)), ov, secs, motion, work / f"clip_{i}.mp4", layout=L, extra=extra), secs))
            for it in la["items"]:
                if it["file"]:
                    voices_t.append((it["file"], t + it["start"]))
            first, last_ = la["items"][0], la["items"][-1]
            panel_times.append({"i": i, "start": round(t, 2), "voice_start": round(t + first["start"], 2), "voice_end": round(t + last_["end"], 2),
                                "end": round(t + secs - XF, 2), "narration": " ".join(it["line"]["text"] for it in la["items"]),
                                "lines": [{"speaker": it["line"]["speaker"], "kind": it["line"]["kind"], "start": round(t + it["start"], 2),
                                           "end": round(t + it["end"], 2), "text": it["line"]["text"]} for it in la["items"]]})
        else:
            ov = make_overlay(work / f"ov_{i}.png", narration=p["narration"], bubble=p.get("bubble", ""), speaker_x=sx, layout=L, occ=occ, head=head, head_zone=head_zone)
            clips.append((render_clip(ffmpeg, fitted(img, str(i)), ov, secs, motion, work / f"clip_{i}.mp4", layout=L), secs))
            voices_t.append((vfile, t + 0.05))
            panel_times.append({"i": i, "start": round(t, 2), "voice_start": round(t + 0.05, 2), "voice_end": round(t + 0.05 + vdur, 2),
                                "end": round(t + secs - XF, 2), "narration": p["narration"]})
        t += secs - XF
    final = part >= int(series.get("total_parts", 1))
    if end_card is not None:
        e_title, e_sub = end_card if isinstance(end_card, (tuple, list)) else (
            "The End" if final else "To be continued...", "" if final else f"Part {part + 1} coming soon")
        end_ov = make_overlay(work / "ov_end.png", title=e_title, subtitle=e_sub, layout=L)
        clips.append((render_clip(ffmpeg, fitted(images[-1], "end"), end_ov, END_S, "zoom_out", work / "clip_end.mp4", dim=True, layout=L), END_S))
    total = sum(s for _, s in clips) - XF * (len(clips) - 1)

    # video: xfade chain; audio: per-panel voices placed on the timeline, music ducked under them
    voice_track = None
    vlist = [(f, st) for f, st in voices_t if f]
    if vlist:
        ins, parts = [], []
        for k, (f, st) in enumerate(vlist):
            ins += ["-i", str(f)]
            parts.append(f"[{k}:a]aresample=44100,adelay={int(st * 1000)}|{int(st * 1000)}[a{k}]")
        graph = ";".join(parts) + f";{''.join(f'[a{k}]' for k in range(len(vlist)))}amix=inputs={len(vlist)}:normalize=0:duration=longest[a]"
        voice_track = work / "voice_track.wav"
        _run([ffmpeg, "-y", "-v", "error", *ins, "-filter_complex", graph, "-map", "[a]", "-t", f"{total:.2f}", str(voice_track)])
    music_wav = None
    if music:
        try:
            music_wav = generate_music(music_text, min(total, 30), work / "music.wav")
        except Exception as exc:  # noqa: BLE001 - music is optional
            notes.append(f"music skipped: {str(exc)[:80]}")
    audio = None
    if voice_track or music_wav:
        audio = mix_audio(ffmpeg, work / "audio_mix.wav", total, music_wav, voice_track)

    inputs, fchain, last, offset = [], [], "[0:v]", 0.0
    for k, (c, _) in enumerate(clips):
        inputs += ["-i", str(c)]
    for k in range(1, len(clips)):
        offset += clips[k - 1][1] - XF
        tr = TRANSITIONS[(k - 1) % len(TRANSITIONS)]
        fchain.append(f"{last}[{k}:v]xfade=transition={tr}:duration={XF}:offset={offset:.3f}[x{k}]")
        last = f"[x{k}]"
    cmd = [ffmpeg, "-y", "-v", "error", *inputs]
    if audio:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-filter_complex", ";".join(fchain), "-map", last, "-map", f"{len(clips)}:a", "-t", f"{total:.2f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-af", "apad", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-shortest", str(out_mp4)]
    _run(cmd, timeout=1800)
    return {"seconds": round(total, 2), "voice_notes": notes, "music": bool(music_wav), "panel_times": panel_times, **({"intro": intro} if intro else {})}


def _comfy_up(cfg: dict) -> bool:
    import requests

    try:
        url = cfg.get("models", {}).get("comfyui", {}).get("api_url", "http://127.0.0.1:8188")
        for path in ("/system_stats", "/queue"):  # newer builds expose system_stats; older ones only /queue
            try:
                if requests.get(f"{url}{path}", timeout=3).ok:
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False
    except Exception:  # noqa: BLE001
        return False


def _negative(cfg: dict) -> str:
    base = cfg.get("providers", {}).get("local_comfyui", {}).get("negative_prompt", "lowres, bad anatomy, bad hands, extra fingers, deformed")
    return f"{base}, {NO_TEXT_NEG}"  # the image model must not draw its own bubbles/gibberish text


_SOFTEN = [(r"\b(blood\w*|gore|gory)\b", "crimson light"), (r"\b(kill\w*|murder\w*|stab\w*|impal\w*|decapitat\w*|slaughter\w*)\b", "defeat"),
           (r"\b(slash\w*|slice\w*|cut)\b", "swing at"), (r"\b(wound\w*|injur\w*|bleed\w*)\b", "weary"),
           (r"\b(corpse\w*|dead|death|dying|die)\b", "fallen"), (r"\b(scream\w*|agony|torture\w*)\b", "shout"),
           (r"\b(gun|rifle|shoot\w*)\b", "energy beam"), (r"\b(sexy|nude|naked|seductive)\b", "elegant")]


def soften_prompt(prompt: str, level: int = 1) -> str:
    """Rewrite violent/risky wording so NIM's safety filter accepts the panel (level 2 adds a family-friendly suffix)."""
    for pat, repl in _SOFTEN:
        prompt = re.sub(pat, repl, prompt, flags=re.I)
    extra = ", wholesome, safe for all ages, dramatic heroic pose, no violence" if level >= 2 else ", non-violent dramatic action"
    return research._cut(prompt, 795 - len(extra)) + extra  # NIM rejects prompts over 800 characters


def _comfy_panels(cfg, sheet, panels, first, seed, job, neg, ref_path=None):
    """ComfyUI panels [first:] conditioned on ONE reference image via IP-Adapter. Returns (images, timings, ref_path)."""
    import time as _t

    from backend.app.providers.local_comfyui import LocalComfyUIProvider

    ccfg = cfg.get("comic", {})
    prov, steps = LocalComfyUIProvider(cfg), int(ccfg.get("steps", 20))
    res = list(ccfg.get("comfy_resolution", [512, 896]))
    floor, mult = int(ccfg.get("comfy_timeout_sec", 1800)), float(ccfg.get("timeout_multiplier", 3))
    weight = float(cfg.get("providers", {}).get("local_comfyui", {}).get("reference_weight", 0.4))
    images, timings = [], []
    if ref_path is None:
        t0 = _t.time()
        ref_prompt = (f"{research.COMIC_STYLE}, {sheet[0]['name']}: {sheet[0]['description']}, upper body, looking at viewer, "
                      "simple plain background, character reference, vibrant colors, clean lineart")
        ref = prov.generate_with_params(ref_prompt, "anime", res, f"{job}_ref", negative_prompt=neg, seed=seed, steps=steps,
                                        timeout=max(floor, 3600))  # first image: no measurement yet -> generous
        ref_path = ref.output_path
        timings.append(round(_t.time() - t0, 1))
    for i in range(first, len(panels)):
        t0 = _t.time()
        limit = int(max(floor, mult * sorted(timings)[len(timings) // 2])) if timings else max(floor, 3600)
        r = prov.generate_with_reference(ref_path, research.panel_prompt(panels[i], sheet), "anime", res, f"{job}_p{i}",
                                         negative_prompt=neg, seed=seed, steps=steps, weight=weight, timeout=limit)
        images.append(Path(r.output_path))
        timings.append(round(_t.time() - t0, 1))
    return images, timings, ref_path


def _panel_images(cfg, switchboard, comic, seed, job, nim_res, image_mode=None, cast_ref=None):
    """Panel images. image_mode (config comic.image_mode): "nim" (default: Flux via NIM; ComfyUI+IP-Adapter only if NIM
    fails/is capped), "auto"/"comfy" (ComfyUI first; "comfy" never touches NIM). Every fallback is explicit and logged."""
    import time as _t

    sheet, panels = comic["character_sheet"], comic["panels"]
    mode = image_mode or cfg.get("comic", {}).get("image_mode", "nim")
    neg, cost_dir = _negative(cfg), Path(cfg.get("cost_dir", COST_DIR))
    cap_reel = int(cfg.get("comic", {}).get("max_nim_credits_per_reel", 8))
    cap_day = int(os.environ.get("AURORA_NIM_DAILY_CAP") or cfg.get("providers", {}).get("nvidia_nim", {}).get("max_credits_per_day", 10))  # env = explicit one-off override

    retries_left = [2]

    def nim_panels(images, timings, start=0):  # fills the lists in place so finished panels survive a mid-run failure
        saved = getattr(switchboard, "fallback_order", None)
        if saved is not None:
            switchboard.fallback_order = ["nvidia_nim"]  # NIM only here: the ComfyUI fallback below uses the reference
        try:
            for i in range(start, len(panels)):
                if len(images) >= cap_reel:
                    raise RuntimeError(f"comic.max_nim_credits_per_reel={cap_reel} reached")
                if credits_today("nvidia_nim", cost_dir) >= cap_day:
                    raise RuntimeError(f"daily NIM cap {cap_day} credits reached")
                t0 = _t.time()
                prompt = research.panel_prompt(panels[i], sheet)
                for attempt in range(2):  # CONTENT_FILTERED -> at most ONE retry (softened wording, new seed); each call may cost a credit
                    try:
                        # NIM's filter judges the OUTPUT image: a fixed seed reproduces the same blocked image, so retries get a new seed
                        r = switchboard.generate(prompt, "anime", list(nim_res), f"{job}_p{i}_{attempt}" if attempt else f"{job}_p{i}",
                                                 negative_prompt=neg, seed=seed + attempt * 7919)
                    except Exception as exc:  # noqa: BLE001
                        log_credits("nvidia_nim", 1, cost_dir)
                        if "CONTENT_FILTERED" in str(exc) and attempt < 1:
                            prompt = soften_prompt(prompt, attempt + 1)
                            print(f"[comic] panel {i}: NIM safety filter, retrying with softened prompt (attempt {attempt + 2}); "
                                  f"prompt was: {prompt[:300]}")
                            continue
                        raise
                    break
                if getattr(r, "provider", "") == "nvidia_nim" and getattr(r, "latency_ms", 1):  # cache hits cost nothing
                    log_credits("nvidia_nim", 1, cost_dir)
                    try:
                        drawn = has_drawn_bubble(Path(r.output_path))
                    except Exception:  # noqa: BLE001 - the check is a best-effort net
                        drawn = False
                    if drawn and retries_left[0] > 0:  # at most 2 extra (counted) regenerations per reel
                        retries_left[0] -= 1
                        print(f"[comic] panel {i}: image-drawn speech bubble detected, regenerating once with a new seed")
                        r2 = switchboard.generate(research._cut(prompt, 795 - 70) + ", wordless illustration, clean art, no speech balloons, no lettering",
                                                  "anime", list(nim_res), f"{job}_p{i}_retry", negative_prompt=neg, seed=seed + 1000 + i)
                        log_credits("nvidia_nim", 1, cost_dir)
                        r = r2
                images.append(Path(r.output_path))
                timings.append(round(_t.time() - t0, 1))
        finally:
            if saved is not None:
                switchboard.fallback_order = saved

    if mode == "nim":
        images, timings = [], []
        try:
            nim_panels(images, timings)
            return images, timings, "NIM (Flux), consistent via fixed seed + character sheet text", "nim"
        except Exception as exc:  # noqa: BLE001 - quota/credit/API failure -> ComfyUI for the REMAINING panels
            why = f"NIM failed after {len(images)}/{len(panels)} panels ({str(exc)[:120]})"
            if not cfg.get("comic", {}).get("nim_comfy_fallback", False):  # default: nim mode NEVER touches ComfyUI
                raise RuntimeError(f"{why}; ComfyUI fallback is OFF (comic.nim_comfy_fallback: false), so the reel stops here "
                                   "(text-only locked cast on NIM; retry later or after the NIM cap resets)") from exc
            if not _comfy_up(cfg):  # optional enhancement: preflight, never crash on a dead server
                print(f"[comic] WARNING: {why}; ComfyUI fallback is on but ComfyUI is not reachable (system_stats) -> no fallback possible")
                raise RuntimeError(f"{why}; ComfyUI fallback is on but ComfyUI is not reachable") from exc
            print(f"[comic] FALLBACK to ComfyUI IP-Adapter: {why}")
            ref = cast_ref or (images[0] if images else None)  # the locked hero reference beats a panel
            more, t2, _ = _comfy_panels(cfg, sheet, panels, len(images), seed, job, neg, ref_path=ref)
            return (images + more, timings + t2, f"{why}; remaining panels via ComfyUI character reference + IP-Adapter "
                    "(look may differ from the NIM panels)", "nim+comfy_fallback")
    if mode in ("auto", "comfy") and _comfy_up(cfg):
        try:
            images, timings, _ = _comfy_panels(cfg, sheet, panels, 0, seed, job, neg, ref_path=cast_ref)
            return images, timings, "ComfyUI character reference + IP-Adapter (consistent look)", "comfy_ipadapter"
        except Exception as exc:  # noqa: BLE001
            if mode == "comfy":
                raise
            note = f"ComfyUI failed ({str(exc)[:80]}); "
    elif mode == "comfy":
        raise RuntimeError("image_mode=comfy but ComfyUI is not reachable")
    else:
        note = ""
    # auto mode: ComfyUI unavailable -> NIM, explicit and capped
    need = len(panels)
    if need > cap_reel or credits_today("nvidia_nim", cost_dir) + need > cap_day:
        raise RuntimeError(f"{note}NIM fallback would use {need} credits (reel cap {cap_reel}, daily cap {cap_day}, used today "
                           f"{credits_today('nvidia_nim', cost_dir)}); start ComfyUI or raise the cap")
    print(f"[comic] FALLBACK to NIM for {need} panels ({note.strip() or 'ComfyUI not reachable'})")
    images, timings = [], []
    nim_panels(images, timings)
    return (images, timings, f"{note or 'ComfyUI not reachable; '}NIM fallback used {need} credits: character consistency may drop "
            "between panels", "nim_fallback")


def make_comic_reel(channel: str, premise: str, out_dir: Path, cfg: dict | None = None, language: str = "English",
                    panels: int = 6, voice: str = "en-US-AvaNeural", comic: dict | None = None, switchboard=None,
                    music: bool = True, series_dir: Path | None = None, resolution: tuple[int, int] | None = None,
                    client=None, image_mode: str | None = None, chapter: dict | None = None, images: list | None = None, record: bool = True) -> dict:
    """Research (series-aware) -> panel images (NIM -> local ComfyUI anime) -> render -> validate -> sidecar -> save series state.

    Returns {"video", "sidecar", "title", "caption", "part", "source", "seconds"}. Raises on validation failure."""
    import yaml

    from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

    cfg = cfg if cfg is not None else yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    sdir = Path(series_dir) if series_dir else research.SERIES_DIR
    source = "given"
    if comic is None and cfg.get("comic", {}).get("chapter_parts"):  # Series -> Chapter N -> Part M (>= 10 parts per chapter)
        res = research.research_part(channel, premise, language, panels, cfg=cfg, client=client, series_dir=sdir)
        comic, source, chapter = res["comic"], res["source"], res["chapter"]
    elif comic is None:
        res = research.research_comic(channel, premise, language, panels, cfg=cfg, client=client, series_dir=sdir)
        comic, source = res["comic"], res["source"]
    state = research.load_series(channel, sdir)
    seed = int(state.get("seed") or abs(hash(channel)) % 2**31 or 7)  # fixed per series -> consistent look
    if switchboard is None:
        from backend.app.services.image_switchboard import ImageSwitchboard

        switchboard = ImageSwitchboard(cfg)
    part = comic["series"]["part"]
    ptag = f"ch{chapter['number']}-part{part}" if chapter else f"part{part}"
    slug = re.sub(r"[^a-z0-9]+", "-", f"{channel}-{ptag}-{comic['series']['title']}".lower()).strip("-")[:60]
    job = f"comic_{datetime.now():%Y%m%d_%H%M%S}_{slug[:24]}"
    resolution = tuple(resolution or cfg.get("comic", {}).get("nim_resolution", [1024, 1088]))  # ~ the art window's shape
    from aurora import cast as cast_mod

    lock = bool(cfg.get("comic", {}).get("cast_lock", False))
    if images:  # re-render from cached panels: no image generation, no NIM credits
        images, timings, img_note, mode_used = [Path(p) for p in images], [], "re-rendered from cached panels (0 credits)", "cached"
    else:
        images, timings, img_note, mode_used = _panel_images(cfg, switchboard, comic, seed, job, resolution, image_mode,
                                                             cast_mod.reference_image(channel) if lock else None)
    out_dir = Path(out_dir) / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{slug}.mp4"
    ffmpeg = FFmpegKenBurns().ffmpeg
    extra = {}
    if chapter:  # end cards "Part M+1 tomorrow" / "Chapter N finale"; cover "CH.N PART M"
        extra = {"end_card": tuple(comic["end_card"]) if comic.get("end_card") else research.end_card_for(chapter["number"], chapter["part"], chapter["parts"]),
                 "cover_label": f"CH.{chapter['number']} PART"}
    if cfg.get("comic", {}).get("voices"):  # manga: per-character voices
        extra["voices"] = cfg["comic"]["voices"]
    if cfg.get("comic", {}).get("retention"):  # series-pack `retention:` (hook-first opening, Previously… card)
        extra["retention"] = cfg["comic"]["retention"]
    info = render_comic(ffmpeg, comic, images, mp4, ROOT / "outputs" / "comic_work" / slug, voice, music, language,
                        layout=cfg.get("comic", {}).get("layout"), **extra)
    rep = validate_video(mp4, W, H, fps=FPS, require_audio=True, deep=True)
    if not rep.ok:
        raise RuntimeError(f"comic reel failed validation: {rep.errors}")
    title = (research.part_title(comic["series"]["title"], chapter["number"], part, comic.get("hook", "")) if chapter
             else f"{comic['series']['title']} - Part {part}")
    caption = f"{comic['cliffhanger'] or comic['series']['recap']} {comic['cta']}".strip()
    side = {"status": "ok", "format": "comic", "channel": channel, "title": title, "part": part, "caption": caption,
            "source": source, "seed": seed, "series": comic["series"], "image_mode": mode_used, "image_note": img_note,
            "image_timings_secs": timings, "character_sheet": comic["character_sheet"],
            "panels": comic["panels"], "audio": info, "reel": {"path": str(mp4), "width": rep.width, "height": rep.height,
            "fps": rep.fps, "duration_secs": rep.duration_secs, "has_audio": rep.has_audio, "md5": rep.md5, "sha256": rep.sha256}}
    if lock:  # characters that debuted in this reel join the permanent cast
        side["cast"] = {"locked": [m["name"] for m in cast_mod.load_cast(channel)], "added": cast_mod.save_new_members(channel, comic, images)}
    if chapter:
        side.update(chapter=chapter, playlist=comic.get("playlist") or comic["series"]["title"], hook=comic.get("hook", ""), panel_times=info["panel_times"],
                    panel_images=[str(p) for p in images], cover_s=COVER_S,
                    hook_title=comic.get("hook_title", ""), engagement_question=comic.get("engagement_question", ""), intro=info.get("intro"))
    sidecar = mp4.with_suffix(".json")
    sidecar.write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
    compiled = None
    if chapter and record:
        research.record_part(channel, comic, chapter, {"part": part, "video": str(mp4), "sidecar": str(sidecar), "title": title}, sdir)
        if chapter["final"]:  # the chapter is complete: build the 16:9 compilation from the cached parts (0 NIM credits)
            try:
                from aurora import chapter as chapter_mod

                compiled = chapter_mod.compile_chapter(channel, chapter["number"], cfg, series_dir=sdir)
            except Exception as exc:  # noqa: BLE001 - never fail the part; retry with `python -m aurora.chapter compile`
                print(f"[comic] WARNING: chapter compilation failed ({str(exc)[:160]}); retry: python -m aurora.chapter compile --channel {channel}")
                compiled = {"error": str(exc)[:300]}
            side["chapter_compilation"] = compiled
            sidecar.write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
    elif not chapter:
        research.save_series(channel, comic, sdir)
    return {"video": str(mp4), "sidecar": str(sidecar), "title": title, "caption": caption, "part": part,
            "source": source, "seconds": info["seconds"], **({"chapter": chapter} if chapter else {})}
