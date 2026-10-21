"""
main.py — AI Video Factory: Full Sequential Pipeline
──────────────────────────────────────────────────────
Phases:
  1. LLMManager        → script JSON           (CPU)
  2. ComfyOrchestrator → keyframe image        (GPU: ~7 GB)
  3. VideoEngine       → SVD motion clip       (GPU: ~10 GB) [VRAM flush]
  4. AudioEngine       → voiceover WAV         (GPU: ~3.5 GB) [VRAM flush]
  5. LipSyncProcessor  → lip-synced MP4        (subprocess, isolated VRAM)
  6. MediaAssembler    → upscaled final MP4    (CPU: FFmpeg)

Usage:
    # Full pipeline — talking head
    python main.py --topic "Agile Transformations in Banking"

    # Beach run — high motion, no lipsync, music overlay
    python main.py --topic "Bikini girl running on beach" --frames 16 --motion 150 --skip-lipsync --augmentation 0.15 --denoise 0.7 --resolution 512 896

    # Load a named preset
    python main.py --preset beach_run

    # Script only — no GPU
    python main.py --topic "DeFi Explained" --dry-run

    # Custom image prompt (overrides LLM output for image phase)
    python main.py --topic "Beach Run" --image-prompt "Athletic woman in bikini running on beach..." --neg-prompt "distorted legs..."
"""

import gc
import sys
import json
import os
import random
import re
import subprocess
import time
import yaml
import shutil
import argparse
import logging
from pathlib import Path

sys.path.insert(0, "src")

from modules.llm_manager        import LLMManager
from modules.comfy_orchestrator import ComfyOrchestrator
from modules.video_engine       import VideoEngine
from modules.audio_engine       import AudioEngine
from modules.lipsync_processor  import LipSyncProcessor
from modules.media_assembler    import MediaAssembler
from modules.motion_engine      import MotionEngine
from modules.pipeline_logger    import PipelineLogger
from backend.app.services.image_switchboard import ImageSwitchboard
from aurora.assets import ManifestManager, CacheGate

try:
    import torch
    _TORCH = True
except ImportError:
    torch  = None
    _TORCH = False


logger = logging.getLogger("aurora.main")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def slugify(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s)[:40].lower()


def vram_cleanup(label: str, orch: ComfyOrchestrator = None) -> None:
    """Sequential VRAM purge — call between every GPU-heavy phase."""
    print(f"\n[VRAM] ── Cleanup: {label} ─────────────────────")
    gc.collect()
    if orch:
        orch.free_vram()
    if _TORCH and torch.cuda.is_available():
        before = torch.cuda.memory_allocated() / 1e9
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
        after  = torch.cuda.memory_allocated() / 1e9
        free   = torch.cuda.get_device_properties(0).total_memory / 1e9 - after
        print(f"[VRAM]   {before:.2f} GB → {after:.2f} GB | {free:.2f} GB free")
    else:
        print("[VRAM]   CUDA unavailable — skipping GPU flush.")
    time.sleep(2)
    print(f"[VRAM] ── Done ──────────────────────────────────\n")


def load_preset(name: str) -> dict:
    """Load a named preset from presets.json."""
    p = Path("presets.json")
    if not p.exists():
        return {}
    presets = json.loads(p.read_text(encoding="utf-8"))
    for preset in presets:
        if preset.get("_name") == name:
            return preset
    print(f"[Preset] ⚠️  Preset '{name}' not found. Available: "
          f"{[pr.get('_name') for pr in presets]}")
    return {}


def _ffprobe_path(cfg: dict) -> str:
    ffmpeg_bin = cfg.get("paths", {}).get("ffmpeg_bin", "ffmpeg")
    candidate = Path(ffmpeg_bin).with_name("ffprobe.exe")
    return str(candidate) if candidate.exists() else "ffprobe"


def _ffmpeg_path(cfg: dict) -> str:
    return cfg.get("paths", {}).get("ffmpeg_bin", "ffmpeg")


