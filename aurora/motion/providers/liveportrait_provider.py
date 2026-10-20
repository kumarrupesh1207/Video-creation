from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from aurora.motion.base import MotionProvider, MotionResult
from aurora.motion.providers.remote_i2v_base import build_shared_media_payload


class LivePortraitProvider(MotionProvider):
    """Remote-worker wrapper for notebook-based LivePortrait motion jobs."""

    def __init__(self, config: dict):
        super().__init__(config)
        cfg = config.get("motion", {}).get("liveportrait", {})
        self.enabled = cfg.get("enabled", True)
        self.timeout_sec = int(cfg.get("wait_for_remote_response_sec", 120))
        self.request_dir = Path(cfg.get("request_dir", "outputs/liveportrait_requests"))
        self.response_dir = Path(cfg.get("response_dir", "outputs/liveportrait_responses"))
        self.archive_dir = Path(cfg.get("archive_dir", "outputs/liveportrait_archived_requests"))
        self.aurora_log = Path(cfg.get("aurora_log", "aurora.jsonl"))
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.response_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        image_path: Path,
        duration: int = 5,
        caption_text: str = "",
        template_name: str = "talking_head",
        job_id: str = "",
        asset_key: str = "",
        audio_narration_path: Path | None = None,
        visual_complexity: str = "standard",
        generation_seed: int | None = None,
        reference_image_path: Path | None = None,
        identity_lock: bool = False,
    ) -> MotionResult:
        del caption_text, template_name, visual_complexity
        if not self.enabled:
            raise RuntimeError("[LivePortrait] Provider disabled in config.")

        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"[LivePortrait] Image not found: {image_path}")

        request_id = job_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        request_path = self.request_dir / f"{request_id}.json"
        response_path = self.response_dir / f"{request_id}.mp4"
        response_json = response_path.with_suffix(".json")

        payload = {
            "request_id": request_id,
            "asset_key": asset_key,
            "source_image": str(image_path.resolve()),
            "reference_image_path": str((reference_image_path or image_path).resolve()),
            "reference_anchor_asset": asset_key,
            "generation_seed": generation_seed,
            "seed": generation_seed,
            "identity_lock": bool(identity_lock),
            "expected_output": str(response_path.resolve()),
            "duration_sec": duration,
            "motion_mode": "talking_motion",
            "status": "queued",
            "response_status_path": str(response_json.resolve()),
            "instructions": [
                "Generate subtle pixel-level facial motion.",
                "Prefer blink, mouth movement, and slight head tilt.",
                "Return a 9:16 portrait mp4 when possible.",
            ],
        }
        if audio_narration_path:
            payload["audio_narration"] = build_shared_media_payload(audio_narration_path)
            payload["audio_narration_path"] = str(Path(audio_narration_path).resolve())
        request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[LivePortrait] Notebook request written → {request_path}")
        self._update_job_metadata(
            job_id,
            motion_status="waiting_remote_gpu",
            motion_provider_requested="liveportrait_provider",
            motion_request_path=str(request_path),
            motion_response_path=str(response_path),
            motion_response_status_path=str(response_json),
            motion_wait_timeout_sec=self.timeout_sec,
        )

        response, response_details = self.wait_for_response(job_id, request_path, response_path, response_json)
        if response:
            print(f"[LivePortrait] ✅ Remote motion clip received → {response}")
            return MotionResult(
                provider="liveportrait_provider",
                output_path=response,
                motion_mode="talking_motion",
                review_required=True,
                badge="Motion R&D",
                context=response_details,
            )

        raise RuntimeError(
            "[LivePortrait] Remote worker unavailable or no response received before timeout."
        )

    def wait_for_response(
        self,
        job_id: str,
        request_path: Path,
        response_path: Path,
        response_json: Path,
    ) -> tuple[Path | None, dict]:
        deadline = time.time() + self.timeout_sec
        details: dict = {}
        while time.time() < deadline:
            if response_json.exists():
                details = self._read_response_json(response_json)
                if details:
                    self._update_job_metadata(job_id, motion_remote_status=details.get("status"))
                    if details.get("status") == "PROVIDER_CRASH":
                        self._archive_request(request_path, suffix="provider_crash")
                        raise RuntimeError(self._format_remote_failure(details, "[LivePortrait] Remote worker reported PROVIDER_CRASH."))
                    if details.get("status") == "FAILED":
                        self._archive_request(request_path, suffix="failed")
                        raise RuntimeError(self._format_remote_failure(details, details.get("error", "[LivePortrait] Remote worker failed.")))
            if response_path.exists() and response_path.stat().st_size > 0:
                self._archive_request(request_path, suffix="completed")
                self._update_job_metadata(
                    job_id,
                    motion_status="remote_gpu_complete",
                    motion_provider_used="liveportrait_provider",
                    motion_response_path=str(response_path),
                    lip_sync_status=details.get("lip_sync_status"),
                    lip_sync_warning=details.get("lip_sync_warning"),
                )
                return response_path, details or self._read_response_json(response_json)
            time.sleep(2)

        self._archive_request(request_path, suffix="timed_out")
        self._update_job_metadata(
            job_id,
            motion_status="remote_gpu_timeout",
            motion_provider_requested="liveportrait_provider",
        )
        return None, {}

    def _archive_request(self, request_path: Path, suffix: str) -> None:
        if not request_path.exists():
            return
        archived = self.archive_dir / f"{request_path.stem}_{suffix}{request_path.suffix}"
        shutil.move(str(request_path), str(archived))

    def _update_job_metadata(self, job_id: str, **fields) -> None:
        if not job_id:
            return
        base_output = Path(self.config.get("paths", {}).get("base_output", "outputs"))
        job_log_path = base_output / job_id / "logs" / "job.json"
        job_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {}
        if job_log_path.exists():
            try:
                payload = json.loads(job_log_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
        payload.update(fields)
        job_log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _read_response_json(self, response_json: Path) -> dict:
        if not response_json.exists():
            return {}
        try:
            return json.loads(response_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _format_remote_failure(self, details: dict, default_message: str) -> str:
        error_type = details.get("error_type") or "UNKNOWN_ERROR"
        exit_code = details.get("exit_code")
        stderr_tail = details.get("stderr_tail") or []
        message = details.get("error") or default_message
        suffix = f" type={error_type}"
        if exit_code is not None:
            suffix += f" exit_code={exit_code}"
        if stderr_tail:
            suffix += " stderr_tail=" + " | ".join(str(line) for line in stderr_tail)
        return f"{message}{suffix}"
