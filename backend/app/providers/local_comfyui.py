from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

from backend.app.providers.base import ImageProvider, ImageResult
from aurora.assets.manifest import normalize_prompt
from src.modules.comfy_orchestrator import ComfyOrchestrator


class LocalComfyUIProvider(ImageProvider):
    """Local fallback provider backed by the existing ComfyUI workflow."""

    DEFAULT_NEGATIVE = (
        "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
        "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
        "signature, watermark, username, blurry, artist name, realistic, 3d, photorealistic"
    )

    def generate(self, prompt: str, style: str, resolution: list[int], job_id: str) -> ImageResult:
        return self.generate_with_params(prompt, style, resolution, job_id)

    def generate_with_params(
        self,
        prompt: str,
        style: str,
        resolution: list[int],
        job_id: str,
        negative_prompt: str | None = None,
        seed: int | None = None,
    ) -> ImageResult:
        start = time.time()
        cfg = copy.deepcopy(self.config)
        job_root = Path(cfg.get("paths", {}).get("base_output", "outputs")) / job_id
        image_dir = job_root / "images"
        video_dir = job_root / "videos"
        image_dir.mkdir(parents=True, exist_ok=True)
        video_dir.mkdir(parents=True, exist_ok=True)
        cfg.setdefault("paths", {})
        cfg["paths"]["images_dir"] = str(image_dir)
        cfg["paths"]["videos_dir"] = str(video_dir)

        comfy_cfg = cfg.get("models", {}).get("comfyui", {})
        img_wf = comfy_cfg.get("image_workflow", "src/workflows/image_gen.json")
        overrides_cfg = comfy_cfg.get("image_overrides", {})
        runtime_negative = negative_prompt
        if runtime_negative is None:
            runtime_negative = (
                self.config.get("providers", {})
                .get("local_comfyui", {})
                .get("negative_prompt", self.DEFAULT_NEGATIVE)
            )
        try:
            runtime_seed = int(seed) if seed is not None else 0
        except (TypeError, ValueError):
            runtime_seed = 0
        if runtime_seed <= 0:
            runtime_seed = random.randint(1, 2**31 - 1)
        negative_prompt_node = overrides_cfg.get("negative_prompt_node", "7")
        negative_prompt_bypassed = False
        workflow = json.loads(Path(img_wf).read_text(encoding="utf-8"))

        seed_node = overrides_cfg.get("seed_node", "3")
        if seed_node not in workflow:
            negative_prompt_bypassed = False

        workflow_overrides = {
            overrides_cfg.get("positive_prompt_node", "6"): {"text": prompt},
            overrides_cfg.get("latent_size_node", "5"): {
                "width": int(resolution[0]),
                "height": int(resolution[1]),
            },
            overrides_cfg.get("output_node", "10"): {
                "filename_prefix": f"vf_{job_id}_ComfyUI_Final"
            },
            seed_node: {"seed": runtime_seed},
        }

        if negative_prompt_node in workflow:
            workflow_overrides[negative_prompt_node] = {"text": runtime_negative}
        else:
            negative_prompt_bypassed = True

        orch = ComfyOrchestrator(cfg)
        outputs = orch.run_workflow(
            img_wf,
            workflow_overrides,
        )
        if not outputs:
            raise RuntimeError("[LocalComfyUI] No image outputs were returned.")

        image_path = Path(outputs[0])
        model = workflow.get("4", {}).get("inputs", {}).get("ckpt_name", "unknown")
        latency_ms = int((time.time() - start) * 1000)
        return ImageResult(
            provider="local_comfyui",
            model=model,
            style=style,
            latency_ms=latency_ms,
            output_path=str(image_path),
            width=int(resolution[0]),
            height=int(resolution[1]),
            seed=runtime_seed,
            negative_prompt=runtime_negative,
            normalized_prompt=normalize_prompt(prompt),
            context={
                "orchestrator": orch,
                "negative_prompt_bypassed": negative_prompt_bypassed,
                "workflow_overrides": workflow_overrides,
            },
        )
