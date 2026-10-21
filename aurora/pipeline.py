from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence

if not __package__:  # `python aurora/pipeline.py ...` puts aurora/ (not the repo root) on sys.path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora import ensure_utf8_console, load_env
from aurora.logs import StructuredLogger
from aurora.monitoring.media_validator import validate_video
from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns


def run(
    image_path: Path,
    output_path: Path,
    duration_secs: float = 25.0,
    fps: int = 24,
    width: int = 1080,
    height: int = 1920,
    audio_path: Path | None = None,
    motion: str = "camera",
    config: dict | None = None,
) -> Path:
    """Run the Sprint 1 static cinematic pipeline."""
    logger = StructuredLogger(component="pipeline")
    provider = FFmpegKenBurns(logger=logger)
    logger.info("pipeline_start", provider=provider.name, mode=provider.mode.value, tier=provider.tier.value, duration_secs=duration_secs)
    if motion == "ai":
        ai_path = _ai_motion_reel(image_path, output_path, duration_secs, fps, width, height, audio_path,
                                  config or {}, provider.ffmpeg, logger)
        if ai_path:
            report = validate_video(ai_path, width, height, fps=fps, duration_secs=duration_secs,
                                    require_audio=True, deep=True, input_path=image_path)
            if report.ok:
                return ai_path
            logger.error("output_validation_failed", provider="kaggle_provider", errors=report.errors)
        logger.warning("ai_motion_fallback", provider="ffmpeg_kenburns", reason="AI motion unavailable or invalid")
    report = None
    for attempt in (1, 2):  # one retry if the render is missing/corrupt
        result = provider.generate(
            image_path=image_path,
            output_path=output_path,
            duration_secs=duration_secs,
            fps=fps,
            job_id=logger.job_id,
            width=width,
            height=height,
            audio_path=audio_path,
        )
        report = validate_video(
            result.output_path, width, height, fps=fps, duration_secs=duration_secs,
            require_audio=True, input_path=image_path,
        )
        if report.ok:
            break
        logger.error("output_validation_failed", provider=result.provider, attempt=attempt, errors=report.errors,
                     output_path=str(result.output_path))
    if not report.ok:
        raise RuntimeError(f"Reel failed validation: {'; '.join(report.errors)}")
    logger.info(
        "pipeline_complete",
        provider=result.provider,
        mode=result.mode.value,
        tier=result.tier.value,
        image_hash=result.image_hash,
        duration_secs=result.duration_secs,
        gen_time_secs=result.gen_time_secs,
        cost_usd=result.cost_usd,
    )
    return result.output_path


def _ai_motion_reel(image_path, output_path, duration_secs, fps, width, height, audio_path, config, ffmpeg, logger):
    """AI motion (Kaggle Wan clip, boomerang-looped to duration). Returns the reel path, or None to fall back."""
    try:
        from aurora.motion.providers.ffmpeg_kenburns import audio_args
        from aurora.motion.providers.kaggle_provider import KaggleProvider

        clip = KaggleProvider(config).generate(
            image_path, duration=int(duration_secs), caption_text=config.get("_motion_prompt", ""),
            job_id=logger.job_id,
        ).output_path
        boom = Path(output_path).with_suffix(".boom.mp4")
        subprocess.run([ffmpeg, "-y", "-i", str(clip), "-filter_complex",
                        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                        f"setsar=1,fps={fps},split[f][r];[r]reverse[rr];[f][rr]concat=n=2:v=1:a=0[v]",
                        "-map", "[v]", "-an", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(boom)],
                       capture_output=True, text=True, timeout=300, check=True)
        audio_in, audio_out = audio_args(audio_path)
        subprocess.run([ffmpeg, "-y", "-stream_loop", "-1", "-i", str(boom), *audio_in, "-t", str(duration_secs),
                        "-r", str(fps), "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", *audio_out, str(output_path)],
                       capture_output=True, text=True, timeout=300, check=True)
        boom.unlink(missing_ok=True)
        return Path(output_path)
    except Exception as exc:  # noqa: BLE001 - any failure -> local camera motion
        logger.error("ai_motion_failed", provider="kaggle_provider", error=str(exc))
        return None


