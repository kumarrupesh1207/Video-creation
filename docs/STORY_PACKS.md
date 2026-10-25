# Story packs: add a series with ZERO code

Stories are content. The engine (`aurora/story.py`, `comic.py`, `manga.py`, `research.py`) only reads packs.

## Layout
```
stories/<slug>/
  series.yaml            title, channel, playlist, language, format (comic|manga), parts_per_chapter (>= 10), style_head, opening_card, narrator, state_key ...
  characters/<name>.yaml locked look (description), role, seed, voice {voice, rate, pitch, fx}, optional reference images
  chapters/CH_01.md ...  authored script (below). Missing chapters are written by research IN THE SAME FORMAT and saved back here: edit them any time.
  world.md               optional canon rules fed into research prompts
```

## Add a new series (copy-paste)
1. `cp -r stories/_template stories/<your-slug>` (Windows: copy the folder). Edit `series.yaml` (title, channel, format) and the character files.
2. Write `chapters/CH_01.md` (or leave `chapters/` empty and let research write it).
3. Check it: `python -m aurora.story lint <your-slug>` (friendly errors with file/part/panel; fix until 0 errors).
4. Schedule it: in `config.yaml` under the channel, add `{slug: <your-slug>, per_day: 1}` to `series:`.
   `per_day: 0` or `enabled: false` keeps it off. The scheduler lints before rendering; a bad pack is skipped with a report line
   and the channel falls back to its legacy flow. Nothing publishes unless the global/per-channel publish switches are on.
5. Test render (costs NIM credits): `python -m aurora.story render <your-slug>`.

## Script format (`chapters/CH_NN.md`)
```
# Series — Chapter 1: "Chapter Title"
## Part 1 — "Part Title"            (parts numbered 1..N in order, N <= parts_per_chapter; 3-10 panels each)
1. Wide: what the picture shows     (Wide:/Close-up:/Insert: set the shot; a panel with no character names is scenery)
   **NARRATOR** (calm): "Caption or verse read by the narrator."      (NARRATOR + (verse...) = parchment box)
   **HERO** (whisper): "A spoken line."   (emotion in brackets: whisper -> dashed bubble, shout/roar/yell -> spiky bubble, others -> speech)
End card: *Part 2 tomorrow*         (optional; also "Cliffhanger card: *Text*")
```
- comic format: one narrator reads the narration lines; the first spoken line becomes the speech bubble.
- manga format: every character speaks their own lines in their own voice; panel time = lines + 0.4 s.
- Speakers must exist in `characters/` (or `extra_voices` / NARRATOR). Typos get a "did you mean" hint.

## What lint checks (`python -m aurora.story lint <slug>|--all`)
series.yaml fields and format; parts_per_chapter >= 10; narrator and character voices (Edge-TTS names, rate `+10%`, pitch `+0Hz`, fx `echo|reverb`);
reference images exist; script structure (part numbering, panel counts, empty panels); speakers exist and have a voice (manga);
line length; the respect charter (no deity fighting/mocked, no prophets, no claim that Veer is the prophesied saviour) and all-ages banned words
(plus `rules.banned_words`). Warnings never block; errors do.

## Notes
- `state_key` is where progress lives (`outputs/series/<state_key>.json`). Echoes of the Aether uses `shinobi` (its existing state); new series: leave it as the slug.
- Compilations/publishing for a series with its own state key need the Publisher to list that key (Veer is not scheduled yet).
- Legacy folders `assets/characters/<channel>/` still work for channels without a pack.

## Hard rules (permanent, owner-mandated content rules)
In `series.yaml`:
```
rules:
  hard_rules: ["Any sentence research must always obey."]
  forbidden_relationships:
    - {pair: [NameA, NameB], type: romantic, note: "The sentence shown to research verbatim."}
```
and/or `hard_rules: [...]` inside a character file. The engine (no story-specific code):
1. **Lint** checks the schema and that both pair names exist in `characters/` (did-you-mean), and rejects authored chapters where a segment names BOTH members of a pair with a romance cue.
2. **Research prompts** (chapter outline and every part) always carry every hard rule verbatim (`story.pack_rules`).
3. **Post-generation guard** (`story.make_guard`): keyword scan + a Gemini yes/no check per pair on every generated outline and part; a violation is regenerated (max 2 more tries), and if it still fails the offline fallback is used (also guarded); a violating part is never rendered.
