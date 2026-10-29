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
    hook: str = ""  # 3-6 word teaser for the video title (chapter mode)
    hook_panel: int = 0  # 1-based number of the most dramatic panel (opens the Short); 0 = let the renderer pick
    hook_line: str = ""  # the strongest <= 8 word line / sfx of that panel
    hook_title: str = ""  # <= 45 chars curiosity title the part really delivers (publisher)
    engagement_question: str = ""  # short story-specific question for viewers (publisher)
    previously: str = ""  # ONE line recap of the story so far ("Previously…" card, Part 2+)


def load_series(channel: str, series_dir: Path = SERIES_DIR) -> dict:
    p = Path(series_dir) / f"{channel}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_series(channel: str, comic: dict, series_dir: Path = SERIES_DIR) -> dict:
    """Continuity state for the next part: characters, last-part summary, fixed image seed."""
    prev = load_series(channel, series_dir)
    state = {**prev, "channel": channel, "title": comic["series"]["title"], "part": comic["series"]["part"],
             "total_parts": comic["series"].get("total_parts", 1),
             "characters": comic["character_sheet"], "last_summary": " ".join(p["narration"] for p in comic["panels"])[:600],
             "cliffhanger": comic.get("cliffhanger", ""), "seed": prev.get("seed") or (abs(hash(channel)) % 2**31 or 7)}
    Path(series_dir).mkdir(parents=True, exist_ok=True)
    (Path(series_dir) / f"{channel}.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return state


# Words NVIDIA NIM's filter rejects (CONTENT_FILTERED) although they are harmless: "violet" is blocked (looks like "violent"),
# "purple" passes. Found by bisecting a blocked prompt; extend this list when a new false positive is confirmed.
NIM_SAFE_WORDS = [(r"(?<![A-Za-z])violets?(?![A-Za-z])", "purple")]  # lookarounds = word boundaries


def nim_safe(text: str) -> str:
    import re

    for pat, repl in NIM_SAFE_WORDS:
        text = re.sub(pat, repl, text, flags=re.I)
    return text


PROMPT_SUFFIX = (", composition: character in the lower two thirds, clear empty sky or background in the upper third (room for text), "
                 "single full-bleed scene, no panel borders, no frame, no text, no letters, no captions, no signature, no watermark, "
                 "no speech bubbles, wordless, vibrant colors, clean lineart, original characters")
MAX_PROMPT_CHARS = 780  # NIM rejects prompts over 800 characters (HTTP 422 string_too_long)


def _cut(text: str, n: int) -> str:
    """Shorten to <= n chars at the last comma/space, keeping whole phrases."""
    if len(text) <= n:
        return text
    cut = text[:n]
    i = max(cut.rfind(","), cut.rfind(" "))
    return (cut[:i] if i > n // 2 else cut).rstrip(" ,;")


PROMPT_SUFFIX_WIDE = (", composition: widescreen 16:9 frame, character off-centre with clear empty background space on one side (room for text), "
                      "single full-bleed scene, no panel borders, no frame, no text, no letters, no captions, no signature, no watermark, "
                      "no speech bubbles, wordless, vibrant colors, clean lineart, original characters")


def panel_prompt(panel: dict, sheet: list[dict], max_chars: int = MAX_PROMPT_CHARS, wide: bool = False) -> str:
    """Final image prompt: style + the FIXED character descriptions for this panel + shot + scene + composition/no-text suffix.
    Kept under max_chars (NIM limit): character descriptions and the scene are shortened first; the suffix and shot never are."""
    names = {n.lower() for n in panel.get("characters", [])}
    chars = [] if panel.get("scenery") else ([c for c in sheet if not names or c["name"].lower() in names] or sheet[:1])
    head = panel.get("style_head") or "cel-shaded anime illustration, single panel, "
    shot = f", {panel.get('shot', 'medium')} shot, "
    for desc_n, scene_n in ((10_000, 10_000), (170, 260), (120, 200), (80, 150), (50, 100)):
        who = ", ".join(f"{c['name']}: {_cut(c['description'], desc_n)}" for c in chars)
        text = head + who + shot + _cut(panel["image_prompt"], scene_n) + (PROMPT_SUFFIX_WIDE if wide else PROMPT_SUFFIX)
        if len(text) <= max_chars:
            return nim_safe(text)
    return nim_safe(text[:max_chars])


COMIC_PROMPT = (
    "You write ORIGINAL anime-style motion-comic scripts for vertical short videos (never use existing franchises or "
    "their characters). Language of narration, speech bubbles and CTA: {language}. Series premise: {premise}. "
    "{cast}Continuity state (JSON, may be empty; keep these characters' looks EXACTLY and continue the story): {state}. "
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
                   client=None, series_dir: Path = SERIES_DIR, max_parts: int | None = None, cast_root: Path | None = None) -> dict:
    """Part N+1 of a series: {"comic": {...validated...}, "source": "gemini|topic_fallback", "note": str}."""
    cfg = cfg if cfg is not None else _settings()
    rcfg, state = cfg.get("research", {}), load_series(channel, series_dir)
    from aurora import cast as cast_mod

    cast = cast_mod.load_cast(channel, cast_root or cast_mod.CAST_ROOT) if cfg.get("comic", {}).get("cast_lock", False) else []
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
                    cast=cast_mod.prompt_text(cast),
                    style=COMIC_STYLE,
                    total_rule=(f"exactly {total} parts" if state else f"1 or 2 parts (set total_parts; at most {max_parts})"),
                    ending=("this is the FINAL part: resolve the story, no cliffhanger, cta = 'The End'" if part >= total else
                            "end on a cliffhanger and cta = 'Part " + str(part + 1) + " coming soon'")),
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Comic))
            comic = Comic.model_validate_json(resp.text)
            if cast:  # the model may not redesign a locked character: force name + description verbatim
                comic.character_sheet = [Character(**c) for c in cast_mod.merge_cast([c.model_dump() for c in comic.character_sheet], cast)]
            comic.series.part = part
            comic.series.total_parts = max(part, min(comic.series.total_parts, max_parts, total if state else max_parts))
            if part >= comic.series.total_parts:
                comic.cliffhanger, comic.cta = "", "The End"
            return {"comic": comic.model_dump(), "source": "gemini", "note": ""}
        except Exception as exc:  # noqa: BLE001
            reason = f"Gemini failed/invalid: {str(exc)[:100]}"
    fb = _comic_fallback(premise, n, state, reason)
    if cast:
        fb["character_sheet"] = cast_mod.merge_cast(fb["character_sheet"], cast)
        hero = fb["character_sheet"][0]["name"]
        for p in fb["panels"]:  # the template's beats follow the (locked) protagonist
            p["characters"] = [hero]
            p["image_prompt"] = p["image_prompt"].replace(state.get("characters", [{"name": "Kaito"}])[0]["name"], hero)
            p["narration"] = p["narration"].replace(state.get("characters", [{"name": "Kaito"}])[0]["name"], hero)
    fb["series"]["total_parts"] = total if state else min(2, max_parts)
    if part >= fb["series"]["total_parts"]:
        fb["cliffhanger"], fb["cta"] = "", "The End"
    else:
        fb["cta"] = f"Part {part + 1} coming soon"
    return {"comic": Comic.model_validate(fb).model_dump(),
            "source": "topic_fallback", "note": reason}


