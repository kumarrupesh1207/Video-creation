"""
Aurora Asset Library Cache Gate System
Implements aggressive normalization and deterministic asset key generation.
Decouples matching logic from structural JSON storage to guarantee SQLite readiness.
"""

import os
import hashlib
import logging
import re
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

from aurora.monitoring.observability import TelemetryCollector

logger = logging.getLogger("aurora.assets.gate")


class CacheGate:
    def __init__(self, manifest_manager):
        self.manifest = manifest_manager
        # Initialize the decoupled collector
        self.telemetry = TelemetryCollector()

    def normalize_prompt(self, prompt: str) -> str:
        """
        Aggressively normalizes input strings to ensure semantic match parity.
        1. Forces lowercase
        2. Strips leading/trailing white space
        3. Collapses internal multi-spaces, tabs, and newlines into a single standard space
        """
        if not prompt:
            return ""
        cleaned = prompt.lower().replace("\n", " ").replace("\t", " ").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    def generate_asset_key(self, config: Dict[str, Any]) -> Tuple[str, str]:
        """
        Generates a deterministic structural asset_key based on multiple operational coordinates.
        Does NOT rely on raw prompt hashing or pHash.

        Returns a tuple: (asset_key, normalized_prompt)
        """
        normalized_prompt = self.normalize_prompt(config.get("prompt", ""))
        normalized_negative = self.normalize_prompt(config.get("negative_prompt", ""))

        components = [
            f"prompt:{normalized_prompt}",
            f"negative_prompt:{normalized_negative}",
            f"style_preset:{str(config.get('style_preset', '')).lower().strip()}",
            f"provider:{str(config.get('provider', '')).lower().strip()}",
            f"model:{str(config.get('model', '')).lower().strip()}",
            f"dimensions:{int(config.get('width', 0))}x{int(config.get('height', 0))}",
            f"seed:{int(config.get('seed', -1))}",
        ]

        compiled_string = "|".join(components)
        asset_key = hashlib.sha256(compiled_string.encode("utf-8")).hexdigest()

        return asset_key, normalized_prompt

    def check_gate(self, config: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Primary pipeline intercept hook. Instrumentated for unified observability."""
        transaction_id = config.get("transaction_id", f"tx_{int(os.getpid())}")
        topic_text = config.get("topic", "unspecified")

        self.telemetry.start_stage(transaction_id, "image_gen", {"topic": topic_text})

        self.manifest.increment_metric("asset_lookup_started")
        logger.info("[Log] ▶ asset_lookup_started")

        asset_key, normalized_prompt = self.generate_asset_key(config)

        telemetry_patch = {
            "provider_used": config.get("provider", "nvidia_nim"),
            "cache_hit": False,
            "retry_count": 0,
        }

        if config.get("force_new_image", False):
            self.manifest.increment_metric("cache_miss_count")
            logger.info("[Log] 🛑 asset_cache_miss (Forced bypass override enabled)")

            self.telemetry.complete_stage(
                transaction_id,
                "image_gen",
                metrics_patch=telemetry_patch,
                status_str="success",
                error_msg="Forced cache bypass override",
            )
            return False, asset_key, None

        asset_record = self.manifest.get_asset(asset_key)

        if not asset_record:
            logger.info("[Log] Strict key match missed. Scanning manifest lineage for semantic prompt match...")
            manifest_data = self.manifest._read_manifest()
            for k, record in manifest_data.get("assets", {}).items():
                if record.get("lineage", {}).get("prompt_normalized") == normalized_prompt:
                    file_path = record.get("file_path", "")
                    if file_path and os.path.exists(file_path):
                        logger.info("[Log] Match recovered via prompt fallback lineage registry.")
                        asset_key = k
                        asset_record = record
                        break

        if asset_record:
            self.manifest.increment_metric("cache_hit_count")
            self.manifest.increment_metric("provider_skip_count")
            logger.info(f"[Log] 🎯 asset_cache_hit (Valid asset verified on disk for key: {asset_key})")
            logger.info("[Log] ⏭ provider_call_skipped (Bypassing generation calls)")

            telemetry_patch["cache_hit"] = True
            telemetry_patch["provider_used"] = asset_record.get("versioning", {}).get("provider", "local_cache")
            self.telemetry.complete_stage(
                transaction_id,
                "image_gen",
                metrics_patch=telemetry_patch,
                status_str="success",
            )

            return True, asset_key, asset_record
        else:
            self.manifest.increment_metric("cache_miss_count")
            self.manifest.increment_metric("provider_call_count")
            logger.info("[Log] 🔍 asset_cache_miss (No matching layout found)")
            logger.info("[Log] 🚀 provider_call_started")

            self.telemetry.complete_stage(
                transaction_id,
                "image_gen",
                metrics_patch=telemetry_patch,
                status_str="success",
            )
            return False, asset_key, None

    def commit_generation_success(self, asset_key: str, config: Dict[str, Any], file_path: str) -> bool:
        """Encapsulates post-generation registration mapping structural runtime parameters."""
        normalized_prompt = self.normalize_prompt(config.get("prompt", ""))

        metadata = {
            "file_path": file_path,
            "registered_at": datetime.utcnow().isoformat() + "Z",
            "prompt_normalized": normalized_prompt,
            "style_preset": config.get("style_preset"),
            "width": config.get("width"),
            "height": config.get("height"),
            "seed": config.get("seed"),
            "provider": config.get("provider"),
            "model": config.get("model"),
            "provider_version": config.get("provider_version", "1.0.0"),
            "model_version": config.get("model_version", "1.0.0"),
            "workflow_version": config.get("workflow_version", "1.0.0"),
        }

        success = self.manifest.register_asset(asset_key, metadata)
        if success:
            logger.info(f"[Log] ✅ asset_registered (Key: {asset_key} cleanly committed to lineage manifest)")
        else:
            logger.error(f"[Log] ❌ provider_call_failed (Failed to write metadata asset record for key: {asset_key})")
        return success
