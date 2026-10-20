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
