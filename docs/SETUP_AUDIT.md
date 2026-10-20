# Project Aurora — Setup Audit (DevOps / Platform)

Date: 2026-10-20 · Scope: dependencies, Python versions, config, secrets, repo hygiene, tests, remote access.
Method: static import scan (AST over `aurora/ src/ backend/ scripts/ tests/ main.py server.py streamlit_app.py test_*.py`), `git grep`, `git status`, and a read-only `pytest` run. **Nothing in the repo was changed except this file.** Everything below marked "proposed" is a proposal only.

---

## 0. Executive summary

| Area | Verdict |
|---|---|
| Dependencies | `requirements.txt` is a ~100-line `pip freeze` of the dev machine. It lacks **torch, diffusers, TTS, pynvml, transformers/accelerate**, yet carries ~45 unused or transitive pins (Flask, three Postgres drivers, alembic, passlib, pandas, matplotlib …). |
| Python | Dev box runs **3.14.0**. Coqui `TTS` and the Wav2Lip stack do not support it. Use 3.11 for the GPU worker / TTS; 3.12 for the control plane. |
| Config | `config.yaml` (12 GB VRAM, 720×1280, Ollama, ComfyUI `--cpu`) contradicts `AURORA.md` (no GPU, 1080×1920, FreeLLMAPI). Six hard-coded Windows path groups. `FREELLMAPI_KEY` is documented as mandatory but **no code reads it**. |
| Secrets | **No secrets found in tracked or untracked files.** `.env.example` covers 2 of the vars the code reads; 7 listed vars are read by nothing (planned providers); ~50 config-ish env vars are undocumented; nothing loads `.env`. |
| Repo | Local `origin/main` == `HEAD`, but the working tree has 10 modified + ~35 untracked files → most motion/switchboard/backend work is **not on GitHub**. `.gitignore` is 12 lines with many gaps. |
| Tests | `python -m pytest -q` → **6 passed in 3.77 s** (Python 3.14.0). Only 2 test files exist. |
| Remote/mobile | Server binds `127.0.0.1:8000` with `CORS *` and no auth. Recommend **Tailscale** (primary) + optional Cloudflare Tunnel with Access (§7). |

### Reuse-first (applies to every proposal below)

| Need | Reuse this existing thing | Don't create |
|---|---|---|
| Config + env overrides | `config.yaml` (extend in place; many modules already read `AURORA_*`/`SVDXT_*`/`LIVEPORTRAIT_*` env vars) + `pydantic-settings` already pinned | a second config tree; `config/aurora.yaml` in §3.4 is a *target shape* — apply it to `config.yaml` |
| Remote GPU / self-hosted worker | `aurora/motion/providers/remote_i2v_base.py`, `scripts/remote_gpu_worker.py`, `scripts/gpu_provision.sh`, `AURORA_SHARED_ROOT` request/response dirs | new worker protocol |
| Provider routing / tiers | `aurora/motion/switchboard.py`, `backend/app/services/image_switchboard.py` | new router |
| Logging | `aurora/logs/logger.py` (Rule 4 fields); retire `src/modules/pipeline_logger.py` duplicate | third logger |
| Docs | `AURORA.md` (update status header + Rule 1 instead of new docs) | extra README files |
| UI/auth | `ui/` + `server.py` (add bearer middleware, restrict CORS) | new web server |
| Tests | `tests/` (move the two root `test_*.py` there) | new test harness |
| Requirements | existing pins + `requirements_wav2lip.txt` (just move) | rewriting from scratch |

Concretely, the smallest viable change set is: edit `.gitignore`; split `requirements.txt` (§1.5); edit `config.yaml`/`.env.example`; add `load_dotenv()` in `server.py`; add auth in `server.py`.

---

## 1. Dependencies

### 1.1 Third-party imports found

