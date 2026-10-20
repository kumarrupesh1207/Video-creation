from __future__ import annotations

from pathlib import Path

from aurora.motion.providers.remote_i2v_base import (
    RemoteAuthenticationError,
    RemoteI2VProvider,
    RemoteWorkerCrashError,
)


class Wan2GPProvider(RemoteI2VProvider):
    provider_name = "wan2gp_provider"

    def _provider_config(self) -> dict:
        i2v_cfg = self.config.get("motion", {}).get("i2v", {})
        return i2v_cfg.get(self.provider_name, {})

    def _auth_headers(self) -> dict:
        import os

        provider_cfg = self._provider_config()
        api_key_env = provider_cfg.get("api_key_env", "WAN_API_KEY")
        api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise RemoteAuthenticationError(f"[{self.provider_name}] Missing API key in env var: {api_key_env}")
        return {
            "Authorization": f"Bearer {api_key}",
        }

    def _submit_remote_job(self, payload, request_path, response_path, response_json):
        del request_path, response_path, response_json
        import requests

        provider_cfg = self._provider_config()
        base_url = provider_cfg.get("base_url", "").rstrip("/")
        submit_endpoint = provider_cfg.get("submit_endpoint", "/tasks")
        timeout = int(provider_cfg.get("submit_timeout_sec", 60))
        if not base_url:
            return None

        source_image = Path(payload["source_image"])
        endpoint = f"{base_url}{submit_endpoint}"
        headers = self._auth_headers()
        data = {
            "motion_mode": payload.get("motion_mode"),
            "duration_sec": payload.get("duration_sec"),
            "visual_complexity": payload.get("visual_complexity"),
            "motion_prompt": payload.get("motion_prompt"),
            "template_name": payload.get("template_name"),
            "asset_key": payload.get("asset_key"),
            "seed": payload.get("seed"),
            "generation_seed": payload.get("generation_seed"),
            "identity_lock": json_bool(payload.get("identity_lock")),
            "reference_anchor_asset": payload.get("reference_anchor_asset"),
            "reference_image_path": payload.get("reference_image_path"),
        }
        with source_image.open("rb") as image_file:
            files = {
                "source_image": (source_image.name, image_file, "image/jpeg"),
            }
            response = requests.post(
                endpoint,
                headers=headers,
                data=data,
                files=files,
                timeout=timeout,
            )
        if response.status_code in {401, 403}:
            raise RemoteAuthenticationError(f"[{self.provider_name}] Authentication failed with status {response.status_code}.")
        response.raise_for_status()
        body = response.json()
        task_id = body.get("task_id") or body.get("id") or body.get("job_id")
        if not task_id:
            raise RuntimeError(f"[{self.provider_name}] Remote submit response did not contain a task identifier.")
        status_endpoint = provider_cfg.get("status_endpoint", f"/tasks/{task_id}")
        if "{task_id}" in status_endpoint:
            status_endpoint = status_endpoint.format(task_id=task_id)
        return {
            "task_id": task_id,
            "status_url": f"{base_url}{status_endpoint}",
            "submit_response": body,
        }

    def _poll_remote_status(self, remote_job, response_json):
        del response_json
        import requests

        provider_cfg = self._provider_config()
        timeout = int(provider_cfg.get("status_timeout_sec", 30))
        response = requests.get(
            remote_job["status_url"],
            headers=self._auth_headers(),
            timeout=timeout,
        )
        if response.status_code in {401, 403}:
            raise RemoteAuthenticationError(f"[{self.provider_name}] Authentication failed with status {response.status_code}.")
        response.raise_for_status()
        body = response.json()
        raw_status = str(body.get("status", "")).strip().lower()
        if raw_status in {"failed", "crashed", "error"}:
            raise RemoteWorkerCrashError(
                body.get("error")
                or body.get("message")
                or f"[{self.provider_name}] Remote worker reported failed status."
            )
        normalized_status = raw_status
        if raw_status in {"done", "completed", "succeeded", "success"}:
            normalized_status = "COMPLETED"
        elif raw_status in {"pending", "queued"}:
            normalized_status = "pending"
        elif raw_status in {"processing", "running", "in_progress"}:
            normalized_status = "processing"
        return {
            "status": normalized_status,
            "task_id": remote_job.get("task_id"),
            "download_url": body.get("download_url") or body.get("artifact_url") or body.get("output_url"),
            "raw_response": body,
        }

    def _download_remote_artifact(self, remote_job, response_path, status_details=None):
        del remote_job
        import requests

        status_details = status_details or {}
        download_url = status_details.get("download_url")
        if not download_url:
            raise RuntimeError(f"[{self.provider_name}] Completed remote job did not include a download URL.")
        timeout = int(self._provider_config().get("download_timeout_sec", 300))
        with requests.get(
            download_url,
            headers=self._auth_headers(),
            timeout=timeout,
            stream=True,
        ) as response:
            if response.status_code in {401, 403}:
                raise RemoteAuthenticationError(f"[{self.provider_name}] Authentication failed with status {response.status_code}.")
            response.raise_for_status()
            response_path.parent.mkdir(parents=True, exist_ok=True)
            with response_path.open("wb") as output_file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output_file.write(chunk)
        return response_path


def json_bool(value) -> str:
    return "true" if bool(value) else "false"
