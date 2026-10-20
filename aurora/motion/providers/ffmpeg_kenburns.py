from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from aurora.logs import StructuredLogger
from aurora.motion.base import ExecutionTier, MotionMode, MotionProvider, MotionResult, VideoResult


def audio_args(audio_path: Path | str | None) -> tuple[list[str], list[str]]:
    """Return (input args, output codec args): the given audio, or a silent stereo track."""
    if audio_path:
        inputs = ["-i", str(audio_path)]
    else:
        inputs = ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    return inputs, ["-af", "apad", "-c:a", "aac", "-b:a", "128k", "-shortest"]


class FFmpegKenBurns(MotionProvider):
    """CPU-only Ken Burns motion provider implemented with FFmpeg."""

    name = "ffmpeg_kenburns"
    mode = MotionMode.STATIC_CINEMATIC
    tier = ExecutionTier.LOCAL
    priority = 1

    def __init__(self, config: dict | None = None, logger: StructuredLogger | None = None):
        super().__init__(config or {})
        paths = self.config.get("paths", {}) if self.config else {}
        configured_ffmpeg = paths.get("ffmpeg_bin", "ffmpeg")
        bundled_ffmpeg = Path("C:/Rupesh/App/ffmpeg/bin/ffmpeg.exe")
        self.ffmpeg = configured_ffmpeg
        if not shutil.which(configured_ffmpeg) and not Path(configured_ffmpeg).exists():
            self.ffmpeg = shutil.which("ffmpeg") or (str(bundled_ffmpeg) if bundled_ffmpeg.exists() else configured_ffmpeg)
        self.logger = logger or StructuredLogger(component=self.name)

    def is_available(self) -> bool:
        """Return True when FFmpeg can be found on PATH or at the configured path."""
        return shutil.which(self.ffmpeg) is not None or Path(self.ffmpeg).exists()

    def estimated_cost_usd(self, duration_secs: float) -> float:
        """Estimate local FFmpeg generation cost."""
        del duration_secs
        return 0.0

    def estimated_time_secs(self, duration_secs: float) -> float:
        """Estimate local FFmpeg generation time."""
        return max(5.0, duration_secs * 1.5)

    def generate(
        self,
        image_path: Path,
        output_path: Path | None = None,
        duration_secs: float = 25.0,
        fps: int = 24,
        job_id: str | None = None,
        width: int = 1080,
        height: int = 1920,
        audio_path: Path | str | None = None,
        **kwargs: object,
    ) -> VideoResult | MotionResult:
        """Render a width x height (default 1080x1920) MP4 with a 1.0 to 1.08 Ken Burns zoom and an audio track."""
        legacy_call = output_path is None and "duration" in kwargs
        if legacy_call:
            duration_secs = float(kwargs.get("duration", duration_secs))
            output_dir = Path(self.config.get("paths", {}).get("videos_dir", "outputs/videos"))
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{Path(image_path).stem}_ken_burns.mp4"
        if output_path is None:
            raise ValueError("output_path is required")

        job_id = job_id or str(uuid.uuid4())
        image_path = Path(image_path)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image_hash = self._image_hash(image_path)
        frame_count = max(1, int(duration_secs * fps))
        zoom_denominator = max(1, frame_count - 1)
        filter_chain = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            "setsar=1,"
            f"zoompan=z='min(1.0+0.08*on/{zoom_denominator},1.08)':"
            "x='iw/2-(iw/zoom/2)':"
            "y='ih/2-(ih/zoom/2)':"
            f"d={frame_count}:s={width}x{height}:fps={fps},"
            "format=yuv420p"
        )
        self.logger.info(
            "provider_selected",
            job_id=job_id,
            provider=self.name,
            mode=self.mode.value,
            tier=self.tier.value,
            image_hash=image_hash,
            duration_secs=duration_secs,
        )
        started = time.perf_counter()
        audio_in, audio_out = audio_args(audio_path)
        cmd = [
            self.ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            *audio_in,
            "-vf",
            filter_chain,
            "-frames:v",
            str(frame_count),
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            *audio_out,
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            result.check_returncode()
        except (subprocess.SubprocessError, OSError) as exc:
            gen_time_secs = time.perf_counter() - started
            error = str(exc)
            self.logger.error(
                "generation_failed",
                job_id=job_id,
                provider=self.name,
                mode=self.mode.value,
                tier=self.tier.value,
                image_hash=image_hash,
                duration_secs=duration_secs,
                gen_time_secs=gen_time_secs,
                error=error,
            )
            raise RuntimeError(f"FFmpeg Ken Burns generation failed: {error}") from exc

        gen_time_secs = time.perf_counter() - started
        self.logger.info(
            "generation_success",
            job_id=job_id,
            provider=self.name,
            mode=self.mode.value,
            tier=self.tier.value,
            image_hash=image_hash,
            duration_secs=duration_secs,
            gen_time_secs=gen_time_secs,
            cost_usd=0.0,
        )
        if legacy_call:
            return MotionResult(provider=self.name, output_path=output_path, motion_mode=self.mode.value)
        return VideoResult(
            output_path=output_path,
            provider=self.name,
            mode=self.mode,
            tier=self.tier,
            job_id=job_id,
            image_hash=image_hash,
            duration_secs=duration_secs,
            gen_time_secs=gen_time_secs,
            cost_usd=0.0,
            fps=fps,
            width=width,
            height=height,
        )

    def _image_hash(self, image_path: Path) -> str:
        digest = hashlib.sha256()
        with image_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()[:12]