# ----------------------------------------------------------------------------- long episodes (16:9, ~4-5 min, ~45 panels, 3 acts)
class ActOutline(BaseModel):
    name: str
    summary: str


class EpisodeOutline(BaseModel):
    title: str = Field(min_length=3)
    hook: str = Field(min_length=3)
    description: str
    keywords: list[str]
    hashtags: list[str]
    series: SeriesInfo
    character_sheet: list[Character] = Field(min_length=1)
    acts: list[ActOutline] = Field(min_length=3, max_length=3)
    cta: str = ""


class ActPanels(BaseModel):
    panels: list[Panel] = Field(min_length=6, max_length=24)


class Act(BaseModel):
    name: str
    summary: str
    panels: list[Panel]


class Episode(BaseModel):
    title: str
    hook: str
    description: str
    keywords: list[str]
    hashtags: list[str]
    series: SeriesInfo
    character_sheet: list[Character]
    acts: list[Act] = Field(min_length=3, max_length=3)
    cta: str = ""


ACT_SPLIT = (0.31, 0.38, 0.31)  # act 1 setup / act 2 conflict / act 3 climax + resolution
EP_OUTLINE_PROMPT = (
    "You write ORIGINAL anime-style YouTube story episodes (16:9, about 4-5 minutes, never existing franchises). Language of titles, "
    "narration and bubbles: {language}. Premise: {premise}. {cast}{learn}Plan Episode {ep} as a complete story in exactly 3 acts "
    "(setup, conflict, climax and resolution). Return: title (curiosity-driven, <= 70 chars, no clickbait lies), hook (first line "
    "that grabs in 3 seconds), description (3 short paragraphs: hook, recap, what to expect), keywords (8-12 search phrases people type), "
    "hashtags (5-8), series {{title, part (= episode number), total_parts, recap}}, character_sheet (name + fixed visual description of hair, "
    "eyes, outfit, colours; style '{style}'), acts (3 x {{name, summary of what happens}}), cta. Safe for all audiences."
)
EP_ACT_PROMPT = (
    "Continue the ORIGINAL anime episode '{title}' (language {language}). Story outline: {outline}. {cast}Write ACT {k} ('{act_name}': {act_summary}) as "
    "exactly {n} panels. Previous act ended with: {previous}. Each panel: shot close-up|wide|medium|action, characters present (names exactly "
    "as in the character sheet: {names}), image_prompt = the scene only in English (no text, no lettering, single scene), narration of 12-16 words "
    "in {language}, optional short bubble line, sfx/mood tag, action true for fights/chases. Keep pacing varied and end the act on a beat that pulls "
    "into the next. Safe for all audiences."
)


