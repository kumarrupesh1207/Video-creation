from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from aurora.assets.manifest import (
    composite_asset_key,
    find_asset,
    ManifestManager,
    normalize_prompt,
)
from backend.app.providers.base import ImageResult
from backend.app.providers.local_comfyui import LocalComfyUIProvider
from backend.app.providers.manual_upload import ManualUploadProvider
from backend.app.providers.nvidia_nim import NvidiaNIMProvider


class ImageSwitchboard:
    """Provider orchestrator that applies configured fallback order."""

    def __init__(self, config: dict):
        self.config = config
        self.manifest_manager = ManifestManager("assets/images/manifest.json")
        providers_cfg = config.get("providers", {})
        self.default = providers_cfg.get("default", "local_comfyui")
        self.fallback_order = providers_cfg.get(
            "fallback_order",
            ["nvidia_nim", "local_comfyui", "manual_upload"],
        )
        self.providers = {
            "nvidia_nim": NvidiaNIMProvider(config),
            "local_comfyui": LocalComfyUIProvider(config),
            "manual_upload": ManualUploadProvider(config),
        }
        self.reuse_existing_asset = config.get("assets", {}).get("reuse_existing_asset", True)

    def generate(
        self,
        prompt: str,
        style: str,
        resolution: list[int],
        job_id: str,
        negative_prompt: str = "",
        seed: int | None = None,
        force_new_image: bool = False,
    ) -> ImageResult:
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        attempts = []
        last_result: ImageResult | None = None
        normalized = normalize_prompt(prompt)
        self._append_event(job_id, "asset_lookup_started", {
            "style": style,
            "force_new_image": force_new_image,
        })

        if self.reuse_existing_asset and not force_new_image:
            cached = self._lookup_cached_asset(normalized, style, resolution, negative_prompt)
            if cached:
                self._append_event(job_id, "asset_cache_hit", {
                    "asset_key": cached["asset_key"],
                    "provider": cached["provider"],
                })
                self._append_event(job_id, "provider_call_skipped", {
                    "provider": cached["provider"],
                })
                result = ImageResult(
                    provider=cached["provider"],
                    model=cached["model"],
                    style=style,
                    latency_ms=0,
                    output_path=cached["output_path"],
                    generation_cost=float(cached.get("generation_cost", 0.0)),
                    width=int(cached["width"]),
                    height=int(cached["height"]),
                    seed=int(cached["seed"]),
                    negative_prompt=cached["negative_prompt"],
                    normalized_prompt=normalized,
                    asset_key=cached["asset_key"],
                    fallback_used=False,
                )
                self._write_job_metadata(job_id, result, [{
                    "provider": cached["provider"],
                    "status": "cached",
                    "latency_ms": 0,
                    "generation_cost": float(cached.get("generation_cost", 0.0)),
                    "output_path": cached["output_path"],
                }])
                return result

        self._append_event(job_id, "asset_cache_miss", {"style": style})

        for index, provider_name in enumerate(self.fallback_order):
            provider = self.providers.get(provider_name)
            if provider is None:
                attempts.append({
                    "provider": provider_name,
                    "status": "error",
                    "error": "Provider not implemented",
                })
                continue

            started = time.time()
            try:
                if hasattr(provider, "generate_with_params"):
                    result = provider.generate_with_params(
                        prompt,
                        style,
                        resolution,
                        job_id,
                        negative_prompt=negative_prompt,
                        seed=seed,
                    )
                else:
                    result = provider.generate(prompt, style, resolution, job_id)
                result.prompt_hash = prompt_hash
                result.fallback_used = index > 0
                if not result.normalized_prompt:
                    result.normalized_prompt = normalized
                result.asset_key = composite_asset_key(
                    normalized_prompt=result.normalized_prompt,
                    style_preset=style,
                    provider=result.provider,
                    model=result.model,
                    width=result.width,
                    height=result.height,
                    seed=result.seed,
                    negative_prompt=result.negative_prompt or negative_prompt,
                )
                attempts.append({
                    "provider": provider_name,
                    "status": "success" if not result.needs_manual_upload else "needs_manual_upload",
                    "latency_ms": result.latency_ms,
                    "output_path": result.output_path,
                })
                if not result.needs_manual_upload:
                    self.manifest_manager.register_asset(
                        result.asset_key,
                        {
                            "file_path": result.output_path,
                            "registered_at": datetime.utcnow().isoformat() + "Z",
                            "prompt_normalized": result.normalized_prompt,
                            "style_preset": style,
                            "width": result.width,
                            "height": result.height,
                            "seed": result.seed,
                            "provider": result.provider,
                            "model": result.model,
                            "provider_version": "1.0.0",
                            "model_version": "1.0.0",
                            "workflow_version": "1.0.0",
                            "negative_prompt": result.negative_prompt,
                            "generation_cost": result.generation_cost,
                            "latency_ms": result.latency_ms,
                        },
                    )
                    result.output_path = result.output_path
                    self._append_event(job_id, "asset_registered", {
                        "asset_key": result.asset_key,
                        "provider": result.provider,
                    })
                self._write_job_metadata(job_id, result, attempts)
                return result
            except Exception as exc:
                latency_ms = int((time.time() - started) * 1000)
                attempts.append({
                    "provider": provider_name,
                    "status": "error",
                    "latency_ms": latency_ms,
                    "error": str(exc),
                })
                last_result = ImageResult(
                    provider=provider_name,
                    model="unknown",
                    style=style,
                    latency_ms=latency_ms,
                    output_path="",
                    prompt_hash=prompt_hash,
                    fallback_used=index > 0,
                )

        if last_result is None:
            raise RuntimeError("[ImageSwitchboard] No providers were configured.")

        self._write_job_metadata(job_id, last_result, attempts)
        raise RuntimeError("[ImageSwitchboard] All image providers failed.")

    def _lookup_cached_asset(
        self,
        normalized_prompt: str,
        style: str,
        resolution: list[int],
        negative_prompt: str,
    ) -> dict | None:
        for provider_name in self.fallback_order:
            descriptor = self._provider_descriptor(provider_name, style, resolution, negative_prompt)
            if descriptor is None:
                continue
            asset_key = composite_asset_key(
                normalized_prompt=normalized_prompt,
                style_preset=style,
                provider=provider_name,
                model=descriptor["model"],
                width=descriptor["width"],
                height=descriptor["height"],
                seed=descriptor["seed"],
                negative_prompt=descriptor["negative_prompt"],
            )
            cached = find_asset(asset_key)
            if cached:
                return cached
        return None

    def _provider_descriptor(
        self,
        provider_name: str,
        style: str,
        resolution: list[int],
        negative_prompt: str,
    ) -> dict | None:
        if provider_name == "nvidia_nim":
            model = (
                self.config.get("providers", {})
                .get("nvidia_nim", {})
                .get("models", {})
                .get(style, self.config.get("providers", {}).get("nvidia_nim", {}).get("models", {}).get("cinematic", ""))
            )
            if model == "black-forest-labs/flux.2-klein-4b":
                width, height = (768, 1344) if resolution[1] > resolution[0] else (1344, 768)
            else:
                width, height = int(resolution[0]), int(resolution[1])
            return {
                "model": model,
                "width": width,
                "height": height,
                "seed": 0,
                "negative_prompt": negative_prompt,
            }
        if provider_name == "local_comfyui":
            workflow_path = (
                self.config.get("models", {})
                .get("comfyui", {})
                .get("image_workflow", "src/workflows/image_gen.json")
            )
            workflow = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
            return {
                "model": workflow.get("4", {}).get("inputs", {}).get("ckpt_name", "unknown"),
                "width": int(resolution[0]),
                "height": int(resolution[1]),
                "seed": int(workflow.get("3", {}).get("inputs", {}).get("seed", 42)),
                "negative_prompt": negative_prompt,
            }
        return None

    def _write_job_metadata(self, job_id: str, result: ImageResult, attempts: list[dict]) -> None:
        log_dir = Path(self.config.get("paths", {}).get("base_output", "outputs")) / job_id / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        job_log_path = log_dir / "job.json"
        existing = {}
        if job_log_path.exists():
            try:
                existing = json.loads(job_log_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        payload = {
            **existing,
            "job_id": job_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "provider": result.provider,
            "provider_used": result.provider,
            "model": result.model,
            "style": result.style,
            "prompt_hash": result.prompt_hash,
            "latency_ms": result.latency_ms,
            "generation_cost": result.generation_cost,
            "output_path": result.output_path,
            "fallback_used": result.fallback_used,
            "needs_manual_upload": result.needs_manual_upload,
            "asset_key": result.asset_key or existing.get("asset_key"),
            "attempts": attempts,
            "progress_percent": existing.get("progress_percent", 50),
        }
        job_log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _append_event(self, job_id: str, event_name: str, details: dict | None = None) -> None:
        log_dir = Path(self.config.get("paths", {}).get("base_output", "outputs")) / job_id / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        job_log_path = log_dir / "job.json"
        payload = {}
        if job_log_path.exists():
            try:
                payload = json.loads(job_log_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        events = payload.get("events", [])
        events.append({
            "event": event_name,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "details": details or {},
        })
        payload["events"] = events
        job_log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[Asset] {event_name}")
