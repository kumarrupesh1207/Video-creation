"""SEO metadata for YouTube uploads: competitor research (search.list) + Gemini wording + a solid template fallback.

    meta = build_metadata(topic, series, part, recap, service=svc, budget=QuotaBudget(), chapter=1, beat=..., parts=10, ...)

Keywords are PHRASES ("dark fantasy anime"), never stop-words or the series title split into words. Competitor research and
Gemini failures are logged loudly and replaced by the template (hook, recap, next-part line, playlist mention, follow CTA,
keywords used naturally, hashtags once, ONE AI-disclosure line); nothing off-niche is ever invented. search.list costs 100
quota units, so its result is cached for a day per query.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOP = set("""the a an and or of to in on for with is are was were be been this that these those it its at by from as how why what who when
where you your i my me we our us they them their he she his her him not no yes but if then than so too very just can will would could
should do does did done have has had get got new old one two all any more most some such into out up down over under again about
video videos part parts episode full official watch shorts short youtube anime story stories""".split()) - {"anime", "story", "stories"}
GENERIC = {"the", "of", "and", "a", "an", "to", "in", "on", "for", "with", "ch", "part"}
GENRE = set("""anime fantasy dark fight fighting battle action epic story original motion comic manga sword magic hero power powers
supernatural adventure mystery ruins sky city legend warrior demon spirit ancient shounen seinen cinematic animated""".split())
DEFAULT_NICHE = ["anime story", "original anime", "dark fantasy anime", "anime fight", "anime shorts", "motion comic", "anime action"]
AI_NOTE = "This story and its visuals are AI-generated (synthetic media)."
LONG_SECS = 180  # videos at least this long get chapters
CACHE_DIR = ROOT / "outputs" / "publish_state" / "seo_cache"


def _log(msg: str) -> None:
    print(f"[seo] !!! {msg}", file=sys.stderr)


def title_tokens(*titles: str | None) -> set[str]:
    return {w for t in titles if t for w in re.findall(r"[a-z0-9']+", t.lower())}


def keywords_from(texts: list[str], exclude: set[str] | None = None, niche: list[str] | None = None, n: int = 12) -> list[str]:
    """Phrases, best first: curated niche phrases, then 2-word phrases frequent in competitor titles/tags. Single stop-words and
    words of the series title (`exclude`) never become keywords."""
    exclude = {e.lower() for e in (exclude or set())}
    out: list[str] = []

    def ok_word(w: str) -> bool:
        return len(w) >= 3 and w not in STOP and w not in GENERIC and w not in exclude and not w.isdigit()

    for p in niche or []:
        p = p.strip().lower()
        if p and p not in out:
            out.append(p)
    relevant = ({w for p in (niche or []) for w in re.findall(r"[a-z0-9']+", p.lower())} | GENRE) - STOP - GENERIC
    # competitor phrases only when EVERY word is genre vocabulary: never other shows' names or off-niche words
    grams: Counter = Counter()
    for t in texts:
        words = re.findall(r"[a-z0-9']+", html.unescape(t).lower())
        for a, b in zip(words, words[1:]):
            if ok_word(a) and ok_word(b) and {a, b} <= relevant:
                grams[f"{a} {b}"] += 1
        if 2 <= len(words) <= 3 and all(ok_word(w) for w in words) and set(words) <= relevant:  # a TAG that is already a phrase
            grams[" ".join(words)] += 2
    for g, _ in grams.most_common(n * 3):
        if g not in out and not any(g in o or o in g for o in out):
            out.append(g)
    return out[:n]


def competitor_research(service, query: str, budget=None, max_results: int = 10, cache_dir: Path | None = None) -> dict:
    """Top videos for `query`: {"titles": [...], "tags": [...]}. search.list 100 + videos.list 1 units; cached for a day."""
    cdir = Path(cache_dir or CACHE_DIR)
    cf = cdir / (hashlib.sha1(query.encode()).hexdigest()[:12] + ".json")
    try:
        c = json.loads(cf.read_text(encoding="utf-8"))
        if time.time() - c["at"] < 86400:
            return c["data"]
    except (OSError, ValueError, KeyError):
        pass
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
    data = {"titles": titles, "tags": [t for t, _ in Counter(tags).most_common(20)]}
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        cf.write_text(json.dumps({"at": time.time(), "data": data}), encoding="utf-8")
    except OSError:
        pass
    return data


OPTIONS_PROMPT = (
    "You write YouTube metadata for an original anime story channel (Shorts), following YouTube's guidelines. Series: {series}, chapter {chapter}, "
    "part {part} of {parts}. What happens in THIS part: {events} Beat: {beat} Recap so far: {recap}. Competitor titles (ideas only, never copy): {titles}. "
    "Main keyword: {main}. Return JSON {{\"titles\": [3 distinct titles, each <= 70 characters, honest, include the main keyword only if natural], "
    "\"hook\": \"one punchy first sentence that contains the main keyword once\", \"summary\": \"2-3 sentences unique to THIS part, "
    "built only from the events above, no spoilers beyond this part, natural wording: no keyword lists, no hashtags, no links, no 'Part N tomorrow'\"}}. "
    "Language: English."
)


def generate_options(series: str, part: int, recap: str, research: dict, keywords: list[str], client=None, cfg: dict | None = None,
                     chapter: int | None = None, parts: int | None = None, beat: str = "", caption: str = "",
                     events: list[str] | None = None, main_keyword: str = "") -> dict:
    """Gemini proposes titles + hook + summary. Returns {"titles", "hook", "summary", "source": "gemini"|"none"}."""
    cfg = cfg or {}
    rc = cfg.get("research", {}) or {}
    none = {"titles": [], "hook": "", "summary": "", "source": "none"}
    key = os.getenv(rc.get("api_key_env", "GEMINI_API_KEY"), "").strip()
    if client is None and not key:
        return none
    try:
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        from google.genai import types

        resp = client.models.generate_content(
            model=rc.get("model", "gemini-3.1-flash-lite"),
            contents=OPTIONS_PROMPT.format(series=series, chapter=chapter or 1, part=part, parts=parts or "?",
                                           events=" ".join(events or [caption])[:700] or "-", beat=beat[:300] or "-",
                                           recap=recap[:400] or "-", titles=research.get("titles", [])[:6], main=main_keyword or "anime story"),
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        data = json.loads(resp.text)
        titles = [str(t).strip() for t in data.get("titles", []) if str(t).strip()][:3]
        return {"titles": titles, "hook": str(data.get("hook", "")).strip(), "summary": str(data.get("summary", "")).strip(), "source": "gemini"}
    except Exception as e:  # noqa: BLE001 - never block publishing on SEO
        _log(f"Gemini failed ({str(e)[:100]}); using the template")
        return none


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


def series_hashtag(series: str | None) -> str:
    """'Echoes of the Aether' -> '#EchoesOfTheAether' (used when the series pack does not set `hashtag`)."""
    words = re.findall(r"[A-Za-z0-9]+", series or "")
    return "#" + "".join(w if w.isupper() else w.capitalize() for w in words) if words else ""


def clean_hashtags(hashtags: list[str] | None, is_short: bool = True, limit: int = 3, shorts_tag: bool = False) -> list[str]:
    """Exactly the first `limit` (default 3) unique hashtags, most relevant first (the first three show above the title).
    #Shorts is optional (vertical <= 3 min is a Short anyway) and at most once."""
    out: list[str] = []
    seen: set[str] = set()
    for h in list(hashtags or []) + (["#Shorts"] if shorts_tag and is_short else []):
        h = "#" + re.sub(r"[^A-Za-z0-9_]", "", str(h).lstrip("#"))
        key = h.lower()
        if len(h) > 1 and key not in seen and (key != "#shorts" or (shorts_tag and is_short)):
            seen.add(key)
            out.append(h)
    return out[:limit]


