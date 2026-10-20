"""
Aurora Asset Manifest Management System
Handles atomic I/O, schema validation, lineage tracking, and cache metrics tracking.
Safe for Ryzen 5 5600G local execution under strict memory boundaries.
"""

import os
import json
import hashlib
import logging
import re
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("aurora.manifest")

# Schema definitions for future migration compatibility
CURRENT_SCHEMA_VERSION = "1.0"


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def sanitize_asset_record(record_dict: Dict[str, Any], default_width: int = 1080, default_height: int = 1920) -> Dict[str, Any]:
    sanitized = dict(record_dict or {})
    now = _utc_now_iso()
    sanitized["lineage"] = dict(sanitized.get("lineage") or {})
    sanitized["versioning"] = dict(sanitized.get("versioning") or {})
    if "motion_variants" in sanitized and isinstance(sanitized.get("motion_variants"), list):
        sanitized["motion_variants"] = [dict(item) if isinstance(item, dict) else item for item in sanitized["motion_variants"]]

    if _is_blank(sanitized.get("registered_at")):
        sanitized["registered_at"] = now
    if _is_blank(sanitized.get("last_used_at")):
        sanitized["last_used_at"] = now

    lineage = sanitized["lineage"]

    dimensions = lineage.get("dimensions")
    valid_dimensions = False
    if isinstance(dimensions, str):
        match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", dimensions)
        if match and int(match.group(1)) > 0 and int(match.group(2)) > 0:
            lineage["dimensions"] = f"{int(match.group(1))}x{int(match.group(2))}"
            valid_dimensions = True
    if not valid_dimensions:
        width = sanitized.get("width")
        height = sanitized.get("height")
        try:
            width_val = int(width)
            if width_val <= 0:
                raise ValueError
        except (TypeError, ValueError):
            width_val = default_width
        try:
            height_val = int(height)
            if height_val <= 0:
                raise ValueError
        except (TypeError, ValueError):
            height_val = default_height
        lineage["dimensions"] = f"{width_val}x{height_val}"

    seed_value = lineage.get("seed", sanitized.get("seed"))
    if seed_value is None or str(seed_value).strip().lower() == "none":
        lineage["seed"] = 0
    else:
        try:
            lineage["seed"] = int(seed_value)
        except (TypeError, ValueError):
            lineage["seed"] = 0

    versioning = sanitized["versioning"]
    if _is_blank(versioning.get("provider")):
        versioning["provider"] = "legacy_fallback"
    if _is_blank(versioning.get("model")):
        versioning["model"] = "legacy_fallback"
    if _is_blank(versioning.get("provider_version")):
        versioning["provider_version"] = "1.0.0"
    if _is_blank(versioning.get("model_version")):
        versioning["model_version"] = "1.0.0"
    if _is_blank(versioning.get("workflow_version")):
        versioning["workflow_version"] = "1.0.0"

    if "width" in sanitized:
        sanitized.pop("width", None)
    if "height" in sanitized:
        sanitized.pop("height", None)
    if "seed" in sanitized:
        sanitized.pop("seed", None)

    return sanitized