def _episode_fallback(channel: str, premise: str, n_panels: int, cast: list[dict], ep_no: int) -> dict:
    """Deterministic 3-act template (no API): a valid Episode built from the locked cast; flagged topic_fallback by the caller."""
    sheet = [{"name": m["name"], "description": m["description"], "style": COMIC_STYLE} for m in cast] or [
        {"name": "Kaito", "style": COMIC_STYLE, "description": "young man, spiky silver hair, bright blue eyes, long black coat with red scarf"}]
    hero = sheet[0]["name"]
    beats = [
        ("wide", "{h} walking alone through ruins under a huge pale moon", "{h} had been walking for days, and the silence was getting heavier.", False),
        ("close-up", "close-up of {h}'s determined face lit by faint blue light", "Something deep inside him refused to turn back.", False),
        ("medium", "{h} crossing a broken stone bridge over a glowing abyss", "Every step on that old bridge sounded like a warning.", False),
        ("action", "{h} dodging a shockwave, debris flying, dynamic angle", "Then the ground shook and the ancient defenses woke up.", True),
        ("close-up", "{h} breathing hard, sweat on his brow, eyes sharp", "He was tired, but fear was not going to win today.", False),
        ("wide", "an enormous shadowy guardian rising from the ruins", "Something huge was rising out of the dust ahead of him.", False),
    ]
    sizes = [round(n_panels * f) for f in ACT_SPLIT]
    sizes[2] = n_panels - sizes[0] - sizes[1]
    names = ["Act 1: The Silence", "Act 2: The Awakening", "Act 3: The Light"]
    acts, k = [], 0
    for ai, size in enumerate(sizes):
        panels = []
        for j in range(size):
            shot, img, nar, act = beats[(k + j) % len(beats)]
            panels.append(Panel(shot=shot, characters=[hero], image_prompt=img.format(h=hero), narration=nar.format(h=hero), action=act).model_dump())
        acts.append({"name": names[ai], "summary": f"{hero} " + ["sets out", "faces the guardian", "finds the light"][ai] + ".", "panels": panels})
        k += size
    title = premise.split(".")[0][:60] or "The Last Light"
    return {"title": title, "hook": f"{hero} had one chance left.", "description": f"{premise}\n\nAn original anime story episode.",
            "keywords": ["anime story", "original anime", "motion comic", "fantasy adventure", hero.lower()],
            "hashtags": ["#anime", "#animestory", "#originalstory"],
            "series": {"title": title, "part": ep_no, "total_parts": ep_no, "recap": ""}, "character_sheet": sheet, "acts": acts,
            "cta": "Subscribe for the next episode."}


def _call_json(client, model: str, contents: str, schema, retries: int = 1):
    """generate_content -> validated pydantic object; one retry on invalid JSON."""
    from google.genai import types

    last = None
    for _ in range(retries + 1):
        try:
            resp = client.models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=schema))
            return schema.model_validate_json(resp.text)
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise last


