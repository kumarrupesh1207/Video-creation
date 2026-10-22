# Perf baseline — before/after Phase-0 fixes

Measured with `scripts/perf_bench.py` (reuse it to re-measure). Machine: Ryzen 5 5600G, CPU only. Prompts are neutral (man in a suit, woman in an ethnic dress).

| Path | Before (HEAD 2b6d6c6) | After | Verdict |
|---|---|---|---|
| `pytest -q` | 6 passed | 21 passed | OK (+15 new tests) |
| ImageSwitchboard → nvidia_nim, 512×512 | 4.9 s, returned 1344×768 (wrong aspect) | 5.7 s, returned **1024×1024** (honours request) | OK; latency within network noise (1 sample each; the ~2.7 s seen earlier varies) |
| LocalComfyUIProvider 512×512, 15 steps (CPU) | ~247 s (earlier clean run). My own run took 478 s because it queued behind the user's job — not comparable | **221 s** (same 15 steps, new cfg 5.5 / dpmpp_2m) | OK, no regression |
| `aurora/pipeline.py` reel, 15 s, 1080×1920 | 4.8 s, 4.6 s | 4.9 s, 4.3 s, 4.4 s via `python aurora/pipeline.py` (now with audio track, 24 fps) | OK |

Notes
- NIM and reel "before" numbers were taken before any edit; the ComfyUI "before" run read the original workflow/config at start (edited files afterwards). NIM credits used: 2 of 3.
- New defaults (25 steps, 512×768) cost more than the 15-step bench by design: expect roughly ×1.7 steps and ×1.5 pixels on CPU. Quality was the explicit goal; use `steps=` to trade down.
- Reel after: validated 1080×1920, 24 fps, 15.0 s, audio stream present (`outputs/perf/reel_after.mp4`).
- Outputs: `outputs/perf_nim_{before,after}/`, `outputs/perf_comfy_{before2,after}/`, `outputs/perf/reel_*.mp4`.

Review follow-ups (not in Phase 0): wire `media_validator` into provider result writing / job records (QA, Architect); dHash placeholder detection and a batch-uniqueness scan; black/frozen-frame checks. NIM size mapping is reported in `ImageResult.context["adjustments"]`.

## Phase 1 (validator wiring + autorun), measured 2026-10-21

| Path | Result |
|---|---|
| `pytest -q` | 24 passed |
| Reel render incl. built-in validation (ffprobe + hash), 15 s 1080x1920 24 fps | 4.4 s and 4.7 s inside autorun (Phase-0 reel: 4.4-4.9 s) -> no regression |
| Autorun topic 1 via local_comfyui, 512x768, 25 steps, dpmpp_2m, cfg 5.5 | image 594 s + reel 4.7 s = **599 s** (CPU; ~1.7x steps x 1.5x pixels vs the 221 s 512x512/15-step bench; pytest ran concurrently for a few seconds) |
| Autorun topic 2 via nvidia_nim | image 6.9 s (768x1344, mapped from 512x768, recorded in sidecar `adjustments`) + reel 4.4 s = **11.9 s**; 1 NIM credit |
| Resume | second run skipped topic 1, rendered only topic 2 |

Outputs: `outputs/final/2026-10-21/*.mp4` + `.json` sidecars (1080x1920, 24 fps, 15.0 s, audio track present). Command: `python -m aurora.autorun --topics topics.txt --providers local_comfyui --no-llm`.
Notes: Ollama is up but only `qwen3:8b` is installed (config says llama3:8b); autorun picks an installed model, but on this CPU the call exceeded the 120 s timeout, so it fell back to the topic text. Use `--no-llm` to skip the wait. Kaggle tier (`kaggle_provider`, `scripts/kaggle/aurora_kaggle_worker.py`) is prepared, OFF, untested (no credentials).

QA follow-ups applied: switchboard checks exact size/fps/duration for its own static renderer, deep mode adds black-frame sampling, autorun rejects a reel byte-identical to another topic's and records `engine_mode`. Still open: dHash placeholder list, job-record rule (SUCCESS vs mock/null GPU), non-silent audio check when `--music`/`--voiceover`, Kaggle fake-worker contract test.

## Audio (2026-10-22)
Test reel with tone + Piper voiceover (autorun, camera motion, NIM image): reel step 19.1 s total (audio processing ~14 s: Piper load+synth, loudnorm, ducking mix); silent/no-music path unchanged (no audio step). Voice: Piper en_US-lessac / hi_IN-pratham (CPU, offline). Coqui XTTS cannot run here: not installed, no Python 3.14 support, and assets/reference_voice.wav is missing.