def run_prompt_ledger(image_path: Path, output_path: Path, prompt_ledger: Path) -> Path:
    """Render a multi-segment prompt ledger and stitch it into one master MP4."""
    logger = StructuredLogger(component="pipeline")
    ledger = _load_prompt_ledger(prompt_ledger)
    fps = int(ledger.get("fps", 24))
    seed = ledger.get("seed")
    visual_complexity = str(ledger.get("visual_complexity", "standard"))
    target_resolution = ledger.get("target_resolution", {"width": 1080, "height": 1920})
    size_kwargs = _size_kwargs(target_resolution)
    segments = ledger.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise ValueError(f"Prompt ledger has no segments: {prompt_ledger}")

    provider = FFmpegKenBurns(logger=logger)
    raw_dir = Path("outputs") / "raw"
    final_dir = output_path.parent if output_path.parent != Path("") else Path("outputs") / "final"
    raw_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    output_path = final_dir / output_path.name

    logger.info(
        "prompt_ledger_start",
        provider=provider.name,
        mode=str(ledger.get("motion_mode", provider.mode.value)),
        tier=provider.tier.value,
        duration_secs=float(ledger.get("duration_secs", 0.0)),
        prompt_ledger=str(prompt_ledger),
        seed=seed,
        visual_complexity=visual_complexity,
        target_resolution=target_resolution,
        total_frames=ledger.get("total_frames"),
    )

    segment_paths: list[Path] = []
    total_gen_time_secs = 0.0
    for index, segment in enumerate(segments, start=1):
        segment_id = _segment_id(segment, index)
        frame_start = _segment_int(segment, "start_frame", "frame_start")
        frame_end = _segment_int(segment, "end_frame", "frame_end")
        frame_count = frame_end - frame_start + 1
        duration_secs = float(segment.get("duration_secs", frame_count / fps))
        start_secs = float(segment.get("start_secs", frame_start / fps))
        end_secs = float(segment.get("end_secs", start_secs + duration_secs))
        prompt = str(segment["prompt"])
        segment_path = raw_dir / f"{Path(prompt_ledger).stem}_{index:02d}_{segment_id}.mp4"
        segment_started = time.perf_counter()
        logger.info(
            "segment_start",
            provider=provider.name,
            mode=str(ledger.get("motion_mode", provider.mode.value)),
            tier=provider.tier.value,
            duration_secs=duration_secs,
            segment_id=segment_id,
            segment_name=segment_id,
            segment_index=index,
            start_frame=frame_start,
            end_frame=frame_end,
            start_secs=start_secs,
            end_secs=end_secs,
            seed=seed,
            visual_complexity=visual_complexity,
            prompt=prompt,
            global_character_specs=ledger.get("global_character_specs", {}),
        )
        result = provider.generate(
            image_path=image_path,
            output_path=segment_path,
            duration_secs=duration_secs,
            fps=fps,
            job_id=logger.job_id,
            **size_kwargs,
        )
        segment_gen_time_secs = time.perf_counter() - segment_started
        total_gen_time_secs += segment_gen_time_secs
        logger.info(
            "segment_complete",
            provider=result.provider,
            mode=str(ledger.get("motion_mode", result.mode.value)),
            tier=result.tier.value,
            image_hash=result.image_hash,
            duration_secs=duration_secs,
            gen_time_secs=segment_gen_time_secs,
            cost_usd=result.cost_usd,
            segment_id=segment_id,
            segment_index=index,
            start_frame=frame_start,
            end_frame=frame_end,
            output_path=str(segment_path),
        )
        segment_paths.append(segment_path)

    _concat_segments(provider.ffmpeg, segment_paths, output_path, logger)
    logger.info(
        "prompt_ledger_complete",
        provider=provider.name,
        mode=str(ledger.get("motion_mode", provider.mode.value)),
        tier=provider.tier.value,
        duration_secs=float(ledger.get("duration_secs", 0.0)),
        gen_time_secs=total_gen_time_secs,
        cost_usd=0.0,
        output_path=str(output_path),
        prompt_ledger=str(prompt_ledger),
        segment_count=len(segment_paths),
    )
    return output_path


