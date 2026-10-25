# VEER: manga-dialogue format (second series on channel shinobi)
Status: approved by the user via the coordinator (priority below Echoes of the Aether Ch.1)        Date: 2026-10-25

## Goal
A script-driven "manga" renderer: characters speak their OWN lines in their own voices; narration only for verses and caption/time cards.
Series "VEER – Wrath of the Forgotten God" (docs/stories/veer/: STORY_BIBLE.md respect charter §2 and style guide §9 are HARD rules).
Nothing is queued or published for Veer. Only Ch.1 Part 1 may be rendered as a test, after Echoes' daily NIM parts (cap 25/day shared).

## Decisions
- Reuse, don't fork: `aurora/manga.py` parses `CHAPTER_NN.md` into the existing comic dict (panels + new `lines`), then calls `comic.make_comic_reel`
  (images via `_panel_images`, cap/retries, sidecar, chapter state, 16:9 compilation all unchanged). State/cast key = series key `veer`
  (`outputs/series/veer.json`, `assets/characters/veer/*.json`); the sidecar names channel `shinobi` and playlist "VEER – Wrath of the Forgotten God".
- Voices: per character JSON `voice {voice, rate, pitch, fx}` (Edge-TTS; fx = ffmpeg echo/reverb for Jalaghna and Smaran); emotion shifts rate/pitch
  (whisper slower+quiet, shout faster+higher). Narrator calm. Each line is its own audio file placed on the timeline.
- Timing: panel time = sum of its lines + 0.4 s (silent establishing panels 2.5 s). A bubble shows while its line is spoken (ffmpeg overlay `enable=between`).
- Bubbles by type: speech (existing user-approved segmentation-based placement, tail to the speaker's face), shout (spiky), whisper (dashed),
  verse (parchment box, no tail), caption card (existing narration box). Full-bleed art unchanged.
- Look: manga prompt head (cel-shade, ink outlines, screentone, palette saffron/river-teal/night-indigo/ember-red), `speed lines` in the prompt and a
  procedural speed-line overlay on action panels, light halftone screentone overlay.
- Respect charter in code: opening card on every part "A work of fiction inspired by the world's mythologies, told with respect." (cover); a guard
  refuses panels whose text/prompt shows a deity fighting/humiliated (deities = silhouettes/reliefs), and no prophet depictions.
- Visual lock: locked descriptions + seeds in `assets/characters/veer/`; reference sheets generated later within the NIM cap.

## Files
`aurora/manga.py` (new), `aurora/comic.py` (render_comic `lines` branch, timed overlays, bubble styles, end_card/cover_note from the script),
`aurora/research.py` (`panel_prompt` style head override), `assets/characters/veer/*.json`, `config.yaml` (`series.veer`), tests, solutions note.

## Risks
- Speaker->face mapping with several faces is heuristic (left-to-right order of appearance); off-screen speakers get no tail.
- Edge-TTS voices are limited; echo/reverb is post-FX. Lines are English with Indian names.
- NIM: ~4-6 credits/part; Veer waits for credits left after Echoes.

## Test plan
Parser on the real CHAPTER_01.md (10 parts, speakers, kinds, cards); voice map + prosody; bubble styles render; render with fake TTS/board: per-line audio,
timing = lines + 0.4, timed overlays, cover note, end card; charter guard; state/numbering; full suite.

## Done when
Tests green, commit (no push), solutions note, reported. No uploads, nothing queued.