| Import | Used in | In `requirements.txt`? |
|---|---|---|
| `yaml` | main.py, server.py, scripts/render_from_asset.py + 7 more | yes (PyYAML) |
| `requests` | 11 files (backend providers, wan2gp, scripts) | yes |
| `fastapi`, `pydantic` | server.py | yes |
| `uvicorn` | server.py, aurora/api.py | yes (no `[standard]` extra; httptools/websockets/watchfiles pinned by hand) |
| `streamlit` | streamlit_app.py | yes |
| `psutil` | server.py | yes |
| `PIL` | main.py, scripts/remote_gpu_worker.py, scripts/render_from_asset.py | yes (pillow) |
| `websocket` | src/modules/comfy_orchestrator.py | yes (websocket-client) |
| `pytest` | tests/ | yes |
| `torch` | main.py, remote_gpu_worker.py, audio_engine.py, lipsync_processor.py, verify_setup.py | **MISSING** (torch 2.12.0 is installed locally though) |
| `TTS` (Coqui) | src/modules/audio_engine.py | **MISSING** |
| `diffusers` | scripts/remote_gpu_worker.py | **MISSING** |
| `pynvml` | scripts/remote_gpu_worker.py | **MISSING** |
| `accelerate safetensors transformers huggingface_hub xformers aria2p gdown` | pip-installed ad hoc by `scripts/gpu_provision.sh` | **MISSING** from every requirements file |

`aurora`, `src`, `backend`, `server`, `comfy_orchestrator` are local. Note two path hacks: `aurora/api.py` imports root `server`; `video_engine.py` imports `comfy_orchestrator` as a top-level module. Fragile → make a proper package (`pip install -e .`).

### 1.2 Missing
`torch`, `torchaudio` (XTTS), `TTS`, `diffusers`, `transformers`, `accelerate`, `safetensors`, `huggingface_hub`, `pynvml` (use the maintained `nvidia-ml-py`, which provides the `pynvml` module). `python-dotenv` is pinned but **never imported**, so `.env` is never auto-loaded (§4).

