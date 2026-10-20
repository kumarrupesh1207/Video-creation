from __future__ import annotations

import importlib
import logging
import os
from datetime import datetime
from pathlib import Path

from aurora.assets.manifest import find_asset, register_motion_variant
from aurora.monitoring.media_validator import validate_video
from aurora.monitoring.observability import TelemetryCollector
from aurora.motion.base import MotionResult

logger = logging.getLogger("aurora.motion.switchboard")


class MotionSwitchboard:
    @staticmethod
    def _require_valid_video(result: MotionResult, provider_key: str, duration: float | None = None) -> None:
        """Never report success for a missing, corrupt or known-mock clip (Rule 4/5): raise so fallback runs."""
        expect = {}
        if provider_key == "static_cinematic" and duration:  # our own renderer: size/fps/duration are exact
            expect = {"width": 1080, "height": 1920, "fps": 24, "duration_secs": float(duration)}
        report = validate_video(result.output_path, **expect)
        if not report.ok:
            logger.error(f"[Log] ❌ Output validation failed for '{provider_key}': {report.errors}")
            raise RuntimeError(f"[MotionSwitchboard] Invalid output from {provider_key}: {'; '.join(report.errors)}")

    def __init__(self, config: dict):
        self.config = config
        self.provider_registry = {
            "static_cinematic": ("aurora.motion.providers.ffmpeg_static", "FFmpegStaticMotionProvider"),
            "talking_motion": ("aurora.motion.providers.liveportrait_provider", "LivePortraitProvider"),
            "full_i2v_motion": ("aurora.motion.providers.framepack_provider", "FramepackProvider"),
            "framepack_provider": ("aurora.motion.providers.framepack_provider", "FramepackProvider"),
            "svdxt_provider": ("aurora.motion.providers.svdxt_provider", "SVDXTProvider"),
            "wan2gp_provider": ("aurora.motion.providers.wan2gp_provider", "Wan2GPProvider"),
            "kaggle_provider": ("aurora.motion.providers.kaggle_provider", "KaggleProvider"),  # OFF (config)
        }
        self._provider_cache: dict[str, object] = {}
        self.telemetry = TelemetryCollector()
        self.last_result: MotionResult | None = None

    def generate(
        self,
        motion_mode: str,
        image_path: Path,
        duration: int = 5,
        caption_text: str = "",
        template_name: str = "ken_burns",
        job_id: str = "",
        asset_key: str = "",
        visual_complexity: str = "standard",
        audio_narration_path: Path | None = None,
        generation_seed: int | None = None,
        reference_image_path: Path | None = None,
        identity_lock: bool = False,
    ) -> Path:
        transaction_id = job_id or f"tx_{int(os.getpid())}"
        topic_text = caption_text or "unspecified"
        self.telemetry.start_stage(transaction_id, "video_gen", {"topic": topic_text})

        anchor_record = find_asset(asset_key) if asset_key else None
        effective_reference_image = reference_image_path or (Path(anchor_record["output_path"]) if anchor_record else image_path)
        reference_lock = self._should_lock_identity(asset_key, identity_lock, anchor_record)
        requested_motion_intent = motion_mode or "static_cinematic"
        provider_key = self._select_provider_key(motion_mode, visual_complexity)
        selected_provider = provider_key
        fallback_key = "static_cinematic"
        telemetry_patch = {
            "provider_used": provider_key,
            "cache_hit": False,
            "retry_count": 0,
            "motion_status": "running",
        }
        provider = self._get_provider(provider_key)
        if provider is None:
            self.telemetry.complete_stage(
                transaction_id,
                "video_gen",
                metrics_patch=telemetry_patch,
                status_str="failed",
                error_msg=f"Unknown motion mode: {motion_mode}",
            )
            raise RuntimeError(f"[MotionSwitchboard] Unknown motion mode: {motion_mode}")
        try:
            logger.info(f"[Log] Instantiating motion provider on demand: {provider_key}")
            logger.info(f"[Log] executing primary motion provider: {provider_key}")
            result = provider.generate(
                image_path=image_path,
                duration=duration,
                caption_text=caption_text,
                template_name=template_name,
                job_id=job_id,
                asset_key=asset_key,
                visual_complexity=visual_complexity,
                audio_narration_path=audio_narration_path,
                generation_seed=generation_seed,
                reference_image_path=effective_reference_image,
                identity_lock=reference_lock,
            )
            self._require_valid_video(result, provider_key, duration)
            motion_telemetry = {
                "requested_motion_intent": requested_motion_intent,
                "selected_provider": selected_provider,
                "actual_provider": selected_provider,
                "motion_status": "generative_motion",
                "fallback_used": False,
                "fallback_reason": None,
            }
            success_context = dict(result.context or {})
            success_context["provider_used"] = provider_key
            success_context["fallback_triggered"] = False
            success_context["motion_telemetry"] = motion_telemetry
            result.context = success_context
            telemetry_patch["motion_status"] = "generative_motion"
            self.telemetry.complete_stage(
                transaction_id,
                "video_gen",
                metrics_patch=telemetry_patch,
                status_str="success",
            )
        except Exception as exc:
            logger.error(f"[Log] ❌ Primary motion provider '{provider_key}' failed: {str(exc)}")
            if motion_mode == "talking_motion" and audio_narration_path is not None:
                self.telemetry.complete_stage(
                    transaction_id,
                    "video_gen",
                    metrics_patch=telemetry_patch,
                    status_str="failed",
                    error_msg=str(exc),
                )
                raise
            if motion_mode not in {"talking_motion", "full_i2v_motion"} and provider_key == fallback_key:
                self.telemetry.complete_stage(
                    transaction_id,
                    "video_gen",
                    metrics_patch=telemetry_patch,
                    status_str="failed",
                    error_msg=str(exc),
                )
                raise
            logger.warning("[Log] ⚠️ Graceful degradation triggered. Falling back to local FFmpegStaticMotionProvider.")
            self._release_provider(provider_key)
            provider = None
            fallback_provider = self._get_provider(fallback_key)
            try:
                result = fallback_provider.generate(
                    image_path=image_path,
                    duration=duration,
                    caption_text=caption_text,
                    template_name="ken_burns",
                    job_id=job_id,
                    asset_key=asset_key,
                )
                self._require_valid_video(result, fallback_key, duration)
                result.motion_mode = motion_mode
                result.degraded_from = provider_key
                result.badge = "Motion R&D"
                result.review_required = True
                motion_telemetry = {
                    "requested_motion_intent": requested_motion_intent,
                    "selected_provider": selected_provider,
                    "actual_provider": fallback_key,
                    "motion_status": "fallback_visual_effects",
                    "fallback_used": True,
                    "fallback_reason": str(exc),
                }
                fallback_context = dict(result.context or {})
                fallback_context.update(
                    {
                        "provider_used": fallback_key,
                        "fallback_triggered": True,
                        "primary_provider_attempted": provider_key,
                        "failure_reason": str(exc),
                        "motion_telemetry": motion_telemetry,
                        "degraded_error": str(exc),
                        "degraded_error_type": getattr(exc, "error_type", type(exc).__name__.upper()),
                        "degraded_stderr_tail": getattr(exc, "stderr_tail", []),
                        "degraded_exit_code": getattr(exc, "exit_code", None),
                    }
                )
                result.context = fallback_context
                telemetry_patch["provider_used"] = f"{provider_key}->{fallback_key}"
                telemetry_patch["motion_status"] = "fallback_visual_effects"
                self.telemetry.complete_stage(
                    transaction_id,
                    "video_gen",
                    metrics_patch=telemetry_patch,
                    status_str="degraded",
                    error_msg=str(exc),
                )
            except Exception as fallback_error:
                logger.error(f"[Log] Critical System Failure: Local fallback provider failed: {str(fallback_error)}")
                self.telemetry.complete_stage(
                    transaction_id,
                    "video_gen",
                    metrics_patch=telemetry_patch,
                    status_str="failed",
                    error_msg=f"Fallback failed: {str(fallback_error)}",
                )
                raise fallback_error
            finally:
                if fallback_key != provider_key:
                    self._release_provider(fallback_key)
        finally:
            self._release_provider(provider_key)

        self.last_result = result
        if asset_key:
            actual_provider = result.context.get("provider", result.provider) if result.context else result.provider
            motion_telemetry = result.context.get("motion_telemetry") if result.context else None
            register_motion_variant(
                asset_key,
                {
                    "provider": actual_provider,
                    "motion_mode": result.motion_mode,
                    "output_path": str(result.output_path),
                    "degraded_from": result.degraded_from,
                    "review_required": result.review_required,
                    "status": "success" if not result.degraded_from else "fallback",
                    "latency_ms": result.context.get("latency_ms") if result.context else None,
                    "error_type": result.context.get("degraded_error_type") if result.context else None,
                    "stderr_tail": result.context.get("degraded_stderr_tail") if result.context else None,
                    "exit_code": result.context.get("degraded_exit_code") if result.context else None,
                    "visual_complexity": visual_complexity,
                    "lip_sync_status": result.context.get("lip_sync_status") if result.context else None,
                    "lip_sync_warning": result.context.get("lip_sync_warning") if result.context else None,
                    "lip_sync_time_ms": result.context.get("lip_sync_time_ms") if result.context else None,
                    "reference_anchor_asset": asset_key,
                    "parent_identity_asset": asset_key,
                    "reference_image_path": str(effective_reference_image) if effective_reference_image else None,
                    "generation_seed": generation_seed,
                    "identity_lock": reference_lock,
                    "motion_telemetry": motion_telemetry,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                },
            )
        return result.output_path

    def _should_lock_identity(self, asset_key: str, identity_lock: bool, anchor_record: dict | None) -> bool:
        if identity_lock:
            return True
        if not asset_key or not anchor_record:
            return False
        return bool(anchor_record.get("motion_variant_count", 0))

    def _select_provider_key(self, motion_mode: str, visual_complexity: str) -> str:
        if motion_mode == "full_i2v_motion":
            complexity = (visual_complexity or "standard").strip().lower()
            if complexity in {"high action", "combat", "aura"}:
                return "framepack_provider"
            if complexity in {"atmospheric", "subtle", "dreamy"}:
                return "svdxt_provider"
            return "wan2gp_provider"
        return motion_mode

    def _get_provider(self, provider_key: str):
        provider = self._provider_cache.get(provider_key)
        if provider is not None:
            return provider
        spec = self.provider_registry.get(provider_key)
        if spec is None:
            return None
        module_name, class_name = spec
        module = importlib.import_module(module_name)
        provider_cls = getattr(module, class_name)
        provider = provider_cls(self.config)
        self._provider_cache[provider_key] = provider
        return provider

    def _release_provider(self, provider_key: str) -> None:
        provider = self._provider_cache.pop(provider_key, None)
        if provider is None:
            return
        cleanup = getattr(provider, "cleanup", None)
        unload = getattr(provider, "unload", None)
        free_vram = getattr(provider, "free_vram", None)
        try:
            if callable(cleanup):
                cleanup()
            elif callable(unload):
                unload()
            elif callable(free_vram):
                free_vram()
        except Exception:
            pass
