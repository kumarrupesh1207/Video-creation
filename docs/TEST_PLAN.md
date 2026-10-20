# Project Aurora — Test Plan (QA Lead)

Date: 2026-10-20. Scope: current-state test audit + strategy for the new product (full creative control: text→image, text→video, image→video, multi-segment long video, any aspect ratio/duration, mobile UI, self-hosted/rented remote GPU as primary compute).
No code or tests were changed to produce this document.

---
## 0. Reuse map (extend, don't add)

| Need | Reuse / extend |
|---|---|
| Provider contract suite | **reuse** `aurora/motion/base.py` (`MotionProvider`, `VideoResult`) and `aurora/motion/providers/remote_i2v_base.py` (`RemoteI2VProvider`; existing `RemoteAuthenticationError`, `RemoteQueueTimeoutError`, `RemoteWorkerCrashError` map directly to F6 cases) |
| Fake GPU worker | extend the mock path already in `test_wan2gp_handshake.py`, but make it a real local HTTP stub speaking the `RemoteI2VProvider` protocol and rendering distinct clips with the existing `ffmpeg_kenburns.py`/`ffmpeg_static.py` |
| Routing tests | convert `test_motion_routing.py` in place (reuse `MotionSwitchboard`), add asserts |
| Result gate / telemetry rules | add the validator call inside `VideoResult` creation/`aurora/monitoring/observability.py`, not a new service |
| Logs/records | reuse `StructuredLogger` (`aurora/logs`) and the existing JSON response records |
| Fixtures & tmp dirs | reuse `workspace_tmp_path` and `_write_test_png` from `tests/test_sprint1.py` (move to `tests/conftest.py`) |
| Ledger/segment tests | extend `tests/test_prompt_ledgers.py` and `aurora/prompts/*.json` |
| Validation command | keep `AURORA.md` command; add the new markers there instead of a new doc |
| UI tests | Playwright against existing `ui/` (it already has `package.json`); no new frontend |
| Docs | this plan stays the single QA doc; link from `AURORA.md` |

Only genuinely new files: `tests/support/media_validator.py`, `tests/conftest.py`, `pytest.ini`, `.github/workflows/*`.

## 1. Current test inventory

| File | Style | What it covers | Real assertions? |
|---|---|---|---|
| `tests/test_sprint1.py` (4 tests) | pytest | `StructuredLogger` writes valid JSON; `FFmpegKenBurns.is_available()` returns bool; `VideoResult` dataclass fields; `generate()` with a 100x100 PNG | **Weak**: `subprocess.run` is monkeypatched and writes the bytes `b"mp4"`; the test only checks the file exists. Never runs real FFmpeg, never probes output. |
| `tests/test_prompt_ledgers.py` (2 tests) | pytest | Titan-battle ledger JSON fields (60 s, 24 fps, 1440 frames, 1080x1920, seed); `run_prompt_ledger` produces segments + concat | Metadata/plumbing only; generation is mocked. Ledger says 24 fps, real outputs are 25 fps — nothing links the two. |
| `test_motion_routing.py` (root) | script (`run_test_suite()`) | `MotionSwitchboard({})` keyword routing (high action / atmospheric / standard) | **Not collected by pytest** (0 tests). |
| `test_wan2gp_handshake.py` (root) | script (`main()`) | Wan2GP forced-fallback and "mocked success" flows (reads `config.yaml`, manifest) | **Not collected** (0 tests). Its mocked-success path is what produced `wan_mock_success_*` and SUCCESS records with `engine_mode: mock`. |
| `src/test_audio.py` | manual script (XTTS/GPU) | TTS smoke | Not collected; needs models/GPU. |
| `src/test_lipsync.py` | manual script | Wav2Lip smoke | Not collected; needs models. |
| `src/test_video.py` | manual script | SVD-XT via ComfyUI (imports `modules.video_engine`) | Not collected; needs ComfyUI + 12 GB NVIDIA GPU. |

Absent: `.github/`, `conftest.py`, `pytest.ini`, any server/API/UI test, any ffprobe-based check.

## 2. Current results (run 2026-10-20, Python 3.14, Windows 11, no NVIDIA GPU)

| Command | Result |
|---|---|
| `python -m pytest -q` (whole repo) | **6 passed**, 1 warning (7.5 s) |
| `tests/test_sprint1.py` | 4 passed |
| `tests/test_prompt_ledgers.py` | 2 passed |
| `test_motion_routing.py` | 0 collected ("no tests ran") |
| `test_wan2gp_handshake.py` | 0 collected |
| `src/test_audio.py`, `src/test_lipsync.py`, `src/test_video.py` | 0 collected each |

