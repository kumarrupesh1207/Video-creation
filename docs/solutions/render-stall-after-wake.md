# 0 videos on 2026-10-31: ffprobe stalled right after the PC woke

**Symptom:** daily report shows two `render_failed` for `shinobi`: NIM `CONTENT_FILTERED` after 1/6 panels, then an ffprobe
command that "timed out after 30 seconds" on a 20 KB `voice_0.mp3`. Rendered today: 0.

**Root cause:** the PC slept from 2026-10-30 16:08 to 2026-10-31 11:43 IST, so the first render of the day began 18 s after wake,
alongside the Windows and antivirus catch-up work. Attempt 1 lost to a filter false positive. Attempt 2 got all 6 panels, then
the machine stalled: `clip_hook` took 14 s and `clip_recap` 34 s (normally 1-2 s), Avira's updater service timed out at 11:49:02,
and the first ffprobe hit its 30 s limit. The same probe takes 0.09 s now, so the file was fine.

**Fix:** `_ffprobe` (`aurora/monitoring/media_validator.py`) retries once with a 180 s limit. A second stall still raises.

**Not a scheduler bug:** the niche text in `render_failed` is only `idea["topic"]`, the placeholder from `get_ideas` for comic
channels. `render_idea` still goes through `story.render_for_channel`; the work dir was `shinobi-ch2-part2-echoes-of-the-aether`.

**Known cost:** a part with no authored script (`CH_02.md` has only Part 1) gets a new Gemini script on every attempt, so panels
paid for by a failed attempt do not hit the image cache on the next one. Attempt 2 cost 6 credits and a retry pays again.
