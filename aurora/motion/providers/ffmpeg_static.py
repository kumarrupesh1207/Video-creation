from __future__ import annotations

import subprocess
from pathlib import Path

from aurora.motion.base import MotionProvider, MotionResult


class FFmpegStaticMotionProvider(MotionProvider):
    UPSCALE_FILTER = (
        "scale=1080:1920:flags=lanczos:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    )
    ZOOMPAN_FILTER = (
        f"{UPSCALE_FILTER},"
        "unsharp=3:3:1.5:3:3:0.5,"
        "noise=alls=7:allf=t:all_seed=123,"
        "zoompan="
        "z='zoom+0.0008':"
        "x='iw/2-(iw/zoom/2)':"
        "y='ih/2-(ih/zoom/2)':"
        "d=125:"
        "s=1080x1920:"
        "fps=25"
    )
    SIDE_DRIFT_FILTER = (
        "crop="
        "iw/1.1:"
        "ih/1.1:"
        "x='(iw-ow)/2+(iw-ow)/2*sin(t/2)':"
        "y='(ih-oh)/2',"
        f"{UPSCALE_FILTER},"
        "unsharp=3:3:1.5:3:3:0.5,"
        "noise=alls=7:allf=t:all_seed=123"
    )

    def __init__(self, config: dict):
        super().__init__(config)
        self.ffmpeg = config.get("paths", {}).get("ffmpeg_bin", "ffmpeg")
        self.out_dir = Path(config.get("paths", {}).get("videos_dir", "outputs/videos"))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._verify_ffmpeg()

    def generate(
        self,
        image_path: Path,
        duration: int = 5,
        caption_text: str = "",
        template_name: str = "ken_burns",
        job_id: str = "",
        asset_key: str = "",
        visual_complexity: str = "standard",
        audio_narration_path: Path | None = None,
        generation_seed: int | None = None,
        reference_image_path: Path | None = None,
        identity_lock: bool = False,
    ) -> MotionResult:
        del job_id, asset_key
        del visual_complexity, audio_narration_path, generation_seed, reference_image_path, identity_lock
        del caption_text
        filter_chain = self.ZOOMPAN_FILTER if template_name == "ken_burns" else self.SIDE_DRIFT_FILTER
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"[MotionEngine] Image not found: {image_path}")
        out_path = self.out_dir / f"{image_path.stem}_{template_name}.mp4"
        cmd = [
            self.ffmpeg, "-y",
            "-loop", "1",
            "-i", str(image_path),
            "-vf", filter_chain,
            "-t", str(duration),
            "-r", "25",
            "-c:v", "libx264",
            "-preset", "slow",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out_path),
        ]
        print(f"[MotionEngine] Template: {template_name}")
        print(f"[MotionEngine] Rendering cinematic motion → {out_path.name}")
        self._run(cmd)
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"[MotionEngine] ✅ Motion clip ready → {out_path} ({size_mb:.1f} MB)")
        return MotionResult(
            provider="ffmpeg_static",
            output_path=out_path,
            motion_mode="static_cinematic",
        )

    def _verify_ffmpeg(self) -> None:
        for bin_path in [self.ffmpeg, "ffmpeg"]:
            try:
                result = subprocess.run(
                    [bin_path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    self.ffmpeg = bin_path
                    return
            except FileNotFoundError:
                continue
        raise RuntimeError(
            "[MotionEngine] ❌ FFmpeg not found.\n"
            "  Install FFmpeg and update paths.ffmpeg_bin in config.yaml."
        )

    def _run(self, cmd: list[str]) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            err_lines = [line for line in result.stderr.splitlines() if line.strip()]
            summary = "\n".join(err_lines[-10:])
            raise RuntimeError(f"[MotionEngine] ❌ FFmpeg motion render failed:\n{summary}")
