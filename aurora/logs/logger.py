from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StructuredLogger:
    """Append-only JSONL logger for Aurora pipeline events."""

    REQUIRED_FIELDS = (
        "ts",
        "level",
        "component",
        "event",
        "job_id",
        "provider",
        "mode",
        "tier",
        "image_hash",
        "duration_secs",
        "gen_time_secs",
        "cost_usd",
        "error",
    )

    def __init__(self, component: str, log_path: Path | str = Path("logs") / "aurora.jsonl", job_id: str | None = None):
        self.component = component
        self.log_path = Path(log_path)
        self.job_id = job_id or str(uuid.uuid4())
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def info(self, event: str, **fields: Any) -> dict[str, Any]:
        """Write an info-level event and return the emitted record."""
        return self._write("INFO", event, **fields)

    def warning(self, event: str, **fields: Any) -> dict[str, Any]:
        """Write a warning-level event and return the emitted record."""
        return self._write("WARNING", event, **fields)

    def error(self, event: str, **fields: Any) -> dict[str, Any]:
        """Write an error-level event and return the emitted record."""
        return self._write("ERROR", event, **fields)

    def _write(self, level: str, event: str, **fields: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "component": self.component,
            "event": event,
            "job_id": fields.pop("job_id", self.job_id),
            "provider": fields.pop("provider", ""),
            "mode": fields.pop("mode", ""),
            "tier": fields.pop("tier", ""),
            "image_hash": fields.pop("image_hash", ""),
            "duration_secs": fields.pop("duration_secs", 0.0),
            "gen_time_secs": fields.pop("gen_time_secs", 0.0),
            "cost_usd": fields.pop("cost_usd", 0.0),
            "error": fields.pop("error", ""),
        }
        record.update(fields)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        return record