Only warning (environmental): `PytestCacheWarning: could not create cache path ...\.pytest_cache\v\cache [WinError 183]`. No failures, no errors.

**"Green" is misleading**: the 6 passing tests exercise mocks; none validates a real video file.

## 3. Gap analysis

### 3.1 Critical: mocks reported as SUCCESS (verified)
Evidence in `outputs/`:
- 10 of 11 `.mp4` files under `outputs/i2v_responses/` and `outputs/liveportrait_responses/` share **one MD5 `9a75c418be5f8b5a2735d437737461df`** — across framepack, svdxt, wan2gp and LivePortrait, on different runs. One placeholder is being copied around.
- `20260508_225509_wan2gp_provider.json`: `"status": "SUCCESS"` but `gpu_name: null`, every `vram_*: null`, `latency_ms: 0`, `engine_mode: "mock"`.
- ffprobe of the placeholder: h264 1080x1920, 25 fps, 5.0 s, **and an AAC audio stream** (so "has audio / no audio" cannot prove real vs fake either).
- `wan_mock_success_wan2gp_provider.mp4` (MD5 `8bf5…`) is **not a valid MP4** (`error reading header`) yet has a success record.

Root causes the tests must guard: status not derived from evidence of real work; `engine_mode: mock` coexisting with SUCCESS; no output validation; same bytes from different providers never flagged.

### 3.2 Other gaps
1. Spec says 24 fps and 25–30 s; outputs are 25 fps / 5 s. Nothing asserts the contract.
2. The only real path (FFmpeg Ken Burns) is never run for real in tests.
3. No tests for `server.py`, `ui/index.html`/`main.js`, `streamlit_app.py`, `backend/`, `aurora/monitoring`, `aurora/assets`, `config.yaml` schema, `presets.json`.
4. Script-style tests are invisible to pytest and have no failing asserts → cannot gate CI.
5. No negative tests: timeout, OOM, corrupt input image, worker disconnect, partial upload, cancel.
6. No multi-segment concat test with real files (duration sum, A/V sync, seams).
7. No aspect-ratio/duration matrix (everything is 9:16 1080x1920).
8. No cost/budget guard tests (needed for rented GPUs).
9. Tests use CWD-relative paths (`aurora/prompts/...`, `.test_tmp`) — fragile.

## 4. Output validation toolkit (the anti-mock net)

Build once as `tests/support/media_validator.py` (ffprobe/ffmpeg, PIL, numpy; CPU-only). Call it from **every** media test **and** from the production result gate: a job may become `SUCCESS` only if the validator passes.

`validate_video(path, expected) -> ValidationReport`:

| # | Check | Method | Catches |
|---|---|---|---|
| V1 | Decodable | `ffprobe -v error` exit 0 and `ffmpeg -v error -i f -f null -` no errors | corrupt file (`8bf5…`), `b"mp4"` stub |
| V2 | Duration | `format=duration` within ±max(0.1 s, 1 frame) of requested | 5 s when 30 s asked |
| V3 | FPS | `r_frame_rate` == requested (24 default) | 25 vs 24 |
| V4 | Resolution/aspect | width/height == requested, even, SAR 1:1 | wrong aspect |
| V5 | Frame count | `nb_read_frames` ≈ duration × fps | dropped/duplicated frames |
| V6 | Audio | stream present iff requested; audio duration within 0.1 s of video | unexpected AAC in placeholder, missing narration |
| V7 | Non-static | frames at t=0, mid, end differ (mean abs diff > ε; SSIM(first,last) < 0.995 for motion modes) | frozen clip |
| V8 | Not black/constant | per-frame stddev > ε, mean luma in [5,250] | black frames |
| V9 | Not a known placeholder | SHA-256 and dHash of 5 sampled frames not in `tests/fixtures/known_placeholders.json` (seed with MD5 `9a75c418…` and `8bf5…`) | copied mock |
| V10 | Fidelity to input (i2v) | frame 0 vs input image perceptual distance < τ; later frames differ from frame 0 | unrelated video |
| V11 | Uniqueness | in a batch, outputs from different (prompt, seed, provider) never share a hash | the existing duplicate-MD5 defect |
| V12 | Prompt relevance (GPU/nightly) | CLIP(prompt, mid-frame) ≥ threshold | off-prompt output |

