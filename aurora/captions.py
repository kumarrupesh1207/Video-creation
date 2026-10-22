"""Burned-in captions for 9:16 reels: short phrases, timed evenly, bold white text with a black outline (ASS via libass)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

FONT = "Nirmala UI"  # Windows font with Latin + Devanagari glyphs (C:/Windows/Fonts/Nirmala.ttf, NirmalaB.ttf bold)
FONTS_DIR = "C:/Windows/Fonts"


def split_phrases(text: str, max_words: int = 6) -> list[str]:
    """Split on sentence/clause punctuation (incl. Devanagari danda), then into chunks of <= max_words words."""
    phrases: list[str] = []
    for clause in re.split(r"(?<=[.!?।,;:])\s+|\n+", text.strip()):
        words = clause.split()
        if not words:
            continue
        n = -(-len(words) // max_words)  # balanced chunks, e.g. 7 words -> 4+3 not 6+1
        size = -(-len(words) // n)
        phrases += [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    return phrases


def _ts(sec: float) -> str:
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def build_ass(text: str, duration: float, width: int, height: int, out: Path, start: float = 0.0) -> Path | None:
    """Write an ASS file: phrases share `duration` seconds in proportion to their word count. None if no text."""
    phrases = split_phrases(text)
    if not phrases:
        return None
    total_words = sum(len(p.split()) for p in phrases)
    size = round(height * 0.042)  # ~80 px on 1920
    margin_v = round(height * 0.17)  # lower third, above the platform UI
    margin_h = round(width * 0.08)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding",
        f"Style: Cap,{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(3, size // 18)},1,2,{margin_h},{margin_h},{margin_v},1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    t = start
    for phrase in phrases:
        d = duration * len(phrase.split()) / total_words
        lines.append(f"Dialogue: 0,{_ts(t)},{_ts(t + d)},Cap,,0,0,0,,{phrase.replace(chr(10), ' ')}")
        t += d
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _esc(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def burn_captions(ffmpeg: str, video: Path, text: str, duration: float, width: int, height: int,
                  caption_seconds: float | None = None) -> Path:
    """Re-encode `video` in place with captions (audio stream copied). Returns the path; no-op if no text."""
    video = Path(video)
    ass = build_ass(text, min(caption_seconds or duration, duration), width, height, video.with_suffix(".ass"))
    if ass is None:
        return video
    tmp = video.with_suffix(".cap.mp4")
    vf = f"ass=filename='{_esc(str(ass.resolve()))}':fontsdir='{_esc(FONTS_DIR)}'"
    proc = subprocess.run(
        [ffmpeg, "-y", "-i", str(video), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(tmp)],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError("caption burn failed: " + "\n".join(proc.stderr.splitlines()[-4:]))
    tmp.replace(video)
    ass.unlink(missing_ok=True)
    return video
