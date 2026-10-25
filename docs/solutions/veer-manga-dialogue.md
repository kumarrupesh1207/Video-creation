# VEER: manga-dialogue format (aurora/manga.py)

**What:** a scripted series where characters speak their OWN lines (distinct Edge-TTS voices); narration only for verses/caption cards.
Script `docs/stories/veer/CHAPTER_NN.md` -> `manga.parse_chapter` -> the normal comic dict (panels + `lines`) -> `comic.make_comic_reel` (images, caps,
sidecar, chapter state, 16:9 compilation all reused). Series key `veer` = state (`outputs/series/veer.json`) + cast (`assets/characters/veer/`); config `manga_series.veer`
(channel shinobi, playlist "VEER – Wrath of the Forgotten God", `enabled: false`: nothing is queued or published).

- Voices: `voice {voice, rate, pitch, fx}` per character JSON; `_voices.json` has NARRATOR/ANCHOR. Emotion shifts rate/pitch (`manga.prosody`); fx `echo`/`reverb` (Jalaghna, Smaran) via ffmpeg `aecho` + `apad` so the tail is not cut.
- Timing: a panel lasts 0.3 s lead + lines + 0.2 s gaps + 0.4 s (>= 2.5 s). Each line is its own audio file on the timeline; its bubble is a timed ffmpeg overlay (`render_clip(extra=...)`, `enable=between`).
- Bubbles: speech (existing segmentation placement + tail to the speaker's face), shout (spiky burst), whisper (dashed), verse (parchment, no tail), caption (yellow box). Speaker -> face = left-to-right order of the panel's characters; off-screen speakers get no tail.
- Look: `style_head` (cel-shade, ink outlines, screentone, saffron/teal/indigo/ember palette; speed lines on action) + procedural halftone and radial speed-line overlay (`manga.tone_png`). Establishing panels (`scenery`) carry no cast in the prompt.
- Respect charter in code: `manga.check_charter` (no deity fighting/defeated/mocked, no prophets, never "Kali Avatar" = Veer); every cover shows the "work of fiction ... told with respect" card.
- Credits: `make_manga_reel` refuses unless `used + panels <= cap - reserve_credits` (14 kept for Echoes' 2 parts/day); `--force` overrides.
- Gotchas: voice files from edge are mp3 even when named .wav (ffmpeg does not care); a `voices` list name clashed inside `render_comic` (renamed `voices_t`).
