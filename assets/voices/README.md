# Voices for reel voiceovers

Engine order (`audio.voice_engine: auto` in config.yaml, or `--voice-engine edge|xtts|piper`):

1. **edge** (default, natural): Microsoft Edge neural voices via the `edge-tts` package. Free, needs internet.
   Hindi/Hinglish -> `hi-IN-SwaraNeural` (female) / `hi-IN-MadhurNeural` (male); English -> `en-IN-NeerjaNeural` / `en-IN-PrabhatNeural`.
   NOTE: this uses Microsoft's online service *unofficially* (no API key, no SLA). It is free but can break or change at any time, which is why the fallbacks exist.
2. **xtts** (offline, voice cloning): Coqui XTTS-v2 in its own Python 3.11 venv. Run `scripts/setup_tts.ps1` once. Drop a clean 10-30 s mono WAV of the voice you want
   (no music, no noise) into this folder and set `audio.speaker_wav: assets/voices/me.wav` (or `--speaker-wav`). Only clone voices you have the right to use.
3. **piper** (basic, offline): small English/Hindi voices in `models/piper/`.

If an engine fails (no internet, venv missing...) the next one is used and the sidecar notes which one ran. `.wav` files here are not committed to git.
