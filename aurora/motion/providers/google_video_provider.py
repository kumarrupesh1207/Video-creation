"""Google video tier (Veo / Gemini Omni via the official `google-genai` SDK). OFF unless enabled AND billed.

Image -> ~8 s 9:16 clips (native audio optional); longer videos chain the last frame of each clip into the next
one and concatenate. Every call is cost-estimated first and refused if it would break the monthly budget; spend is
logged to outputs/costs/YYYY-MM.json. Model ids come from config (preview ids shut down 2026-10-22; never hard-code).
Needs GEMINI_API_KEY (AI Studio) with billing enabled: video generation has no free tier.
"""
from __future__ import annotations

import base64
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

from aurora.costs import BudgetExceeded, check_budget, log_cost  # noqa: F401 - BudgetExceeded re-exported
from aurora.motion.base import MotionProvider, MotionResult


class GoogleVideoUnavailable(RuntimeError):
    """No key, billing not enabled, disabled in config, or the API refused: caller falls back (Kaggle -> camera)."""


def default_rate(model: str) -> float:
    """USD per output second incl. audio (Aug-Oct 2026 price page); override with motion.i2v.google.rate_usd_per_sec."""
    m = model.lower()
    if "lite" in m:
        return 0.05
    if "fast" in m or "omni" in m:
        return 0.10
    return 0.40


class GoogleVideoProvider(MotionProvider):
    name = "google_video"

    def __init__(self, config: dict | None = None):
        super().__init__(config or {})
        self.cfg = (config or {}).get("motion", {}).get("i2v", {}).get("google", {})
        self.model = self.cfg.get("model", "gemini-omni-1.1-flash")
        self.clip_seconds = int(self.cfg.get("clip_seconds", 8))
        self.cost_dir = Path(self.cfg["cost_dir"]) if self.cfg.get("cost_dir") else None

    # --- helpers -----------------------------------------------------------------
    def rate(self) -> float:
        return float(self.cfg.get("rate_usd_per_sec", default_rate(self.model)))

    def estimated_cost_usd(self, duration_secs: float) -> float:
        return round(math.ceil(duration_secs / self.clip_seconds) * self.clip_seconds * self.rate(), 4)

    def is_available(self) -> bool:
        return bool(self.cfg.get("enabled", False)) and bool(os.getenv(self.cfg.get("api_key_env", "GEMINI_API_KEY")))

    def _client(self):
        key = os.getenv(self.cfg.get("api_key_env", "GEMINI_API_KEY"), "").strip()
        if not key:
            raise GoogleVideoUnavailable("GEMINI_API_KEY is not set (create one in Google AI Studio and enable billing).")
        from google import genai

        return genai.Client(api_key=key)

    def _clip(self, client, image: Path, prompt: str, out: Path) -> Path:
        """One clip from `image`. Veo ids use generate_videos (long-running op); others use the interactions API."""
        from google.genai import types

        mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
        res, audio = self.cfg.get("resolution", "720p"), bool(self.cfg.get("generate_audio", True))
        if self.model.lower().startswith("veo"):
            op = client.models.generate_videos(
                model=self.model, prompt=prompt, image=types.Image(image_bytes=image.read_bytes(), mime_type=mime),
                config=types.GenerateVideosConfig(aspect_ratio="9:16", resolution=res, duration_seconds=self.clip_seconds,
                                                  generate_audio=audio),
            )
            deadline = time.time() + int(self.cfg.get("timeout_sec", 900))
            while not op.done:
                if time.time() > deadline:
                    raise GoogleVideoUnavailable("Veo operation timed out")
                time.sleep(int(self.cfg.get("poll_sec", 10)))
                op = client.operations.get(op)
            video = op.response.generated_videos[0].video
            client.files.download(file=video)
            video.save(str(out))
        else:
            inter = client.interactions.create(
                model=self.model,
                input=[{"type": "image", "data": base64.b64encode(image.read_bytes()).decode("ascii"), "mime_type": mime},
                       {"type": "text", "text": prompt}],
                response_format={"type": "video", "aspect_ratio": "9:16", "resolution": res},
            )
            out.write_bytes(base64.b64decode(inter.output_video.data))
        if not out.exists() or out.stat().st_size == 0:
            raise GoogleVideoUnavailable("Google returned no video data")
        return out

    @staticmethod
    def _last_frame(ffmpeg: str, clip: Path, out_png: Path) -> Path:
        subprocess.run([ffmpeg, "-y", "-v", "error", "-sseof", "-0.15", "-i", str(clip), "-frames:v", "1", str(out_png)],
                       capture_output=True, check=True, timeout=60)
        return out_png

    # --- public ------------------------------------------------------------------
    def generate(self, image_path: Path, duration: int = 8, caption_text: str = "", job_id: str = "", **kwargs) -> MotionResult:
        del kwargs
        if not self.cfg.get("enabled", False):
            raise GoogleVideoUnavailable("Google video tier is disabled (motion.i2v.google.enabled).")
        n_clips = max(1, math.ceil(duration / self.clip_seconds))
        per_clip = self.clip_seconds * self.rate()
        budget = dict(monthly_budget_inr=float(self.cfg.get("monthly_budget_inr", 0)), usd_inr=float(self.cfg.get("usd_inr", 85)))
        extra = {"cost_dir": self.cost_dir} if self.cost_dir else {}
        client = self._client()
        from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

        ffmpeg = FFmpegKenBurns().ffmpeg
        work = Path(tempfile.mkdtemp(prefix="aurora_google_"))
        clips, current = [], Path(image_path)
        prompt = caption_text or "subtle natural motion, cinematic, keep the person's face and clothes exactly the same"
        for i in range(n_clips):
            check_budget(per_clip, **budget, **extra)  # before EVERY paid call
            try:
                clip = self._clip(client, current, prompt, work / f"clip{i}.mp4")
            except GoogleVideoUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 - map SDK/API failures to a clear fallback signal
                text = str(exc)
                hint = " (billing is probably not enabled for this API key: video generation is paid-only)" \
                    if any(k in text.lower() for k in ("billing", "paid", "permission", "quota", "precondition", "403", "429")) else ""
                raise GoogleVideoUnavailable(f"Google video call failed: {text[:200]}{hint}") from exc
            log_cost(self.name, self.model, self.clip_seconds, per_clip, job_id, **extra)
            clips.append(clip)
            if i < n_clips - 1:
                current = self._last_frame(ffmpeg, clip, work / f"last{i}.png")
        out = work / "google_motion.mp4"
        if len(clips) == 1:
            out = clips[0]
        else:
            lst = work / "list.txt"
            lst.write_text("".join(f"file '{c.resolve().as_posix()}'\n" for c in clips), encoding="utf-8")
            subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)],
                           capture_output=True, check=True, timeout=300)
        return MotionResult(provider=self.name, output_path=out, motion_mode="google_i2v", review_required=True,
                            context={"model": self.model, "clips": n_clips, "cost_usd": round(per_clip * n_clips, 4)})
