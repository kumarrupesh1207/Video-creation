"""
video_engine.py
───────────────
Day 2: SVD-XT Image-to-Video via ComfyUI API.

Responsibilities:
  - Accept a keyframe image (from First Light output)
  - Queue the SVD-XT workflow via ComfyOrchestrator
  - Enforce VRAM cleanup before SVD loads
  - Return the output .mp4 / .webp path

VRAM Protocol (12 GB budget):
  Phase          Model              Est. VRAM
  ─────────────────────────────────────────────
  Image Gen      SDXL / SD1.5       ~6–8 GB
  [CLEANUP]      free_vram()        ~0 GB
  Video Gen      SVD-XT (fp16)      ~9–11 GB
  [CLEANUP]      free_vram()        ~0 GB
  Audio          XTTS-v2            ~3–4 GB
  [CLEANUP]      unload()           ~0 GB
  Lip Sync       Wav2Lip subprocess (isolated)
"""

import gc
import time
from pathlib import Path

try:
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    torch = None
    _TORCH_AVAILABLE = False

try:
    from .comfy_orchestrator import ComfyOrchestrator
except ImportError:
    try:
        from modules.comfy_orchestrator import ComfyOrchestrator
    except ImportError:
        from comfy_orchestrator import ComfyOrchestrator


class VideoEngine:
    """SVD-XT Image-to-Video generator via ComfyUI API."""

    def __init__(self, config: dict, orchestrator: ComfyOrchestrator = None):
        self.cfg   = config
        self.orch  = orchestrator or ComfyOrchestrator(config)
        self.vid_cfg = config.get("models", {}).get("comfyui", {})
        self.res     = config.get("project", {}).get("resolution", [576, 1024])
        self.vid_out = Path(config.get("paths", {}).get("videos_dir", "outputs/videos"))
        self.vid_out.mkdir(parents=True, exist_ok=True)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────
    def generate(self, image_path: Path, motion_bucket: int = 127,
                 fps: int = 6, frames: int = 14,
                 augmentation: float = 0.0,
                 denoise: float = 0.5,
                 resolution: list = None,
                 timeout: int = 900) -> Path:
        """
        Run SVD-XT on a keyframe image.

        Args:
            image_path:    Path to the keyframe PNG/JPG
            motion_bucket: 1–255. Higher = more motion. Default 127.
                           Beach run: 150.  Talking head: 80.
            fps:           Output video FPS. SVD-XT works best at 6–8.
            frames:        Number of frames. 16 = ~2.7s at fps=6. Max 25 before OOM.
            augmentation:  Augmentation level 0.0–0.5. 0.15 adds water-splash detail.
            denoise:       Denoising strength 0.0–1.0. 0.7 keeps skin texture stable.
            resolution:    [W, H] override. Default: from config.yaml.
                           Use [512, 896] for VRAM-safe beach-run generation.

        Returns:
            Path to the generated video file.
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"[VideoEngine] Keyframe not found: {image_path}")

        # Resolution override (e.g. 512x896 for beach run)
        if resolution:
            self.res = resolution

        print(f"\n[VideoEngine] Keyframe     : {image_path.name}")
        print(f"[VideoEngine] Frames       : {frames} @ {fps}fps \u2248 {frames/fps:.1f}s")
        print(f"[VideoEngine] Motion       : {motion_bucket} | augment={augmentation} | denoise={denoise}")
        print(f"[VideoEngine] Resolution   : {self.res[0]}x{self.res[1]}")

        uploaded_name = self._upload_image(image_path)
        overrides     = self._build_svd_overrides(
            uploaded_name, motion_bucket, fps, frames,
            augmentation, denoise
        )

        # Step 3: Run the SVD workflow
        wf_path = self.vid_cfg.get("video_workflow", "src/workflows/svd_video.json")
        outputs = self.orch.run_workflow(wf_path, overrides, timeout=timeout)

        if not outputs:
            raise RuntimeError("[VideoEngine] SVD produced no outputs. Check ComfyUI logs.")

        video_path = outputs[0]
        print(f"[VideoEngine] ✅ Video ready → {video_path}")
        return video_path

    # ──────────────────────────────────────────────────────────────────────────
    # VRAM Cleanup (call before and after SVD)
    # ──────────────────────────────────────────────────────────────────────────
    @staticmethod
    def vram_cleanup(label: str = "VideoEngine") -> None:
        """
        Full VRAM purge protocol.
        Must be called:
          1. AFTER image gen (before SVD loads)
          2. AFTER SVD (before XTTS-v2 loads)
        """
        print(f"\n[VRAM] ── Cleanup: {label} ──────────────────")
        gc.collect()
        if _TORCH_AVAILABLE and torch.cuda.is_available():
            before = torch.cuda.memory_allocated() / 1e9
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
            after = torch.cuda.memory_allocated() / 1e9
            free  = torch.cuda.get_device_properties(0).total_memory / 1e9 - after
            print(f"[VRAM]   Before: {before:.2f} GB → After: {after:.2f} GB "
                  f"| Free: {free:.2f} GB")
        else:
            print("[VRAM]   CUDA/torch not available — skipping GPU cleanup.")
        time.sleep(2)   # let CUDA streams drain
        print(f"[VRAM] ── Done ───────────────────────────────\n")

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────
    def _upload_image(self, image_path: Path) -> str:
        """
        POST the keyframe image to ComfyUI's /upload/image endpoint.
        Returns the filename as ComfyUI registered it (use in LoadImage node).
        """
        import requests
        api_url = self.vid_cfg.get("api_url", "http://127.0.0.1:8188")
        url = f"{api_url}/upload/image"

        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                files={"image": (image_path.name, f, "image/png")},
                data={"overwrite": "true"},
                timeout=30
            )
        resp.raise_for_status()
        result = resp.json()
        uploaded_name = result.get("name", image_path.name)
        print(f"[VideoEngine] Uploaded keyframe → ComfyUI input: {uploaded_name}")
        return uploaded_name

    def _build_svd_overrides(self, image_name: str, motion_bucket: int,
                              fps: int, frames: int,
                              augmentation: float = 0.0,
                              denoise: float = 0.5) -> dict:
        """
        Build the node override dict for the SVD workflow.
        Node IDs come from config.yaml → models.comfyui.video_overrides.
        """
        vo = self.vid_cfg.get("video_overrides", {})

        image_node = str(vo.get("image_input_node",      "10"))
        svd_node   = str(vo.get("svd_conditioning_node", "11"))
        out_node   = str(vo.get("output_node",           "12"))

        overrides = {
            image_node: {
                "image": image_name
            },
            svd_node: {
                "width":              self.res[0],
                "height":             self.res[1],
                "video_frames":       frames,
                "motion_bucket_id":   motion_bucket,
                "fps":                fps,
                "augmentation_level": augmentation,
            },
        }

        print(f"[VideoEngine] Override map:")
        print(f"  Node [{image_node}] → image: {image_name}")
        print(f"  Node [{svd_node}]  → {self.res[0]}x{self.res[1]}, "
              f"frames={frames}, fps={fps}, motion={motion_bucket}, "
              f"augment={augmentation}")
        print(f"  Output node: [{out_node}]")

        return overrides