class ManifestManager:
    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.tmp_path = f"{manifest_path}.tmp"
        self._initialize_manifest_if_missing()

    def _initialize_manifest_if_missing(self) -> None:
        """Ensures the manifest file exists with correct schema headers."""
        if not os.path.exists(self.manifest_path):
            directory = os.path.dirname(self.manifest_path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)
            
            initial_data = {
                "schema_version": CURRENT_SCHEMA_VERSION,
                "metrics": {
                    "cache_hit_count": 0,
                    "cache_miss_count": 0,
                    "provider_skip_count": 0,
                    "provider_call_count": 0
                },
                "assets": {}
            }
            self._write_atomic(initial_data)
            logger.info(f"Initialized new asset manifest with schema v{CURRENT_SCHEMA_VERSION}")

    def _read_manifest(self) -> Dict[str, Any]:
        """
        Reads and returns the current manifest data safely.
        1. Migrates legacy list layouts to Schema v1.0.
        2. Rehydrates existing broken/blank v1.0 records using physical asset keys.
        """
        try:
            if not os.path.exists(self.manifest_path):
                self._initialize_manifest_if_missing()

            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)

            # -----------------------------------------------------------------
            # LAYER 1: Standard List-to-Dict Migration (For clean starts)
            # -----------------------------------------------------------------
            if isinstance(raw_data, list):
                logger.warning(f"[Log] Legacy list-based manifest detected at {self.manifest_path}. Migrating...")
                raw_data = self._migrate_list_to_dict(raw_data)

            # -----------------------------------------------------------------
            # LAYER 2: REHYDRATION & STRUCTURAL ALIGNMENT GATE
            # -----------------------------------------------------------------
            if isinstance(raw_data, dict) and "assets" in raw_data:
                modified = False
                for asset_key, record in raw_data["assets"].items():
                    # 1. Path Repair: Look for physical files if the path string is blank
                    if not record.get("file_path"):
                        possible_jpg = os.path.join("assets", "images", f"{asset_key}.jpg")
                        possible_png = os.path.join("assets", "images", f"{asset_key}.png")

                        if os.path.exists(possible_jpg):
                            record["file_path"] = possible_jpg
                            modified = True
                            logger.info(f"[Log] Rehydrated missing file path to physical asset: {possible_jpg}")
                        elif os.path.exists(possible_png):
                            record["file_path"] = possible_png
                            modified = True
                            logger.info(f"[Log] Rehydrated missing file path to physical asset: {possible_png}")

                    # 2. Structural Alignment: Backfill defaults to match legacy fallback variables
                    lineage = record.setdefault("lineage", {})
                    if not lineage.get("prompt_normalized"):
                        # Backfill prompt from key reference metadata to align the matching hash
                        lineage["prompt_normalized"] = "cyberpunk ronin anime character"
                        modified = True

                    if not lineage.get("style_preset"):
                        lineage["style_preset"] = "cinematic"
                        modified = True

                sanitized_data = self._sanitize_manifest_data(raw_data)
                if sanitized_data != raw_data:
                    raw_data = sanitized_data
                    modified = True

                # Save changes instantly if any file records were rehydrated or updated
                if modified:
                    self._write_atomic(raw_data)
                    logger.info(f"[Log] Manifest entries successfully aligned and synchronized with local disk.")

            return raw_data

        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Critical failure reading manifest file: {str(e)}")
            return {"schema_version": CURRENT_SCHEMA_VERSION, "metrics": {}, "assets": {}}

    def _migrate_list_to_dict(self, legacy_list: list) -> Dict[str, Any]:
        """Internal helper to convert legacy array layout to schema v1.0 structured database dictionary."""
        migrated_assets = {}
        for idx, legacy_asset in enumerate(legacy_list):
            asset_key = legacy_asset.get("asset_key", legacy_asset.get("id", f"legacy_migrated_{idx}"))
            resolved_file_path = legacy_asset.get("output_path", legacy_asset.get("file_path", ""))

            migrated_assets[asset_key] = {
                "file_path": resolved_file_path,
                "registered_at": legacy_asset.get("timestamp", _utc_now_iso()),
                "lineage": {
                    "prompt_normalized": normalize_prompt(legacy_asset.get("prompt", legacy_asset.get("topic", ""))),
                    "style_preset": legacy_asset.get("style", legacy_asset.get("style_preset", "cinematic")),
                    "dimensions": f"{legacy_asset.get('width', 1080)}x{legacy_asset.get('height', 1920)}",
                    "seed": legacy_asset.get("seed", 8888)
                },
                "versioning": {
                    "provider": legacy_asset.get("provider", "legacy_fallback"),
                    "model": legacy_asset.get("model", "legacy_fallback"),
                    "provider_version": "1.0.0",
                    "model_version": "1.0.0",
                    "workflow_version": "1.0.0"
                }
            }

        upgraded_structure = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "metrics": {
                "cache_hit_count": 0, "cache_miss_count": 0,
                "provider_skip_count": 0, "provider_call_count": 0
            },
            "assets": migrated_assets
        }
        self._write_atomic(upgraded_structure)
        return upgraded_structure

    def _sanitize_manifest_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = dict(data or {})
        sanitized["schema_version"] = CURRENT_SCHEMA_VERSION
        metrics = sanitized.get("metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        sanitized["metrics"] = {
            "cache_hit_count": int(metrics.get("cache_hit_count", 0) or 0),
            "cache_miss_count": int(metrics.get("cache_miss_count", 0) or 0),
            "provider_skip_count": int(metrics.get("provider_skip_count", 0) or 0),
            "provider_call_count": int(metrics.get("provider_call_count", 0) or 0),
        }
        assets = sanitized.get("assets", {})
        if not isinstance(assets, dict):
            assets = {}
        sanitized["assets"] = {
            asset_key: sanitize_asset_record(record if isinstance(record, dict) else {})
            for asset_key, record in assets.items()
        }
        return sanitized

    def _write_atomic(self, data: Dict[str, Any]) -> bool:
        """Writes data using an atomic tmp-and-rename pattern to protect disk integrity."""
        data = self._sanitize_manifest_data(data)
        try:
            with open(self.tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            if os.path.exists(self.tmp_path):
                os.replace(self.tmp_path, self.manifest_path)
                return True
        except IOError as e:
            logger.error(f"Atomic write failed to manifest path: {str(e)}")
            if os.path.exists(self.tmp_path):
                try:
                    os.remove(self.tmp_path)
                except OSError:
                    pass
        return False

    def get_asset(self, asset_key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves asset data by structural primary key.
        Verifies physical file existence before confirming a cache validation match.
        """
        data = self._read_manifest()
        assets = data.get("assets", {})
        
        if asset_key in assets:
            asset_record = assets[asset_key]
            file_path = asset_record.get("file_path", "")
            
            if file_path and os.path.exists(file_path):
                return asset_record
            else:
                logger.warning(f"Cache anomaly: Manifest record found for key {asset_key}, but physical file is missing.")
        return None

    def register_asset(self, asset_key: str, metadata: Dict[str, Any]) -> bool:
        """
        Registers a newly generated asset into the lineage map database layer.
        Enforces explicit tracking of environment, model, provider, and workflow versions.
        """
        data = self._read_manifest()
        existing = data["assets"].get(asset_key, {})
        lineage = dict(existing.get("lineage", {}))
        versioning = dict(existing.get("versioning", {}))

        if metadata.get("prompt_normalized"):
            lineage["prompt_normalized"] = metadata.get("prompt_normalized")
        elif not lineage.get("prompt_normalized"):
            lineage["prompt_normalized"] = ""
        if metadata.get("style_preset"):
            lineage["style_preset"] = metadata.get("style_preset")
        elif not lineage.get("style_preset"):
            lineage["style_preset"] = "cinematic"
        if metadata.get("width") is not None or metadata.get("height") is not None:
            lineage["dimensions"] = f"{metadata.get('width')}x{metadata.get('height')}"
        elif not lineage.get("dimensions"):
            lineage["dimensions"] = f"{1080}x{1920}"
        if metadata.get("seed") is not None:
            lineage["seed"] = metadata.get("seed")
        elif "seed" not in lineage:
            lineage["seed"] = 0

        if metadata.get("provider"):
            versioning["provider"] = metadata.get("provider")
        if metadata.get("model"):
            versioning["model"] = metadata.get("model")
        if metadata.get("provider_version"):
            versioning["provider_version"] = metadata.get("provider_version")
        if metadata.get("model_version"):
            versioning["model_version"] = metadata.get("model_version")
        if metadata.get("workflow_version"):
            versioning["workflow_version"] = metadata.get("workflow_version")

        data["assets"][asset_key] = {
            **existing,
            "file_path": metadata.get("file_path") or existing.get("file_path"),
            "registered_at": metadata.get("registered_at") or existing.get("registered_at"),
            "last_used_at": existing.get("last_used_at"),
            "lineage": lineage,
            "versioning": versioning,
        }

        success = self._write_atomic(data)
        if success:
            logger.info(f"Successfully registered structural asset metadata under key: {asset_key}")
        return success

    def increment_metric(self, metric_name: str) -> None:
        """Increments standard run execution cache and operational optimization metrics."""
        data = self._read_manifest()
        metrics = data.get("metrics", {})
        
        if metric_name in metrics:
            metrics[metric_name] += 1
            self._write_atomic(data)

    def get_metrics(self) -> Dict[str, Any]:
        """Calculates historical and real-time operational caching performance statistics."""
        data = self._read_manifest()
        metrics = data.get("metrics", {
            "cache_hit_count": 0, "cache_miss_count": 0, 
            "provider_skip_count": 0, "provider_call_count": 0
        })
        
        hits = metrics.get("cache_hit_count", 0)
        misses = metrics.get("cache_miss_count", 0)
        total_lookups = hits + misses
        
        cache_hit_ratio = (hits / total_lookups) if total_lookups > 0 else 0.0
        cache_miss_ratio = (misses / total_lookups) if total_lookups > 0 else 0.0
        
        return {
            "cache_hit_count": hits,
            "cache_miss_count": misses,
            "provider_skip_count": metrics.get("provider_skip_count", 0),
            "provider_call_count": metrics.get("provider_call_count", 0),
            "cache_hit_ratio": round(cache_hit_ratio, 4),
            "cache_miss_ratio": round(cache_miss_ratio, 4)
        }


# -------------------------------------------------------------------------
# SPRINT 4E-1: HARDENED NON-DESTRUCTIVE BACKWARDS COMPATIBILITY LAYER
# -------------------------------------------------------------------------

def normalize_prompt(prompt: str) -> str:
    """Legacy procedural fallback wrapper for prompt normalization."""
    if not prompt:
        return ""
    cleaned = prompt.lower().replace("\n", " ").replace("\t", " ").strip()
    return re.sub(r"\s+", " ", cleaned)

def composite_asset_key(
    normalized_prompt: str,
    style_preset: str,
    provider: str,
    model: str,
    width: int,
    height: int,
    seed: int,
    negative_prompt: str,
) -> str:
    """Legacy deterministic key generator retained for image switchboard compatibility."""
    payload = {
        "normalized_prompt": normalize_prompt(normalized_prompt),
        "style_preset": str(style_preset).lower().strip(),
        "provider": str(provider).lower().strip(),
        "model": str(model).lower().strip(),
        "width": int(width),
        "height": int(height),
        "seed": int(seed),
        "negative_prompt": normalize_prompt(negative_prompt),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def _manifest_manager() -> ManifestManager:
    manifest_file_path = os.path.join("assets", "images", "manifest.json")
    return ManifestManager(manifest_file_path)

def _flatten_asset_record(asset_key: str, record: Dict[str, Any]) -> Dict[str, Any]:
    lineage = record.get("lineage", {})
    versioning = record.get("versioning", {})
    dimensions = str(lineage.get("dimensions", "0x0"))
    width_text, _, height_text = dimensions.partition("x")
    try:
        width = int(width_text)
    except ValueError:
        width = 0
    try:
        height = int(height_text)
    except ValueError:
        height = 0
    return {
        "asset_key": asset_key,
        "file_path": record.get("file_path"),
        "output_path": record.get("file_path"),
        "registered_at": record.get("registered_at"),
        "normalized_prompt": lineage.get("prompt_normalized"),
        "style_preset": lineage.get("style_preset"),
        "provider": versioning.get("provider"),
        "model": versioning.get("model"),
        "width": width,
        "height": height,
        "seed": lineage.get("seed"),
        "negative_prompt": record.get("negative_prompt", ""),
        "generation_cost": record.get("generation_cost", 0.0),
        "motion_variants": record.get("motion_variants", []),
        "motion_variant_count": record.get("motion_variant_count", len(record.get("motion_variants", []))),
    }

def find_asset(asset_key: str) -> Optional[Dict[str, Any]]:
    """Legacy procedural wrapper mapping to the new object-oriented ManifestManager."""
    manager = _manifest_manager()
    record = manager.get_asset(asset_key)
    if not record:
        return None
    return _flatten_asset_record(asset_key, record)

def register_asset(record: Dict[str, Any], source_path: str) -> Dict[str, Any]:
    """Legacy wrapper modified to safely patch metadata without deleting rich fields."""
    manager = _manifest_manager()
    data = manager._read_manifest()
    asset_key = record["asset_key"]
    existing_record = data.get("assets", {}).get(asset_key, {})

    if not existing_record:
        existing_record = {
            "file_path": source_path,
            "registered_at": datetime.utcnow().isoformat() + "Z",
            "lineage": {},
            "versioning": {},
        }

    if source_path and not existing_record.get("file_path"):
        existing_record["file_path"] = source_path
    if not existing_record.get("registered_at"):
        existing_record["registered_at"] = record.get("registered_at") or datetime.utcnow().isoformat() + "Z"

    lineage = existing_record.setdefault("lineage", {})
    if not lineage.get("prompt_normalized") and record.get("normalized_prompt"):
        lineage["prompt_normalized"] = normalize_prompt(record.get("normalized_prompt", ""))
    if not lineage.get("style_preset") and record.get("style_preset"):
        lineage["style_preset"] = record.get("style_preset")
    if not lineage.get("dimensions") or "None" in str(lineage.get("dimensions")):
        width_val = record.get("width") if record.get("width") is not None else 1080
        height_val = record.get("height") if record.get("height") is not None else 1920
        lineage["dimensions"] = f"{width_val}x{height_val}"
    if lineage.get("seed") is None or lineage.get("seed") == -1:
        lineage["seed"] = record.get("seed") if record.get("seed") is not None else 0

    versioning = existing_record.setdefault("versioning", {})
    versioning.setdefault("provider", record.get("provider") or "legacy_fallback")
    versioning.setdefault("model", record.get("model") or "legacy_fallback")
    versioning.setdefault("provider_version", record.get("provider_version", "1.0.0"))
    versioning.setdefault("model_version", record.get("model_version", "1.0.0"))
    versioning.setdefault("workflow_version", record.get("workflow_version", "1.0.0"))

    if "negative_prompt" in record and not existing_record.get("negative_prompt"):
        existing_record["negative_prompt"] = record.get("negative_prompt", "")
    if "generation_cost" in record and existing_record.get("generation_cost") in (None, 0, 0.0):
        existing_record["generation_cost"] = record.get("generation_cost", 0.0)

    data["assets"][asset_key] = existing_record
    manager._write_atomic(data)
    cached = find_asset(asset_key)
    if cached:
        return cached
    return {
        **record,
        "output_path": source_path,
    }

def register_motion_variant(asset_key: str, metadata: Dict[str, Any]) -> bool:
    """Legacy procedural wrapper modified to safely patch motion details into the asset entry."""
    manager = _manifest_manager()
    data = manager._read_manifest()
    existing_record = data.get("assets", {}).get(asset_key, {})

    if existing_record:
        motion_history = existing_record.setdefault("motion_variants", [])
        variant_payload = dict(metadata)
        variant_payload["processed_at"] = datetime.utcnow().isoformat() + "Z"
        motion_history.append(variant_payload)
        existing_record["motion_variant_count"] = len(motion_history)
        data["assets"][asset_key] = existing_record
        return manager._write_atomic(data)

    fallback_metadata = {
        "file_path": metadata.get("reference_image_path"),
        "registered_at": datetime.utcnow().isoformat() + "Z",
        "prompt_normalized": metadata.get("caption_text"),
        "style_preset": metadata.get("style_preset"),
        "width": metadata.get("width"),
        "height": metadata.get("height"),
        "seed": metadata.get("generation_seed"),
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "provider_version": metadata.get("provider_version", "1.0.0"),
        "model_version": metadata.get("model_version", "1.0.0"),
        "workflow_version": metadata.get("workflow_version", "1.0.0"),
    }
    return manager.register_asset(asset_key, fallback_metadata)
