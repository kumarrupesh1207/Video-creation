from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ImageResult:
    provider: str
    model: str
    style: str
    latency_ms: int
    output_path: str
    generation_cost: float = 0.0
    width: int = 0
    height: int = 0
    seed: int = 0
    negative_prompt: str = ""
    normalized_prompt: str = ""
    prompt_hash: str = ""
    asset_key: str = ""
    fallback_used: bool = False
    needs_manual_upload: bool = False
    context: dict = field(default_factory=dict)


class ImageProvider(ABC):
    """Abstract image provider interface for cloud/local/manual backends."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def generate(self, prompt: str, style: str, resolution: list[int], job_id: str) -> ImageResult:
        """Generate an image or provide a manual-upload fallback marker."""
        raise NotImplementedError