def research_episode(channel: str, premise: str, language: str = "English", n_panels: int = 45, cfg: dict | None = None, client=None,
                     series_dir: Path = SERIES_DIR, cast_root: Path | None = None, analytics: dict | None = None) -> dict:
    """A 16:9 long episode: {"episode": {...validated Episode...}, "source": "gemini|topic_fallback", "note": str}.
    analytics (optional, from the Publisher's weekly report): {"top_titles": [...], "top_keywords": [...]} -> "what worked" in the prompt."""
    cfg = cfg if cfg is not None else _settings()
    rcfg = cfg.get("research", {})
    from aurora import cast as cast_mod

    cast = cast_mod.load_cast(channel, cast_root or cast_mod.CAST_ROOT) if cfg.get("comic", {}).get("cast_lock", False) else []
    state = load_series(f"{channel}-episodes", series_dir)
    ep_no = int(state.get("episode", 0)) + 1
    n_panels = max(24, min(int(n_panels), 54))
    key = os.getenv(rcfg.get("api_key_env", "GEMINI_API_KEY"), "").strip()
    reason = "no GEMINI_API_KEY"
    if client is not None or key:
        try:
            if client is None:
                from google import genai

                client = genai.Client(api_key=key)
            model = rcfg.get("model", "gemini-3.1-flash-lite")
            learn = ""
            if analytics:
                learn = ("What worked best on this channel recently (learn from the patterns, never copy): titles "
                         f"{analytics.get('top_titles', [])[:5]}; keywords {analytics.get('top_keywords', [])[:8]}. ")
            outline = _call_json(client, model, EP_OUTLINE_PROMPT.format(language=language, premise=premise, cast=cast_mod.prompt_text(cast),
                                                                         learn=learn, ep=ep_no, style=COMIC_STYLE), EpisodeOutline)
            if cast:
                outline.character_sheet = [Character(**c) for c in cast_mod.merge_cast([c.model_dump() for c in outline.character_sheet], cast)]
            outline.series.part = ep_no
            sizes = [round(n_panels * f) for f in ACT_SPLIT]
            sizes[2] = n_panels - sizes[0] - sizes[1]
            names = ", ".join(c.name for c in outline.character_sheet)
            acts, previous = [], "the story begins"
            for k, size in enumerate(sizes, 1):
                got = _call_json(client, model, EP_ACT_PROMPT.format(
                    title=outline.title, language=language, outline=json.dumps([a.model_dump() for a in outline.acts], ensure_ascii=False)[:1500],
                    cast=cast_mod.prompt_text(cast), k=k, act_name=outline.acts[k - 1].name, act_summary=outline.acts[k - 1].summary, n=size,
                    previous=previous, names=names), ActPanels)
                acts.append(Act(name=outline.acts[k - 1].name, summary=outline.acts[k - 1].summary, panels=got.panels))
                previous = got.panels[-1].narration
            ep = Episode(title=outline.title, hook=outline.hook, description=outline.description, keywords=outline.keywords,
                         hashtags=outline.hashtags, series=outline.series, character_sheet=outline.character_sheet, acts=acts, cta=outline.cta)
            total = sum(len(a.panels) for a in ep.acts)
            if not 36 <= total <= 56:
                raise ValueError(f"episode has {total} panels (want 36-56)")
            return {"episode": ep.model_dump(), "source": "gemini", "note": ""}
        except Exception as exc:  # noqa: BLE001
            reason = f"Gemini failed/invalid: {str(exc)[:100]}"
    fb = _episode_fallback(channel, premise, n_panels, cast, ep_no)
    return {"episode": Episode.model_validate(fb).model_dump(), "source": "topic_fallback", "note": reason}


