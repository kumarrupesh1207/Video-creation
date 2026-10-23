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


# ----------------------------------------------------------------------------- motion-comic format
SERIES_DIR = ROOT / "outputs" / "series"
COMIC_STYLE = "cel-shaded anime comic panel"


class Character(BaseModel):
    name: str
    description: str = Field(min_length=10)  # FIXED look: hair, eyes, outfit, colours (reused verbatim in every prompt)
    style: str = COMIC_STYLE


class Panel(BaseModel):
    shot: str = "medium"  # close-up | wide | medium | action
    characters: list[str] = []
    image_prompt: str = Field(min_length=5)  # the scene only; the character sheet is added by code
    narration: str = Field(min_length=3)  # ~6-12 words
    bubble: str = ""
    sfx: str = ""
    action: bool = False


class SeriesInfo(BaseModel):
    title: str
    part: int = 1
    total_parts: int = 1  # the story is planned as 1 or 2 parts
    recap: str = ""


class Comic(BaseModel):
    series: SeriesInfo
    character_sheet: list[Character] = Field(min_length=1)
    panels: list[Panel] = Field(min_length=3, max_length=10)
    cliffhanger: str = ""
    cta: str = ""


def load_series(channel: str, series_dir: Path = SERIES_DIR) -> dict:
    p = Path(series_dir) / f"{channel}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_series(channel: str, comic: dict, series_dir: Path = SERIES_DIR) -> dict:
    """Continuity state for the next part: characters, last-part summary, fixed image seed."""
    prev = load_series(channel, series_dir)
    state = {"channel": channel, "title": comic["series"]["title"], "part": comic["series"]["part"],
             "total_parts": comic["series"].get("total_parts", 1),
             "characters": comic["character_sheet"], "last_summary": " ".join(p["narration"] for p in comic["panels"])[:600],
             "cliffhanger": comic.get("cliffhanger", ""), "seed": prev.get("seed") or (abs(hash(channel)) % 2**31 or 7)}
    Path(series_dir).mkdir(parents=True, exist_ok=True)
    (Path(series_dir) / f"{channel}.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return state


def panel_prompt(panel: dict, sheet: list[dict]) -> str:
    """Final image prompt: style + the FIXED character descriptions for this panel + shot + scene."""
    names = {n.lower() for n in panel.get("characters", [])}
    chars = [c for c in sheet if not names or c["name"].lower() in names] or sheet[:1]
    return ("cel-shaded anime illustration, single panel, one single full-bleed scene, " + ", ".join(f"{c['name']}: {c['description']}" for c in chars)
            + f", {panel.get('shot', 'medium')} shot, {panel['image_prompt']}, composition: leave clear empty space (sky or background) in the upper third of the frame for text, character placed in the lower two thirds, vibrant colors, clean lineart, original characters, single full-frame illustration, no panel borders, no frame, no text, no letters, no words, no captions, no signature, no watermark, no speech bubbles, no sound effect lettering, wordless illustration, no speech balloons")


COMIC_PROMPT = (
    "You write ORIGINAL anime-style motion-comic scripts for vertical short videos (never use existing franchises or "
    "their characters). Language of narration, speech bubbles and CTA: {language}. Series premise: {premise}. "
    "Continuity state (JSON, may be empty; keep these characters' looks EXACTLY and continue the story): {state}. "
    "Write Part {part} with exactly {n} panels. Plan the whole story as {total_rule}. Return: series {{title, part, total_parts, recap}} (recap = one line of the story so far), "
    "character_sheet (each main character: name, a fixed visual description of hair, eyes, outfit, colours; style "
    "'{style}'), panels (each: shot close-up|wide|medium|action, characters present, image_prompt = the scene only in "
    "English, narration of 8-12 words, optional short bubble line, sfx/mood tag, action true for fight/chase panels), "
    "{ending}. Safe for all audiences."
)


def _comic_fallback(premise: str, n: int, state: dict, note: str) -> dict:
    sheet = state.get("characters") or [{"name": "Kaito", "style": COMIC_STYLE, "description":
             "young man, spiky silver hair, bright blue eyes, long black coat with red scarf"}]
    hero = sheet[0]["name"]
    beats = [("wide", "establishing shot of the city skyline at dusk", f"{premise}. The story begins tonight.", False),
             ("close-up", f"close-up of {hero}'s determined face", f"{hero} knew there was no turning back.", False),
             ("medium", f"{hero} walking through a crowded neon street", "A strange signal pulled him forward.", False),
             ("action", f"{hero} dodging a glowing energy blast, dynamic angle", "Then the ambush began.", True),
             ("action", f"{hero} striking back with a flash of light, speed lines", "He fought with everything he had.", True),
             ("close-up", f"{hero} staring at a shadowy figure in the smoke, shocked", "But the figure knew his name...", False)]
    panels = [Panel(shot=s, characters=[hero], image_prompt=p, narration=t, action=a).model_dump()
              for s, p, t, a in (beats * 2)[:max(3, min(n, 10))]]
    part = int(state.get("part", 0)) + 1
    return {"series": {"title": state.get("title") or premise[:40], "part": part, "recap": state.get("last_summary", "")[:120]},
            "character_sheet": sheet, "panels": panels, "cliffhanger": "The figure knew his name.",
            "cta": f"Follow for Part {part + 1}"}


def research_comic(channel: str, premise: str, language: str = "English", panels: int = 6, cfg: dict | None = None,
                   client=None, series_dir: Path = SERIES_DIR, max_parts: int | None = None) -> dict:
    """Part N+1 of a series: {"comic": {...validated...}, "source": "gemini|topic_fallback", "note": str}."""
    cfg = cfg if cfg is not None else _settings()
    rcfg, state = cfg.get("research", {}), load_series(channel, series_dir)
    max_parts = int(max_parts or rcfg.get("max_parts", 2))
    if state and int(state.get("part", 0)) >= int(state.get("total_parts", 1)):
        state = {}  # previous story is finished -> start a new one (new characters, part 1)
    n, part = max(5, min(int(panels), 10)), int(state.get("part", 0)) + 1
    total = max(part, min(int(state.get("total_parts", max_parts)), max_parts))
    key = os.getenv(rcfg.get("api_key_env", "GEMINI_API_KEY"), "").strip()
    reason = "no GEMINI_API_KEY"
    if client is not None or key:
        try:
            if client is None:
                from google import genai

                client = genai.Client(api_key=key)
            from google.genai import types

            resp = client.models.generate_content(
                model=rcfg.get("model", "gemini-3.1-flash-lite"),
                contents=COMIC_PROMPT.format(
                    language=language, premise=premise, state=json.dumps(state, ensure_ascii=False)[:3000], part=part, n=n,
                    style=COMIC_STYLE,
                    total_rule=(f"exactly {total} parts" if state else f"1 or 2 parts (set total_parts; at most {max_parts})"),
                    ending=("this is the FINAL part: resolve the story, no cliffhanger, cta = 'The End'" if part >= total else
                            "end on a cliffhanger and cta = 'Part " + str(part + 1) + " coming soon'")),
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Comic))
            comic = Comic.model_validate_json(resp.text)
            comic.series.part = part
            comic.series.total_parts = max(part, min(comic.series.total_parts, max_parts, total if state else max_parts))
            if part >= comic.series.total_parts:
                comic.cliffhanger, comic.cta = "", "The End"
            return {"comic": comic.model_dump(), "source": "gemini", "note": ""}
        except Exception as exc:  # noqa: BLE001
            reason = f"Gemini failed/invalid: {str(exc)[:100]}"
    fb = _comic_fallback(premise, n, state, reason)
    fb["series"]["total_parts"] = total if state else min(2, max_parts)
    if part >= fb["series"]["total_parts"]:
        fb["cliffhanger"], fb["cta"] = "", "The End"
    else:
        fb["cta"] = f"Part {part + 1} coming soon"
    return {"comic": Comic.model_validate(fb).model_dump(),
            "source": "topic_fallback", "note": reason}
