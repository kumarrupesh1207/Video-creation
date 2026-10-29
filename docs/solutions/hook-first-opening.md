# Hook-first opening + "Previously…" card (retention, 2026-10-29)

**Why:** Shinobi analytics: ~85% of views come from the Shorts feed. Ch.1 Part 3 opened on a power moment (108% retention); the slow
cover-card opens of Parts 1-2 got 44% / 31%, so later parts lost reach.

**What (future parts only, never re-render published ones):** per series pack, `series.yaml`:
```yaml
retention: {hook_first: true, hook_s: 1.5, previously: true, previously_s: 1.2, punch_zoom: true}
```
- `hook_first`: no cover card. The first <=1.5 s is the part's most dramatic panel (research `hook_panel`; else action+sfx panel, any action, any sfx,
  else the build-up panel) with its strongest line (`hook_line`, else bubble/sfx/narration) in big text and a punch-zoom; the series / "CH.N PART M" title
  is a small top tag. Then the story runs in order (the hook panel may repeat).
- `previously` (Part 2+): <=1.2 s card, top-band "Previously…" + one line (`previously`, from research/series recaps).
- Research (`research.Comic`) also returns `hook_title` (<=45 chars), `engagement_question`; stored in the part sidecar for the publisher
  (`hook_title`, `engagement_question`, `intro`). `research.finish_retention` normalises them (also for authored parts).
- Lint validates the block (`aurora.story lint`); packs without `retention` keep the classic cover card. Code: `comic.render_comic(retention=...)`.
- `panel_times` are real video seconds and start AFTER the intro, so chapter compilation (cuts by panel_times) is unchanged.

**Gotchas:** the hook segment is silent (music only) so it never delays the first voice; no new images, so no extra NIM credits.
Tests: `tests/test_retention.py`.
