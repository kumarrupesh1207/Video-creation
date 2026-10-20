"""Compatibility wrapper around the Aurora motion switchboard."""

from pathlib import Path

from aurora.motion.switchboard import MotionSwitchboard


class MotionEngine:
    """Backwards-compatible facade for the new motion switchboard."""

    def __init__(self, config: dict):
        self.switchboard = MotionSwitchboard(config)
        self.last_result = None

    def generate_motion(
        self,
        motion_mode: str,
        image_path: Path,
        duration: int = 5,
        caption_text: str = "",
        template_name: str = "ken_burns",
        job_id: str = "",
        asset_key: str = "",
        visual_complexity: str = "standard",
        audio_narration_path: Path | None = None,
        generation_seed: int | None = None,
        reference_image_path: Path | None = None,
        identity_lock: bool = False,
    ) -> Path:
        output_path = self.switchboard.generate(
            motion_mode,
            image_path=image_path,
            duration=duration,
            caption_text=caption_text,
            template_name=template_name,
            job_id=job_id,
            asset_key=asset_key,
            visual_complexity=visual_complexity,
            audio_narration_path=audio_narration_path,
            generation_seed=generation_seed,
            reference_image_path=reference_image_path,
            identity_lock=identity_lock,
        )
        self.last_result = self.switchboard.last_result
        return output_path

    def generate_ken_burns(self, image_path: Path, duration: int = 5,
                           caption_text: str = "") -> Path:
        return self.generate_motion(
            "static_cinematic",
            image_path=image_path,
            duration=duration,
            caption_text=caption_text,
            template_name="ken_burns",
        )

    def generate_side_drift(self, image_path: Path, duration: int = 5,
                            caption_text: str = "") -> Path:
        return self.generate_motion(
            "static_cinematic",
            image_path=image_path,
            duration=duration,
            caption_text=caption_text,
            template_name="side_drift",
        )