### 1.3 Unused / suspicious pins (no import in scanned code)
- Flask stack: `Flask, Werkzeug, blinker, itsdangerous`
- DB stack (no DB code): `alembic, SQLAlchemy, sqlmodel, greenlet, psycopg, psycopg-binary, psycopg2-binary`
- Auth stack (no auth code yet; keep only if the new UI adds login): `passlib, bcrypt, python-jose, PyJWT, ecdsa, rsa, pyasn1, cryptography`
- Data/plot: `pandas, matplotlib, contourpy, cycler, kiwisolver, fonttools, pyparsing, pyarrow, altair, pydeck` (Streamlit transitive; shouldn't be hand-pinned)
- Misc: `pypdf, GitPython, opencv-python` (no direct import outside the Wav2Lip venv), `toml, tenacity, pydantic-settings, watchdog, tornado`
- Everything else is transitive. Generate a lockfile with `uv pip compile`/`pip-compile`, don't hand-maintain.

### 1.4 Other defects
- Three Postgres drivers pinned.
- `requirements_wav2lip.txt` header says "main venv Python 3.10+", but host is 3.14; its torch 1.12.1+cu116 wheels exist only for ≤3.10. It must stay in an isolated 3.8 venv. Wav2Lip is GPU-only and contradicts AURORA.md Rule 6 ("GPU modules never run locally") → treat as part of the GPU worker, or retire for a newer lip-sync model.
- No `pyproject.toml`, lockfile, or `.python-version`.

### 1.5 Proposed split

New directory `requirements/`; keep a root `requirements.txt` containing only `-r requirements/base.txt`; move `requirements_wav2lip.txt` → `requirements/wav2lip-py38.txt`.

**`requirements/base.txt`** — CPU/local control plane (Python 3.12; 3.11–3.14 OK). No torch.
```text
# Aurora control plane — home PC / any CPU box
fastapi==0.121.3
uvicorn[standard]==0.38.0
pydantic==2.12.4
pydantic-settings==2.12.0   # config + env overrides (see §3.4)
python-multipart==0.0.20
requests==2.32.5
websocket-client==1.9.0     # ComfyUI WS client
PyYAML==6.0.3
python-dotenv==1.2.1        # load .env (currently never loaded)
psutil==7.2.2
pillow==12.1.1
streamlit==1.54.0           # interim UI; drop when the new web UI replaces it
tenacity==9.1.4             # retry/backoff (Rule 5); drop if unused
```

**`requirements/gpu-worker.txt`** — self-hosted / rented NVIDIA box (Python 3.11, CUDA 12.x). Install torch first.
```text
-r base.txt
--extra-index-url https://download.pytorch.org/whl/cu124
torch==2.5.1+cu124          # pin to newest cu12x wheel that boots; re-pin after first success
torchvision
torchaudio
diffusers>=0.31
transformers>=4.46
accelerate>=1.0
safetensors>=0.4
huggingface_hub>=0.26
nvidia-ml-py>=12.560        # provides `pynvml`
imageio[ffmpeg]
opencv-python-headless>=4.9
numpy<2.3
xformers; platform_system=="Linux"   # optional, gpu_provision.sh tolerates failure
TTS==0.22.0                 # Coqui XTTS-v2: requires Python <=3.11 -> only in the 3.11 worker env
# LivePortrait / FramePack / Wan2GP: install from their own repos into their own venvs.
# Wav2Lip: requirements/wav2lip-py38.txt in a Python 3.8 venv (optional/legacy).
```

**`requirements/dev.txt`**
```text
-r base.txt
pytest==9.0.2
pytest-asyncio==1.3.0
httpx>=0.27                 # FastAPI TestClient
ruff>=0.6
mypy>=1.11                  # .mypy_cache exists, so mypy is already in use
pip-tools>=7.4              # or use `uv pip compile`
```

---

## 2. Python version

Observed: `__pycache__/*.cpython-314.pyc`; `python --version` = **3.14.0**; torch 2.12.0 installed locally but not recorded in requirements.

| Component | Status on 3.14 (verify with `pip download --only-binary=:all:` before relying on it) | Recommendation |
|---|---|---|
| torch / torchvision / torchaudio | Recent releases ship cp314 wheels; CUDA builds and surrounding ecosystem (xformers, flash-attn, bitsandbytes, onnxruntime-gpu) lag | GPU worker: **3.11** (3.12 acceptable) |
| Coqui `TTS` | Supports **<3.12**; won't install on 3.12/3.14 | Keep in the 3.11 worker, or move to the maintained `coqui-tts` fork (≤3.12) or an API TTS |
| diffusers / transformers / accelerate | Pure Python, but tested mostly on 3.10–3.12 | 3.11 |
| xformers / flash-attn | Typically no cp314 wheels | 3.11 Linux only |
| opencv, numpy 2.3, pillow 12, pydantic-core, psutil, PyYAML | cp314 wheels available (installed, tests pass) | OK on 3.14 |
| Wav2Lip (torch 1.12.1, librosa 0.8.1, numpy 1.23.5) | Max 3.8–3.10 | Isolated **3.8** venv, or replace |
| LivePortrait / FramePack / Wan2GP | READMEs target 3.10–3.12 | Own venvs, 3.10/3.11 |
| ComfyUI portable | Embedded interpreter | independent |

Recommended: **control plane 3.12**, **GPU worker 3.11 + CUDA 12.x**, **Wav2Lip 3.8** (isolated). Add `.python-version` per env and `requires-python = ">=3.11,<3.14"` in a future `pyproject.toml`. Use `uv venv --python 3.11` to manage several interpreters without touching the global install.

---

## 3. Configuration contradictions

### 3.1 `config.yaml` vs `AURORA.md` vs code

| # | Topic | `config.yaml` | `AURORA.md` | Code | Resolution |
|---|---|---|---|---|---|
| 1 | Hardware | Header "12 GB VRAM"; `min_vram_mb 8192`, `recommended 12288` | "No dedicated GPU, 0 GB VRAM"; Rule 6: GPU modules never local | `remote_gpu_worker.py` does VRAM checks; `main.py` `vram_cleanup` assumes local GPU | GPU = remote/self-hosted worker; VRAM thresholds belong to the worker profile |
| 2 | Resolution | `resolution: [720,1280]` "VRAM-safe" | 1080×1920 final | `aurora/*` hard-codes 1080×1920 (`motion/base.py:42`, `ffmpeg_kenburns.py:72-78,160`, `ffmpeg_static.py:11-23`, `assets/manifest.py:29,204,306,475`); `main.py` uses 540×960 for cinematic and 720×1280→1080×1920 upscale | Split `generation_size` vs `output_size`; aurora reads config |
| 3 | LLM | `llm.provider: ollama`, `llama3:8b`, `127.0.0.1:11434` | Rule 1: ALL LLM calls via FreeLLMAPI `localhost:3001/v1`, `FREELLMAPI_KEY`, model `auto` | `src/modules/llm_manager.py` calls Ollama directly; **no code uses `FREELLMAPI_KEY` or port 3001** | Implement OpenAI-compatible client or amend AURORA.md; schema supports both |
| 4 | XTTS | "loads to GPU, unload before Wav2Lip" | "XTTS-v2 is CPU-side only" | `audio_engine.py` imports torch/TTS | `tts.device: cpu\|cuda`, default `cpu` |
| 5 | ComfyUI | launcher `--cpu`, `ComfyUI_windows_portable_nvidia` dir, `auto_start: true` | no ComfyUI mention (image gen = NIM / manual) | `comfy_orchestrator.py` | `auto_start: false`; path via env |
| 6 | Image provider | `providers.*` tree **and** `models.comfyui.*` tree both configure ComfyUI | Rule 2 asset-first; Rule 3 cheapest-first | `backend/app/providers/*` | One `providers` tree |
| 7 | Compute tiers | `motion.liveportrait/i2v` only; no Kaggle/Vast/Modal/Replicate/RunPod | Rule 3 lists these tiers; `.env.example` has their keys | `aurora/motion/switchboard.py` (untracked) | `compute.backends[]` list (schema below) |
| 8 | Logging | `aurora_log: aurora.jsonl`; env `AURORA_JSONL_PATH`, `AURORA_LOCAL_JSONL_PATH` | Rule 4: `aurora.jsonl` + mandatory fields | Two files exist (`aurora.jsonl`, `logs/aurora.jsonl`); **two different loggers** (`src/modules/pipeline_logger.py`, `aurora/logs/logger.py`) | One path, one logger |
| 9 | Pipelines | `main.py`/`src/` (Wav2Lip, SVD via ComfyUI) | `aurora/pipeline.py` | Two overlapping pipelines | Architecture decision; config must target one |
| 10 | Clip length | `fps 24`; svdxt `25 frames @ 5 fps` | 25–30 s | `titan_battle_60s.json` is 60 s (AURORA.md also says 60-second sheet was rendered) | Doc fix |
| 11 | Wav2Lip | GPU venv `C:/tools/Wav2Lip` | GPU never local | `lipsync_processor.py`, `verify_setup.py` | Worker profile or retire |
| 12 | Status | — | "Hibernated; DO NOT BUILD (incl. Docker)" | User is resuming | Update AURORA.md header |
| 13 | Binding | — | — | `server.py:833`, `aurora/api.py:11` bind 127.0.0.1:8000; `scripts/launch.py:55` binds **0.0.0.0**; CORS `*` | One `server.host/port`; auth required when non-loopback |

### 3.2 Hard-coded environment-specific paths
| Where | Value |
|---|---|
| `config.yaml` `paths.wav2lip_root`, `models.wav2lip.*` | `C:/tools/Wav2Lip/...` — also defaults in `src/modules/lipsync_processor.py:44-48`, `src/verify_setup.py:125`, `src/test_lipsync.py` |
| `config.yaml` `paths.ffmpeg_bin` | `C:/Rupesh/App/ffmpeg/bin/ffmpeg.exe`; `aurora/motion/providers/ffmpeg_kenburns.py:26` hard-codes the same. On this machine `ffmpeg`/`ffprobe` resolve from PATH (WinGet Links). Code derives ffprobe via `.with_name("ffprobe.exe")` (`main.py:109`, `server.py:124`) — Windows-only |
| `config.yaml` `comfyui.launcher_dir` | `C:/Rupesh/App/ComfyUI_windows_portable_nvidia/...`; also `src/download_models.py:24` |
| `server.py:745` | `psutil.disk_usage("C:\\")` — breaks off Windows / other drives |
| `scripts/gpu_provision.sh` | `MODEL_ROOT=/tmp/models` (ephemeral by design on Kaggle/Vast; make configurable) |

### 3.3 Env vars the code reads (all undocumented in `.env.example`, except where noted)
`AURORA_SHARED_ROOT, AURORA_REQUEST_DIR, AURORA_RESPONSE_DIR, AURORA_ARCHIVE_DIR, AURORA_JSONL_PATH, AURORA_LOCAL_JSONL_PATH, AURORA_I2V_TIMEOUT_SEC, AURORA_POLL_INTERVAL_SEC, AURORA_MIN_VRAM_MB, AURORA_RECOMMENDED_VRAM_MB, API_BASE, MODEL_ROOT, SVD_MODEL_ID, ENABLE_XFORMERS, FORCE_FP16`; `SVDXT_*` (MODEL_ID, WIDTH, HEIGHT, FPS, NUM_FRAMES, MOTION_BUCKET_ID, DECODE_CHUNK_SIZE, NOISE_AUG_STRENGTH, NUM_INFERENCE_STEPS, ENABLE_XFORMERS, ENABLE_TORCH_COMPILE); `FRAMEPACK_*` (REPO, PYTHON_BIN, ENTRY_SCRIPT, DRIVER_VIDEO, OUTPUT_DIR, HIGH_FIDELITY_VRAM_MB, ENABLE_FLASH_ATTN, ENABLE_XFORMERS); `LIVEPORTRAIT_*` (REPO, PYTHON_BIN, DRIVER_VIDEO, MOCK_SOURCE_MP4, OUTPUT/REQUEST/RESPONSE/ARCHIVE_DIR, EXECUTION_MODE, WORKER_MODE, POLL_INTERVAL_SEC, INFER_TIMEOUT_SEC, ENABLE_*); `WAN2GP_COMMAND, WAN2GP_OUTPUT_DIR`; `WAV2LIP_*` (PYTHON_BIN, SCRIPT, CHECKPOINT, OUTPUT_DIR, PAD_*, RESIZE_FACTOR, TIMEOUT_SEC).
An ad-hoc env-override pattern exists but with **no precedence rule** versus `config.yaml` (some modules read only env, some only YAML).

### 3.4 Proposed config schema

`config/aurora.yaml` (committed; no secrets; no absolute paths) + `config/local.yaml` (git-ignored) + env vars. Precedence **env > local.yaml > aurora.yaml > defaults**. Env naming `AURORA__<SECTION>__<KEY>` (double underscore = nesting; parse with `pydantic-settings`); legacy single-underscore vars stay as aliases.

```yaml
schema_version: 2
profile: local            # local | gpu-worker   (AURORA__PROFILE)

render:
  output_size: [1080, 1920]       # final deliverable
  generation_size: [576, 1024]    # what the GPU model generates, upscaled afterwards
  fps: 24
  container: mp4
  crf: 18
  preset: slow

paths:                            # relative to ${AURORA_HOME} (default: repo root)
  data_dir: data
  outputs_dir: outputs
  assets_dir: assets
  logs_dir: logs
  shared_root: ""                 # AURORA_SHARED_ROOT — request/response exchange dir
  ffmpeg_bin: ""                  # "" => shutil.which("ffmpeg") / FFMPEG_BIN
  ffprobe_bin: ""

server:
  host: 127.0.0.1                 # non-loopback requires auth.enabled
  port: 8000
  cors_origins: []                # explicit list, never "*"
  auth: {enabled: false, mode: bearer}   # token ONLY from env AURORA_AUTH_TOKEN

llm:
  provider: openai_compat         # openai_compat | ollama
  base_url: http://localhost:3001/v1     # FreeLLMAPI (AURORA.md Rule 1)
  model: auto
  temperature: 0.7
  max_tokens: 1024                # key: env FREELLMAPI_KEY

tts: {engine: xtts_v2, device: cpu, language: en, speed: 1.0}

images:
  default_provider: nvidia_nim
  fallback_order: [nvidia_nim, local_comfyui, manual_upload]
  reuse_existing_asset: true
  nvidia_nim: {base_url: "https://ai.api.nvidia.com/v1/genai/", timeout: 60, quota: 80,
               models: {anime: "black-forest-labs/flux.2-klein-4b", cinematic: "black-forest-labs/flux.2-klein-4b"}}
  local_comfyui: {url: "http://127.0.0.1:8188", auto_start: false, launcher_dir: "", workflow: src/workflows/image_gen.json}

compute:                          # replaces motion.*; Rule 3 ordering preserved
  tier_order: [local, self_hosted, free, cheap, premium]
  retries_per_provider: 2
  request_timeout_sec: 120
  backends:
    - {id: local_ffmpeg, tier: local,       kind: ffmpeg}
    - {id: home_gpu,     tier: self_hosted, kind: remote_worker, url: "", min_vram_mb: 12288}
    - {id: kaggle,       tier: free,        kind: kaggle}
    - {id: vast,         tier: cheap,       kind: vast}
    - {id: runpod,       tier: cheap,       kind: runpod}
    - {id: modal,        tier: premium,     kind: modal}
    - {id: replicate,    tier: premium,     kind: replicate}

motion_models:                    # worker-side only; ignored when profile=local
  svdxt: {model_id: stabilityai/stable-video-diffusion-img2vid-xt, width: 576, height: 1024, num_frames: 25, fps: 5, motion_bucket_id: 127, decode_chunk_size: 4, noise_aug_strength: 0.08, steps: 25}
  framepack: {repo_dir: "", python_bin: "", entry_script: inference.py}
  liveportrait: {repo_dir: "", python_bin: "", driver_video: ""}
  wan2gp: {command: ""}
  wav2lip: {enabled: false, root: "", python_bin: "", checkpoint: ""}

logging: {path: logs/aurora.jsonl, level: INFO}
```
Validate at startup (pydantic model, `extra=forbid`) and fail loud (Rule 5), including refusing `server.host != 127.0.0.1` without auth.

---

## 4. Secrets

### 4.1 Committed-secret scan (values not printed)
- `git grep -nIE` on tracked files for `nvapi-…`, `sk-…`, `r8_…`, `AKIA…`, `ghp_…`, `hf_…` and `(api_key|token|secret|password) = "<16+ chars>"` → **0 hits**.
- Same key-prefix scan on untracked files (excluding `models/ outputs/ .mypy_cache node_modules`) → **0 hits**.
- No `.env` exists; `.gitignore` ignores `.env`, `.env.*`, un-ignores `.env.example` (verified with `git check-ignore`).
- Caveats: only HEAD scanned (2 commits) — run `gitleaks detect` for full history; `.pytest_cache/` is permission-denied for the current user (skipped); `test_wan2gp_handshake.py` sets `WAN_API_KEY="invalid_token_test"` (dummy). Check `aurora.jsonl` / `logs/aurora.jsonl` before ever committing them (scanned clean now).

### 4.2 `.env.example` coverage
| Variable | Read by code? |
|---|---|
| `NVIDIA_API_KEY` | **yes** (backend NIM provider, `check_nim_models.py`, `gpu_provision.sh`) |
| `WAN_API_KEY` | yes, indirectly (`wan2gp_provider.py` `api_key_env` default) |
| `FREELLMAPI_KEY` | **no** — Rule 1 mandates it, unimplemented |
| `REPLICATE_API_TOKEN`, `MODAL_TOKEN_ID/SECRET`, `RUNPOD_API_KEY`, `VAST_API_KEY`, `KAGGLE_USERNAME/KEY` | **no** (providers not written yet; vendor SDKs read these natively) |

Missing from `.env.example`: everything in §3.3, plus `HF_TOKEN` (gated SVD-XT license), `AURORA_AUTH_TOKEN`, worker shared secret. And **no `load_dotenv()` call exists**, so a filled `.env` is silently ignored unless exported in the shell.

### 4.3 Proposed `.env.example`
```dotenv
# Project Aurora — copy to .env (git-ignored). SECRETS ONLY; other settings live in config/aurora.yaml.
# --- LLM (AURORA.md Rule 1) ---
FREELLMAPI_KEY=
# --- Image / motion providers ---
NVIDIA_API_KEY=
WAN_API_KEY=
HF_TOKEN=                      # gated Hugging Face models (SVD-XT)
# --- Compute backends (planned) ---
REPLICATE_API_TOKEN=
MODAL_TOKEN_ID=
MODAL_TOKEN_SECRET=
RUNPOD_API_KEY=
VAST_API_KEY=
KAGGLE_USERNAME=
KAGGLE_KEY=
# --- Self-hosted GPU worker ---
AURORA_HOME_GPU_TOKEN=         # shared secret control plane <-> worker
# --- Remote/phone access ---
AURORA_AUTH_TOKEN=             # bearer token, required when server.host != 127.0.0.1
# --- Optional non-secret overrides ---
# AURORA_SHARED_ROOT=
# AURORA__SERVER__HOST=127.0.0.1
# AURORA__PATHS__FFMPEG_BIN=
# MODEL_ROOT=/workspace/models
```

---

## 5. Repo hygiene

### 5.1 State (`git status`; no fetch performed)
- Branch `main`, 2 commits (`8e6013c`, `d2716b0`); local `origin/main` == `HEAD` → **GitHub holds none of the working-tree changes**.
- **Modified (10):** `config.yaml, main.py, server.py, src/modules/{__init__,comfy_orchestrator,media_assembler,pipeline_logger}.py, src/workflows/image_gen.json, ui/index.html, ui/main.js`.
- **Untracked (~35 files):** `.agents/`, `assets/` (1 jpg + `manifest.json`), `aurora.jsonl`, `aurora/assets/`, `aurora/monitoring/`, `aurora/motion/switchboard.py`, 6 provider files (`ffmpeg_static, framepack, liveportrait, remote_i2v_base, svdxt, wan2gp`), `backend/` (14 files), `logs/`, `scripts/` (7 files incl. `gpu_provision.sh`), `src/modules/motion_engine.py`, `streamlit_app.py`, `test_motion_routing.py`, `test_wan2gp_handshake.py`.
- On-disk noise: `.mypy_cache/` 38 MB (ignored), `.pytest_cache/` (unreadable, **not ignored**, causes git warning), `.test_tmp/` (empty, not ignored), `outputs/` 406 MB (ignored), `models/` (empty, ignored).
- Risk: AURORA.md says `assets/images/manifest.json` "must remain intact", yet `assets/` is untracked → one `git clean -fdx` destroys it.

### 5.2 Commit vs ignore
| Path | Action | Why |
|---|---|---|
| `aurora/motion/switchboard.py`, `aurora/motion/providers/*.py`, `aurora/assets/`, `aurora/monitoring/` | **commit** | Core code only exists locally |
| `backend/` | **commit** | Image switchboard (parallels `src/`; pending architecture decision) |
| `scripts/` | **commit** | Worker bootstrap; strip hard-codes first |
| `src/modules/motion_engine.py`, `streamlit_app.py`, `.agents/skills/*` | **commit** | |
| 10 modified files | **review diff, then commit** | hold `config.yaml` until §3 cleanup |
| `test_motion_routing.py`, `test_wan2gp_handshake.py` | **move into `tests/`**, then commit (the second makes a network call with a dummy key → mark `integration`) | currently stray root scripts |
| `assets/images/manifest.json` | **commit** | required state |
| `assets/images/*.jpg` | ignore (or Git LFS if it's a canonical character reference) | binary growth |
| `aurora.jsonl`, `logs/` | **ignore** | runtime logs |
| `.test_tmp/ .pytest_cache/ .mypy_cache/ __pycache__/` | **ignore** | |
| `outputs/`, `models/` | already ignored | |
| `docs/` | **commit** | |

### 5.3 `.gitignore` gaps and proposed content
Currently ignores only `.mypy_cache/ __pycache__/ *.py[cod] outputs/ models/ ui/node_modules/ ui/dist/ .env .env.* !.env.example`. Missing: `.pytest_cache/`, `.test_tmp/`, `.ruff_cache/`, virtualenvs, `logs/`, `*.jsonl`, `*.log`, model blobs, `shared_mount/`, `third_party/`, root `node_modules/`, OS/IDE files, local config.
```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
gpu_env/
.mypy_cache/
.pytest_cache/
.ruff_cache/
.test_tmp/
*.egg-info/
# Secrets / local config
.env
.env.*
!.env.example
config/local.yaml
# Runtime data
logs/
*.jsonl
*.log
outputs/
models/
shared_mount/
third_party/
*.safetensors
*.ckpt
*.pth
*.gguf
# Assets: keep manifest, ignore generated binaries
assets/images/*
!assets/images/manifest.json
!assets/images/.gitkeep
# Node
node_modules/
ui/node_modules/
ui/dist/
dist/
# OS/IDE
.DS_Store
Thumbs.db
.idea/
.vscode/
```
Extras: `.pre-commit-config.yaml` (gitleaks, ruff, `check-added-large-files --maxkb=1024`); `.gitattributes` (`* text=auto eol=lf`); commit in 3–4 logical commits (motion providers → backend → scripts/UI → config). **Push soon** — GitHub is currently not a backup of any of this work.

---

## 6. Test suite (read-only run)

Env: Windows 11, Python 3.14.0, pytest 9.0.2, run with `-p no:cacheprovider` to avoid the unreadable `.pytest_cache`.
```
$ python -m pytest -q -p no:cacheprovider
......                                                                   [100%]
6 passed in 3.77s
```
- **PASS 6/6**; the fallback command (`tests\test_sprint1.py tests\test_prompt_ledgers.py`) wasn't needed — those are the only two test files.
- Coverage: Sprint 1 only (logger, `VideoResult`, `FFmpegKenBurns`, prompt-ledger). **No tests** for switchboard routing, providers, backend image switchboard, server API, config loading, manifest repair, or `src/` pipeline. FFmpeg is on PATH here; CI must install it.
- Suggested: `pytest.ini` (`testpaths=tests`, markers `integration`, `gpu`), env-isolating fixtures, GitHub Actions (ubuntu, Python 3.12, `requirements/dev.txt`, apt `ffmpeg`).

---

## 7. Remote / mobile access to the locally running UI

Current: FastAPI on `127.0.0.1:8000`, Streamlit default 8501, Vite UI 5173; `scripts/launch.py` binds a child to `0.0.0.0`; CORS `*`; **no auth**. Exposing as-is gives anyone job control and the ability to burn paid API credits → **never port-forward the router**.

### 7.1 Options
| Option | Security | Setup | Phone UX | Cost | Notes |
|---|---|---|---|---|---|
| **A. Tailscale (recommended)** | WireGuard mesh, private to your devices, ACLs | ~10 min | Tailscale app + MagicDNS/HTTPS URL | Free personal tier | Also links PC ↔ GPU box ↔ phone; works behind CGNAT |
| **B. Cloudflare Tunnel + Access** | Outbound-only tunnel; public hostname gated by Access (OTP/SSO) | ~20 min, needs a Cloudflare domain | Plain HTTPS in any browser, no app | Free (Zero Trust ≤50 users) | A tunnel *without* Access is public — don't |
| C. Quick tunnels (ngrok, trycloudflare) | Public random URL, weak | 2 min | Works | Free limits | Demos only |
| D. LAN only (bind 0.0.0.0 + private-profile firewall) | Home Wi-Fi only, no auth | 2 min | Only at home | — | Stopgap; still add bearer token |
| E. VPS + Caddy + WireGuard | Strong, self-operated | hours | Good | VPS | Overkill now |

### 7.2 Tailscale steps (Windows PC + phone)
1. PC: `winget install Tailscale.Tailscale`; phone: install the Tailscale app; sign in with the **same** account (enable 2FA at the identity provider).
2. Admin console → DNS: enable **MagicDNS** and **HTTPS Certificates**.
3. Keep the app on loopback and let Tailscale proxy it (no firewall holes):
   ```powershell
   tailscale serve --bg --https=443 http://127.0.0.1:8000
   tailscale serve status
   ```
   Phone opens `https://<pc-name>.<tailnet>.ts.net/`. Do **not** use `tailscale funnel` (public).
4. Harden: ACL allowing only your phone + PC; disable key expiry on the PC; stop Windows sleeping on AC (`powercfg /change standby-timeout-ac 0`); start the app at logon via Task Scheduler (`pythonw scripts\launch.py`).
5. App: set `AURORA_AUTH_TOKEN`, enforce `Authorization: Bearer` (or trust the `Tailscale-User-Login` header from `tailscale serve`), set `cors_origins` to the exact ts.net origin.
6. Test from the phone with Wi-Fi **off**; add to home screen if the UI ships a PWA manifest.
7. GPU worker: install Tailscale there too; the control plane addresses `http://gpu-box:PORT` (`compute.backends.home_gpu.url`); worker listens on the tailnet interface only, token `AURORA_HOME_GPU_TOKEN`.

### 7.3 Cloudflare Tunnel + Access steps
1. Domain on Cloudflare (free). `winget install Cloudflare.cloudflared`.
2. `cloudflared tunnel login` → `cloudflared tunnel create aurora` → `cloudflared tunnel route dns aurora aurora.<yourdomain>`.
3. `config.yml`:
   ```yaml
   tunnel: <UUID>
   credentials-file: C:\Users\<you>\.cloudflared\<UUID>.json
   ingress:
     - hostname: aurora.<yourdomain>
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```
4. `cloudflared service install` (starts at boot).
5. Zero Trust → Access → Applications → self-hosted app for the hostname; policy "Allow – your email", One-time PIN. **Do this before sharing the URL.**
6. Keep the bearer token on as well. Free-plan proxy upload limit is ~100 MB; serve large video downloads via Tailscale or short-lived signed links.

### 7.4 Pre-enable checklist
- [ ] Auth middleware + non-wildcard CORS merged; `server.host` stays loopback.
- [ ] Rate-limit job-creation endpoints (NIM quota 80 etc.).
- [ ] Download endpoints validate paths (no traversal outside `outputs/`, `assets/`).
- [ ] Unauthenticated minimal `/health` for uptime checks.
- [ ] `server.py:745` disk check made portable; ffmpeg resolved via `shutil.which`.

---

## 8. Prioritised actions
1. Fix `.gitignore` (§5.3), commit untracked code in logical commits, push.
2. Replace `requirements.txt` with the 3-file split (§1.5); add `.python-version` (3.12 control plane / 3.11 worker).
3. Decide LLM path (FreeLLMAPI vs Ollama); make code, AURORA.md and config agree.
4. Introduce `config/aurora.yaml` + env overrides; remove hard-coded `C:/…` paths; resolve ffmpeg via PATH.
5. Load `.env` at startup; refresh `.env.example` (§4.3); add gitleaks.
6. Add auth + restricted CORS; enable Tailscale (§7).
7. Move root `test_*.py` into `tests/`; add CI.
