from __future__ import annotations

import json
import os
import sys

import requests


def main() -> int:
    api_key = os.getenv("NVIDIA_API_KEY")
    if not api_key:
        print("NVIDIA_API_KEY is not set.")
        return 1

    key_preview = f"{api_key[:4]}..." if len(api_key) >= 4 else "***"
    url = "https://integrate.api.nvidia.com/v1/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    print(f"NVIDIA key preview: {key_preview}")
    print(f"Discovery URL: {url}")

    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"Model discovery failed: {exc}")
        return 1

    try:
        body = resp.json()
    except json.JSONDecodeError:
        print("Discovery response was not valid JSON.")
        return 1

    data = body.get("data", body if isinstance(body, list) else [])
    if not isinstance(data, list):
        print("Discovery response did not contain a model list.")
        print(json.dumps(body, indent=2)[:2000])
        return 1

    model_ids = sorted(
        str(item.get("id")).strip()
        for item in data
        if isinstance(item, dict) and item.get("id")
    )

    print(f"Found {len(model_ids)} model(s):")
    for model_id in model_ids:
        print(model_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