def save_episode_state(channel: str, episode: dict, series_dir: Path = SERIES_DIR) -> None:
    last = episode["acts"][-1]["panels"][-1]["narration"] if episode.get("acts") else ""
    Path(series_dir).mkdir(parents=True, exist_ok=True)
    (Path(series_dir) / f"{channel}-episodes.json").write_text(json.dumps(
        {"channel": channel, "episode": episode["series"]["part"], "title": episode["title"], "last_summary": last,
         "characters": episode["character_sheet"]}, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------- chapters: Series -> Chapter N -> Part M (>= 10 parts each)
class Beat(BaseModel):
    role: str  # setup | rising stakes | twist | climax | cliffhanger
    summary: str = Field(min_length=3)


class ChapterOutline(BaseModel):
    series_title: str = Field(min_length=2)
    chapter_title: str = Field(min_length=2)
    summary: str = ""
    beats: list[Beat] = Field(min_length=10, max_length=12)
    character_sheet: list[Character] = []


def chapter_parts(cfg: dict | None) -> int:
    """Parts per chapter: config comic.chapter_parts, never fewer than 10."""
    return max(10, int(((cfg or {}).get("comic") or {}).get("chapter_parts", 10) or 10))


def guard_text(comic: dict) -> str:
    """All story text of a generated part (for hard-rule guards)."""
    bits = [comic.get("hook", ""), comic.get("cliffhanger", ""), (comic.get("series") or {}).get("recap", "")]
    for p in comic.get("panels", []):
        bits += [p.get("image_prompt", ""), p.get("narration", ""), p.get("bubble", "")] + [ln.get("text", "") for ln in p.get("lines", [])]
    return "\n".join(b for b in bits if b)


HOOK_TITLE_MAX = 38  # <= 45 by spec; 38 leaves room for " | <Series> Pt N" inside the 70-char YouTube title


def finish_retention(comic: dict, recaps: list[str], part: int) -> dict:
    """Normalise the retention fields: valid hook_panel, hook_title <= 45 chars, a question, and (Part 2+) a one-line `previously` from the series state."""
    n = len(comic.get("panels", []))
    hp = int(comic.get("hook_panel") or 0)
    comic["hook_panel"] = hp if 1 <= hp <= n else 0
    ht = " ".join((comic.get("hook_title") or "").replace(":", " ").split()).strip(" .")
    if not ht or len(ht) > HOOK_TITLE_MAX:
        ht = _short_hook(ht or comic.get("hook", ""), HOOK_TITLE_MAX)
    comic["hook_title"] = ht
    comic["engagement_question"] = " ".join((comic.get("engagement_question") or "").split())[:120] or "What do you think happens next?"
    prev = " ".join((comic.get("previously") or "").split()) if part >= 2 else ""
    if part >= 2 and not prev and recaps:
        prev = " ".join(str(recaps[-1]).split())
    if len(prev.split()) > 14:  # <= 14 words, ends on a clean sentence stop
        prev = " ".join(prev.split()[:14]).rstrip(" ,;:-") + "."
    comic["previously"] = prev if part >= 2 else ""
    return comic


HOOK_MAX = 40


def _short_hook(hook: str, room: int = HOOK_MAX) -> str:
    """Hook <= 40 characters (and whatever still fits in the 100-character title), cut at a word boundary."""
    hook = " ".join((hook or "").replace(":", " ").split()).rstrip(".")
    if len(hook) <= room:
        return hook
    words = (hook[:room].rsplit(" ", 1)[0] or hook[:room]).split()
    while len(words) > 2 and words[-1].lower().strip(",;-") in {"a", "an", "the", "in", "of", "and", "to", "with", "at", "on", "for", "by", "from", "into", "its", "his", "her"}:
        words.pop()
    return " ".join(words).rstrip(" ,;-")


def part_title(series: str, chapter: int, part: int, hook: str = "") -> str:
    """"<Series> – Ch.<N> Part <M>: <hook>" (the en dash is intentional, it is the format the user approved). One Part marker,
    hook <= 40 characters, whole title <= 100."""
    base = f"{series} – Ch.{chapter} Part {part}"
    h = _short_hook(hook, min(HOOK_MAX, 100 - len(base) - 2))
    return base + (f": {h}" if h else "")


def fixed_series_title(channel: str) -> str | None:
    """The series title is CONTENT (story pack series.yaml): research may never rename it."""
    try:
        from aurora import story

        pdir = story.find_pack(channel)
        return str(story.load_pack(pdir.name)["series"]["title"]) if pdir else None
    except Exception:  # noqa: BLE001 - a broken pack is reported by `story lint`, never here
        return None


def end_card_for(chapter: int, part: int, parts: int) -> tuple[str, str]:
    """End card text: "Part <M+1> tomorrow" or "Chapter <N> finale"."""
    return (f"Chapter {chapter} finale", "The next chapter is coming") if part >= parts else ("To be continued...", f"Part {part + 1} tomorrow")


_BEAT_ROLES = ["setup", "setup", "rising stakes", "rising stakes", "rising stakes", "twist", "twist", "climax", "climax", "cliffhanger"]


def _roles(n: int) -> list[str]:
    roles = _BEAT_ROLES[:]
    while len(roles) < n:  # 11-12 beats: extra rising stakes before the twist
        roles.insert(4, "rising stakes")
    return roles[:n]


def _write_state(channel: str, state: dict, series_dir: Path) -> None:
    Path(series_dir).mkdir(parents=True, exist_ok=True)
    (Path(series_dir) / f"{channel}.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def reset_for_chapters(channel: str, series_title: str = "Echoes of the Aether", series_dir: Path = SERIES_DIR) -> dict:
    """Start Chapter 1 FRESH: back up the old (pilot) state, keep the cast, the seed and the old story as `background` lore only."""
    old = load_series(channel, series_dir)
    backup = Path(series_dir) / f"{channel}.pilot-backup.json"
    if old and not backup.exists():
        backup.write_text(json.dumps(old, indent=2, ensure_ascii=False), encoding="utf-8")
    state = {"channel": channel, "series_title": series_title, "title": series_title, "background": old.get("last_summary", ""),
             "characters": old.get("characters", []), "seed": old.get("seed"), "chapter": None, "chapters_done": 0, "chapters": {},
             "part": 0, "total_parts": chapter_parts(None)}
    _write_state(channel, state, series_dir)
    return state


def _outline_fallback(premise: str, parts: int, cast: list[dict], state: dict, number: int) -> dict:
    hero = (cast or state.get("characters") or [{"name": "Kaito"}])[0]["name"]
    summaries = [
        f"{hero} finds the first sign that the old world is not dead.", f"{hero} meets someone who knows more than they say.",
        f"A threat shows itself and {hero} has to choose a side.", "An ally is lost and the cost of the fight becomes real.",
        f"{hero} follows a trail into forbidden ground.", "A truth about the old power changes what everyone believed.",
        "A betrayal turns allies into enemies.", f"{hero} faces the guardian of the old power.", f"{hero} pays a price to win.",
        "The victory opens a door that should have stayed closed.", "A new enemy watches from the dark.", "The chapter ends on a question."]
    roles = _roles(parts)
    return {"series_title": state.get("series_title") or (premise.split(".")[0][:50] or "The Last Light"),
            "chapter_title": f"Chapter {number}: The Road Begins", "summary": f"{hero}'s first chapter.",
            "beats": [{"role": roles[i], "summary": summaries[i % len(summaries)]} for i in range(parts)], "character_sheet": []}


def research_part(channel: str, premise: str, language: str = "English", panels: int = 6, cfg: dict | None = None, client=None,
                  series_dir: Path = SERIES_DIR, cast_root: Path | None = None, world: str = "", series_title: str | None = None,
                  rules: list[str] | None = None, guard=None) -> dict:
    """Part M of Chapter N, written from beat M of the chapter outline + the recaps of parts 1..M-1 (continuity).
    Returns {"comic": {...Comic...}, "chapter": {number, title, part, parts, beat, series_title, final}, "source", "note"}."""
    cfg = cfg if cfg is not None else _settings()
    rcfg = cfg.get("research", {})
    from aurora import cast as cast_mod

    parts = chapter_parts(cfg)
    cast = cast_mod.load_cast(channel, cast_root or cast_mod.CAST_ROOT) if cfg.get("comic", {}).get("cast_lock", False) else []
    state = load_series(channel, series_dir)
    fixed = series_title or fixed_series_title(channel)
    if fixed and state.get("series_title") != fixed:  # the fixed title always wins over whatever an earlier run stored
        state.update(series_title=fixed, title=fixed)
        _write_state(channel, state, series_dir)
    ch = state.get("chapter")
    key = os.getenv(rcfg.get("api_key_env", "GEMINI_API_KEY"), "").strip()
    live = client is not None or bool(key)
    model = rcfg.get("model", "gemini-3.1-flash-lite")
    if live and client is None:
        from google import genai

        client = genai.Client(api_key=key)
    note = "" if live else "no GEMINI_API_KEY"
    source = "gemini" if live else "topic_fallback"
    cast_text = cast_mod.prompt_text(cast)
    rules_text = ("HARD RULES of this series (verbatim, never violate them, never even hint at them): " + " | ".join(rules) + " ") if rules else ""

    def violation(text: str) -> str | None:  # `guard` (story packs: forbidden relationships etc.) judges every generated text
        return guard(text, client if live else None, model) if guard else None

    if not ch or ch.get("complete"):  # a chapter starts: plan its whole arc first
        number = int(state.get("chapters_done", 0)) + 1
        outline = None
        if live:
            try:
                prompt = (f"You plan ORIGINAL anime story chapters (never existing franchises). Series: {state.get('series_title') or premise}. Language: {language}. "
                          f"Premise: {premise}. Background lore of the series so far: {state.get('background', '')[:600]}. World canon (obey): {world[:1500]} "
                          f"Earlier chapters: {json.dumps(state.get('chapter_recaps', []), ensure_ascii=False)[:1200]}. {cast_text}{rules_text}"
                          f"Plan Chapter {number} as exactly {parts} beats, one per daily 30-second part: setup, rising stakes, twist, climax and a final "
                          "cliffhanger beat, in that order. Return series_title (keep the existing one if given), chapter_title, summary, beats "
                          "(role + one-sentence summary each) and character_sheet (name + fixed look) for any NEW characters. Safe for all audiences.")
                got = _call_json(client, model, prompt, ChapterOutline)
                for _ in range(2):  # a hard-rule violation in the outline -> regenerate (max 2 more tries)
                    bad = violation(" ".join([got.chapter_title, got.summary] + [b.summary for b in got.beats]))
                    if not bad:
                        break
                    got = _call_json(client, model, prompt + f" Your previous outline broke a hard rule ({bad}); rewrite it without that.", ChapterOutline)
                bad = violation(" ".join([got.chapter_title, got.summary] + [b.summary for b in got.beats]))
                if bad:
                    raise ValueError(f"outline violates a hard rule: {bad}")
                beats = [b.model_dump() for b in got.beats]
                if len(beats) > parts:
                    beats = beats[: parts - 1] + beats[-1:]
                if len(beats) != parts:
                    raise ValueError(f"outline has {len(beats)} beats, want {parts}")
                outline = {**got.model_dump(), "beats": beats}
            except Exception as exc:  # noqa: BLE001
                note, source = f"outline fallback: {str(exc)[:90]}", "topic_fallback"
        if outline is None:
            outline = _outline_fallback(premise, parts, cast, state, number)
            bad = violation(" ".join(b["summary"] for b in outline["beats"]))
            if bad:
                raise ValueError(f"hard rule violated by the offline outline: {bad}")
        series_title = fixed or state.get("series_title") or outline["series_title"]  # the outline's own series_title is ignored when fixed
        ch = {"number": number, "title": outline["chapter_title"], "summary": outline.get("summary", ""), "outline": outline["beats"], "parts": parts,
              "parts_done": 0, "recaps": [], "rendered": [], "complete": False}
        state.update(series_title=series_title, title=series_title, chapter=ch)
        _write_state(channel, state, series_dir)  # the outline persists even if this part's render fails
    m = int(ch["parts_done"]) + 1
    beat = ch["outline"][m - 1]
    series_title = fixed or state.get("series_title") or premise[:40]
    n = max(5, min(int(panels), 10))
    final = m >= int(ch["parts"])
    comic = None
    if live:
        try:
            prompt = (f"{COMIC_PROMPT_HEAD.format(language=language)}{cast_text}Series: {series_title}. Chapter {ch['number']}: {ch['title']}. "
                      f"This is Part {m} of {ch['parts']} (one ~30 s episode). Beat for this part ({beat['role']}): {beat['summary']} "
                      f"Previous parts so far: {json.dumps(ch['recaps'], ensure_ascii=False)[:1800]}. Series background: {state.get('background', '')[:400]}. World canon (obey): {world[:1500]} {rules_text}"
                      f"Write exactly {n} panels. Return: series {{title='{series_title}', part={m}, total_parts={ch['parts']}, recap = ONE line of what happens in THIS part}}, "
                      f"hook (3-6 words that tease this part, for the video title), hook_panel (1-based number of the single most dramatic panel), hook_line (its strongest line or sfx, <= 8 words), hook_title (<= 45 chars, curiosity-driven, must be true to what this part shows), engagement_question (short, specific to THIS part, e.g. 'Would you touch the relic?'), previously (ONE line, at most 14 words, of the story so far; empty for Part 1), character_sheet (locked cast verbatim + any new characters with a fixed look; style '{COMIC_STYLE}'), "
                      "panels (each: shot close-up|wide|medium|action, characters present, image_prompt = the scene only in English, narration of 8-12 words, optional short bubble, "
                      "sfx/mood tag, action true for fights/chases), "
                      + ("this is the CHAPTER FINALE: resolve this chapter's main conflict but open a hook for the next chapter; cta = 'Chapter " + str(ch['number']) + " finale'. "
                         if final else f"end on a cliffhanger that pulls into Part {m + 1}; cta = 'Part {m + 1} tomorrow'. ") + "Safe for all audiences.")
            got = _call_json(client, model, prompt, Comic)
            for _ in range(2):  # a hard-rule violation in the generated part -> regenerate (max 2 more tries)
                bad = violation(guard_text(got.model_dump()))
                if not bad:
                    break
                got = _call_json(client, model, prompt + f" Your previous draft broke a hard rule ({bad}); rewrite the part without it.", Comic)
            bad = violation(guard_text(got.model_dump()))
            if bad:
                raise ValueError(f"part violates a hard rule: {bad}")
            if cast:
                got.character_sheet = [Character(**c) for c in cast_mod.merge_cast([c.model_dump() for c in got.character_sheet], cast)]
            got.series.title, got.series.part, got.series.total_parts = series_title, m, int(ch["parts"])
            got.cta = f"Chapter {ch['number']} finale" if final else f"Part {m + 1} tomorrow"
            if final:
                got.cliffhanger = got.cliffhanger or "A new threat rises."
            comic = got.model_dump()
        except Exception as exc:  # noqa: BLE001
            note, source = f"part fallback: {str(exc)[:90]}", "topic_fallback"
    if comic is None:
        fb = _comic_fallback(beat["summary"], n, {"characters": state.get("characters"), "title": series_title}, note)
        fb["series"] = {"title": series_title, "part": m, "total_parts": int(ch["parts"]), "recap": beat["summary"]}
        fb["cta"] = f"Chapter {ch['number']} finale" if final else f"Part {m + 1} tomorrow"
        if cast:
            fb["character_sheet"] = cast_mod.merge_cast(fb["character_sheet"], cast)
            hero = fb["character_sheet"][0]["name"]
            old = (state.get("characters") or [{"name": "Kaito"}])[0]["name"]
            for p in fb["panels"]:
                p["characters"] = [hero]
                p["image_prompt"] = p["image_prompt"].replace(old, hero)
                p["narration"] = p["narration"].replace(old, hero)
        fb["hook"] = beat["role"].title()
        comic = Comic.model_validate(fb).model_dump()
        bad = violation(guard_text(comic))
        if bad:
            raise ValueError(f"hard rule violated by the offline part: {bad}")
    finish_retention(comic, ch["recaps"], m)
    info = {"number": int(ch["number"]), "title": ch["title"], "part": m, "parts": int(ch["parts"]), "beat": beat, "series_title": series_title, "final": final}
    return {"comic": comic, "chapter": info, "source": source, "note": note}


COMIC_PROMPT_HEAD = ("You write ORIGINAL anime-style motion-comic scripts for vertical short videos (never use existing franchises or their characters). "
                     "Language of narration, speech bubbles and CTA: {language}. ")


def record_part(channel: str, comic: dict, info: dict, rendered: dict, series_dir: Path = SERIES_DIR) -> dict:
    """After a part rendered OK: advance the chapter (recap for continuity, rendered files, completion). A failed part never calls this."""
    state = load_series(channel, series_dir)
    ch = state["chapter"]
    ch["parts_done"] = int(info["part"])
    ch["recaps"].append((comic["series"].get("recap") or " ".join(p["narration"] for p in comic["panels"]))[:220])
    ch["rendered"].append(rendered)
    state.update(part=int(info["part"]), total_parts=int(info["parts"]), characters=comic["character_sheet"],
                 last_summary=" ".join(p["narration"] for p in comic["panels"])[:600], cliffhanger=comic.get("cliffhanger", ""))
    if info["part"] >= info["parts"]:
        ch["complete"] = True
        state["chapters_done"] = int(ch["number"])
        state.setdefault("chapters", {})[str(ch["number"])] = ch
        state.setdefault("chapter_recaps", []).append(f"Ch.{ch['number']} {ch['title']}: " + " ".join(ch["recaps"])[:500])
    _write_state(channel, state, series_dir)
    return state
