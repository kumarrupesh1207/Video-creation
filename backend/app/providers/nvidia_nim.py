from __future__ import annotations

import base64
import os
import random
import time
from pathlib import Path

import requests

from backend.app.providers.base import ImageProvider, ImageResult
from aurora.assets.manifest import normalize_prompt


class NvidiaNIMProvider(ImageProvider):
    """OpenAI-compatible NVIDIA NIM image provider."""

    FLUX_PORTRAIT = (768, 1344)

    def _resolve_endpoint(
        self,
        model: str,
        base_url: str | None = None,
        endpoint_mode: str = "direct_model",
    ) -> str:
        model_path = model.strip().strip("/")
        root = (base_url or "https://ai.api.nvidia.com/v1/genai/").rstrip("/")
        if endpoint_mode == "direct_model":
            return f"{root}/{model_path}"
        return root

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
        provider_cfg = self.config.get("providers", {}).get("nvidia_nim", {})
        if not provider_cfg.get("enabled", False):
            raise RuntimeError("[NVIDIA NIM] Provider is disabled in config.")

        api_key = os.getenv("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError("[NVIDIA NIM] NVIDIA_API_KEY is not set.")

        start = time.time()
        base_url = provider_cfg.get("base_url", "https://integrate.api.nvidia.com/v1/images/generations")
        endpoint_mode = provider_cfg.get("endpoint_mode", "direct_model")
        timeout = int(provider_cfg.get("timeout", 60))
        credit_cost = float(provider_cfg.get("credit_cost", 1.0))
        model_map = provider_cfg.get("models", {})
        model = model_map.get(style, model_map.get("cinematic", "stabilityai/stable-diffusion-3.5-large"))
        width = int(resolution[0])
        height = int(resolution[1])
        endpoint = self._resolve_endpoint(model, base_url, endpoint_mode)
        resolved_negative_prompt = negative_prompt
        if resolved_negative_prompt is None:
            resolved_negative_prompt = (
                self.config.get("providers", {})
                .get("local_comfyui", {})
                .get(
                    "negative_prompt",
                    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, "
                    "fewer digits, cropped, worst quality, low quality, normal quality, jpeg artifacts, "
                    "signature, watermark, username, blurry, artist name, realistic, 3d, photorealistic",
                )
            )
        payload = {"prompt": prompt}
        actual_width = width
        actual_height = height
        try:
            resolved_seed = int(seed) if seed is not None else 0
        except (TypeError, ValueError):
            resolved_seed = 0
        if resolved_seed <= 0:
            resolved_seed = random.randint(1, 2**31 - 1)
        negative_prompt_bypassed = False

        if model == "black-forest-labs/flux.2-klein-4b":
            flux_width, flux_height = self.FLUX_PORTRAIT if height > width else self.FLUX_PORTRAIT[::-1]
            actual_width = flux_width
            actual_height = flux_height
            payload.update({
                "width": flux_width,
                "height": flux_height,
                "samples": 1,
                "steps": 4,
                "seed": resolved_seed,
            })
            negative_prompt_bypassed = bool(resolved_negative_prompt)
        else:
            aspect_ratio = "9:16" if height > width else "16:9"
            payload["aspect_ratio"] = aspect_ratio
            payload["negative_prompt"] = resolved_negative_prompt
            payload["seed"] = resolved_seed
            if endpoint_mode != "direct_model":
                payload["model"] = model
            if not model.startswith("stabilityai/stable-diffusion-3"):
                payload.update({
                    "width": width,
                    "height": height,
                })

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, image/jpeg",
        }

        resp = requests.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        job_root = Path(self.config.get("paths", {}).get("base_output", "outputs")) / job_id
        image_dir = job_root / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        content_type = resp.headers.get("Content-Type", "").lower()
        image_path = image_dir / "nvidia_nim.jpg"

        if content_type.startswith("image/"):
            image_path.write_bytes(resp.content)
        else:
            body = resp.json()
            artifacts = body.get("artifacts") or []
            data = (body.get("data") or [{}])[0]
            if artifacts and isinstance(artifacts[0], dict) and artifacts[0].get("base64"):
                image_path = image_dir / "nvidia_nim.jpg"
                image_path.write_bytes(base64.b64decode(artifacts[0]["base64"]))
            elif artifacts and isinstance(artifacts[0], dict) and artifacts[0].get("finishReason"):
                raise RuntimeError(
                    f"[NVIDIA NIM] Generation blocked: {artifacts[0].get('finishReason')}"
                )
            elif data.get("b64_json"):
                image_path = image_dir / "nvidia_nim.png"
                image_path.write_bytes(base64.b64decode(data["b64_json"]))
            elif data.get("url"):
                downloaded = requests.get(data["url"], timeout=timeout)
                downloaded.raise_for_status()
                image_path.write_bytes(downloaded.content)
            else:
                raise RuntimeError("[NVIDIA NIM] No image payload found in response.")

        return ImageResult(
            provider="nvidia_nim",
            model=model,
            style=style,
            latency_ms=int((time.time() - start) * 1000),
            output_path=str(image_path),
            generation_cost=credit_cost,
            width=actual_width,
            height=actual_height,
            seed=resolved_seed,
            negative_prompt=resolved_negative_prompt,
            normalized_prompt=normalize_prompt(prompt),
            context={
                "request_payload": payload,
                "negative_prompt_bypassed": negative_prompt_bypassed,
            },
        )
