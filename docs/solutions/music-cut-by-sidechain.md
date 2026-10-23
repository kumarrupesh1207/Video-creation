# Background music stops halfway when a voiceover is added

Problem: reels with voiceover had music only for the first ~7 s of 15 s; the rest was silent.
Root cause: ffmpeg `sidechaincompress` ends when its shorter input (the voice) ends, which cut the ducked music.
Fix: pad the voice with `apad` before `asplit`/`sidechaincompress` in `aurora/audio.py` `mix_audio` (2a57670).
How to recognise: per-second RMS (`astats`) shows `-inf` after the voice finishes.