def _size_kwargs(target_resolution: Any) -> dict[str, int]:
    """Width/height kwargs from a ledger resolution ({"width","height"} or "WxH"); empty when unparseable."""
    try:
        if isinstance(target_resolution, dict):
            width, height = int(target_resolution["width"]), int(target_resolution["height"])
        else:
            width, height = (int(v) for v in str(target_resolution).lower().split("x"))
    except (KeyError, ValueError, TypeError):
        return {}
    return {"width": width, "height": height}


def _load_prompt_ledger(prompt_ledger: Path) -> dict[str, Any]:
    """Load and validate the prompt ledger JSON document."""
    try:
        with Path(prompt_ledger).open("r", encoding="utf-8") as handle:
            ledger = json.load(handle)
    except OSError as exc:
        raise FileNotFoundError(f"Unable to load prompt ledger: {prompt_ledger}") from exc
    if not isinstance(ledger, dict):
        raise ValueError(f"Prompt ledger must be a JSON object: {prompt_ledger}")
    return ledger


def _segment_id(segment: dict[str, Any], index: int) -> str:
    """Return a filesystem-safe segment identifier."""
    raw_id = segment.get("id", segment.get("segment_id", index))
    safe_id = str(raw_id).strip().replace(" ", "_")
    return f"segment_{safe_id}"


def _segment_int(segment: dict[str, Any], preferred_key: str, fallback_key: str) -> int:
    """Read an integer segment boundary from either supported ledger schema."""
    if preferred_key in segment:
        return int(segment[preferred_key])
    if fallback_key in segment:
        return int(segment[fallback_key])
    raise KeyError(f"Segment is missing {preferred_key} or {fallback_key}")


def _concat_segments(ffmpeg: str, segment_paths: list[Path], output_path: Path, logger: StructuredLogger) -> None:
    """Use FFmpeg concat demuxer to stitch rendered segment files."""
    concat_list_path = Path("outputs") / "raw" / f"{output_path.stem}_concat.txt"
    concat_list_path.parent.mkdir(parents=True, exist_ok=True)
    concat_lines = [f"file '{path.resolve().as_posix()}'" for path in segment_paths]
    concat_list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    logger.info(
        "concat_start",
        provider="ffmpeg_concat",
        mode="assembly",
        tier="local",
        duration_secs=0.0,
        segment_count=len(segment_paths),
        concat_list=str(concat_list_path),
    )
    started = time.perf_counter()
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(output_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        result.check_returncode()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error(
            "concat_failed",
            provider="ffmpeg_concat",
            mode="assembly",
            tier="local",
            gen_time_secs=time.perf_counter() - started,
            error=str(exc),
            output_path=str(output_path),
        )
        raise RuntimeError(f"FFmpeg concat failed: {exc}") from exc
    logger.info(
        "concat_complete",
        provider="ffmpeg_concat",
        mode="assembly",
        tier="local",
        gen_time_secs=time.perf_counter() - started,
        output_path=str(output_path),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and render a Ken Burns MP4."""
    ensure_utf8_console()
    load_env()
    parser = argparse.ArgumentParser(description="Project Aurora Sprint 1 pipeline")
    parser.add_argument("image_path", type=Path)
    parser.add_argument("output_path", type=Path)
    parser.add_argument("--duration-secs", type=float, default=25.0)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--audio", type=Path, default=None, help="optional audio file (silent track if omitted)")
    parser.add_argument("--prompt-ledger", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.prompt_ledger is not None:
        output_path = run_prompt_ledger(args.image_path, args.output_path, args.prompt_ledger)
    else:
        output_path = run(
            args.image_path, args.output_path, args.duration_secs,
            fps=args.fps, width=args.width, height=args.height, audio_path=args.audio,
        )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
