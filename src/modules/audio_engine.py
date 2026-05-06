"""
audio_engine.py
───────────────
Day 3: Coqui XTTS-v2 speech synthesis.

Responsibilities:
  - Load XTTS-v2 model to GPU on demand
  - Synthesize speech from text using a reference voice
  - Explicit unload() to clear VRAM before Wav2Lip loads
  - Segment-aware synthesis for multi-part scripts

VRAM Protocol:
  Load  → ~3.5 GB consumed
  Unload → torch.cuda.empty_cache() → back to ~0 GB

⚠️  HIGH RISK: TTS library installs a specific torch version.
    Install in your main venv AFTER confirming CUDA version:
    pip install TTS  (pulls torch 2.x automatically)
    Then verify: python -c "from TTS.api import TTS; print('OK')"
"""

from pathlib import Path

# Soft-import TTS — won't crash if not yet installed
try:
    from TTS.api import TTS as CoquiTTS
    _TTS_AVAILABLE = True
except ImportError:
    CoquiTTS = None
    _TTS_AVAILABLE = False

try:
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    torch = None
    _TORCH_AVAILABLE = False


class AudioEngine:
    """Coqui XTTS-v2 text-to-speech engine with explicit VRAM lifecycle."""

    MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"

    def __init__(self, config: dict):
        xtts_cfg = config.get("models", {}).get("xtts", {})
        self.model_name  = xtts_cfg.get("model_name", self.MODEL_NAME)
        self.language    = xtts_cfg.get("language",   "en")
        self.speed       = xtts_cfg.get("speed",      1.0)
        self.temperature = xtts_cfg.get("temperature", 0.65)
        self.ref_audio   = Path(config.get("paths", {}).get(
            "reference_voice", "assets/reference_voice.wav"))
        self.out_dir     = Path(config.get("paths", {}).get(
            "audio_dir", "outputs/audio"))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._tts = None   # Not loaded until load() is called

    # ──────────────────────────────────────────────────────────────────────────
    # VRAM Lifecycle
    # ──────────────────────────────────────────────────────────────────────────
    def load(self) -> None:
        """Load XTTS-v2 to GPU. Call once before synthesize() calls."""
        if not _TTS_AVAILABLE:
            print(
                "[AudioEngine] ⚠️ TTS not installed.\n"
                "  Will use dummy silent audio for pipeline completion."
            )
            return
        if self._tts is not None:
            print("[AudioEngine] Already loaded — skipping.")
            return

        self._verify_reference_audio()
        device = "cuda" if (_TORCH_AVAILABLE and torch.cuda.is_available()) else "cpu"
        print(f"[AudioEngine] Loading {self.model_name} → {device.upper()}...")

        # agree=True skips the interactive license prompt
        self._tts = CoquiTTS(model_name=self.model_name, progress_bar=True).to(device)
        if _TORCH_AVAILABLE and device == "cuda":
            vram_used = torch.cuda.memory_allocated() / 1e9
            print(f"[AudioEngine] ✅ Loaded. VRAM in use: {vram_used:.2f} GB")
        else:
            print("[AudioEngine] ✅ Loaded on CPU (slower but functional).")

    def unload(self) -> None:
        """
        ⚠️ CRITICAL: Call this before LipSyncProcessor runs.
        Releases XTTS-v2 from GPU memory completely.
        """
        import gc
        if self._tts is not None:
            del self._tts
            self._tts = None
        gc.collect()
        if _TORCH_AVAILABLE and torch.cuda.is_available():
            before = torch.cuda.memory_allocated() / 1e9
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
            after = torch.cuda.memory_allocated() / 1e9
            print(f"[AudioEngine] 🧹 Unloaded XTTS-v2. "
                  f"VRAM: {before:.2f} GB → {after:.2f} GB")
        else:
            print("[AudioEngine] 🧹 XTTS-v2 unloaded (CPU mode).")

    # ──────────────────────────────────────────────────────────────────────────
    # Synthesis
    # ──────────────────────────────────────────────────────────────────────────
    def synthesize(self, text: str, filename: str = "voiceover.wav") -> Path:
        """
        Synthesize a single text string to a WAV file.

        Args:
            text:     The full script text to synthesize.
            filename: Output filename inside outputs/audio/.

        Returns:
            Path to the generated .wav file.
        """
        if self._tts is None:
            self.load()

        out_path = self.out_dir / filename
        print(f"\n[AudioEngine] Synthesizing {len(text)} chars → {filename}")
        print(f"[AudioEngine] Reference: {self.ref_audio.name}")

        if not _TTS_AVAILABLE:
            print(f"[AudioEngine] ⚠️ TTS not installed. Writing a valid silent 3-second WAV file.")
            import wave
            with wave.open(str(out_path), 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(22050)
                wav_file.writeframes(b'\x00' * (22050 * 2 * 3))
        else:
            self._tts.tts_to_file(
                text         = text,
                speaker_wav  = str(self.ref_audio),
                language     = self.language,
                file_path    = str(out_path),
                speed        = self.speed,
            )

        size_kb = out_path.stat().st_size / 1024
        print(f"[AudioEngine] ✅ Saved → {out_path} ({size_kb:.1f} KB)")
        return out_path

    def synthesize_segments(self, segments: list, base_name: str = "segment") -> list:
        """
        Synthesize a list of script segments as separate WAV files.

        Args:
            segments:  List of dicts: [{"text": "...", "duration_sec": 5}, ...]
            base_name: Filename prefix (e.g. 'segment' → segment_001.wav)

        Returns:
            List of Paths to generated WAV files.
        """
        if self._tts is None:
            self.load()

        out_paths = []
        for i, seg in enumerate(segments, start=1):
            text = seg.get("text", "")
            if not text.strip():
                continue
            fname = f"{base_name}_{i:03d}.wav"
            path  = self.synthesize(text, fname)
            out_paths.append(path)

        print(f"\n[AudioEngine] ✅ {len(out_paths)} segment(s) synthesized.")
        return out_paths

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────
    def _verify_reference_audio(self) -> None:
        if not self.ref_audio.exists():
            raise FileNotFoundError(
                f"[AudioEngine] ❌ Reference voice not found: {self.ref_audio.resolve()}\n"
                "  Action required:\n"
                "    1. Record or find a clean 10–30 second voice clip (.wav)\n"
                "    2. Save it to: assets/reference_voice.wav\n"
                "    3. Requirements: 22050 Hz, mono or stereo, no background noise\n"
                "  Quick test clip: use Audacity or online recorder"
            )
        size_kb = self.ref_audio.stat().st_size / 1024
        if size_kb < 50:
            print(f"[AudioEngine] ⚠️  Reference audio is very small ({size_kb:.0f} KB). "
                  "XTTS needs at least 6 seconds for good voice cloning.")
        else:
            print(f"[AudioEngine] ✅ Reference audio: {self.ref_audio.name} ({size_kb:.0f} KB)")

    @staticmethod
    def check_installation() -> bool:
        """Quick check: is TTS installed and importable?"""
        if not _TTS_AVAILABLE:
            print("[AudioEngine] ❌ TTS not installed.")
            print("  Install: pip install TTS")
            return False
        print("[AudioEngine] ✅ TTS library available.")
        return True
