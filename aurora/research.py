"""AI research / prompt writing: niche -> N video ideas with scene breakdowns (Gemini text, free tier).

    research_ideas("Indian street food", "Hindi", "Instagram Reels", n=3)

Strict JSON schema (pydantic), validated. Without GEMINI_API_KEY (or on any API failure) it falls back to the
local LLM (Ollama via LLMManager) and finally to the niche text itself, so callers always get valid ideas.
Google Search grounding is paid-only and off unless research.grounding is true in config.yaml.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

ROOT = Path(__file__).resolve().parents[1]


class Scene(BaseModel):
    image_prompt: str = Field(min_length=5)
    motion_prompt: str = Field(min_length=3)
    voiceover: str = Field(min_length=1)


class Idea(BaseModel):
    title: str = Field(min_length=3)
    hook: str = Field(min_length=3)
    description: str
    hashtags: list[str]
    scenes: list[Scene] = Field(min_length=3, max_length=6)
    ai_disclosure: bool = True  # label AI-generated/altered content on the platform


class Ideas(BaseModel):
    ideas: list[Idea] = Field(min_length=1)


PROMPT = (
    "You are a short-form video strategist. Niche: {niche}. Language of titles, hooks and voiceover: {language}. "
    "Platform: {platform} (vertical 9:16, 15-30 s). Return {n} distinct video ideas. Each idea: a scroll-stopping hook, "
    "a title, a description, 5-8 hashtags, and 3-6 scenes. Each scene has an image_prompt (English, photorealistic, one "
    "person or subject, setting, lighting, camera), a motion_prompt (English, what moves), and a voiceover line in {language} "
    "(one short sentence). Keep everything advertiser-friendly and safe for all audiences. Set ai_disclosure to true."
)


def _settings() -> dict:
    try:
        import yaml

        return (yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {})
    except Exception:  # noqa: BLE001
        return {}


def _fallback(niche: str, language: str, n: int, reason: str) -> dict:
    """Valid ideas without any API: one idea built from the niche text (plus a note on why)."""
    scene = Scene(image_prompt=f"{niche}, photorealistic, natural light, vertical portrait",
                  motion_prompt="slow gentle camera push-in", voiceover=niche)
    idea = Idea(title=niche[:80] or "Untitled", hook=niche[:80] or "Watch this", description=niche,
                hashtags=["#shorts", "#reels"], scenes=[scene, scene, scene], ai_disclosure=True)
    return {"ideas": [idea.model_dump()], "source": "topic_fallback", "note": reason}


def _local_llm(niche: str, language: str, platform: str, n: int, cfg: dict) -> dict | None:
    try:
        from src.modules.llm_manager import LLMManager

        llm = LLMManager(cfg)
        if not llm._server_alive():
            return None
        data = llm._call_ollama(PROMPT.format(niche=niche, language=language, platform=platform, n=n)
                                + ' Reply ONLY with JSON: {"ideas": [{"title","hook","description","hashtags":[],'
                                  '"scenes":[{"image_prompt","motion_prompt","voiceover"}],"ai_disclosure":true}]}')
        return {"ideas": [i.model_dump() for i in Ideas.model_validate(data).ideas], "source": "local_llm", "note": ""}
    except Exception:  # noqa: BLE001
        return None


def research_ideas(niche: str, language: str = "English", platform: str = "Instagram Reels", n: int = 3,
                   cfg: dict | None = None, client=None) -> dict:
    """Returns {"ideas": [...validated...], "source": "gemini|local_llm|topic_fallback", "note": str}."""
    cfg = cfg if cfg is not None else _settings()
    rcfg = cfg.get("research", {})
    key = os.getenv(rcfg.get("api_key_env", "GEMINI_API_KEY"), "").strip()
    if client is None and not key:
        return _local_llm(niche, language, platform, n, cfg) or _fallback(niche, language, n, "no GEMINI_API_KEY")
    try:
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        from google.genai import types

        config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=Ideas)
        if rcfg.get("grounding", False):  # paid-only on Google's side
            config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
        resp = client.models.generate_content(
            model=rcfg.get("model", "gemini-3.1-flash-lite"),
            contents=PROMPT.format(niche=niche, language=language, platform=platform, n=n), config=config)
        data = Ideas.model_validate_json(resp.text) if resp.text else resp.parsed
        ideas = data if isinstance(data, Ideas) else Ideas.model_validate(data)
        return {"ideas": [i.model_dump() for i in ideas.ideas][:n], "source": "gemini", "note": ""}
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        reason = f"invalid Gemini JSON: {str(exc)[:100]}"
    except Exception as exc:  # noqa: BLE001 - network/quota/etc: never break the caller
        reason = f"Gemini failed: {str(exc)[:120]}"
    return _local_llm(niche, language, platform, n, cfg) or _fallback(niche, language, n, reason)
