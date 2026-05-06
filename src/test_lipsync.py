"""
test_lipsync.py — Day 4: Wav2Lip Final Render Test
────────────────────────────────────────────────────
Merges test_motion_001.mp4 + test_voice.wav into factory_video_v1.mp4.
Writes outputs/log.json with per-step status.

Usage (run from project root):
    python src/test_lipsync.py

    # Explicit asset paths:
    python src/test_lipsync.py \\
        --video outputs/videos/test_motion_001.mp4 \\
        --audio outputs/audio/test_voiceover.wav \\
        --out   factory_video_v1.mp4

    # Validate environment only (no render):
    python src/test_lipsync.py --check-only
"""

import sys
import time
import yaml
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from modules.lipsync_processor import LipSyncProcessor
from modules.pipeline_logger   import PipelineLogger


CONFIG_PATH = "config.yaml"
VIDEOS_DIR  = Path("outputs/videos")
AUDIO_DIR   = Path("outputs/audio")
FINAL_DIR   = Path("outputs/final")


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config.yaml not found: {p.resolve()}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def find_latest(directory: Path, patterns: list) -> Path:
    """Return the most-recently-modified file matching any pattern."""
    matches = []
    for pat in patterns:
        matches.extend(directory.glob(pat))
    if not matches:
        return None
    return max(matches, key=lambda f: f.stat().st_mtime)


def main():
    parser = argparse.ArgumentParser(
        description="Day 4: Wav2Lip Final Render — merge video + audio"
    )
    parser.add_argument("--video",      default=None,
                        help="Input video (default: latest test_motion_*.mp4)")
    parser.add_argument("--audio",      default=None,
                        help="Input audio WAV (default: latest .wav in outputs/audio)")
    parser.add_argument("--out",        default="factory_video_v1.mp4",
                        help="Output filename in outputs/final/ (default: factory_video_v1.mp4)")
    parser.add_argument("--config",     default=CONFIG_PATH)
    parser.add_argument("--check-only", action="store_true",
                        help="Validate Wav2Lip environment without rendering")
    args = parser.parse_args()

    print("=" * 60)
    print("  🎬 VIDEO FACTORY — Day 4: Lip-Sync Final Render")
    print("=" * 60)

    # 1. Load config
    cfg = load_config(args.config)
    print("[Config] ✅ Loaded.")

    # 2. Init logger
    log = PipelineLogger(cfg, topic="test_lipsync")

    # 3. Init processor
    lp = LipSyncProcessor(cfg)

    # 4. Environment check
    print("\n[LipSync] Validating Wav2Lip environment...")
    env_ok = lp.validate_env()
    if not env_ok:
        print("\n  ─── Wav2Lip Setup Guide ─────────────────────────────")
        print("  1. Install Python 3.8 from python.org")
        print("  2. py -3.8 -m venv C:/tools/Wav2Lip/venv")
        print("  3. C:/tools/Wav2Lip/venv/Scripts/activate")
        print("  4. pip install -r requirements_wav2lip.txt")
        print("  5. git clone https://github.com/Rudrabha/Wav2Lip C:/tools/Wav2Lip")
        print("  6. Download wav2lip_gan.pth → C:/tools/Wav2Lip/checkpoints/")
        print("  ──────────────────────────────────────────────────────")
        log.fail_step("lipsync", "Wav2Lip environment not ready")
        sys.exit(1)

    if args.check_only:
        print("\n[LipSync] ✅ Environment check passed. Ready to render.")
        print("  Run without --check-only to execute.")
        sys.exit(0)

    # 5. Resolve video input
    if args.video:
        video_path = Path(args.video)
    else:
        video_path = find_latest(VIDEOS_DIR, ["test_motion_*.mp4", "*.mp4"])
    if not video_path or not video_path.exists():
        print(f"\n[Error] ❌ No video found in {VIDEOS_DIR}")
        print("  → Run: python src/test_video.py --frames 12 --motion 80")
        sys.exit(1)
    print(f"\n[Assets] ✅ Video : {video_path.name}")

    # 6. Resolve audio input
    if args.audio:
        audio_path = Path(args.audio)
    else:
        audio_path = find_latest(AUDIO_DIR, ["*.wav"])
    if not audio_path or not audio_path.exists():
        print(f"\n[Error] ❌ No audio found in {AUDIO_DIR}")
        print("  → Run: python src/test_audio.py")
        sys.exit(1)
    print(f"[Assets] ✅ Audio : {audio_path.name}")

    # 7. Set up output path in final/
    FINAL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FINAL_DIR / args.out

    # 8. Run Wav2Lip
    log.start_step("lipsync")
    print(f"\n[LipSync] Rendering → outputs/final/{args.out}")
    print("[LipSync] ⏳ Wav2Lip processes ~2 FPS — expect 1–3 mins for a 2s clip...\n")
    t_start = time.time()

    try:
        result = lp.process(
            video_path  = video_path,
            audio_path  = audio_path,
            output_name = args.out     # goes to outputs/final/ via out_dir override below
        )
        # lipsync_processor saves to videos_dir by default; move to final/
        if result.parent != FINAL_DIR and result.exists():
            import shutil
            final_path = FINAL_DIR / result.name
            shutil.move(str(result), str(final_path))
            result = final_path

    except Exception as e:
        log.fail_step("lipsync", str(e))
        print(f"\n[LipSync] ❌ {e}")
        sys.exit(1)

    log.end_step("lipsync", str(result))
    log.complete(final_output=str(result))
    elapsed = time.time() - t_start

    # 9. Final report
    size_mb = result.stat().st_size / (1024 * 1024) if result.exists() else 0

    print("\n" + "=" * 60)
    print(f"  🏁 FACTORY FINISH  ({elapsed:.1f}s)")
    print("=" * 60)
    print(f"\n  📁 Final output : {result.resolve()}")
    print(f"  📊 File size    : {size_mb:.1f} MB")
    print(f"  📋 Log          : outputs/log.json")
    print(f"\n  ✅ Success Checklist:")
    print(f"     □ Open outputs/final/{args.out}")
    print(f"     □ Check mouth movement syncs with voice")
    print(f"     □ Confirm 9:16 vertical frame")
    print(f"     □ Open Task Manager — VRAM back to baseline (~1-2 GB)")
    print(f"     □ Open outputs/log.json — all steps = 'success'\n")
    print(f"  Next → Phase 3 Batch: python src/batch_orchestrator.py")


if __name__ == "__main__":
    main()
