"""
test_video.py — Day 2: SVD-XT Video Generation Test
─────────────────────────────────────────────────────
Takes the First Light image and generates a 4-second motion clip.

Usage (run from project root):
    # Auto-picks the latest image from outputs/images/
    python src/test_video.py

    # Or specify an image explicitly
    python src/test_video.py --image outputs/images/ComfyUI_00001_.png

    # Conservative settings for first run (fewer frames, less VRAM)
    python src/test_video.py --frames 14 --motion 80

VRAM Budget (12 GB):
    Before run   → SDXL cleared via free_vram()
    SVD-XT run   → ~9–11 GB
    After run    → free_vram() clears for XTTS
"""

import sys
import time
import yaml
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from modules.comfy_orchestrator import ComfyOrchestrator
from modules.video_engine import VideoEngine


CONFIG_PATH   = "config.yaml"
IMAGES_DIR    = Path("outputs/images")
VIDEOS_DIR    = Path("outputs/videos")


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config.yaml not found: {p.resolve()}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def pick_latest_image(directory: Path) -> Path:
    """Auto-select the most recently created image from outputs/images/."""
    images = sorted(
        [f for f in directory.glob("*.png")] +
        [f for f in directory.glob("*.jpg")] +
        [f for f in directory.glob("*.jpeg")],
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    if not images:
        raise FileNotFoundError(
            f"No images found in {directory.resolve()}\n"
            "  → Run first_light.py first to generate a keyframe."
        )
    return images[0]


def check_svd_workflow(cfg: dict) -> str:
    wf = cfg.get("models", {}).get("comfyui", {}).get(
        "video_workflow", "src/workflows/svd_video.json")
    p = Path(wf)
    if not p.exists():
        raise FileNotFoundError(
            f"SVD workflow not found: {p.resolve()}\n"
            "  → Export your SVD workflow from ComfyUI:\n"
            "     Settings → Enable Dev Mode → Load SVD workflow → Save (API Format)\n"
            f"    Save to: {p.resolve()}"
        )
    return str(p)


def main():
    parser = argparse.ArgumentParser(description="Day 2: SVD-XT Video Generation Test")
    parser.add_argument("--image",    default=None,   help="Path to keyframe image")
    parser.add_argument("--config",   default=CONFIG_PATH)
    parser.add_argument("--frames",   type=int, default=14,
                        help="Number of video frames (14=2.3s, 25=4.2s). Default: 14")
    parser.add_argument("--motion",   type=int, default=127,
                        help="Motion bucket 1-255. Higher = more motion. Default: 127")
    parser.add_argument("--fps",      type=int, default=6,
                        help="Output FPS. Default: 6")
    parser.add_argument("--out-name", default=None,
                        help="Output filename (default: test_motion_001.mp4, auto-increments)")
    parser.add_argument("--skip-cleanup", action="store_true",
                        help="Skip pre-run VRAM cleanup (only if no image gen ran before)")
    args = parser.parse_args()

    print("=" * 60)
    print("  🎬 VIDEO FACTORY — Day 2: SVD-XT Video Test")
    print("=" * 60)

    # 1. Load config
    print(f"\n[Config] Loading: {args.config}")
    cfg = load_config(args.config)
    print("[Config] ✅ Loaded.")

    # 2. Resolve keyframe
    if args.image:
        image_path = Path(args.image)
        if not image_path.exists():
            print(f"[Error] ❌ Image not found: {image_path}")
            sys.exit(1)
    else:
        print(f"\n[Image] Auto-selecting latest from: {IMAGES_DIR}")
        try:
            image_path = pick_latest_image(IMAGES_DIR)
        except FileNotFoundError as e:
            print(f"[Error] ❌ {e}")
            sys.exit(1)
    print(f"[Image] ✅ Keyframe: {image_path.name}")

    # 3. Check SVD workflow exists
    try:
        check_svd_workflow(cfg)
    except FileNotFoundError as e:
        print(f"\n[Workflow] ❌ {e}")
        sys.exit(1)

    # 4. Connect to ComfyUI
    print("\n[ComfyUI] Connecting...")
    try:
        orch = ComfyOrchestrator(cfg)
    except RuntimeError as e:
        print(str(e))
        sys.exit(1)

    # 5. VRAM Cleanup — clear SDXL weights before SVD loads
    if not args.skip_cleanup:
        print("\n[VRAM] Flushing previous models before SVD loads...")
        orch.free_vram()
        VideoEngine.vram_cleanup("pre-SVD")

    # 6. Init VideoEngine and generate
    engine = VideoEngine(cfg, orch)
    duration_est = args.frames / args.fps
    print(f"\n[VideoEngine] Generating {args.frames} frames "
          f"≈ {duration_est:.1f}s @ {args.fps}fps | motion={args.motion}")
    print("[VideoEngine] ⏳ This will take 2–5 minutes on a 12GB GPU...\n")

    t_start = time.time()
    try:
        video_path = engine.generate(
            image_path   = image_path,
            motion_bucket = args.motion,
            fps          = args.fps,
            frames       = args.frames,
        )
    except Exception as e:
        print(f"\n[VideoEngine] ❌ Generation failed: {e}")
        print("\n  Troubleshooting:")
        print("  • CUDA OOM → reduce --frames to 10 or resolution to 512x768 in config.yaml")
        print("  • KeyError on node ID → run: python src/inspect_workflow.py --workflow src/workflows/svd_video.json")
        print("  • Workflow missing → export SVD workflow from ComfyUI (API Format)")
        sys.exit(1)

    elapsed = time.time() - t_start

    # 7. Post-run VRAM cleanup
    print("\n[VRAM] Clearing SVD weights for next phase (XTTS)...")
    orch.free_vram()
    VideoEngine.vram_cleanup("post-SVD")

    # 8. Rename output to test_motion_XXX.mp4
    out_dir = VIDEOS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.out_name:
        final_name = args.out_name
    else:
        # Auto-increment: test_motion_001.mp4, test_motion_002.mp4, ...
        idx = 1
        while (out_dir / f"test_motion_{idx:03d}.mp4").exists():
            idx += 1
        final_name = f"test_motion_{idx:03d}.mp4"

    final_path = out_dir / final_name
    if video_path != final_path and video_path.exists():
        import shutil
        shutil.copy2(video_path, final_path)
        print(f"[VideoEngine] Copied → {final_path.name}")
    else:
        final_path = video_path

    # 9. Report
    print("=" * 60)
    print(f"  ✅ VIDEO GENERATED  ({elapsed:.1f}s)")
    print("=" * 60)
    print(f"\n  📁 Output : {final_path.resolve()}")
    print(f"  📐 Frames : {args.frames} @ {args.fps}fps ≈ {duration_est:.1f}s")
    print(f"  🎯 Motion : bucket={args.motion}")
    print(f"  🧹 VRAM   : Cleared and ready for XTTS (Day 3)")
    print(f"\n  ✅ Success criteria:")
    print(f"     outputs/videos/{final_name}  ← open this")
    print(f"  Next → Day 3: python src/test_audio.py")


if __name__ == "__main__":
    main()
