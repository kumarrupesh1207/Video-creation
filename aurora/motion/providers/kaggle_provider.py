"""Kaggle free-GPU image->video provider (prepared, OFF by default).

Reuses RemoteI2VProvider's polling/timeout/error contract. Needs KAGGLE_USERNAME + KAGGLE_KEY in the
environment and ``motion.i2v.kaggle.enabled: true`` in config.yaml. No account is created here.
UNTESTED against the live Kaggle API (no credentials were available when written).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from aurora.motion.providers.remote_i2v_base import (
    RemoteAuthenticationError,
    RemoteI2VProvider,
    RemoteWorkerCrashError,
)

WORKER_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "kaggle" / "aurora_kaggle_worker.py"


class KaggleProvider(RemoteI2VProvider):
    provider_name = "kaggle_provider"

    def __init__(self, config: dict):
        super().__init__(config)
        cfg = self._provider_config()
        self.timeout_sec = int(cfg.get("timeout_sec", 3600))  # first run downloads ~30 GB of weights
        self.poll_sec = int(cfg.get("poll_sec", 20))

    def _provider_config(self) -> dict:
        return self.config.get("motion", {}).get("i2v", {}).get("kaggle", {})

    def _api(self):
        user, key = os.getenv("KAGGLE_USERNAME", "").strip(), os.getenv("KAGGLE_KEY", "").strip()
        if not user or not key:
            raise RemoteAuthenticationError(f"[{self.provider_name}] KAGGLE_USERNAME / KAGGLE_KEY are not set.")
        if key.startswith("KGAT") and not os.getenv("KAGGLE_API_TOKEN"):
            os.environ["KAGGLE_API_TOKEN"] = key  # new-format token; legacy keys keep using KAGGLE_KEY
        from kaggle.api.kaggle_api_extended import KaggleApi  # pip install kaggle

        api = KaggleApi()
        api.authenticate()
        return api, user

    def _submit_remote_job(self, payload, request_path, response_path, response_json):
        del request_path, response_path, response_json
        cfg = self._provider_config()
        if not cfg.get("enabled", False):
            raise RuntimeError(f"[{self.provider_name}] Provider disabled in config (motion.i2v.kaggle.enabled).")
        api, user = self._api()
        slug = re.sub(r"[^a-z0-9-]", "-", f"aurora-i2v-{payload['request_id']}".lower())[:50]
        from PIL import Image

        with Image.open(payload["source_image"]) as src:
            portrait = src.height > src.width
        long_side, short_side = int(cfg.get("width", 832)), int(cfg.get("height", 480))
        width, height = (short_side, long_side) if portrait else (long_side, short_side)
        request = {
            "mode": "i2v",
            # Wan follows the motion text literally: "powering up / dynamic action" produced crash-zooms and
            # blown-out faces, so lock the camera and ask for subtle, in-place motion around the subject.
            "prompt": (f"{payload.get('motion_prompt') or 'the subject'}. " + cfg.get(
                "motion_suffix", "Static locked-off camera, the subject stays in place and keeps the same pose and "
                "face, subtle natural motion: hair and clothes sway gently, light and aura flicker softly.")),
            "negative": cfg.get("negative_prompt", "camera zoom, zoom in, camera movement, morphing, distorted face, "
                                "deformed body, stretched limbs, overexposed, blown out highlights, flicker, blurry"),
            "seed": payload.get("seed") or int(cfg.get("seed", 42)),
            "width": width,
            "height": height,
            "num_frames": int(cfg.get("num_frames", 49)),
            "steps": int(cfg.get("steps", 30)),
            "model": cfg.get("model", "Wan-AI/Wan2.2-TI2V-5B-Diffusers"),
            "image_b64": base64.b64encode(Path(payload["source_image"]).read_bytes()).decode("ascii"),
        }
        folder = Path(tempfile.mkdtemp(prefix="aurora_kaggle_"))
        (folder / "script.py").write_text(
            f"import json as _json\nREQUEST = _json.loads({json.dumps(request)!r})\n"
            + WORKER_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        (folder / "kernel-metadata.json").write_text(json.dumps({
            "id": f"{user}/{slug}", "title": slug, "code_file": "script.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
        }), encoding="utf-8")
        api.kernels_push(str(folder))
        shutil.rmtree(folder, ignore_errors=True)
        return {"ref": f"{user}/{slug}"}

    def _poll_remote_status(self, remote_job, response_json):
        del response_json
        time.sleep(self.poll_sec)  # be gentle with the Kaggle API
        api, _ = self._api()
        status = str(api.kernels_status(remote_job["ref"]).status).lower()
        if "error" in status or "cancel" in status:
            raise RemoteWorkerCrashError(f"[{self.provider_name}] Kaggle kernel ended with status: {status}")
        return {"status": "COMPLETED" if "complete" in status else "processing", "raw_status": status}

    def _download_remote_artifact(self, remote_job, response_path, status_details=None):
        del status_details
        api, _ = self._api()
        out = Path(tempfile.mkdtemp(prefix="aurora_kaggle_out_"))
        # kaggle writes the run log with the locale encoding; on Windows (cp1252) that crashes, so download in UTF-8 mode
        import subprocess
        import sys
        subprocess.run(
            [sys.executable, "-X", "utf8", "-c",
             "import sys; from kaggle.api.kaggle_api_extended import KaggleApi; "
             "a = KaggleApi(); a.authenticate(); a.kernels_output(sys.argv[1], path=sys.argv[2])",
             remote_job["ref"], str(out)],
            check=True, env=os.environ.copy(), timeout=600,
        )
        clips = sorted(out.rglob("out.mp4")) or sorted(out.rglob("*.mp4"))  # the worker's final clip wins over intermediates
        if not clips:
            raise RuntimeError(f"[{self.provider_name}] Kaggle kernel finished without an .mp4 output.")
        response_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(clips[0], response_path)
        shutil.rmtree(out, ignore_errors=True)
        return response_path
