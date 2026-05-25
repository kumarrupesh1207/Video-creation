from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class MotionMode(str, Enum):
    """Supported motion generation modes."""

    STATIC_CINEMATIC = "static_cinematic"
    TALKING = "talking"
    FULL_MOTION = "full_motion"


class ExecutionTier(str, Enum):
    """Provider execution cost tiers."""

    LOCAL = "local"
    FREE = "free"
    CHEAP = "cheap"
    PREMIUM = "premium"


@dataclass
class VideoResult:
    """Structured result returned by motion providers."""

    output_path: Path
    provider: str
    mode: MotionMode
    tier: ExecutionTier
    job_id: str
    image_hash: str
    duration_secs: float
    gen_time_secs: float
    cost_usd: float
    error: str = ""
    fps: int = 24
    width: int = 1080
    height: int = 1920
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MotionResult:
    provider: str
    output_path: Path
    motion_mode: str
    degraded_from: str = ""
    review_required: bool = False
    badge: str = ""
    context: dict = field(default_factory=dict)


class MotionProvider(ABC):
    """Base interface for Aurora motion providers."""

    name: str = "unknown"
    mode: MotionMode = MotionMode.STATIC_CINEMATIC
    tier: ExecutionTier = ExecutionTier.LOCAL
    priority: int = 100

    def __init__(self, config: dict | None = None):
        self.config = config

    def is_available(self) -> bool:
        """Return whether this provider can run in the current environment."""
        return False

    def estimated_cost_usd(self, duration_secs: float) -> float:
        """Estimate generation cost in US dollars."""
        del duration_secs
        return 0.0

    def estimated_time_secs(self, duration_secs: float) -> float:
        """Estimate generation time in seconds."""
        return duration_secs

    @abstractmethod
    def generate(
        self,
        image_path: Path,
        duration: int = 5,
        caption_text: str = "",
        template_name: str = "ken_burns",
        job_id: str = "",
        asset_key: str = "",
        audio_narration_path: Path | None = None,
        generation_seed: int | None = None,
        reference_image_path: Path | None = None,
        identity_lock: bool = False,
    ) -> MotionResult:
        raise NotImplementedError
