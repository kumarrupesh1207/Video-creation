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
import time
import yaml
import shutil
import argparse
from pathlib import Path

sys.path.insert(0, "src")

from modules.llm_manager        import LLMManager
from modules.comfy_orchestrator import ComfyOrchestrator
from modules.video_engine       import VideoEngine
from modules.audio_engine       import AudioEngine
from modules.lipsync_processor  import LipSyncProcessor
from modules.media_assembler    import MediaAssembler
from modules.pipeline_logger    import PipelineLogger

try:
    import torch
    _TORCH = True
except ImportError:
    torch  = None
    _TORCH = False


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


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def run_pipeline(cfg: dict, topic: str,
                 frames:       int   = 12,
                 motion:       int   = 80,
                 fps:          int   = 6,
                 augmentation: float = 0.0,
                 denoise:      float = 0.5,
                 resolution:   list  = None,
                 image_prompt: str   = None,
                 neg_prompt:   str   = None,
                 skip_lipsync: bool  = False,
                 skip_upscale: bool  = False,
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

    # ── Phase 1: Script Generation ───────────────────────────────────────────
    log.start_step("script_gen")
    try:
        llm    = LLMManager(cfg)
        script = llm.generate_script(topic, duration_sec=frames * fps)
        text   = llm.get_full_text(script)
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
    eff_pos = image_prompt or (
        f"Cinematic portrait, {script['hook']}, "
        "photorealistic, 8k, studio lighting, vertical 9:16"
    )
    eff_neg = neg_prompt or "blurry, watermark, distorted, low quality, ugly"

    # ── Phase 2: Image Generation ────────────────────────────────────────────
    log.start_step("image_gen")
    orch = None
    try:
        orch   = ComfyOrchestrator(cfg)
        img_wf = cfg["models"]["comfyui"]["image_workflow"]
        io     = cfg["models"]["comfyui"]["image_overrides"]

        image_outputs = orch.run_workflow(img_wf, {
            io["positive_prompt_node"]: {"text": eff_pos},
            io["negative_prompt_node"]: {"text": eff_neg},
            io["latent_size_node"]:     {"width": res[0], "height": res[1]},
        }, timeout=timeout)
        image_path = image_outputs[0] if image_outputs else None
        if not image_path:
            raise RuntimeError("ComfyUI returned no image outputs.")
        log.end_step("image_gen", str(image_path))
    except Exception as e:
        log.fail_step("image_gen", str(e))
        raise
    finally:
        vram_cleanup("post-image-gen", orch)

    # ── Phase 3: Video Generation ────────────────────────────────────────────
    if skip_upscale:
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
    if skip_lipsync:
        log.skip_step("lipsync", "--skip-lipsync flag set")
        print("[main] ⏭  Lip sync skipped — merging audio directly onto video.")
        asm  = MediaAssembler(cfg)
        merged = asm.merge_audio_video(
            video_path, audio_path, f"{slug}_merged.mp4")
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
    if skip_upscale:
        print("\n[main] Phase 6: MediaAssembler — skipping upscale, just wrapping output...")
        # video_for_final could be an image or video, merge_audio_video handled the muxing
        final_path = video_for_final
    else:
        print("\n[main] Phase 6: MediaAssembler — upscaling to 1080x1920...")
        asm        = MediaAssembler(cfg)
        final_path = asm.upscale_and_encode(
            input_path  = video_for_final,
            output_name = f"{slug}_final.mp4",
            target      = "reels",
        )

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

    # Flags
    parser.add_argument("--skip-lipsync", action="store_true",
                        help="Skip Wav2Lip. Merge audio directly onto video. "
                             "Use for motion/atmosphere clips (beach run, B-roll).")
    parser.add_argument("--skip-upscale", action="store_true",
                        help="Skip SVD and FFmpeg upscale, just outputs static image with audio.")
    parser.add_argument("--force-ffmpeg-absolute", action="store_true",
                        help="Force MediaAssembler to use absolute pathing to resolve 1KB errors.")
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
    print(f"  Topic  : {topic}")
    print(f"  Frames : {frames} @ {fps}fps ≈ {frames/fps:.1f}s")
    print(f"  Motion : {motion} | augment={augmentation} | denoise={denoise}")
    print(f"  Res    : {(resolution or cfg['project']['resolution'])[0]}x"
          f"{(resolution or cfg['project']['resolution'])[1]} → 1080x1920 (upscaled)")
    print(f"  Lipsync: {'SKIP ⏭' if skip_lipsync else 'ENABLED'}")
    if args.preset:
        print(f"  Preset : {args.preset}")
    print("=" * 60)

    t0 = time.time()
    try:
        result = run_pipeline(
            cfg          = cfg,
            topic        = topic,
            frames       = frames,
            motion       = motion,
            fps          = fps,
            augmentation = augmentation,
            denoise      = denoise,
            resolution   = resolution,
            image_prompt = image_prompt,
            neg_prompt   = neg_prompt,
            skip_lipsync = skip_lipsync,
            skip_upscale = args.skip_upscale,
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
    main()
