"""
pipeline_logger.py
──────────────────
Writes outputs/log.json tracking every pipeline phase.

Log structure:
{
  "session_id": "20260505_123456",
  "topic": "Agile Transformations in Banking",
  "started_at": "2026-05-05T12:34:56",
  "steps": {
    "script_gen":  {"status": "success", "output": "...", "elapsed_sec": 3.2},
    "image_gen":   {"status": "success", "output": "...", "elapsed_sec": 45.1},
    "video_gen":   {"status": "success", "output": "...", "elapsed_sec": 182.4},
    "audio_gen":   {"status": "success", "output": "...", "elapsed_sec": 28.7},
    "lipsync":     {"status": "success", "output": "...", "elapsed_sec": 94.3}
  },
  "final_output": "outputs/final/factory_video_v1.mp4",
  "completed_at": "2026-05-05T13:01:00",
  "total_elapsed_sec": 354.0
}
"""

import json
import time
from datetime import datetime
from pathlib import Path


class PipelineLogger:
    """Tracks pipeline execution state and writes outputs/log.json."""

    STEPS = [
        "script_gen",
        "image_gen",
        "video_gen",
        "talking_motion_gen",
        "full_i2v_motion_gen",
        "audio_gen",
        "lipsync",
        "final_assembly",
    ]
    PROGRESS_BY_STEP = {
        "script_gen": 20,
        "image_gen": 50,
        "video_gen": 80,
        "talking_motion_gen": 80,
        "full_i2v_motion_gen": 80,
        "final_assembly": 100,
    }
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED  = "failed"
    STATUS_SKIPPED = "skipped"

    def __init__(self, config: dict, topic: str = ""):
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.topic      = topic
        self._start     = time.time()
        self._step_start: float = 0.0

        self.log_dir = Path(config.get("paths", {}).get(
            "base_output", "outputs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "log.json"
        self.job_log_dir = self.log_dir / self.session_id / "logs"
        self.job_log_dir.mkdir(parents=True, exist_ok=True)
        self.job_log_path = self.job_log_dir / "job.json"

        self.data = {
            "session_id":       self.session_id,
            "topic":            topic,
            "started_at":       datetime.now().isoformat(timespec="seconds"),
            "steps":            {s: {"status": self.STATUS_PENDING,
                                     "output": None,
                                     "elapsed_sec": None,
                                     "error": None}
                                 for s in self.STEPS},
            "final_output":     None,
            "completed_at":     None,
            "total_elapsed_sec": None,
        }
        self._flush()

    # ──────────────────────────────────────────────────────────────────────────
    # Step lifecycle
    # ──────────────────────────────────────────────────────────────────────────
    def start_step(self, step: str) -> None:
        self._validate_step(step)
        self._step_start = time.time()
        self.data["steps"][step]["status"] = self.STATUS_RUNNING
        print(f"\n[Log] ▶ {step}")
        self._flush()

    def end_step(self, step: str, output: str = None) -> None:
        self._validate_step(step)
        elapsed = round(time.time() - self._step_start, 1)
        self.data["steps"][step].update({
            "status":      self.STATUS_SUCCESS,
            "output":      str(output) if output else None,
            "elapsed_sec": elapsed,
            "error":       None,
        })
        print(f"[Log] ✅ {step} — {elapsed}s"
              + (f" → {Path(output).name}" if output else ""))
        self._flush()

    def fail_step(self, step: str, error: str) -> None:
        self._validate_step(step)
        elapsed = round(time.time() - self._step_start, 1)
        self.data["steps"][step].update({
            "status":      self.STATUS_FAILED,
            "elapsed_sec": elapsed,
            "error":       str(error),
        })
        print(f"[Log] ❌ {step} FAILED ({elapsed}s): {str(error)[:120]}")
        self._flush()

    def skip_step(self, step: str, reason: str = "") -> None:
        self._validate_step(step)
        self.data["steps"][step]["status"] = self.STATUS_SKIPPED
        if reason:
            self.data["steps"][step]["error"] = reason
        print(f"[Log] ⏭  {step} skipped" + (f": {reason}" if reason else ""))
        self._flush()

    # ──────────────────────────────────────────────────────────────────────────
    # Session lifecycle
    # ──────────────────────────────────────────────────────────────────────────
    def complete(self, final_output: str = None) -> None:
        self.data["final_output"]      = str(final_output) if final_output else None
        self.data["completed_at"]      = datetime.now().isoformat(timespec="seconds")
        self.data["total_elapsed_sec"] = round(time.time() - self._start, 1)
        self._flush()
        self._print_summary()

    def set_metadata(self, **fields) -> None:
        self.data.update(fields)
        self._flush()

    def get_step_status(self, step: str) -> str:
        return self.data["steps"].get(step, {}).get("status", "unknown")

    def all_successful(self) -> bool:
        return all(
            v["status"] == self.STATUS_SUCCESS
            for v in self.data["steps"].values()
            if v["status"] != self.STATUS_SKIPPED
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────
    def _flush(self) -> None:
        serialized = json.dumps(self.data, indent=2)
        self.log_path.write_text(serialized, encoding="utf-8")

        existing = {}
        if self.job_log_path.exists():
            try:
                existing = json.loads(self.job_log_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}

        job_payload = {
            **existing,
            "job_id": self.session_id,
            "topic": self.topic,
            "started_at": self.data["started_at"],
            "steps": self.data["steps"],
            "final_output": self.data["final_output"],
            "completed_at": self.data["completed_at"],
            "total_elapsed_sec": self.data["total_elapsed_sec"],
            "progress_percent": self._compute_progress_percent(),
        }
        for key, value in self.data.items():
            if key not in job_payload:
                job_payload[key] = value
        self.job_log_path.write_text(
            json.dumps(job_payload, indent=2), encoding="utf-8"
        )

    def _compute_progress_percent(self) -> int:
        if self.data["completed_at"]:
            return 100
        progress = 0
        for step, pct in self.PROGRESS_BY_STEP.items():
            if self.data["steps"][step]["status"] == self.STATUS_SUCCESS:
                progress = max(progress, pct)
        return progress

    def _validate_step(self, step: str) -> None:
        if step not in self.STEPS:
            raise ValueError(f"Unknown pipeline step: '{step}'. "
                             f"Valid: {self.STEPS}")

    def _print_summary(self) -> None:
        total = self.data["total_elapsed_sec"]
        print("\n" + "═" * 60)
        print("  PIPELINE LOG SUMMARY")
        print("═" * 60)
        for step, info in self.data["steps"].items():
            icon = {"success": "✅", "failed": "❌",
                    "skipped": "⏭ ", "pending": "⏸ ",
                    "running": "▶ "}.get(info["status"], "?")
            t    = f"{info['elapsed_sec']}s" if info["elapsed_sec"] else "—"
            print(f"  {icon}  {step:<12} {t:>8}")
        print(f"\n  Total: {total}s")
        if self.data["final_output"]:
            print(f"  Final: {self.data['final_output']}")
        print(f"  Log  : {self.log_path.resolve()}")
        print("═" * 60 + "\n")
