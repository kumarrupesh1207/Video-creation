"""
Aurora Structured Observability & Telemetry System
Centralized aggregator tracking execution latencies, cache metrics, and error boundaries.
Safe for high-frequency, un-locked micro-writes on Ryzen local storage environments.
"""

import os
import json
import time
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger("aurora.monitoring")


class TelemetryCollector:
    def __init__(self, telemetry_dir: str = "outputs/telemetry"):
        self.telemetry_dir = telemetry_dir
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._ensure_directory_exists()

    def _ensure_directory_exists(self) -> None:
        """Guarantees telemetry storage path is accessible on disk."""
        if not os.path.exists(self.telemetry_dir):
            os.makedirs(self.telemetry_dir, exist_ok=True)

    def start_stage(self, transaction_id: str, stage_name: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Registers the start execution timestamp for a distinct pipeline stage."""
        now = datetime.utcnow().isoformat() + "Z"
        session_key = f"{transaction_id}:{stage_name}"

        self.sessions[session_key] = {
            "transaction_id": transaction_id,
            "timestamp": now,
            "pipeline_stage": stage_name,
            "topic": (metadata or {}).get("topic", "unspecified"),
            "start_time_raw": time.perf_counter(),
            "metrics": {
                "provider_used": "pending",
                "latency_ms": 0.0,
                "cache_hit": False,
                "retry_count": 0,
                "queue_time_ms": (metadata or {}).get("queue_time_ms", 0.0)
            },
            "status": {
                "export_status": "running",
                "failure_reason": None
            }
        }
        logger.info(f"[Telemetry] Started tracking stage '{stage_name}' for transaction: {transaction_id}")

    def complete_stage(self, transaction_id: str, stage_name: str, metrics_patch: Dict[str, Any], status_str: str = "success", error_msg: Optional[str] = None) -> None:
        """Calculates final latency, applies the metric footprint shape, and writes to disk."""
        session_key = f"{transaction_id}:{stage_name}"
        session = self.sessions.get(session_key)

        if not session:
            logger.warning(f"[Telemetry] Attempted to complete stage '{stage_name}' without an active start marker.")
            return

        # Calculate exact execution duration in milliseconds
        duration_ms = (time.perf_counter() - session.pop("start_time_raw")) * 1000.0

        # Merge unified telemetry payload keys carefully
        session["metrics"]["latency_ms"] = round(duration_ms, 2)
        session["metrics"]["provider_used"] = metrics_patch.get("provider_used", session["metrics"]["provider_used"])
        session["metrics"]["cache_hit"] = bool(metrics_patch.get("cache_hit", False))
        session["metrics"]["retry_count"] = int(metrics_patch.get("retry_count", 0))
        for key, value in metrics_patch.items():
            if key not in session["metrics"]:
                session["metrics"][key] = value

        session["status"]["export_status"] = status_str
        session["status"]["failure_reason"] = error_msg

        # Commit record immediately to an isolated transaction file to ensure zero multi-thread blocks
        self._write_telemetry_atomic(transaction_id, stage_name, session)

    def _write_telemetry_atomic(self, tx_id: str, stage_name: str, data: Dict[str, Any]) -> None:
        """Writes metric files atomically to avoid shared file locking corruption."""
        file_name = f"metric_{tx_id}_{stage_name}.json"
        dest_path = os.path.join(self.telemetry_dir, file_name)
        tmp_path = f"{dest_path}.tmp"

        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            if os.path.exists(tmp_path):
                os.replace(tmp_path, dest_path)
                logger.info(f"[Telemetry] Persisted transaction metric record to disk: {file_name}")
        except IOError as e:
            logger.error(f"[Telemetry] Failed to persist structural telemetry output: {str(e)}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