def _extract_first_frame(cfg: dict, video_path: Path, frame_path: Path) -> bool:
    try:
        result = subprocess.run(
            [_ffmpeg_path(cfg), "-y", "-i", str(video_path), "-frames:v", "1", str(frame_path)],
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
        return {
            "checked": True,
            "metric": "MSE",
            "mse": round(float(mse), 3),
            "threshold": 250.0,
            "passed": bool(mse <= 250.0),
        }
    except Exception as exc:
        return {"checked": False, "passed": False, "reason": f"g12_failed:{exc}"}
    finally:
        temp_frame.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def run_pipeline(cfg: dict, topic: str,
                 mode:         str   = "cinematic",
                 motion_mode:  str   = "static_cinematic",
                 visual_complexity: str = "standard",
                 frames:       int   = 12,
                 motion:       int   = 80,
                 fps:          int   = 6,
                 augmentation: float = 0.0,
                 denoise:      float = 0.5,
                 resolution:   list  = None,
                 image_prompt: str   = None,
                 neg_prompt:   str   = None,
                 audio_narration_path: str | None = None,
                 identity_lock: bool = False,
                 skip_lipsync: bool  = False,
                 skip_upscale: bool  = False,
                 force_new_image: bool = False,
                 dry_run:      bool  = False,
                 timeout:      int   = 900) -> Path:
    """
    Execute the full pipeline sequentially.
    Returns the path to the final rendered MP4.
    """
    log       = PipelineLogger(cfg, topic=topic)
    final_dir = Path(cfg["paths"]["final_dir"])
    final_dir.mkdir(parents=True, exist_ok=True)
    slug      = slugify(topic)
    res       = resolution or cfg["project"]["resolution"]
    cinematic = mode == "cinematic"
    talking_mode = motion_mode == "talking_motion"
    full_i2v_mode = motion_mode == "full_i2v_motion"
    narration_audio = Path(audio_narration_path).resolve() if audio_narration_path else None
    log.set_metadata(
        mode=mode,
        motion_mode=motion_mode,
        visual_complexity=visual_complexity,
        human_review_required=talking_mode or full_i2v_mode,
        audio_narration_path=str(narration_audio) if narration_audio else None,
        identity_lock=identity_lock,
    )

    # ── Phase 1: Script Generation ───────────────────────────────────────────
    log.start_step("script_gen")
    try:
        llm    = LLMManager(cfg)
        script_duration = 5 if cinematic else frames * fps
        script = llm.generate_script(topic, duration_sec=script_duration)
        text   = script.get("hook", "").strip() if cinematic else llm.get_full_text(script)
        if not text:
            text = llm.get_full_text(script)
        log.end_step("script_gen", cfg["paths"]["scripts_dir"])
    except Exception as e:
        log.fail_step("script_gen", str(e))
        raise

    if dry_run:
        print("\n[main] --dry-run: stopping after script generation.")
        print(json.dumps(script, indent=2))
        log.complete()
        return None

    # Determine effective image prompt
    style_tags = (
        "masterpiece, best quality, ultra-detailed, highres, solo, 1boy/1girl, "
        f"{topic}, anime style, cel shaded, vibrant colors, sharp lines"
    )
    eff_pos = image_prompt or (
        f"{style_tags}, {script['hook']}, "
        "clean lineart, expressive anime eyes, dramatic anime lighting, vertical 9:16"
    )
    eff_neg = neg_prompt or (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
        "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
        "signature, watermark, username, blurry, artist name, realistic, 3d, photorealistic"
    )
    image_res = (resolution or [540, 960]) if cinematic else res

    # ── Phase 2: Image Generation ────────────────────────────────────────────
    log.start_step("image_gen")
    orch = None
    image_asset_key = None
    image_seed = None
    try:
        # ---------------------------------------------------------------------
        # SPRINT 4E-1: IMAGE REUSE GATE INTERCEPTION BOUNDARY
        # ---------------------------------------------------------------------
        manifest_file_path = os.path.join("assets", "images", "manifest.json")
        manifest_manager = ManifestManager(manifest_file_path)
        cache_gate = CacheGate(manifest_manager)

        job_config = {
            "prompt": topic,
            "negative_prompt": eff_neg,
            "style_preset": "cinematic" if cinematic else "animated",
            "provider": cfg.get("image_provider", "nvidia_nim"),
            "model": cfg.get("image_model", "stable-diffusion-xl"),
            "width": image_res[0],
            "height": image_res[1],
            "seed": cfg.get("seed", 42),
            "force_new_image": force_new_image,
            "provider_version": "1.0.0",
            "model_version": "1.0.0",
            "workflow_version": "1.0.0",
        }
        job_config["transaction_id"] = log.session_id
        job_config["topic"] = topic
        job_config["motion_mode"] = motion_mode or "full_i2v_motion"
        job_config["visual_complexity"] = visual_complexity

        is_cache_hit, asset_key, cached_record = cache_gate.check_gate(job_config)

        if is_cache_hit and cached_record:
            image_path = Path(cached_record["file_path"])
            image_asset_key = asset_key
            image_seed = int(cached_record.get("seed", job_config["seed"]))
            logger.info(f"[Log] Short-circuiting generation. Reusing asset path: {image_path}")
            log.end_step("image_gen", str(image_path))
        else:
            switchboard = ImageSwitchboard(cfg)
            image_result = switchboard.generate(
                prompt=eff_pos,
                style="cinematic" if cinematic else "animated",
                resolution=image_res,
                job_id=log.session_id,
                negative_prompt=eff_neg,
                force_new_image=force_new_image,
            )
            if image_result.needs_manual_upload:
                raise RuntimeError(
                    "[ImageSwitchboard] Manual upload required. "
                    f"See {image_result.output_path}"
                )
            orch = image_result.context.get("orchestrator")
            image_path = Path(image_result.output_path)
            if not image_path:
                raise RuntimeError("ComfyUI returned no image outputs.")
            if os.path.exists(image_path):
                cache_gate.commit_generation_success(
                    asset_key=asset_key,
                    config=job_config,
                    file_path=str(image_path),
                )
            else:
                logger.error(f"[Log] Generation routine completed but output file missing at: {image_path}")
                raise FileNotFoundError(f"Expected generated asset missing: {image_path}")
            image_asset_key = getattr(image_result, "asset_key", asset_key)
            image_seed = getattr(image_result, "seed", job_config["seed"])
            log.end_step("image_gen", str(image_path))
        # ---------------------------------------------------------------------
        # END OF SPRINT 4E-1 CACHE INTERCEPT - Continue to downstream assembly
        # ---------------------------------------------------------------------
    except Exception as e:
        log.fail_step("image_gen", str(e))
        raise
    finally:
        vram_cleanup("post-image-gen", orch)

    # ── Phase 3: Video Generation ────────────────────────────────────────────
    if cinematic:
        motion_step = "talking_motion_gen" if talking_mode else ("full_i2v_motion_gen" if full_i2v_mode else "video_gen")
        log.start_step(motion_step)
        try:
            motion_engine = MotionEngine(cfg)
            if talking_mode:
                motion_label = "Talking Motion R&D"
                print(f"[MotionEngine] Selected cinematic template: {motion_label}")
                video_path = motion_engine.generate_motion(
                    "talking_motion",
                    image_path=image_path,
                    duration=5,
                    caption_text=topic,
                    template_name="talking_head",
                    job_id=log.session_id,
                    asset_key=image_asset_key,
                    visual_complexity=visual_complexity,
                    audio_narration_path=narration_audio,
                    generation_seed=image_seed,
                    reference_image_path=image_path,
                    identity_lock=identity_lock,
                )
            elif full_i2v_mode:
                motion_label = f"Full I2V Motion ({visual_complexity})"
                print(f"[MotionEngine] Selected cinematic template: {motion_label}")
                video_path = motion_engine.generate_motion(
                    "full_i2v_motion",
                    image_path=image_path,
                    duration=5,
                    caption_text=topic,
                    template_name="full_motion",
                    job_id=log.session_id,
                    asset_key=image_asset_key,
                    visual_complexity=visual_complexity,
                    generation_seed=image_seed,
                    reference_image_path=image_path,
                    identity_lock=identity_lock,
                )
            else:
                motion_templates = [
                    ("Ken Burns Zoom", "ken_burns"),
                    ("Side Drift", "side_drift"),
                ]
                motion_label, template_name = random.choice(motion_templates)
                print(f"[MotionEngine] Selected cinematic template: {motion_label}")
                video_path = motion_engine.generate_motion(
                    "static_cinematic",
                    image_path=image_path,
                    duration=5,
                    caption_text=topic,
                    template_name=template_name,
                    job_id=log.session_id,
                    asset_key=image_asset_key,
                    visual_complexity=visual_complexity,
                    generation_seed=image_seed,
                    reference_image_path=image_path,
                    identity_lock=identity_lock,
                )
            motion_result = motion_engine.last_result
            if motion_result:
                actual_motion_provider = motion_result.context.get("provider", motion_result.provider) if motion_result.context else motion_result.provider
                log.set_metadata(
                    reference_anchor_asset=image_asset_key,
                    reference_image_path=str(image_path),
                    generation_seed=image_seed,
                    motion_provider_used=actual_motion_provider,
                    motion_badge=motion_result.badge,
                    motion_degraded_from=motion_result.degraded_from,
                    human_review_required=motion_result.review_required or talking_mode or full_i2v_mode,
                    motion_remote_latency_ms=(
                        motion_result.context.get("total_inference_latency_ms")
                        if motion_result.context else None
                    ),
                    motion_remote_status=motion_result.context.get("status") if motion_result.context else None,
                    motion_gpu_metrics=motion_result.context if motion_result.context else None,
                )
            log.end_step(motion_step, str(video_path))
        except Exception as e:
            log.fail_step(motion_step, str(e))
            raise
    elif skip_upscale:
        log.skip_step("video_gen", "--skip-upscale flag set (bypassing SVD)")
        print("\n[main] ⏭  Video Gen (SVD) skipped — using static image.")
        video_path = image_path
    else:
        log.start_step("video_gen")
        try:
            engine     = VideoEngine(cfg, orch)
            video_path = engine.generate(
                image_path    = image_path,
                motion_bucket = motion,
                fps           = fps,
                frames        = frames,
                augmentation  = augmentation,
                denoise       = denoise,
                resolution    = res,
                timeout       = timeout,
            )
            log.end_step("video_gen", str(video_path))
        except Exception as e:
            log.fail_step("video_gen", str(e))
            raise
        finally:
            vram_cleanup("post-SVD", orch)

    # ── Phase 4: Audio Synthesis ─────────────────────────────────────────────
    log.start_step("audio_gen")
    audio_eng = None
    try:
        if narration_audio:
            if not narration_audio.exists():
                raise FileNotFoundError(f"Narration audio not found: {narration_audio}")
            audio_path = narration_audio
        else:
            audio_eng  = AudioEngine(cfg)
            audio_eng.load()
            audio_path = audio_eng.synthesize(text, f"{slug}_voice.wav")
        log.end_step("audio_gen", str(audio_path))
    except Exception as e:
        log.fail_step("audio_gen", str(e))
        raise
    finally:
        if audio_eng:
            audio_eng.unload()

    # ── Phase 5: Lip Sync (optional) ─────────────────────────────────────────
    if cinematic or skip_lipsync:
        reason = "cinematic mode has no lipsync phase" if cinematic else "--skip-lipsync flag set"
        log.skip_step("lipsync", reason)
        if talking_mode or full_i2v_mode:
            print("[main] ⏭  Lip sync skipped — deferring audio mux to the single-pass final export.")
            video_for_final = video_path
        else:
            print("[main] ⏭  Lip sync skipped — merging audio directly onto video.")
            log.start_step("final_assembly")
            asm  = MediaAssembler(cfg)
            merged = asm.assemble_final_video(
                video_path,
                audio_path,
                f"{slug}_merged.mp4",
                target="reels" if cinematic else "native",
                target_duration=5 if cinematic else None,
            )
            log.end_step("final_assembly", str(merged))
            video_for_final = merged
    else:
        log.start_step("lipsync")
        try:
            lip_proc = LipSyncProcessor(cfg)
            lip_out  = lip_proc.process(video_path, audio_path, f"{slug}_lipsync.mp4")
            log.end_step("lipsync", str(lip_out))
            video_for_final = lip_out
        except Exception as e:
            log.fail_step("lipsync", str(e))
            raise

    # ── Phase 6: Upscale + Final Encode ──────────────────────────────────────
    if cinematic:
        if talking_mode or full_i2v_mode:
            label = "talking motion" if talking_mode else "full I2V motion"
            print(f"\n[main] Phase 6: MediaAssembler — single-pass {label} export to 1080x1920...")
            log.start_step("final_assembly")
            asm = MediaAssembler(cfg)
            final_path = asm.assemble_final_video(
                video_for_final,
                audio_path,
                output_name=f"{slug}_{'talking' if talking_mode else 'full_motion'}_final.mp4",
                target="reels",
                target_duration=5,
            )
            log.end_step("final_assembly", str(final_path))
        else:
            print("\n[main] Phase 6: MediaAssembler — single-pass cinematic export complete.")
            final_path = video_for_final
    elif skip_upscale:
        print("\n[main] Phase 6: MediaAssembler — skipping upscale, just wrapping output...")
        # video_for_final could be an image or video, merge_audio_video handled the muxing
        final_path = video_for_final
    else:
        print("\n[main] Phase 6: MediaAssembler — upscaling to 1080x1920...")
        log.start_step("final_assembly")
        asm        = MediaAssembler(cfg)
        final_path = asm.upscale_and_encode(
            input_path  = video_for_final,
            output_name = f"{slug}_final.mp4",
            target      = "reels",
        )
        log.end_step("final_assembly", str(final_path))

    should_run_g12 = identity_lock or talking_mode or full_i2v_mode
    g12_gate = _g12_drift_check(cfg, image_path, final_path) if should_run_g12 else {
        "checked": False,
        "passed": True,
        "reason": "g12_not_required",
    }
    log.set_metadata(g12_check=g12_gate)
    if should_run_g12 and not g12_gate.get("passed", False):
        raise RuntimeError(f"G12 identity drift check failed: {g12_gate}")

    log.complete(final_output=str(final_path))
    return final_path


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="AI Video Factory — Full Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Talking head with lipsync
  python main.py --topic "Agile Transformations in Banking"

  # Beach run — high motion, skip lipsync
  python main.py --topic "Bikini girl running on beach" --frames 16 --motion 150 --skip-lipsync --augmentation 0.15 --denoise 0.7 --resolution 512 896

  # Load preset
  python main.py --preset beach_run
  python main.py --preset fintech_speaker
        """
    )

    # Core
    parser.add_argument("--topic",        default=None,
                        help="Video topic (required unless --preset used)")
    parser.add_argument("--preset",       default=None,
                        help="Load settings from presets.json by _name key")
    parser.add_argument("--config",       default="config.yaml")
    parser.add_argument("--mode",         choices=["cinematic", "animated", "static_cinematic"],
                        default="cinematic",
                        help="Render mode. cinematic/static_cinematic = still image + Ken Burns motion, animated = SVD video pipeline.")
    parser.add_argument("--motion-mode", choices=["static_cinematic", "talking_motion", "full_i2v_motion"],
                        default="static_cinematic",
                        help="Motion provider mode for cinematic jobs.")
    parser.add_argument("--visual-complexity", choices=["standard", "high action", "atmospheric"],
                        default="standard",
                        help="Routing hint for full_i2v_motion model selection.")

    # SVD Parameters
    parser.add_argument("--frames",       type=int,   default=None,
                        help="SVD frames (default: 12 ≈ 2s)")
    parser.add_argument("--motion",       type=int,   default=None,
                        help="SVD motion bucket 1–255 (default: 80)")
    parser.add_argument("--fps",          type=int,   default=None,
                        help="Video FPS (default: 6)")
    parser.add_argument("--augmentation", type=float, default=None,
                        help="SVD augmentation level 0.0–0.5 (default: 0.0). "
                             "0.15 adds water-splash/motion detail.")
    parser.add_argument("--denoise",      type=float, default=0.5,
                        help="SVD denoise strength 0.0–1.0 (default: 0.5). "
                             "0.7 keeps skin texture stable during motion.")
    parser.add_argument("--resolution",   type=int,   nargs=2,
                        metavar=("W", "H"), default=None,
                        help="Generation resolution W H. "
                             "Default: from config.yaml. "
                             "Use 512 896 for VRAM-safe beach run.")

    # Prompt overrides
    parser.add_argument("--image-prompt", default=None,
                        help="Override positive image prompt (skips LLM auto-prompt)")
    parser.add_argument("--neg-prompt",   default=None,
                        help="Negative image prompt override")
    parser.add_argument("--audio-narration-path", default=None,
                        help="Optional existing narration audio file to reuse for talking-motion lip sync.")
    parser.add_argument("--identity-lock", action="store_true",
                        help="Lock motion jobs to the base asset as the identity anchor.")

    # Flags
    parser.add_argument("--skip-lipsync", action="store_true",
                        help="Skip Wav2Lip. Merge audio directly onto video. "
                             "Use for motion/atmosphere clips (beach run, B-roll).")
    parser.add_argument("--skip-upscale", action="store_true",
                        help="Animated mode only: skip SVD and final upscale, output static image with audio.")
    parser.add_argument("--force-ffmpeg-absolute", action="store_true",
                        help="Force MediaAssembler to use absolute pathing to resolve 1KB errors.")
    parser.add_argument("--force-new-image", action="store_true",
                        help="Bypass reusable asset lookup and force a new image generation.")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Generate script only — no GPU work.")
    parser.add_argument("--timeout",      type=int, default=900,
                        help="Seconds to wait for ComfyUI per phase. "
                             "Default 900 (15 min). CPU mode: use 1800.")
    args = parser.parse_args()

    # Load preset first (CLI args override preset values)
    preset = {}
    if args.preset:
        preset = load_preset(args.preset)
        if not preset:
            sys.exit(1)
        print(f"[Preset] Loaded: {args.preset} — {preset.get('_description', '')}")

    # Resolve final values: CLI > preset > defaults
    topic        = args.topic        or preset.get("topic")
    mode         = "cinematic" if args.mode == "static_cinematic" else args.mode
    motion_mode  = args.motion_mode
    visual_complexity = args.visual_complexity
    frames       = args.frames       or preset.get("frames",       12)
    motion       = args.motion       or preset.get("motion",       80)
    fps          = args.fps          or preset.get("fps",          6)
    augmentation = args.augmentation if args.augmentation is not None \
                   else preset.get("augmentation", 0.0)
    denoise      = args.denoise      if args.denoise is not None \
                   else preset.get("denoise", 0.5)
    resolution   = args.resolution   or preset.get("resolution")
    image_prompt = args.image_prompt or preset.get("positive_prompt")
    neg_prompt   = args.neg_prompt   or preset.get("negative_prompt")
    audio_narration_path = args.audio_narration_path or preset.get("audio_narration_path")
    identity_lock = args.identity_lock or preset.get("identity_lock", False)
    skip_lipsync = args.skip_lipsync or preset.get("skip_lipsync", False)

    if not topic:
        parser.error("--topic is required (or use --preset)")

    # Load config
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[main] ❌ config.yaml not found: {cfg_path.resolve()}")
        sys.exit(1)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # Print run summary
    print("=" * 60)
    print("  🏭 AI VIDEO FACTORY")
    print(f"  Mode   : {mode}")
    print(f"  Motion : {motion_mode}")
    print(f"  Visual : {visual_complexity}")
    print(f"  Anchor : {'LOCKED' if identity_lock else 'adaptive'}")
    print(f"  Topic  : {topic}")
    print(f"  Frames : {frames} @ {fps}fps ≈ {frames/fps:.1f}s")
    print(f"  Motion : {motion} | augment={augmentation} | denoise={denoise}")
    if mode == "cinematic":
        base_res = resolution or [540, 960]
        print(f"  Res    : {base_res[0]}x{base_res[1]} base image → 1080x1920 Ken Burns output")
    else:
        print(f"  Res    : {(resolution or cfg['project']['resolution'])[0]}x"
              f"{(resolution or cfg['project']['resolution'])[1]} → 1080x1920 (upscaled)")
    if mode == "cinematic":
        print("  Lipsync: N/A (cinematic mode)")
    else:
        print(f"  Lipsync: {'SKIP ⏭' if skip_lipsync else 'ENABLED'}")
    if args.preset:
        print(f"  Preset : {args.preset}")
    print("=" * 60)

    t0 = time.time()
    try:
        result = run_pipeline(
            cfg          = cfg,
            topic        = topic,
            mode         = mode,
            motion_mode  = motion_mode,
            visual_complexity = visual_complexity,
            frames       = frames,
            motion       = motion,
            fps          = fps,
            augmentation = augmentation,
            denoise      = denoise,
            resolution   = resolution,
            image_prompt = image_prompt,
            neg_prompt   = neg_prompt,
            audio_narration_path = audio_narration_path,
            identity_lock = identity_lock,
            skip_lipsync = skip_lipsync,
            skip_upscale = args.skip_upscale,
            force_new_image = args.force_new_image,
            dry_run      = args.dry_run,
            timeout      = args.timeout,
        )
        elapsed = time.time() - t0
        if result:
            print(f"\n  🚀 Complete in {elapsed:.1f}s")
            print(f"  📁 {result.resolve()}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"\n[main] ❌ Aborted after {elapsed:.1f}s: {e}")
        print("  Check outputs/log.json for per-step status.")
        sys.exit(1)


if __name__ == "__main__":
    from aurora import ensure_utf8_console, load_env

    load_env()
    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    main()