def misspellings(series: str | None, given: list[str] | None = None, n: int = 3) -> list[str]:
    """Common misspellings people type for the series name (pack `seo_misspellings` first, else simple automatic variants)."""
    if given:
        return [g.lower() for g in given][:n]
    t = (series or "").lower().strip()
    if not t:
        return []
    cands = [re.sub(r"\bthe\b ?", "", t).strip(), re.sub(r"oes\b", "os", t), re.sub(r"ae", "e", t), re.sub(r"(.)\1", r"\1", t, count=1)]
    out: list[str] = []
    for c in cands:
        if c and c != t and c not in out:
            out.append(c)
    return out[:n]


def clean_tags(candidates: list[str], exclude: set[str], lo: int = 5, hi: int = 10) -> list[str]:
    """5-10 clean lowercase tags: phrases, no lone stop-words / series-title tokens, no hashtag marks, <= 450 chars total."""
    out: list[str] = []
    total = 0
    for c in candidates:
        t = re.sub(r"\s+", " ", re.sub(r"[#\"]", "", c)).strip().lower()
        if not t or len(t) > 30 or t in out:
            continue
        if " " not in t and (t in STOP or t in GENERIC or t in exclude or len(t) < 3):
            continue
        if total + len(t) + 1 > 450:
            break
        out.append(t)
        total += len(t) + 1
        if len(out) >= hi:
            break
    return out


def _sentences(text: str) -> str:
    """Caption text without the 'Part N tomorrow' teaser that videos carry on their end card."""
    return re.sub(r"\s*(Part \d+ tomorrow|Chapter \d+ finale)\.?\s*$", "", text.strip(), flags=re.I).strip()


