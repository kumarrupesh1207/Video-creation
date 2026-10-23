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
        timeout: int | None = None,
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
        if not runtime_negative:  # None or "" (switchboard default) -> configured default
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
        if style == "anime" and lc_cfg.get("anime_checkpoint") and "4" in workflow:
            workflow_overrides["4"] = {"ckpt_name": lc_cfg["anime_checkpoint"]}

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
            timeout=timeout,
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

    def generate_from_image(
        self,
        source_path: str | Path,
        prompt: str,
        style: str,
        resolution: list[int],
        job_id: str,
        negative_prompt: str | None = None,
        denoise: float = 0.45,
        seed: int | None = None,
        steps: int | None = None,
        cfg_scale: float | None = None,
        face_lock: bool = False,
    ) -> ImageResult:
        """img2img: restyle/edit an uploaded photo. Lower denoise keeps more of the person/composition.

        face_lock=True adds IP-Adapter (plus-face) conditioned on the same photo to keep the person's face
        (needs the ComfyUI_IPAdapter_plus node + models; weight from providers.local_comfyui.face_lock_weight).
        """
        from PIL import Image, ImageOps

        from aurora.monitoring.media_validator import validate_image
        from src.modules.video_engine import upload_image

        start = time.time()
        source_path = Path(source_path)
        if not source_path.is_file():
            raise FileNotFoundError(f"[LocalComfyUI] Source image not found: {source_path}")
        cfg = copy.deepcopy(self.config)
        lc_cfg = cfg.get("providers", {}).get("local_comfyui", {})
        if not resolution or len(resolution) < 2:
            resolution = lc_cfg.get("default_resolution", self.DEFAULT_RESOLUTION)
        width, height = (int(resolution[0]) // 8 * 8, int(resolution[1]) // 8 * 8)
        denoise = min(1.0, max(0.05, float(denoise)))
        job_root = Path(cfg.get("paths", {}).get("base_output", "outputs")) / job_id
        (job_root / "images").mkdir(parents=True, exist_ok=True)
        (job_root / "videos").mkdir(parents=True, exist_ok=True)
        cfg.setdefault("paths", {})
        cfg["paths"]["images_dir"] = str(job_root / "images")
        cfg["paths"]["videos_dir"] = str(job_root / "videos")

        # Crop/resize to the requested aspect (keeps the centre), then hand to ComfyUI's input folder.
        with Image.open(source_path) as src:
            fitted = ImageOps.fit(ImageOps.exif_transpose(src).convert("RGB"), (width, height), Image.LANCZOS)
        prepared = job_root / "images" / f"source_{width}x{height}.png"
        fitted.save(prepared)
        comfy_cfg = cfg.get("models", {}).get("comfyui", {})
        uploaded = upload_image(prepared, comfy_cfg.get("api_url", "http://127.0.0.1:8188"))

        runtime_negative = negative_prompt or lc_cfg.get("negative_prompt", self.DEFAULT_NEGATIVE)
        try:
            runtime_seed = int(seed) if seed is not None else 0
        except (TypeError, ValueError):
            runtime_seed = 0
        if runtime_seed <= 0:
            runtime_seed = random.randint(1, 2**31 - 1)
        sampler = {"seed": runtime_seed, "denoise": denoise}
        for key, value in (
            ("steps", steps if steps is not None else lc_cfg.get("steps")),
            ("cfg", cfg_scale if cfg_scale is not None else lc_cfg.get("cfg")),
            ("sampler_name", lc_cfg.get("sampler_name")),
            ("scheduler", lc_cfg.get("scheduler")),
        ):
            if value is not None:
                sampler[key] = value
        img_wf = comfy_cfg.get("img2img_workflow", "src/workflows/img2img.json")
        if face_lock:
            img_wf = comfy_cfg.get("img2img_facelock_workflow", "src/workflows/img2img_facelock.json")
        overrides = {
            "3": sampler,
            "6": {"text": prompt},
            "7": {"text": runtime_negative},
            "10": {"filename_prefix": f"vf_{job_id}_img2img"},
            "12": {"image": uploaded},
        }
        if face_lock:
            overrides["15"] = {"weight": float(lc_cfg.get("face_lock_weight", 0.8))}
        orch = ComfyOrchestrator(cfg)
        outputs = orch.run_workflow(img_wf, overrides)
        if not outputs:
            raise RuntimeError("[LocalComfyUI] img2img returned no image outputs.")
        image_path = Path(outputs[0])
        report = validate_image(image_path, width, height)
        if not report.ok:
            raise RuntimeError(f"[LocalComfyUI] img2img output failed validation: {report.errors}")
        workflow = json.loads(Path(img_wf).read_text(encoding="utf-8"))
        return ImageResult(
            provider="local_comfyui",
            model=workflow.get("4", {}).get("inputs", {}).get("ckpt_name", "unknown"),
            style=style,
            latency_ms=int((time.time() - start) * 1000),
            output_path=str(image_path),
            width=width,
            height=height,
            seed=runtime_seed,
            negative_prompt=runtime_negative,
            normalized_prompt=normalize_prompt(prompt),
            context={
                "orchestrator": orch,
                "mode": "img2img",
                "denoise": denoise,
                "face_lock": face_lock,
                "source_path": str(source_path),
                "workflow_overrides": overrides,
            },
        )

    def generate_with_reference(
        self,
        reference_path: str | Path,
        prompt: str,
        style: str,
        resolution: list[int],
        job_id: str,
        negative_prompt: str | None = None,
        seed: int | None = None,
        weight: float | None = None,
        steps: int | None = None,
        cfg_scale: float | None = None,
        timeout: int | None = None,
    ) -> ImageResult:
        """Text-to-image conditioned on a reference image via IP-Adapter (character/outfit consistency across panels)."""
        from aurora.monitoring.media_validator import validate_image
        from src.modules.video_engine import upload_image

        start = time.time()
        reference_path = Path(reference_path)
        if not reference_path.is_file():
            raise FileNotFoundError(f"[LocalComfyUI] Reference image not found: {reference_path}")
        cfg = copy.deepcopy(self.config)
        lc_cfg = cfg.get("providers", {}).get("local_comfyui", {})
        width, height = (int(resolution[0]) // 8 * 8, int(resolution[1]) // 8 * 8)
        job_root = Path(cfg.get("paths", {}).get("base_output", "outputs")) / job_id
        (job_root / "images").mkdir(parents=True, exist_ok=True)
        (job_root / "videos").mkdir(parents=True, exist_ok=True)
        cfg.setdefault("paths", {})
        cfg["paths"]["images_dir"] = str(job_root / "images")
        cfg["paths"]["videos_dir"] = str(job_root / "videos")
        comfy_cfg = cfg.get("models", {}).get("comfyui", {})
        uploaded = upload_image(reference_path, comfy_cfg.get("api_url", "http://127.0.0.1:8188"))
        runtime_negative = negative_prompt or lc_cfg.get("negative_prompt", self.DEFAULT_NEGATIVE)
        try:
            runtime_seed = int(seed) if seed is not None else 0
        except (TypeError, ValueError):
            runtime_seed = 0
        if runtime_seed <= 0:
            runtime_seed = random.randint(1, 2**31 - 1)
        sampler = {"seed": runtime_seed}
        for key, value in (
            ("steps", steps if steps is not None else lc_cfg.get("steps")),
            ("cfg", cfg_scale if cfg_scale is not None else lc_cfg.get("cfg")),
            ("sampler_name", lc_cfg.get("sampler_name")),
            ("scheduler", lc_cfg.get("scheduler")),
        ):
            if value is not None:
                sampler[key] = value
        img_wf = comfy_cfg.get("ipadapter_workflow", "src/workflows/image_gen_ipadapter.json")
        overrides = {
            "3": sampler, "5": {"width": width, "height": height}, "6": {"text": prompt}, "7": {"text": runtime_negative},
            "10": {"filename_prefix": f"vf_{job_id}_ref"}, "12": {"image": uploaded},
            "13": {"ipadapter_file": lc_cfg.get("ipadapter_file", "ip-adapter-plus_sd15.safetensors")},
            "15": {"weight": float(weight if weight is not None else lc_cfg.get("reference_weight", 0.7))},
        }
        if style == "anime" and lc_cfg.get("anime_checkpoint"):
            overrides["4"] = {"ckpt_name": lc_cfg["anime_checkpoint"]}
        orch = ComfyOrchestrator(cfg)
        outputs = orch.run_workflow(img_wf, overrides, timeout=timeout)
        if not outputs:
            raise RuntimeError("[LocalComfyUI] reference render returned no image outputs.")
        image_path = Path(outputs[0])
        report = validate_image(image_path, width, height)
        if not report.ok:
            raise RuntimeError(f"[LocalComfyUI] reference render failed validation: {report.errors}")
        return ImageResult(
            provider="local_comfyui", model=overrides.get("4", {}).get("ckpt_name") or "unknown", style=style,
            latency_ms=int((time.time() - start) * 1000), output_path=str(image_path), width=width, height=height,
            seed=runtime_seed, negative_prompt=runtime_negative, normalized_prompt=normalize_prompt(prompt),
            context={"mode": "reference", "reference": str(reference_path), "workflow_overrides": overrides},
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
