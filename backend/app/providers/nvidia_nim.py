from __future__ import annotations

import base64
import json
import logging
import math
import os
import random
import time
from pathlib import Path

import requests

from backend.app.providers.base import ImageProvider, ImageResult
from aurora.assets.manifest import normalize_prompt

logger = logging.getLogger(__name__)


class NvidiaNIMProvider(ImageProvider):
    """OpenAI-compatible NVIDIA NIM image provider."""

    FLUX_PORTRAIT = (768, 1344)
    # Sizes the Flux endpoint accepts; the request is mapped to the nearest aspect ratio.
    FLUX_SIZES = ((1024, 1024), (768, 1344), (1344, 768), (896, 1152), (1152, 896))

    ASPECT_RATIOS = ("1:1", "2:3", "3:2", "4:5", "5:4", "9:16", "16:9")

    @classmethod
    def nearest_aspect_ratio(cls, width: int, height: int) -> str:
        target = math.log(max(width, 1) / max(height, 1))

        def dist(ratio: str) -> float:
            w, h = (int(x) for x in ratio.split(":"))
            return abs(math.log(w / h) - target)

        return min(cls.ASPECT_RATIOS, key=dist)

    @classmethod
    def nearest_flux_size(cls, width: int, height: int) -> tuple[int, int]:
        """Pick the supported size whose aspect ratio is closest to the request."""
        target = math.log(max(width, 1) / max(height, 1))
        return min(cls.FLUX_SIZES, key=lambda s: abs(math.log(s[0] / s[1]) - target))

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

    @staticmethod
    def _record_headers(resp) -> None:
        """Save quota/credit/rate-limit headers of the latest call (never auth) to outputs/costs/nim_last_headers.json."""
        try:
            keep = {k: v for k, v in resp.headers.items()
                    if any(t in k.lower() for t in ("ratelimit", "credit", "quota", "retry-after", "nvcf", "usage"))}
            out = Path("outputs") / "costs" / "nim_last_headers.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"status": resp.status_code, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "headers": keep},
                                      indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001 - diagnostics only
            pass

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
        if not resolved_negative_prompt:  # None or "" -> configured default
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
        adjustments: list[dict] = []

        if model == "black-forest-labs/flux.2-klein-4b":
            flux_width, flux_height = self.nearest_flux_size(width, height)
            logger.info("[NVIDIA NIM] size map %sx%s -> %sx%s", width, height, flux_width, flux_height)
            if (flux_width, flux_height) != (width, height):
                adjustments.append({"field": "size", "requested": f"{width}x{height}", "used": f"{flux_width}x{flux_height}"})
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
            aspect_ratio = self.nearest_aspect_ratio(width, height)
            logger.info("[NVIDIA NIM] aspect map %sx%s -> %s", width, height, aspect_ratio)
            adjustments.append({"field": "aspect_ratio", "requested": f"{width}x{height}", "used": aspect_ratio})
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
        self._record_headers(resp)
        if resp.status_code >= 400:  # keep the API's own words (quota / credits / rate limit)
            raise requests.HTTPError(f"[NVIDIA NIM] HTTP {resp.status_code}: {resp.text[:300]}", response=resp)
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
                "adjustments": adjustments,
            },
        )