def build_description(hook: str, summary: str, hashtags: list[str], chapters_text: str = "", ai_note: str = AI_NOTE,
                      next_line: str = "", playlist_line: str = "") -> str:
    """Hook (+ main keyword), a unique 2-3 sentence body, at most 2 short boilerplate lines (next part, playlist), ONE disclosure
    line, then the hashtags last. No keyword lists."""
    parts = [hook, summary, chapters_text and f"Chapters:\n{chapters_text}", "\n".join(x for x in (next_line, playlist_line) if x), ai_note,
             " ".join(hashtags)]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())[:4900]


def _body_from_events(beat: str, events: list[str], caption: str, recap: str, n: int = 3) -> str:
    """Template body for when Gemini is down: this part's own events (beat + narration lines), 2-3 distinct sentences."""
    out: list[str] = []
    for t in [beat] + list(events or []) + [caption]:
        t = _sentences(t or "")
        if t and t.lower() not in {o.lower() for o in out} and not any(t.lower() in o.lower() or o.lower() in t.lower() for o in out):
            out.append(t if t[-1] in ".!?\u2026" else t + ".")
        if len(out) >= n:
            break
    return " ".join(out) or _sentences(recap)


def build_metadata(topic: str, series: str | None, part: int | None, recap: str, hashtags: list[str] | None = None,
                   fallback_title: str | None = None, service=None, budget=None, client=None, cfg: dict | None = None,
                   duration_secs: float | None = None, chapters: list[dict] | None = None, state_dir: Path | None = None,
                   chapter: int | None = None, *, beat: str = "", caption: str = "", parts: int | None = None,
                   playlist: str | None = None, niche_keywords: list[str] | None = None, hook: str = "",
                   cache_dir: Path | None = None, events: list[str] | None = None, main_keyword: str | None = None,
                   series_hashtag_: str | None = None, misspell: list[str] | None = None) -> dict:
    """Title, SEO description, 5-10 tags and 3 hashtags. Never raises; failures are logged loudly and the template is used."""
    is_short = (duration_secs or 0) < LONG_SECS
    niche = list(niche_keywords or DEFAULT_NICHE)
    main = (main_keyword or niche[0]).lower()
    research = {"titles": [], "tags": []}
    warnings: list[str] = []
    if service is not None:
        try:
            q = f"{niche[0]} {'shorts' if is_short else 'full episode'}"
            research = competitor_research(service, q, budget, cache_dir=cache_dir)
        except Exception as e:  # noqa: BLE001 - quota/auth: continue with the curated niche list
            warnings.append(f"competitor research failed ({str(e)[:100]})")
            _log(warnings[-1] + "; curated niche keywords only")
    else:
        warnings.append("no YouTube service: competitor research skipped")
    exclude = title_tokens(series, topic, playlist)
    kws = keywords_from(research["titles"] + research["tags"], exclude, niche)
    opts = generate_options(series or topic, part or 1, recap, research, kws, client, cfg, chapter, parts, beat, caption, events, main)
    if opts["source"] == "none":
        warnings.append("Gemini options unavailable: template used")
    best = pick_best(opts["titles"], kws)
    if best and series and chapter and part:  # "... – Ch.N Part M" must survive the title rewrite
        need = f"Ch.{chapter} Part {part}"
        if need.lower() not in best.lower():
            best = f"{best[:100 - len(f' – {need}')]} – {need}"
    elif best and series and chapter and not part and f"chapter {chapter}" not in best.lower() and f"ch.{chapter}" not in best.lower():
        best = f"{best[:100 - len(f' – Chapter {chapter}')]} – Chapter {chapter}"
    elif best and series and part and f"part {part}" not in best.lower():
        best = f"{best[:100 - len(f' – Part {part}')]} – Part {part}"
    title = best or fallback_title or topic
    # line 1-2: hook + ONE main keyword; body: unique to this part
    body = opts["summary"] or _body_from_events(beat, events or [], caption, recap)
    first = opts["hook"] or hook or _sentences(caption) or title
    if main not in (first + " " + body).lower():
        first = f"{first.rstrip('. ')}. Original {main}."
    nxt = ""
    if part and parts and part < parts:
        nxt = f"Watch Part {part + 1} next → a new part every day."
    elif part and parts and part >= parts:
        nxt = f"That was the final part of Chapter {chapter or 1}."
    pl = f"All parts in order: the \"{playlist or series}\" playlist." if (playlist or series) else ""
    hts = clean_hashtags([series_hashtag_ or series_hashtag(series)] + [h for h in (hashtags or [])], is_short)
    name = (series or "").lower()
    tag_pool = ([name] if name else []) + misspellings(series, misspell) + list(niche) + list(kws) + [t.lstrip("#") for t in (hashtags or [])]
    tags = clean_tags(tag_pool, exclude)
    desc = build_description(first, body, hts, format_chapters(chapters, duration_secs), next_line=nxt, playlist_line=pl)
    return {"title": title[:100], "description": desc, "tags": tags, "source": opts["source"], "keywords": kws, "warnings": warnings,
            "hashtags": hts}
