"""
test_audio.py — Day 3: XTTS-v2 Audio Engine Test
──────────────────────────────────────────────────
Synthesizes a test voiceover and validates the VRAM unload cycle.

Prerequisites:
    1. pip install TTS
    2. assets/reference_voice.wav (10–30s clean voice clip)

Usage (run from project root):
    python src/test_audio.py
    python src/test_audio.py --text "Your custom test script here."
    python src/test_audio.py --check-only   # just validates env, no synthesis
"""

import sys
import time
import yaml
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from modules.audio_engine import AudioEngine


CONFIG_PATH = "config.yaml"

TEST_TEXT = (
    "The future of finance is not in banks. It is in the hands of builders. "
    "Three tools are transforming the game right now: AI credit scoring, "
    "real-time payment rails, and decentralized identity. "
    "The question is not if you will use them. It is whether you will lead or follow."
)


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config.yaml not found: {p.resolve()}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Day 3: XTTS-v2 Audio Engine Test")
    parser.add_argument("--text",       default=TEST_TEXT,    help="Text to synthesize")
    parser.add_argument("--config",     default=CONFIG_PATH)
    parser.add_argument("--out",        default="test_voiceover.wav",
                        help="Output filename in outputs/audio/ (default: test_voiceover.wav)")
    parser.add_argument("--check-only", action="store_true",
                        help="Only check the environment, skip synthesis")
    args = parser.parse_args()

    print("=" * 60)
    print("  🎙️  VIDEO FACTORY — Day 3: XTTS-v2 Audio Test")
    print("=" * 60)

    # 1. Load config
    cfg = load_config(args.config)
    print("[Config] ✅ Loaded.")

    # 2. Check TTS installation
    print("\n[AudioEngine] Checking environment...")
    if not AudioEngine.check_installation():
        print("\n  Install TTS: pip install TTS")
        print("  This pulls ~2 GB model on first run — needs internet.")
        sys.exit(1)

    # 3. Check reference audio
    ref_path = Path(cfg.get("paths", {}).get(
        "reference_voice", "assets/reference_voice.wav"))
    if not ref_path.exists():
        print(f"\n[AudioEngine] ❌ Reference voice missing: {ref_path.resolve()}")
        print("\n  Action Required:")
        print("  ─────────────────────────────────────────────────────")
        print("  1. Record 10–30 seconds of the voice you want to clone")
        print("  2. Save as WAV (22050 Hz recommended, mono or stereo)")
        print(f"  3. Place at: {ref_path.resolve()}")
        print("\n  Quick options:")
        print("    • Use Audacity (free) to record from your mic")
        print("    • Download a royalty-free voice from freesound.org")
        print("    • Export from any existing video using FFmpeg:")
        print('      ffmpeg -i your_video.mp4 -vn -acodec pcm_s16le assets/reference_voice.wav')
        sys.exit(1)

    if args.check_only:
        print("\n[AudioEngine] ✅ Environment check passed.")
        print("  Run without --check-only to synthesize audio.")
        sys.exit(0)

    # 4. Initialize engine
    print("\n[AudioEngine] Initializing...")
    engine = AudioEngine(cfg)

    # 5. Load model to GPU
    print("[AudioEngine] Loading XTTS-v2 (first run downloads ~2 GB)...")
    t_load = time.time()
    try:
        engine.load()
    except RuntimeError as e:
        print(f"\n{e}")
        sys.exit(1)
    load_time = time.time() - t_load
    print(f"[AudioEngine] Model loaded in {load_time:.1f}s")

    # 6. Synthesize
    print(f"\n[AudioEngine] Text ({len(args.text)} chars):")
    print(f"  \"{args.text[:80]}{'...' if len(args.text) > 80 else ''}\"")
    t_start = time.time()
    try:
        out_path = engine.synthesize(args.text, args.out)
    except Exception as e:
        print(f"\n[AudioEngine] ❌ Synthesis failed: {e}")
        engine.unload()
        sys.exit(1)
    elapsed = time.time() - t_start

    # 7. Unload VRAM — CRITICAL before Wav2Lip
    print(f"\n[VRAM] Unloading XTTS-v2 (required before Wav2Lip)...")
    engine.unload()

    # 8. Report
    size_kb = out_path.stat().st_size / 1024
    print("\n" + "=" * 60)
    print(f"  ✅ AUDIO SYNTHESIZED  ({elapsed:.1f}s)")
    print("=" * 60)
    print(f"\n  📁 Output  : {out_path.resolve()}")
    print(f"  📊 Size    : {size_kb:.1f} KB")
    print(f"  🎯 Duration: ~{size_kb/44:.1f}s (estimated)")
    print(f"  🧹 VRAM    : Cleared and ready for Wav2Lip (Day 4)")
    print(f"\n  ✅ Listen to: outputs/audio/{args.out}")
    print(f"  Next → Day 4: python src/test_lipsync.py")


if __name__ == "__main__":
    main()