`validate_job_record(json)`:
- `status == SUCCESS` ⇒ `engine_mode != "mock"`. Test doubles must set `test_double: true`, write to a `fake/` dir, and report `SUCCESS_SIMULATED` (never `SUCCESS`).
- GPU providers: `gpu_name` non-empty, `vram_total_mb > 0`, `0 < vram_used_mb ≤ vram_total_mb`, `latency_ms > 0`, `total_inference_latency_ms > 0`. CPU Ken Burns: `gpu_name` null allowed, but `engine_mode == "cpu_ffmpeg"` and `latency_ms > 0`.
- `response_path` exists inside the job dir and record `sha256` matches the file.
- Telemetry must come from the worker (nvidia-smi / torch.cuda) and is cross-checked against the provider API in the E2E job; a fake worker reports `gpu_name: "FAKE-GPU"` and can never produce plain SUCCESS.

Regression test `test_existing_outputs_are_not_identical`: scans `outputs/**`, fails if different providers' outputs share a hash. Expected to fail today → `xfail(strict=True)` until old outputs are quarantined.

## 5. Test strategy for the new product

CPU-only everywhere except layer 6.

1. **Unit** (<1 s each): prompt/ledger parsing; segment planner (duration → N segments ≤ provider max, any aspect, crossfade/overlap math); resolution snapping (multiples of 8/16/64 per model); seed handling; job state machine (QUEUED→RUNNING→SUCCESS/FAILED/CANCELLED/PARTIAL); cost estimator + budget guard; config/preset schema validation (no hard-coded presets: any user W×H/fps/duration accepted within documented limits, otherwise a clear error); the media validator itself (generated good/bad clips: black, frozen, wrong fps, corrupt header, placeholder copy).
2. **Real-FFmpeg component tests** (seconds): Ken Burns on a generated PNG at 9:16, 16:9, 1:1, 4:5, 21:9 and 1 s / 5 s / 30 s → `validate_video`. Concat of N real segments; audio mux; silence check. Replaces the monkeypatched `subprocess.run` test.
3. **Provider-interface contract tests**: one parametrised suite run against every provider (Ken Burns, FramePack, SVD-XT, Wan2GP, LivePortrait, remote-worker client). Asserts: `capabilities()` (max duration, fps, resolutions, modes); `generate()` returns a fully typed `VideoResult`; honours cancel/timeout; raises typed errors (`ProviderUnavailable`, `OutOfMemory`, `InvalidInput`); never SUCCESS without output passing V1–V9; reports `gpu_name`/VRAM honestly. CI runs `mode=fake`; the GPU job runs the same suite `mode=real`.
4. **Remote GPU worker contract tests** (OpenAPI/JSON-schema, Schemathesis): `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/events`, `DELETE /jobs/{id}`, `GET /health` (gpu_name, vram_total/free, driver, models_loaded, protocol version); bearer auth (401/403); idempotency key; artifact download with checksum + range resume; upload size/type limits; version negotiation. Consumer-driven contract file (orchestrator expectations) verified against both the real worker and the fake worker so they cannot drift.
5. **Integration with a fake GPU worker** (CPU, in CI): FastAPI app on localhost speaking the real contract, rendering *genuinely distinct deterministic* clips with FFmpeg (`testsrc2`/`mandelbrot` seeded from prompt+seed, correct fps/res/duration, optional tone). Fault injection: slow/timeout, HTTP 500, connection drop mid-download, corrupt artifact (bad checksum), OOM, worker restart mid-job, duplicate/out-of-order events, token expiry. Scenarios: t2i, t2v, i2v, 6-segment 30 s with one retried segment, cancel mid-run, resume after orchestrator restart, budget exceeded → halt.
6. **Real rented-GPU E2E smoke** — §7.
7. **Golden-file / perceptual**: store derived goldens (small): ffprobe metadata JSON (exact); 8 sampled-frame pHash (Hamming ≤ tolerance); SSIM vs reference frames (≥ 0.9) for deterministic CPU Ken Burns; audio fingerprint tolerance. Diffusion output is non-deterministic across GPUs → use property checks V1–V12 + CLIP, not pixel goldens. Updating goldens needs `--update-goldens` and a reviewed diff.
8. **Mobile UI (Playwright, Chromium, CPU)**: viewports iPhone SE 375×667, iPhone 14 390×844, Pixel 7 412×915, landscape 667×375, iPad 768×1024, touch emulation. Backend = fake worker. Checks: no horizontal scroll, tap targets ≥ 44 px, no text overflow, soft-keyboard viewport shrink, progress updates, cancel, inline `playsinline` preview, download/share, offline/slow-network banner (CDP throttling), resume after tab backgrounding, light/dark, axe-core 0 serious violations, Lighthouse mobile budget, per-viewport screenshot diff (≤0.1 % pixels).
9. **Non-functional**: cost-cap tests, log redaction (no keys/tokens), security (upload type/size, path traversal in filenames, SSRF via image URLs), soak (20 queued jobs), Windows + Linux path handling.

