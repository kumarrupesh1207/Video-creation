from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aurora.assets.manifest import find_asset
from modules.audio_engine import AudioEngine
from modules.media_assembler import MediaAssembler
from modules.motion_engine import MotionEngine
from modules.pipeline_logger import PipelineLogger


def slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s)[:40].lower()


def _ffprobe_path(cfg: dict) -> str:
    ffmpeg_bin = cfg.get("paths", {}).get("ffmpeg_bin", "ffmpeg")
    candidate = Path(ffmpeg_bin).with_name("ffprobe.exe")
    return str(candidate) if candidate.exists() else "ffprobe"


def _ffmpeg_path(cfg: dict) -> str:
    return cfg.get("paths", {}).get("ffmpeg_bin", "ffmpeg")


def _probe_duration(cfg: dict, video_path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                _ffprobe_path(cfg),
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except Exception:
        return None


def _probe_stream_durations(cfg: dict, video_path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                _ffprobe_path(cfg),
                "-v", "error",
                "-show_entries", "stream=codec_type,duration",
                "-of", "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        video_durations = [float(s.get("duration")) for s in streams if s.get("codec_type") == "video" and s.get("duration")]
        audio_durations = [float(s.get("duration")) for s in streams if s.get("codec_type") == "audio" and s.get("duration")]
        return {
            "video_duration_sec": video_durations[0] if video_durations else None,
            "audio_duration_sec": audio_durations[0] if audio_durations else None,
            "audio_stream_present": bool(audio_durations),
        }
    except Exception:
        return {}


def _detect_black_frames(cfg: dict, video_path: Path, frame_count: int = 5) -> dict:
    try:
        result = subprocess.run(
            [
                _ffmpeg_path(cfg),
                "-hide_banner",
                "-i", str(video_path),
                "-vf", "blackframe=amount=98:threshold=32",
                "-frames:v", str(frame_count),
                "-an",
                "-f", "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            return {"checked": False, "flagged": False, "reason": "ffmpeg_check_failed"}
        scores = [int(match) for match in re.findall(r"pblack:(\d+)", result.stderr or "")]
        return {
            "checked": True,
            "flagged": any(score >= 98 for score in scores),
            "scores": scores,
        }
    except Exception:
        return {"checked": False, "flagged": False, "reason": "blackframe_stub"}


def _fr010_check(cfg: dict, video_path: Path, expected_duration_sec: float) -> dict:
    size_bytes = video_path.stat().st_size if video_path.exists() else 0
    duration_sec = _probe_duration(cfg, video_path) if video_path.exists() else None
    black_frames = _detect_black_frames(cfg, video_path) if video_path.exists() else {"checked": False, "flagged": False}
    duration_ok = duration_sec is not None and abs(duration_sec - expected_duration_sec) <= 0.5
    return {
        "file_exists": video_path.exists(),
        "file_size_bytes": size_bytes,
        "file_size_ok": size_bytes > 0,
        "duration_sec": duration_sec,
        "expected_duration_sec": expected_duration_sec,
        "duration_ok": duration_ok,
        "black_frame_check": black_frames,
        "passed": video_path.exists() and size_bytes > 0 and duration_ok,
    }


def _fr011_check(cfg: dict, video_path: Path) -> dict:
    durations = _probe_stream_durations(cfg, video_path)
    video_duration = durations.get("video_duration_sec")
    audio_duration = durations.get("audio_duration_sec")
    delta = abs(video_duration - audio_duration) if video_duration is not None and audio_duration is not None else None
    passed = bool(durations.get("audio_stream_present") and delta is not None and delta <= 0.1)
    return {
        **durations,
        "duration_delta_sec": delta,
        "duration_match_ok": delta is not None and delta <= 0.1,
        "passed": passed,
    }


def _extract_first_frame(cfg: dict, video_path: Path, frame_path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                _ffmpeg_path(cfg),
                "-y",
                "-i", str(video_path),
                "-frames:v", "1",
                str(frame_path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0 and frame_path.exists()
    except Exception:
        return False


def _g12_drift_check(cfg: dict, base_image_path: Path, video_path: Path) -> dict:
    try:
        from PIL import Image, ImageChops, ImageOps, ImageStat  # type: ignore
    except Exception:
        return {"checked": False, "passed": False, "reason": "pillow_unavailable"}

    temp_frame = video_path.with_suffix(".frame0.png")
    if not _extract_first_frame(cfg, video_path, temp_frame):
        return {"checked": False, "passed": False, "reason": "frame_extract_failed"}
    try:
        base = Image.open(base_image_path).convert("RGB")
        frame = Image.open(temp_frame).convert("RGB")
        frame = ImageOps.fit(frame, base.size, method=Image.Resampling.BILINEAR)
        diff = ImageStat.Stat(ImageChops.difference(base, frame))
        mse = sum((value ** 2 for value in diff.mean)) / len(diff.mean)
        passed = mse <= 250.0
        return {
            "checked": True,
            "metric": "MSE",
            "mse": round(float(mse), 3),
            "threshold": 250.0,
            "passed": passed,
        }
    except Exception as exc:
        return {"checked": False, "passed": False, "reason": f"g12_failed:{exc}"}
    finally:
        temp_frame.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a cinematic video from an existing asset key.")
    parser.add_argument("--asset-key", required=True)
    parser.add_argument("--topic", default="")
    parser.add_argument("--mode", default="cinematic")
    parser.add_argument("--motion-mode", default="static_cinematic")
    parser.add_argument("--visual-complexity", default="standard")
    parser.add_argument("--audio-narration-path", default=None)
    parser.add_argument("--identity-lock", action="store_true")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    asset = find_asset(args.asset_key)
    if not asset:
        raise SystemExit("Asset not found.")

    topic = args.topic.strip() or asset.get("normalized_prompt", "Asset Render")
    log = PipelineLogger(cfg, topic=topic)
    log.set_metadata(
        mode=args.mode,
        motion_mode=args.motion_mode,
        visual_complexity=args.visual_complexity,
        asset_key=args.asset_key,
        human_review_required=args.motion_mode in {"talking_motion", "full_i2v_motion"},
        audio_narration_path=args.audio_narration_path,
        identity_lock=args.identity_lock,
        reference_anchor_asset=args.asset_key,
        generation_seed=asset.get("seed"),
    )
    slug = slugify(topic)
    image_path = Path(asset["output_path"])
    target_duration_sec = 5.0

    log.start_step("script_gen")
    log.end_step("script_gen", cfg["paths"]["scripts_dir"])

    log.start_step("image_gen")
    log.end_step("image_gen", str(image_path))

    motion_step = (
        "talking_motion_gen" if args.motion_mode == "talking_motion"
        else "full_i2v_motion_gen" if args.motion_mode == "full_i2v_motion"
        else "video_gen"
    )
    log.start_step(motion_step)
    motion_engine = MotionEngine(cfg)
    if args.motion_mode == "talking_motion":
        video_path = motion_engine.generate_motion(
            "talking_motion",
            image_path=image_path,
            duration=5,
            caption_text=topic,
            job_id=log.session_id,
            asset_key=args.asset_key,
            template_name="talking_head",
            visual_complexity=args.visual_complexity,
            audio_narration_path=Path(args.audio_narration_path).resolve() if args.audio_narration_path else None,
            generation_seed=asset.get("seed"),
            reference_image_path=image_path,
            identity_lock=args.identity_lock,
        )
    elif args.motion_mode == "full_i2v_motion":
        video_path = motion_engine.generate_motion(
            "full_i2v_motion",
            image_path=image_path,
            duration=5,
            caption_text=topic,
            job_id=log.session_id,
            asset_key=args.asset_key,
            template_name="full_motion",
            visual_complexity=args.visual_complexity,
            generation_seed=asset.get("seed"),
            reference_image_path=image_path,
            identity_lock=args.identity_lock,
        )
    else:
        video_path = motion_engine.generate_motion(
            "static_cinematic",
            image_path=image_path,
            duration=5,
            template_name="ken_burns",
            job_id=log.session_id,
            asset_key=args.asset_key,
            visual_complexity=args.visual_complexity,
            generation_seed=asset.get("seed"),
            reference_image_path=image_path,
            identity_lock=args.identity_lock,
        )
    motion_result = motion_engine.last_result
    if motion_result:
        actual_motion_provider = motion_result.context.get("provider", motion_result.provider) if motion_result.context else motion_result.provider
        log.set_metadata(
            motion_provider_used=actual_motion_provider,
            motion_badge=motion_result.badge,
            motion_degraded_from=motion_result.degraded_from,
            reference_anchor_asset=args.asset_key,
            reference_image_path=str(image_path),
            generation_seed=asset.get("seed"),
            identity_lock=args.identity_lock,
            human_review_required=motion_result.review_required or args.motion_mode in {"talking_motion", "full_i2v_motion"},
            motion_remote_latency_ms=(
                motion_result.context.get("total_inference_latency_ms")
                if motion_result.context else None
            ),
            motion_remote_status=motion_result.context.get("status") if motion_result.context else None,
            motion_gpu_metrics=motion_result.context if motion_result.context else None,
        )
    log.end_step(motion_step, str(video_path))

    log.start_step("audio_gen")
    if args.audio_narration_path:
        audio_path = Path(args.audio_narration_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Narration audio not found: {audio_path}")
    else:
        audio_eng = AudioEngine(cfg)
        audio_eng.load()
        try:
            audio_path = audio_eng.synthesize(topic, f"{slug}_voice.wav")
        finally:
            audio_eng.unload()
    log.end_step("audio_gen", str(audio_path))

    log.skip_step("lipsync", "asset render uses direct merge")
    asm = MediaAssembler(cfg)
    log.start_step("final_assembly")
    if args.motion_mode in {"talking_motion", "full_i2v_motion"}:
        final_path = asm.assemble_final_video(
            video_path,
            audio_path,
            output_name=f"{slug}_{'talking' if args.motion_mode == 'talking_motion' else 'full_motion'}_final.mp4",
            target="reels",
            target_duration=target_duration_sec,
        )
    else:
        final_path = asm.assemble_final_video(
            video_path,
            audio_path,
            f"{slug}_merged.mp4",
            target="reels",
            target_duration=target_duration_sec,
        )
    log.end_step("final_assembly", str(final_path))

    quality_gate = _fr010_check(cfg, final_path, target_duration_sec)
    fr011_gate = _fr011_check(cfg, final_path)
    should_run_g12 = args.identity_lock or args.motion_mode in {"talking_motion", "full_i2v_motion"}
    g12_gate = _g12_drift_check(cfg, image_path, final_path) if should_run_g12 else {
        "checked": False,
        "passed": True,
        "reason": "g12_not_required",
    }
    log.set_metadata(fr010_check=quality_gate, fr011_check=fr011_gate, g12_check=g12_gate)
    if not quality_gate["passed"]:
        raise RuntimeError(f"FR-010 validation failed: {quality_gate}")
    if not fr011_gate["passed"]:
        raise RuntimeError(f"FR-011 validation failed: {fr011_gate}")
    if should_run_g12 and not g12_gate.get("passed", False):
        raise RuntimeError(f"G12 identity drift check failed: {g12_gate}")

    log.complete(final_output=str(final_path))
    return 0


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    raise SystemExit(main())
