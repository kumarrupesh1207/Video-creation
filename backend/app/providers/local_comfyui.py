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
        "lowres, bad anatomy, bad hands, extra fingers, deformed, text, error, missing fingers, extra digit, "
        "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
        "signature, watermark, username, blurry, artist name"
    )

    DEFAULT_RESOLUTION = [512, 768]

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
        steps: int | None = None,
        cfg_scale: float | None = None,
        refine: bool | None = None,
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

        lc_cfg = cfg.get("providers", {}).get("local_comfyui", {})
        if not resolution or len(resolution) < 2:
            resolution = lc_cfg.get("default_resolution", self.DEFAULT_RESOLUTION)
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
        sampler = {}
        for key, value in (
            ("steps", steps if steps is not None else lc_cfg.get("steps")),
            ("cfg", cfg_scale if cfg_scale is not None else lc_cfg.get("cfg")),
            ("sampler_name", lc_cfg.get("sampler_name")),
            ("scheduler", lc_cfg.get("scheduler")),
        ):
            if value is not None:
                sampler[key] = value
        if sampler:
            workflow_overrides[seed_node].update(sampler)

        refine_cfg = lc_cfg.get("refine", {})
        do_refine = refine_cfg.get("enabled", False) if refine is None else refine
        if do_refine:
            img_wf, refine_node = self._write_refine_workflow(workflow, seed_node, refine_cfg, job_root)
            workflow_overrides[refine_node] = {
                "seed": runtime_seed,
                **{k: v for k, v in sampler.items() if k != "steps"},
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

    @staticmethod
    def _write_refine_workflow(workflow: dict, seed_node: str, refine_cfg: dict, job_root: Path) -> tuple[str, str]:
        """Add a hires-fix style pass (latent upscale + low-denoise KSampler) and return (new workflow path, refine KSampler id)."""
        wf = copy.deepcopy(workflow)
        base = wf[seed_node]["inputs"]
        up_id = str(max(int(k) for k in wf) + 1)
        ks_id = str(int(up_id) + 1)
        wf[up_id] = {
            "class_type": "LatentUpscaleBy",
            "inputs": {
                "samples": [seed_node, 0],
                "upscale_method": "bislerp",
                "scale_by": float(refine_cfg.get("scale", 1.5)),
            },
        }
        refine_inputs = dict(base)
        refine_inputs.update({
            "latent_image": [up_id, 0],
            "denoise": float(refine_cfg.get("denoise", 0.45)),
            "steps": int(refine_cfg.get("steps", 15)),
        })
        wf[ks_id] = {"class_type": "KSampler", "inputs": refine_inputs}
        rewired = 0
        for node in wf.values():
            if node.get("class_type") in ("VAEDecodeTiled", "VAEDecode"):
                node["inputs"]["samples"] = [ks_id, 0]
                rewired += 1
        if not rewired:
            raise RuntimeError("[LocalComfyUI] refine pass requested but no VAE decode node found in workflow.")
        path = job_root / "image_gen_refine.json"
        path.write_text(json.dumps(wf, indent=2), encoding="utf-8")
        return str(path), ks_id
