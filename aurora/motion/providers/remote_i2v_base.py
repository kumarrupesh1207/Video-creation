from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from aurora.motion.base import MotionProvider, MotionResult


def build_shared_media_payload(path: str | Path | None) -> dict | None:
    if not path:
        return None
    source = Path(path).resolve()
    payload = {
        "absolute_path": str(source),
        "filename": source.name,
    }
    shared_root = os.getenv("AURORA_SHARED_ROOT", "").strip()
    if shared_root:
        shared_path = Path(shared_root).resolve()
        try:
            payload["relative_to_shared_root"] = str(source.relative_to(shared_path))
        except ValueError:
            pass
    return payload


class RemoteAuthenticationError(RuntimeError):
    pass


class RemoteQueueTimeoutError(RuntimeError):
    pass


class RemoteWorkerCrashError(RuntimeError):
    pass


class RemoteI2VProvider(MotionProvider):
    provider_name = "remote_i2v"
    request_dir_key = "request_dir"
    response_dir_key = "response_dir"
    archive_dir_key = "archive_dir"
    timeout_key = "wait_for_remote_response_sec"

    def __init__(self, config: dict):
        super().__init__(config)
        i2v_cfg = config.get("motion", {}).get("i2v", {})
        self.enabled = i2v_cfg.get("enabled", True)
        self.timeout_sec = int(i2v_cfg.get(self.timeout_key, 120))
        self.request_dir = Path(i2v_cfg.get(self.request_dir_key, "outputs/i2v_requests"))
        self.response_dir = Path(i2v_cfg.get(self.response_dir_key, "outputs/i2v_responses"))
        self.archive_dir = Path(i2v_cfg.get(self.archive_dir_key, "outputs/i2v_archived_requests"))
        self.aurora_log = Path(i2v_cfg.get("aurora_log", "aurora.jsonl"))
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.response_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        image_path: Path,
        duration: int = 5,
        caption_text: str = "",
        template_name: str = "full_motion",
        job_id: str = "",
        asset_key: str = "",
        visual_complexity: str = "standard",
        audio_narration_path: Path | None = None,
        generation_seed: int | None = None,
        reference_image_path: Path | None = None,
        identity_lock: bool = False,
    ) -> MotionResult:
        del audio_narration_path
        if not self.enabled:
            raise RuntimeError(f"[{self.provider_name}] Provider disabled in config.")

        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"[{self.provider_name}] Image not found: {image_path}")

        request_id = job_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        request_path = self.request_dir / f"{request_id}_{self.provider_name}.json"
        response_path = self.response_dir / f"{request_id}_{self.provider_name}.mp4"
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
            "response_status_path": str(response_json.resolve()),
            "duration_sec": duration,
            "motion_mode": "full_i2v_motion",
            "provider": self.provider_name,
            "visual_complexity": visual_complexity,
            "motion_prompt": (caption_text or "").strip(),
            "template_name": template_name,
            "status": "queued",
        }

        remote_job = self._submit_remote_job(payload, request_path, response_path, response_json)
        if remote_job is None:
            request_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._update_job_metadata(
                job_id,
                motion_status="waiting_remote_gpu",
                motion_provider_requested=self.provider_name,
                motion_request_path=str(request_path),
                motion_response_path=str(response_path),
                motion_response_status_path=str(response_json),
                motion_wait_timeout_sec=self.timeout_sec,
            )
            response, details = self.wait_for_response(job_id, request_path, response_path, response_json)
        else:
            self._update_job_metadata(
                job_id,
                motion_status="waiting_remote_gpu",
                motion_provider_requested=self.provider_name,
                motion_request_path=str(request_path),
                motion_response_path=str(response_path),
                motion_response_status_path=str(response_json),
                motion_wait_timeout_sec=self.timeout_sec,
                remote_job=remote_job,
            )
            response, details = self._wait_for_remote_completion(
                remote_job,
                response_path,
                response_json,
                job_id=job_id,
            )
        if response:
            return MotionResult(
                provider=self.provider_name,
                output_path=response,
                motion_mode="full_i2v_motion",
                review_required=True,
                badge="Motion R&D",
                context=details,
            )
        raise RuntimeError(f"[{self.provider_name}] Remote worker unavailable or timed out.")

    def _submit_remote_job(self, payload, request_path, response_path, response_json):
        del payload, request_path, response_path, response_json
        return None

    def _poll_remote_status(self, remote_job, response_json):
        del remote_job, response_json
        return None

    def _download_remote_artifact(self, remote_job, response_path, status_details=None):
        del remote_job, response_path, status_details
        return None

    def _wait_for_remote_completion(
        self,
        remote_job: dict,
        response_path: Path,
        response_json: Path,
        job_id: str = "",
    ) -> tuple[Path | None, dict]:
        deadline = time.time() + self.timeout_sec
        details: dict = {}
        while time.time() < deadline:
            status_details = self._poll_remote_status(remote_job, response_json) or {}
            if status_details:
                details.update(status_details)
                self._write_response_json(response_json, details)
                self._update_job_metadata(job_id, motion_remote_status=details.get("status"))
                status_value = str(details.get("status", "")).upper()
                if status_value == "PROVIDER_CRASH":
                    raise RemoteWorkerCrashError(
                        self._format_remote_failure(details, f"[{self.provider_name}] Remote worker reported PROVIDER_CRASH.")
                    )
                if status_value == "FAILED":
                    raise RemoteWorkerCrashError(
                        self._format_remote_failure(details, details.get("error", f"[{self.provider_name}] Remote worker failed."))
                    )
                if status_value == "COMPLETED":
                    downloaded = self._download_remote_artifact(remote_job, response_path, details)
                    if downloaded and Path(downloaded).exists() and Path(downloaded).stat().st_size > 0:
                        self._update_job_metadata(
                            job_id,
                            motion_status="remote_gpu_complete",
                            motion_provider_used=self.provider_name,
                            motion_response_path=str(Path(downloaded)),
                        )
                        return Path(downloaded), details
            time.sleep(2)

        self._update_job_metadata(
            job_id,
            motion_status="remote_gpu_timeout",
            motion_provider_requested=self.provider_name,
        )
        raise RemoteQueueTimeoutError(f"[{self.provider_name}] Remote worker unavailable or timed out.")

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
                        raise RuntimeError(self._format_remote_failure(details, f"[{self.provider_name}] Remote worker reported PROVIDER_CRASH."))
                    if details.get("status") == "FAILED":
                        self._archive_request(request_path, suffix="failed")
                        raise RuntimeError(self._format_remote_failure(details, details.get("error", f"[{self.provider_name}] Remote worker failed.")))
            if response_path.exists() and response_path.stat().st_size > 0:
                self._archive_request(request_path, suffix="completed")
                self._update_job_metadata(
                    job_id,
                    motion_status="remote_gpu_complete",
                    motion_provider_used=self.provider_name,
                    motion_response_path=str(response_path),
                )
                return response_path, details or self._read_response_json(response_json)
            time.sleep(2)

        self._archive_request(request_path, suffix="timed_out")
        self._update_job_metadata(
            job_id,
            motion_status="remote_gpu_timeout",
            motion_provider_requested=self.provider_name,
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

    def _write_response_json(self, response_json: Path, details: dict) -> None:
        response_json.write_text(json.dumps(details, indent=2), encoding="utf-8")

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
