# Solutions index
One line per solved problem. Update an existing note instead of adding a duplicate.

- [kaggle-chained-segments-oom.md](kaggle-chained-segments-oom.md) — chained Kaggle clips OOM/killed → one child process per segment (ae7cf81).
- [supervisor-dies-with-parent.md](supervisor-dies-with-parent.md) — servers silently down → detached WMI supervisor (98c7c37).
- [music-cut-by-sidechain.md](music-cut-by-sidechain.md) — music stops when voice ends → `apad` before sidechaincompress (2a57670).
- [tests-live-gemini.md](tests-live-gemini.md) — tests broke after real key in .env → conftest removes GEMINI_API_KEY (7adc20c).
- [comfy-timeout-silent-nim-fallback.md](comfy-timeout-silent-nim-fallback.md) — CPU timeout caused silent NIM spend → long timeout, no comfy→NIM fallback (65e825f).
- [nim-credits-and-comic-images.md](nim-credits-and-comic-images.md) — NIM balance unreadable; comic images default to NIM with counted, capped credits and a logged ComfyUI fallback.
- [comic-text-on-faces.md](comic-text-on-faces.md) — text on characters: full-bleed art + rembg isnet-anime segmentation placement (bands layout kept behind a flag).
- [nim-violet-content-filter.md](nim-violet-content-filter.md) — harmless prompts blocked as CONTENT_FILTERED: NIM word filter rejects "violet" -> rewritten to purple.
- [comic-locked-cast.md](comic-locked-cast.md) — new stories redesigned the hero -> permanent cast files per channel, verbatim in every prompt, new side characters saved after first appearance.
- [youtube-scopes-quota-windows.md](youtube-scopes-quota-windows.md) — optional analytics scope keeps uploads working, quota budget defers instead of 403, SRT uploaded from memory (Windows file lock).
- [long-episode-resumable.md](long-episode-resumable.md) — 45-panel 16:9 episodes + Shorts across days under the 25/day NIM cap: persisted state, counted credits, act-by-act cached render.
- [nim-mode-never-touches-comfyui.md](nim-mode-never-touches-comfyui.md) — scheduled run crashed on dead ComfyUI in nim mode: fallback off by default, optional ComfyUI is preflighted.
- [chapters-series-parts.md](chapters-series-parts.md) — Series/Chapter/Part structure, >=10 parts per chapter, 16:9 chapter compilation
- [hook-first-opening.md](hook-first-opening.md) — retention opening: hook panel + punch-zoom, small title tag, Part 2+ "Previously…" card, series.yaml `retention:`
- [partial-research-chapter.md](partial-research-chapter.md) — research write-back writes the whole chapter atomically; lint repairs gaps from series state; lint failures raise events
- [veer-manga-dialogue.md](veer-manga-dialogue.md) — scripted manga series: per-character voices, timed bubbles, respect-charter guard
- [STORY_PACKS.md](../STORY_PACKS.md) — content-driven series: packs, lint, cadence, zero-code new series
- [youtube-seo-guidelines.md](youtube-seo-guidelines.md) — SEO text follows YouTube's guidelines (hook+keyword, unique body, 3 hashtags, 5-10 tags + misspellings, one AI line); data lives in series.yaml.
- [template-script-on-gemini-outage.md](template-script-on-gemini-outage.md) — Gemini 503 sent the offline template to YouTube; the scheduler now refuses it and retries next run.
- [render-stall-after-wake.md](render-stall-after-wake.md) — 0 videos: first render right after PC wake stalled ffprobe past 30 s -> one longer retry; retries now reuse the script and paid panels (image cache had never hit).
