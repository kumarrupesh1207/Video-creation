from __future__ import annotations

import time
from pathlib import Path

from backend.app.providers.base import ImageProvider, ImageResult


class ManualUploadProvider(ImageProvider):
    """Final fallback when automated image generation is unavailable."""

    def generate(self, prompt: str, style: str, resolution: list[int], job_id: str) -> ImageResult:
        start = time.time()
        job_root = Path(self.config.get("paths", {}).get("base_output", "outputs")) / job_id
        manual_dir = job_root / "manual_upload"
        manual_dir.mkdir(parents=True, exist_ok=True)
        marker = manual_dir / "needs_manual_upload.txt"
        marker.write_text(
            "Automated image generation failed.\n"
            f"Prompt: {prompt}\n"
            f"Style: {style}\n"
            f"Resolution: {resolution[0]}x{resolution[1]}\n"
            "Please upload a matching still image manually and retry.\n",
            encoding="utf-8",
        )
        return ImageResult(
            provider="manual_upload",
            model="manual",
            style=style,
            latency_ms=int((time.time() - start) * 1000),
            output_path=str(marker),
            normalized_prompt=prompt,
            needs_manual_upload=True,
        )
