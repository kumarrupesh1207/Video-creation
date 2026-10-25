# Chapters: longer stories = Series -> Chapter N -> Part M (+ one 16:9 chapter compilation)
Status: approved (user approved the direction; Echoes of the Aether Ch.1 starts FRESH)        Date: 2026-10-25

## Goal
A story is a series split into chapters; every chapter has at least 10 parts (`comic.chapter_parts`, minimum 10). Each part is one
~30 s comic Short (same final format, locked cast). When all parts of a chapter exist, ONE 16:9 1920x1080 video (~5 min) is built from
the cached parts (no new NIM credits). This replaces the separately generated weekly long episode (aurora/episode.py stays, switched off).

## Decisions
- Chapter outline first: when a chapter starts, Gemini writes 10-12 beats (setup, rising stakes, twist, climax, cliffhanger) with the locked
  cast; stored in `outputs/series/<channel>.json` (`chapter.outline`). Part M is written from beat M + one-line recaps of parts 1..M-1.
- Numbering/titles: "<Series> – Ch.<N> Part <M>: <hook>". End cards: "Part <M+1> tomorrow" (M < last) / "Chapter <N> finale". Cover: "CH.N PART M".
- State: `outputs/series/<channel>.json` keeps the legacy keys and adds `series_title`, `background` (old lore), `chapter {number, title, outline,
  parts_done, parts, recaps, rendered[], complete}`. The two private pilot uploads (BvQaD-dpGvI, UWqPrgLQWtM) are NOT chapter parts: old state is
  backed up (`*.pilot-backup.json`) and Ch.1 starts at Part 1; Kaelen and the old lore stay as background.
- Compilation (`aurora/chapter.py`): per part, cut the story panels out of the rendered Short (drop its cover/end card using stored `panel_times`),
  reframe 9:16 -> 16:9 with a blurred-background fill, add a chapter title card + chapter finale card, join; chapters list = one per part;
  SRT from narration timings; thumbnail (best panel + title); sidecar for the publisher; `publish_at_hint` = next Saturday 18:00 IST after completion.
- NIM: unchanged cap (25/day): 2 parts/day ~ 14 credits. Compilation costs 0 credits.
- Publisher (other session): queue parts in order (2/day) into a per-series playlist ordered by chapter, then part; queue the compilation at
  `publish_at_hint` (Saturday 18:00); SEO title needs `chapter` in `seo.build_metadata`.

## Files to change
- `aurora/research.py` — ChapterOutline/Beat, `research_part`, `record_part`, `part_title`, `chapter_parts()`; legacy `save_series` keeps extra keys.
- `aurora/comic.py` — chapter mode in `make_comic_reel` (end cards, cover label, sidecar: chapter/part/title/panel_times/panel_images, auto-compile on the last part).
- `aurora/chapter.py` (new) — compile_chapter, next_saturday_1800, pending compilations, CLI.
- `aurora/autopilot.py` — comic branch passes chapter/series/playlist keys; compose_meta chapter title; `long_episode` off for shinobi (config).
- `config.yaml` — `comic.chapter_parts: 10`, `long_episode` disabled; `research.max_parts` marked legacy.
- tests + `docs/solutions/chapters-series-parts.md`, CLAUDE.md rule.

## Risks
- Credits: 6-7 per part; 2 parts/day fits the 25 cap. Never regenerate panels. A failed part does not advance the chapter.
- Continuity drift over 10 parts: beat plan + recaps + locked cast + fixed series seed.
- Compilation needs ffmpeg time (~10 clips re-encoded): runs after the last part, failure never fails the part (retry via CLI).
- Old parts without `panel_times` cannot be compiled (only new Ch.1 parts are used).

## Test plan
- Mocked Gemini: outline then part prompts (beat + recaps + cast), numbering across chapters, min 10 parts, title/end-card text, state persistence.
- Fake rendered parts (tiny ffmpeg clips + panel_times): compile -> 1920x1080 with audio, chapters/SRT/thumbnail, no NIM calls; Saturday slot math.

## Done when
- Tests green; one dry run of research for Ch.1 (no credits); config switched; Publisher told what it needs; user can start Ch.1 Part 1.
