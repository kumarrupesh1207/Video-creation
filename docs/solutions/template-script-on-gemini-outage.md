# Template script reached YouTube during a Gemini outage (2026-10-31)

**Symptom:** Ch.2 Part 2 was titled "Setup" and showed Kaelen on a neon street, which has nothing to do with the falling Sky City.

**Root cause:** Gemini returned 503, so `research_part` used `_comic_fallback`. The offline template passed render, went into the queue, and was saved into `CH_02.md`. Its hook was the beat role, "Setup".

**Fix:** `story.render_for_channel` passes `offline_ok=False`. A `topic_fallback` script now raises before any NIM credit is spent, restores the series state, and shows as `render_failed` in the daily report. The next run tries again. Manual `render_next` keeps the offline option for tests and experiments.

**Cost of the fix:** if Gemini stays down, that day has no new part. An empty slot is better than an off-story video, and a template video adds to the inauthentic-content risk.
