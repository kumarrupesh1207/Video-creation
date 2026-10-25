# Chapters: Series -> Chapter N -> Part M (+ one 16:9 compilation per chapter)

**Problem:** 2-part stories were too short. **Fix:** a chapter has >= 10 parts (`comic.chapter_parts`, min 10, enforced by `research.chapter_parts`),
each one ~30 s comic Short (unchanged format, locked cast).

- Outline first: `research.research_part` asks Gemini for 10-12 beats (setup -> rising stakes -> twist -> climax -> cliffhanger) when a chapter
  starts and saves it in `outputs/series/<channel>.json` (`chapter.outline`) before any image is drawn. Part M = beat M + one-line recaps of parts 1..M-1.
- State advances only in `research.record_part`, after a part rendered and validated. A failed render returns the same part next time.
- Titles `"<Series> – Ch.N Part M: <hook>"`; end cards "Part M+1 tomorrow" / "Chapter N finale"; cover "CH.N PART M".
- `aurora/chapter.py`: after the last part, `compile_chapter` cuts each Short's story window (panel_times: first panel start .. last panel end; the cut
  starts at the panel start because its voice begins 0.05 s later - starting at +XF clipped the first word), reframes 9:16 -> 1920x1080 over a blurred copy,
  adds open/finale cards, joins with stream copy. SRT from narration timings, one YouTube chapter marker per part, thumbnail (face-aware crop),
  sidecar `outputs/final/<channel>/chapters/chN/chapter.json` (`kind: chapter`, `publish_at_hint` = next Saturday 18:00 IST, >= 6 h after completion).
  0 NIM credits. A failure never fails the part: `python -m aurora.chapter compile --channel <name>` (`pending` lists chapters to build).
- Replaces the weekly `aurora.episode` long video (`long_episode` commented out in config.yaml; the module stays for manual use).
- Pilots: `research.reset_for_chapters` backs up the old state to `<channel>.pilot-backup.json`, keeps cast/seed and the old story as `background` only.
- Gotcha: the sidecar's `character_sheet` entries need `description` (not `look`) for `Comic` validation.

## Publisher side (autopilot)
One playlist per series, ordered by `(chapter, part)`; the compilation sorts after its parts (`autopilot.order_key`). The insert position comes from the live playlist (`youtube.compute_position`): videos Aurora does not know (pilots) count as earlier, so they never get mixed into the parts. `render_chapters` queues each `chapter.json` once at `publish_at_hint` (queue honours it but never earlier than the veto window).

## Series title is fixed content (2026-10-25 incident)
Research renamed the series ("Chronicles of the Aether Blade") because shinobi's state had no title and the Gemini outline supplied one; SEO then appended a second
marker ("Pt. 1 – Ch.1 Part 1"). Fixes: `research.fixed_series_title(channel)` (story pack series.yaml) always overrides the state title and the outline/part titles in
`research_part`; `part_title` = one marker, hook <= 40 chars, title <= 100; `apply_seo` never rewrites a chapter part title (description/tags only).
Repair without credits: `make_comic_reel(..., images=<cached panels>, record=False)` re-renders a part from its stored panels (`panel_images` in the sidecar).
Gotcha: `PublishQueue()` without `cfg=` uses default slots (19:00); always `PublishQueue(cfg=cfg)` when enqueuing by hand.
