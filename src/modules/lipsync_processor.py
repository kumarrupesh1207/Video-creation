"""
lipsync_processor.py
─────────────────────
Day 4: Wav2Lip subprocess manager.

⚠️  HIGH RISK MODULE — Read before touching:

Wav2Lip requires its OWN isolated Python 3.8 venv.
Running it in the same environment as XTTS / PyTorch 2.x WILL break.

Required isolated venv setup (do this once):
    # 1. Install Python 3.8 (from python.org — keep alongside Python 3.10+)
    # 2. Create the venv
    py -3.8 -m venv C:/tools/Wav2Lip/venv

    # 3. Activate and install pinned deps
    C:/tools/Wav2Lip/venv/Scripts/activate
    pip install torch==1.12.1+cu116 torchvision==0.13.1+cu116 --extra-index-url https://download.pytorch.org/whl/cu116
    pip install librosa==0.8.1 numpy==1.23.5 opencv-python==4.7.0.72 batch-face>=1.3.0

    # 4. Test it
    python C:/tools/Wav2Lip/inference.py --help

Dependency pin rationale:
    librosa==0.8.1  → 0.9+ breaks the audio resampling used internally
    numpy==1.23.5   → 1.24+ removes np.bool/np.int aliases Wav2Lip uses
    torch==1.12.1   → 2.x breaks the model loading checkpoint format
"""

import subprocess
import shutil
from pathlib import Path


class LipSyncProcessor:
    """
    Runs Wav2Lip inference as an isolated Python 3.8 subprocess.
    The parent process (Python 3.10+) has ZERO VRAM footprint during this phase.
    """

    def __init__(self, config: dict):
        wav_cfg          = config.get("models", {}).get("wav2lip", {})
        self.python_bin  = Path(wav_cfg.get("python_bin",
                                "C:/tools/Wav2Lip/venv/Scripts/python.exe"))
        self.script      = Path(wav_cfg.get("inference_script",
                                "C:/tools/Wav2Lip/inference.py"))
        self.checkpoint  = Path(wav_cfg.get("checkpoint",
                                "C:/tools/Wav2Lip/checkpoints/wav2lip_gan.pth"))
        self.resize      = int(wav_cfg.get("resize_factor", 1))
        self.pads        = [
            int(wav_cfg.get("pad_top",    0)),
            int(wav_cfg.get("pad_bottom", 10)),
            int(wav_cfg.get("pad_left",   0)),
            int(wav_cfg.get("pad_right",  0)),
        ]
        self.out_dir = Path(config.get("paths", {}).get(
            "videos_dir", "outputs/videos"))
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────
    def process(self, video_path: Path, audio_path: Path,
                output_name: str = None) -> Path:
        """
        Run Wav2Lip on a video + audio pair.

        Args:
            video_path:  Input video (from SVD output or face reference image)
            audio_path:  Synthesized voiceover WAV
            output_name: Output filename. Default: lipsync_<video_stem>.mp4

        Returns:
            Path to the lip-synced output video.
        """
        video_path = Path(video_path)
        audio_path = Path(audio_path)

        if not video_path.exists():
            raise FileNotFoundError(f"[LipSync] Video not found: {video_path}")
        if not audio_path.exists():
            raise FileNotFoundError(f"[LipSync] Audio not found: {audio_path}")

        # Validate environment before wasting time
        self._validate_env()

        # Build output path
        if output_name is None:
            output_name = f"lipsync_{video_path.stem}.mp4"
        out_path = self.out_dir / output_name

        cmd = self._build_command(video_path, audio_path, out_path)
        self._run(cmd, out_path)
        return out_path

    def validate_env(self) -> bool:
        """
        Public validation — call this in verify_setup.py.
        Returns True if environment is ready, False otherwise.
        """
        try:
            self._validate_env()
            return True
        except Exception as e:
            print(str(e))
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────
    def _validate_env(self) -> None:
        """Check that the Wav2Lip venv and checkpoint exist."""
        errors = []

        if not self.python_bin.exists():
            errors.append(
                f"  ❌ Python 3.8 venv not found: {self.python_bin}\n"
                "     Create it: py -3.8 -m venv C:/tools/Wav2Lip/venv"
            )

        if not self.script.exists():
            errors.append(
                f"  ❌ inference.py not found: {self.script}\n"
                "     Clone Wav2Lip: git clone https://github.com/Rudrabha/Wav2Lip.git C:/tools/Wav2Lip"
            )

        if not self.checkpoint.exists():
            errors.append(
                f"  ❌ Checkpoint not found: {self.checkpoint}\n"
                "     Download wav2lip_gan.pth from the Wav2Lip README\n"
                "     Place at: C:/tools/Wav2Lip/checkpoints/wav2lip_gan.pth"
            )

        if errors:
            raise RuntimeError(
                "[LipSync] Environment not ready:\n" + "\n".join(errors)
            )

        # Quick Python version check
        result = subprocess.run(
            [str(self.python_bin), "--version"],
            capture_output=True, text=True, timeout=5
        )
        ver_str = result.stdout.strip() or result.stderr.strip()
        if "3.8" not in ver_str:
            raise RuntimeError(
                f"[LipSync] ⚠️  Wrong Python version: {ver_str}\n"
                "  Wav2Lip requires Python 3.8 exactly.\n"
                f"  Ensure {self.python_bin} points to a Python 3.8 interpreter."
            )
        print(f"[LipSync] ✅ Env: {ver_str} | checkpoint OK")

    def _build_command(self, video: Path, audio: Path, out: Path) -> list:
        """Construct the Wav2Lip inference command."""
        return [
            str(self.python_bin),
            str(self.script),
            "--checkpoint_path", str(self.checkpoint),
            "--face",            str(video),
            "--audio",           str(audio),
            "--outfile",         str(out),
            "--resize_factor",   str(self.resize),
            "--pads",
                str(self.pads[0]),
                str(self.pads[1]),
                str(self.pads[2]),
                str(self.pads[3]),
            "--nosmooth",
        ]

    def _run(self, cmd: list, expected_out: Path) -> None:
        """Execute the subprocess and stream output in real time."""
        print(f"\n[LipSync] Launching Wav2Lip subprocess...")
        print(f"[LipSync] Command: {' '.join(cmd[:5])} ...")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            # Stream output line by line
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    print(f"  [wav2lip] {line}")

            proc.wait(timeout=600)   # 10-min hard timeout

        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("[LipSync] ❌ Wav2Lip timed out after 10 minutes.")
        except FileNotFoundError:
            raise RuntimeError(
                f"[LipSync] ❌ Cannot execute: {cmd[0]}\n"
                "  Check that the Python 3.8 venv path is correct in config.yaml"
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"[LipSync] ❌ Wav2Lip exited with code {proc.returncode}.\n"
                "  Check the output above for the root cause.\n"
                "  Common fixes:\n"
                "    • 'np.bool' error → numpy==1.23.5 in Wav2Lip venv\n"
                "    • 'librosa' error → librosa==0.8.1 in Wav2Lip venv\n"
                "    • CUDA OOM       → use face image (not video) as --face input"
            )

        if not expected_out.exists():
            raise RuntimeError(
                f"[LipSync] ❌ Expected output not created: {expected_out}\n"
                "  Wav2Lip may have silently failed. Review subprocess output above."
            )

        size_mb = expected_out.stat().st_size / (1024 * 1024)
        print(f"[LipSync] ✅ Done → {expected_out} ({size_mb:.1f} MB)")
