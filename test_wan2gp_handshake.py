"""
Sprint 4F-2 Step 1C validation harness.
Exercises Wan2GP forced-fallback and mocked-success motion flows.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

import yaml

from aurora.motion.switchboard import MotionSwitchboard


ROOT = Path(__file__).resolve().parent
ASSET_KEY = "28a108349bceb1e5fe6c8aa7aee05c80ef71baa827d310371b00bc3d5973ba56"
IMAGE_PATH = ROOT / "assets" / "images" / f"{ASSET_KEY}.jpg"
MANIFEST_PATH = ROOT / "assets" / "images" / "manifest.json"
TELEMETRY_DIR = ROOT / "outputs" / "telemetry"


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def read_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def latest_motion_variant() -> dict:
    manifest = read_manifest()
    return manifest["assets"][ASSET_KEY]["motion_variants"][-1]


def forced_fallback_test() -> dict:
    cfg = load_config()
    cfg.setdefault("motion", {}).setdefault("i2v", {}).setdefault("wan2gp_provider", {})
    cfg["motion"]["i2v"]["wan2gp_provider"].update(
        {
            "base_url": "https://wan2gp.invalid.test",
            "submit_endpoint": "/tasks",
            "status_endpoint": "/tasks/{task_id}",
            "api_key_env": "WAN_API_KEY",
        }
    )
    os.environ["WAN_API_KEY"] = "invalid_token_test"
    switchboard = MotionSwitchboard(cfg)

    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    with patch("requests.post", return_value=FakeResponse()):
        output_path = switchboard.generate(
            "wan2gp_provider",
            image_path=IMAGE_PATH,
            duration=5,
            caption_text="Verification Testing",
            template_name="full_motion",
            job_id="wan_auth_failure",
            asset_key=ASSET_KEY,
            visual_complexity="high action",
            generation_seed=42,
            reference_image_path=IMAGE_PATH,
            identity_lock=False,
        )

    result_context = dict(switchboard.last_result.context or {})
    manifest_variant = latest_motion_variant()
    telemetry_file = TELEMETRY_DIR / "metric_wan_auth_failure_video_gen.json"
    telemetry = json.loads(telemetry_file.read_text(encoding="utf-8")) if telemetry_file.exists() else {}
    return {
        "output_path": str(output_path),
        "last_result_context": result_context,
        "manifest_variant": manifest_variant,
        "telemetry": telemetry,
    }


def mocked_success_test() -> dict:
    cfg = load_config()
    switchboard = MotionSwitchboard(cfg)
    target_output = ROOT / "outputs" / "videos" / f"{ASSET_KEY}_mock_remote.mp4"
    target_output.parent.mkdir(parents=True, exist_ok=True)

    def fake_submit(self, payload, request_path, response_path, response_json):
        del payload, request_path, response_path, response_json
        return {"task_id": "mock-task-123", "status_url": "mock://status/mock-task-123"}

    def fake_poll(self, remote_job, response_json):
        del remote_job, response_json
        return {
            "status": "COMPLETED",
            "task_id": "mock-task-123",
            "download_url": "mock://download/mock-task-123",
        }

    def fake_download(self, remote_job, response_path, status_details=None):
        del remote_job, status_details
        response_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return response_path

    with (
        patch("aurora.motion.providers.wan2gp_provider.Wan2GPProvider._submit_remote_job", fake_submit),
        patch("aurora.motion.providers.wan2gp_provider.Wan2GPProvider._poll_remote_status", fake_poll),
        patch("aurora.motion.providers.wan2gp_provider.Wan2GPProvider._download_remote_artifact", fake_download),
    ):
        output_path = switchboard.generate(
            "wan2gp_provider",
            image_path=IMAGE_PATH,
            duration=5,
            caption_text="Verification Testing",
            template_name="full_motion",
            job_id="wan_mock_success",
            asset_key=ASSET_KEY,
            visual_complexity="standard",
            generation_seed=42,
            reference_image_path=IMAGE_PATH,
            identity_lock=False,
        )

    result_context = dict(switchboard.last_result.context or {})
    telemetry_file = TELEMETRY_DIR / "metric_wan_mock_success_video_gen.json"
    telemetry = json.loads(telemetry_file.read_text(encoding="utf-8")) if telemetry_file.exists() else {}
    return {
        "output_path": str(output_path),
        "last_result_context": result_context,
        "telemetry": telemetry,
    }


def main() -> None:
    if not IMAGE_PATH.exists():
        raise FileNotFoundError(f"Required test image missing: {IMAGE_PATH}")

    forced = forced_fallback_test()
    mocked = mocked_success_test()

    print("=== Forced Fallback Test ===")
    print(json.dumps(forced, indent=2))
    print("=== Mocked Success Test ===")
    print(json.dumps(mocked, indent=2))


if __name__ == "__main__":
    main()
