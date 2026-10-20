"""Output validation: real dimensions/fps/duration, not a known mock placeholder."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# MD5s of the known fake "success" MP4s (see docs/TEST_PLAN.md V9).
KNOWN_MOCK_MD5 = frozenset({
    "9a75c418be5f8b5a2735d437737461df",
    "8bf5672dfac1e2b2b4acba88861d21f1",
})


@dataclass
class MediaReport:
    ok: bool
    path: str
    kind: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration_secs: float = 0.0
    has_audio: bool = False
    md5: str = ""
    sha256: str = ""
    errors: list[str] = field(default_factory=list)


def _digests(path: Path) -> tuple[str, str]:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def _md5(path: Path) -> str:
    return _digests(path)[0]


def default_ffprobe() -> str:
    """ffprobe on PATH, else next to the bundled ffmpeg (like server._ffprobe_path)."""
    found = shutil.which("ffprobe")
    if found:
        return found
    cand = Path("C:/Rupesh/App/ffmpeg/bin/ffprobe.exe")
    return str(cand) if cand.exists() else "ffprobe"


def _ffprobe(path: Path, ffprobe: str | None = None) -> dict:
    exe = ffprobe or default_ffprobe()
    proc = subprocess.run(
        [exe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise ValueError(proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "ffprobe failed")
    return json.loads(proc.stdout)


def _rate(text: str) -> float:
    num, _, den = text.partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def validate_image(path: Path | str, width: int | None = None, height: int | None = None) -> MediaReport:
    from PIL import Image

    p = Path(path)
    rep = MediaReport(ok=False, path=str(p), kind="image")
    if not p.is_file() or p.stat().st_size == 0:
        rep.errors.append("missing or empty file")
        return rep
    rep.md5, rep.sha256 = _digests(p)
    try:
        with Image.open(p) as img:
            img.verify()
        with Image.open(p) as img:
            rep.width, rep.height = img.size
    except Exception as exc:  # noqa: BLE001 - any decode error means invalid
        rep.errors.append(f"not a valid image: {exc}")
        return rep
    if width and rep.width != width:
        rep.errors.append(f"width {rep.width} != {width}")
    if height and rep.height != height:
        rep.errors.append(f"height {rep.height} != {height}")
    rep.ok = not rep.errors
    return rep


def validate_video(
    path: Path | str,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    duration_secs: float | None = None,
    duration_tol: float = 0.15,
    deep: bool = False,
    require_audio: bool = False,
    ffprobe: str | None = None,
    input_path: Path | str | None = None,
) -> MediaReport:
    p = Path(path)
    rep = MediaReport(ok=False, path=str(p), kind="video")
    if not p.is_file() or p.stat().st_size == 0:
        rep.errors.append("missing or empty file")
        return rep
    rep.md5, rep.sha256 = _digests(p)
    if rep.md5 in KNOWN_MOCK_MD5:
        rep.errors.append("known mock placeholder (MD5 match)")
    if input_path and Path(input_path).is_file() and _digests(Path(input_path))[1] == rep.sha256:
        rep.errors.append("output is identical to the input file")
    try:
        info = _ffprobe(p, ffprobe)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        rep.errors.append(f"not a valid video: {exc}")
        return rep
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        rep.errors.append("no video stream")
        return rep
    rep.width, rep.height = int(video.get("width", 0)), int(video.get("height", 0))
    rep.fps = _rate(video.get("avg_frame_rate", "0/1"))
    rep.duration_secs = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    rep.has_audio = any(s.get("codec_type") == "audio" for s in streams)
    if width and rep.width != width:
        rep.errors.append(f"width {rep.width} != {width}")
    if height and rep.height != height:
        rep.errors.append(f"height {rep.height} != {height}")
    if fps and abs(rep.fps - fps) > 0.1:
        rep.errors.append(f"fps {rep.fps:.2f} != {fps}")
    if duration_secs and abs(rep.duration_secs - duration_secs) > duration_tol:
        rep.errors.append(f"duration {rep.duration_secs:.2f}s != {duration_secs}s")
    if rep.duration_secs <= 0:
        rep.errors.append("zero duration")
    if require_audio and not rep.has_audio:
        rep.errors.append("no audio stream")
    if deep:  # full decode catches truncated/corrupt files that ffprobe alone accepts
        proc = subprocess.run(
            [shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(p), "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0 or proc.stderr.strip():
            rep.errors.append(f"decode errors: {proc.stderr.strip()[:200]}")
    rep.ok = not rep.errors
    return rep