Conventions: fixtures generated in code (no big binaries); `tmp_path` and `Path(__file__)`-relative paths only; markers `unit`, `component`, `contract`, `integration`, `ui`, `gpu`, `slow`, `cost` — `gpu` and `cost` deselected by default.

## 6. Acceptance criteria (Given / When / Then)

**F1 Text → Image**
- Given a prompt, 768×1344 (any W×H within limits) and seed 42, When submitted, Then the image is exactly 768×1344, status SUCCESS with real `gpu_name`/`vram_*`, and re-running with the same seed/model/worker gives pHash distance ≤ 4.
- Given W×H outside limits or violating divisibility, When submitted, Then it is snapped with a visible notice or rejected with a 422 naming the field — never silently altered.
- Given an empty prompt, When submitted, Then a validation error shows and no GPU job is created.

**F2 Text → Video**
- Given a prompt, aspect 16:9, 8 s, 24 fps, When submitted, Then output passes V1–V9 at the requested resolution/24 fps/8 s ±0.1 s, is not a known placeholder, and CLIP(prompt, frames) ≥ threshold (nightly).
- Given any aspect in {9:16, 16:9, 1:1, 4:5, 21:9, custom} and duration 1–N s, When generated, Then dimensions and duration match the request exactly (no hard-coded 1080×1920 / 5 s).

**F3 Image → Video**
- Given an uploaded JPG/PNG/WebP of any size/orientation and a motion prompt, When submitted, Then frame 0 matches the image (V10), later frames differ (V7), and aspect follows the user's explicit crop/pad/fit choice.
- Given a corrupt or 0-byte image, When submitted, Then 400 `InvalidInput` and no charge.
- Given the CPU Ken Burns fallback is selected, When run, Then the record says `engine_mode: cpu_ffmpeg` and the UI labels it "pan/zoom only", not AI.

**F4 Multi-segment long video**
- Given 30 s and a provider with 5 s max, When submitted, Then ≥6 segments are planned, final duration is 30 s ±0.2 s at constant fps, seams have no black/frozen frames (boundary frame diff under threshold), and audio stays in sync (|a−v| ≤ 0.1 s).
- Given segment 3 fails transiently, When retried, Then only segment 3 is regenerated (others reused by hash) and the job succeeds.
- Given segment 3 fails permanently, When retries are exhausted, Then the job is FAILED/PARTIAL, finished segments remain downloadable, the reason is shown, and SUCCESS is never emitted.

**F5 Provider honesty (anti-mock)**
- Given any provider response, When status is SUCCESS, Then `engine_mode != mock`, the output passes the validator, and `gpu_name` is non-null for GPU providers.
- Given two jobs with different prompts/seeds, When both succeed, Then output hashes differ.
- Given a test double runs, Then status is `SUCCESS_SIMULATED` and the UI shows a "SIMULATED" badge.

**F6 Remote GPU worker**
- Given a reachable worker and valid token, When `/health` is called, Then it returns real GPU name, VRAM total/free, driver and a compatible protocol version.
- Given an invalid token, When any endpoint is called, Then 401 and no job starts.
- Given a mid-job connection drop, When it recovers within the grace window, Then events resume with no duplicate job (idempotency key); otherwise FAILED with cause `WORKER_LOST`.
- Given GPU OOM, When the job fails, Then `OutOfMemory` is surfaced with a suggestion (lower resolution/duration) and VRAM peak is recorded.

**F7 Cost control**
- Given per-job cap $X and daily cap $Y, When the estimate exceeds a cap, Then the user must confirm or the job is refused before any pod starts.
- Given the cap is crossed mid-run, When the meter trips, Then the job stops, the pod terminates within 60 s, and the UI shows spend.
- Given a job finishes or fails, When cleanup runs, Then no pod/volume stays billable (verified via provider API in E2E).

