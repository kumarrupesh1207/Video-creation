"""SEO metadata for YouTube uploads: competitor research (search.list) + Gemini title options + best pick + description.

    meta = build_metadata(topic, series, part, recap, service=svc, budget=QuotaBudget(), duration_secs=270, chapters=[...])

Never invents off-niche content: with no Gemini key / on any API failure the caller's own title is kept and only the
deterministic parts (keywords, hashtags, AI note, chapters) are added. search.list costs 100 quota units (budgeted).
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOP = set("the a an and or of to in on for with is are was be this that it its at by from how why what you your i my we our".split())
AI_NOTE = "This story and its visuals are AI-generated (synthetic media)."
LONG_SECS = 180  # videos at least this long get chapters


def keywords_from(texts: list[str], extra: list[str] | None = None, n: int = 12) -> list[str]:
    """Most frequent meaningful words across competitor titles/tags, `extra` (e.g. series words) first."""
    words = Counter(w for t in texts for w in re.findall(r"[a-z0-9']{3,}", t.lower()) if w not in STOP)
    out = [e.lower() for e in (extra or [])]
    out += [w for w, _ in words.most_common(n * 2) if w not in out]
    return out[:n]


def competitor_research(service, query: str, budget=None, max_results: int = 10) -> dict:
    """Top videos for `query`: {"titles": [...], "tags": [...]}. Costs search.list (100) + videos.list (1)."""
    if budget:
        budget.spend("search.list")
    found = service.search().list(part="snippet", q=query, type="video", order="viewCount", maxResults=max_results,
                                  relevanceLanguage="en").execute().get("items", [])
    ids = [i["id"]["videoId"] for i in found if i.get("id", {}).get("videoId")]
    titles = [i["snippet"]["title"] for i in found]
    tags: list[str] = []
    if ids:
        if budget:
            budget.spend("videos.list")
        for v in service.videos().list(part="snippet", id=",".join(ids)).execute().get("items", []):
            tags += [t.lower() for t in v["snippet"].get("tags", [])]
    return {"titles": titles, "tags": [t for t, _ in Counter(tags).most_common(15)]}


OPTIONS_PROMPT = (
    "You write YouTube titles for an anime story channel. Series: {series}, part {part}. Premise/recap: {recap}. "
    "Competitor titles (for keyword ideas only, do not copy): {titles}. Frequent keywords: {keywords}. "
    "Return JSON {{\"titles\": [3 distinct titles, each <= 70 characters, curiosity-driven, honest, no clickbait lies, "
    "no emoji spam], \"hook\": \"one-sentence hook for the first line of the description\"}}. Language: English."
)


def generate_options(series: str, part: int, recap: str, research: dict, keywords: list[str], client=None, cfg: dict | None = None) -> dict:
    """Gemini proposes 3 titles + hook. Returns {"titles": [...], "hook": str, "source": "gemini"|"none"}."""
    cfg = cfg or {}
    key = os.getenv((cfg.get("research", {}) or {}).get("api_key_env", "GEMINI_API_KEY"), "").strip()
    if client is None and not key:
        return {"titles": [], "hook": "", "source": "none"}
    try:
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        from google.genai import types

        resp = client.models.generate_content(
            model=(cfg.get("research", {}) or {}).get("model", "gemini-3.1-flash-lite"),
            contents=OPTIONS_PROMPT.format(series=series, part=part, recap=recap[:600], titles=research.get("titles", [])[:8],
                                           keywords=keywords[:10]),
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        data = json.loads(resp.text)
        titles = [str(t).strip() for t in data.get("titles", []) if str(t).strip()][:3]
        return {"titles": titles, "hook": str(data.get("hook", "")).strip(), "source": "gemini"}
    except Exception:  # noqa: BLE001 - never block publishing on SEO
        return {"titles": [], "hook": "", "source": "none"}


def score_title(title: str, keywords: list[str]) -> float:
    """Keyword coverage (0-1) plus a length bonus (45-70 chars is the sweet spot; hard cap 100)."""
    t = title.lower()
    cover = sum(1 for k in keywords[:8] if k in t) / max(1, min(8, len(keywords)))
    n = len(title)
    length = 1.0 if 45 <= n <= 70 else 0.6 if 30 <= n < 45 else 0.3 if n < 30 else 0.4 if n <= 100 else -1.0
    return cover + length


def pick_best(titles: list[str], keywords: list[str]) -> str | None:
    ok = [t for t in titles if 0 < len(t) <= 100]
    return max(ok, key=lambda t: score_title(t, keywords)) if ok else None


def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def format_chapters(chapters: list[dict] | None, duration_secs: float | None) -> str:
    """YouTube chapters need >=3 entries, first at 0:00, each >= 10 s; otherwise return '' (no half-valid chapters)."""
    if not chapters or len(chapters) < 3 or (duration_secs or 0) < LONG_SECS:
        return ""
    ch = sorted(chapters, key=lambda c: c["start"])
    ch[0] = {**ch[0], "start": 0}
    if any(b["start"] - a["start"] < 10 for a, b in zip(ch, ch[1:])):
        return ""
    return "\n".join(f"{_ts(c['start'])} {c['title']}" for c in ch)


def build_description(hook: str, recap: str, keywords: list[str], hashtags: list[str], chapters_text: str = "",
                      ai_note: str = AI_NOTE) -> str:
    tags = " ".join(dict.fromkeys(h if h.startswith("#") else f"#{h}" for h in hashtags))[:200]
    kw = ", ".join(keywords[:8])
    parts = [hook, recap, chapters_text and f"Chapters:\n{chapters_text}", kw and f"Keywords: {kw}", ai_note, tags]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())[:4900]


def build_metadata(topic: str, series: str | None, part: int | None, recap: str, hashtags: list[str] | None = None,
                   fallback_title: str | None = None, service=None, budget=None, client=None, cfg: dict | None = None,
                   duration_secs: float | None = None, chapters: list[dict] | None = None, state_dir: Path | None = None,
                   chapter: int | None = None) -> dict:
    """Title (best of 3 by keyword score, else the caller's own), SEO description and tags. Never raises."""
    research = {"titles": [], "tags": []}
    if service is not None:
        try:
            research = competitor_research(service, f"{series or topic} anime story", budget)
        except Exception:  # noqa: BLE001 - quota/auth: continue without competitor data
            pass
    kws = keywords_from(research["titles"] + research["tags"], extra=re.findall(r"[A-Za-z0-9']{3,}", series or topic)[:3])
    opts = generate_options(series or topic, part or 1, recap, research, kws, client, cfg)
    best = pick_best(opts["titles"], kws)
    if best and series and chapter and part:  # "... – Ch.N Part M" must survive the title rewrite
        need = f"Ch.{chapter} Part {part}"
        if need.lower() not in best.lower():
            best = f"{best[:100 - len(f' – {need}')]} – {need}"
    elif best and series and chapter and f"chapter {chapter}" not in best.lower() and f"ch.{chapter}" not in best.lower():
        best = f"{best[:100 - len(f' – Chapter {chapter}')]} – Chapter {chapter}"  # the full-chapter compilation
    elif best and series and part and f"part {part}" not in best.lower():
        best = f"{best[:100 - len(f' – Part {part}')]} – Part {part}"
    title = best or fallback_title or topic
    tags = list(dict.fromkeys([k for k in kws if len(k) > 2] + [t.lstrip('#') for t in (hashtags or [])]))[:25]
    desc = build_description(opts["hook"], recap, kws, (hashtags or []) + ["#Shorts" if (duration_secs or 0) < LONG_SECS else "#AnimeStory"],
                             format_chapters(chapters, duration_secs))
    return {"title": title[:100], "description": desc, "tags": tags, "source": opts["source"], "keywords": kws}