**F8 Mobile UI**
- Given a 375×667 viewport, When creating a video, Then every control is reachable without horizontal scroll, tap targets ≥ 44 px, and Generate is not hidden by the soft keyboard.
- Given a running job and the user backgrounds then returns to the tab, Then progress is correct within 5 s.
- Given a finished video, When Play is tapped, Then it plays inline; When Download/Share is tapped, Then the file matching the job hash is delivered.
- Given the network drops, Then an offline banner appears and reconnect does not double-submit.
- Given axe-core on every screen, Then 0 serious/critical violations.

**F9 Creative control (no hard-coded presets)**
- Given presets exist, When the user enters custom prompt, negative prompt, seed, W×H, fps, duration, motion strength, model, Then exactly those values reach the worker request (asserted on the wire in integration tests) and are stored in output metadata.
- Given a preset is selected then edited, Then the edited values win and the preset is not re-applied.

**F10 Legacy FFmpeg Ken Burns**
- Given a PNG, duration D and fps F, When generated with real FFmpeg, Then output passes V1–V8 with exactly D, F and the requested resolution (today fixed at 5 s / 25 fps — this test drives the fix).

## 7. Real-GPU E2E smoke (manual + nightly, cost-capped)

- Triggers: `workflow_dispatch` and nightly cron on `main` only; never on PRs/forks. Provider API key in encrypted secrets, from a **dedicated sub-account with a hard prepaid balance ≤ $5**.
- Guards: `MAX_SPEND_USD=1.50` per run, enforced in the harness (estimate first, poll meter, kill at cap); pod max lifetime 20 min; cheapest suitable GPU (24 GB class, spot/community); `if: always()` teardown terminating pods labelled `aurora-ci-<run_id>`; separate hourly reaper workflow killing labelled pods older than 30 min.
- Tiny workloads: one 512×512 t2i, one 2–3 s 480p i2v, one 2-segment 6 s long video. Target <10 min, <$0.50.
- Assertions: contract suite `mode=real`; V1–V12; `gpu_name` equals the rented SKU per provider API; VRAM > 0; outputs differ from each other and from known placeholders; spend ≤ cap and recorded; teardown verified.
- Upload outputs + JSON report as artifacts (7-day retention).
- Self-hosted variant: `pytest -m gpu --worker-url <url>` against the user's own GPU.

## 8. CI proposal (GitHub Actions, CPU-only by default)

`.github/workflows/ci.yml` (push/PR):
- `lint-unit`: ubuntu + windows matrix, Python 3.11 and 3.14, `requirements-ci.txt` (no torch/CUDA), `ruff`, optional `mypy`, `pytest -m "unit or component" --cov`, pip cache.
- `media`: install ffmpeg, `pytest -m "component or contract"` including real Ken Burns and validator self-tests. Fails if any test double ever reports plain `SUCCESS`.
- `integration`: start `fake_gpu_worker`, `pytest -m integration` incl. fault injection; 10 min timeout.
- `ui`: `npx playwright install --with-deps chromium`; mobile suite + axe + screenshot diffs; upload traces on failure.
- `repo-hygiene`: block binaries >5 MB and `.mp4` outside `tests/fixtures`; duplicate-hash scan of `outputs/`; gitleaks.
- Required checks: `lint-unit`, `media`, `integration`, `ui`.

`gpu-nightly.yml` (cron + manual, §7) and `gpu-reaper.yml` (hourly).
Reporting: JUnit + coverage in job summary; coverage target 80 % on orchestration/provider/planner modules; reruns allowed only for `ui`.

## 9. Prioritised first steps (no GPU needed)

1. Add `tests/support/media_validator.py` + self-tests + `known_placeholders.json` (MD5 `9a75c418…`, `8bf5…`) — closes §3.1.
2. Gate provider results: SUCCESS only if validator and telemetry rules pass (F5).
3. Convert `test_motion_routing.py` / `test_wan2gp_handshake.py` to real pytest with asserts; move `src/test_*.py` to `tests/manual/` with `@pytest.mark.gpu`.
4. Replace the monkeypatched Ken Burns test with a real-FFmpeg one (F10).
5. Add `conftest.py`, `pytest.ini` (markers, `--basetemp`), and the CI workflow.
6. Build the fake GPU worker once `docs/ARCHITECTURE.md` fixes the API.

## 10. Open questions
- Final provider interface and worker protocol (field names above are placeholders until `docs/ARCHITECTURE.md` is final).
- GPU rental vendor (drives teardown/reaper code and cost API).
- Default fps (24 vs 25) and max resolution/duration — needed to fix numbers in F2/F4.
- Whether audio (TTS/music) is in v1 scope (V6 branches on it).
