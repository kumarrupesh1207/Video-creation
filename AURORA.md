# PROJECT AURORA - AI Video Factory (HIBERNATED)
## Machine: AMD Ryzen 5 5600G . 16 GB RAM . No Dedicated GPU
## Status: Suspended / On Hold (May 2026) . Core Data Rails Complete

---

## THE ACTIVE BLOCKER (WHY THE PROJECT IS PAUSED)
Local rendering of generative AI video (Wan 2.1 / SVD-XT / HunyuanVideo-class models) is physically impractical on this machine due to the lack of a dedicated NVIDIA GPU (0GB VRAM).

The pipeline structure is operational, passes data contracts, and can run multi-shot segment compilation via `pipeline.py`. However, execution currently falls back to local `FFmpegKenBurns`, which can only animate a still image with camera motion. It cannot synthesize new fight frames from text prompts. A lightweight, zero-VRAM remote automation wrapper has not yet been established.

---

## EXIT CRITERIA TO UNPAUSE THE PROJECT
Do not resume active development until one of the following technical conditions is met:

1. A local execution breakthrough occurs: a major compression framework, such as extreme 1-bit or 2-bit quantization or an optimized ONNX CPU runtime, allows high-tier video models to run directly on an AMD Ryzen 5 5600G APU at acceptable frame generation speeds.
2. A unified cloud container is provisioned: a cheap, stable, programmatic API wrapper or pre-built endpoint script becomes available via a serverless platform, such as a ready-to-use Replicate, Modal, Runpod, or Vast.ai template, that eliminates manual multi-stage polling overhead.
3. Hardware infrastructure is upgraded: an external or dedicated NVIDIA GPU with at least 12GB to 16GB of VRAM is introduced to the local hardware assembly.

---

## WHERE THE CODE WAS LEFT OFF
- Data Integrity Layer: stable. `ManifestManager` implements schema tracking and automatic repair behavior for legacy manifest records.
- Image Switchboard: parameter alignment exists across `NvidiaNIMProvider` and `LocalComfyUIProvider` paths to reduce cache key drift.
- Pipeline Orchestration: `pipeline.py` supports multi-segment prompt ledgers through `--prompt-ledger`. It processed the 60-second `titan_battle_60s.json` exposure sheet, managed three time blocks, generated intermediate segments, and stitched them into a 1440-frame master MP4 container.
- Current Render Reality: local output is an FFmpeg still-image motion render only. True I2V/T2V generation requires a live GPU-backed provider.

---

## WHAT THIS PROJECT IS
Aurora is a local-first AI video factory that converts a single still image into short-form vertical videos (1080x1920, 24fps, 25-30s) for Instagram Reels and YouTube Shorts.

It has THREE motion modes:
- Mode 1: Static Cinematic - FFmpeg/HyperFrames, CPU-only, free, always available
- Mode 2: Talking Motion - LivePortrait/SadTalker, Kaggle P100 free tier
- Mode 3: Full I2V Motion - SVD-XT/Wan2GP, Kaggle/Vast.ai

---

## CORE ARCHITECTURE RULES - NEVER VIOLATE THESE

### Rule 1 - Provider Agnostic LLM
ALL LLM calls use FreeLLMAPI proxy. NEVER hardcode a provider.
  base_url = "http://localhost:3001/v1"
  api_key  = env("FREELLMAPI_KEY")
  model    = "auto"

### Rule 2 - Asset First, Generate Never
ALWAYS check asset library before triggering image generation. A new image credit is spent ONLY when `--force-new-image` is passed explicitly. One image = unlimited video variants.

### Rule 3 - Cheapest Tier First
Switchboard priority order: LOCAL -> FREE (Kaggle) -> CHEAP (Vast.ai) -> PREMIUM (Modal/Replicate). Never skip tiers.

### Rule 4 - Structured Logging Always
Every module logs JSON to `aurora.jsonl` with these mandatory fields:
  ts, level, component, event, job_id, provider, mode, tier,
  image_hash, duration_secs, gen_time_secs, cost_usd, error

### Rule 5 - Fail Loud, Recover Gracefully
Never silently swallow errors. Log everything. Each provider has 2 retry attempts before fallback. All failures are logged with provider name, error type, and fallback_provider.

### Rule 6 - CPU vs GPU Separation
CPU-safe modules: FFmpeg, MiDaS depth, XTTS-v2 TTS, Ollama/FreeLLMAPI
GPU-required: LivePortrait, SadTalker, SVD-XT, Wan2GP, MuseTalk
GPU modules NEVER run locally. They run via Kaggle API or Vast.ai.

---

## HIBERNATION CHECKLIST STATUS
- Dependencies frozen in `requirements.txt`.
- Secret placeholders documented in `.env.example`.
- Generated MP4 caches under `outputs/raw/` and `outputs/final/` may be deleted for disk recovery.
- `assets/images/manifest.json` must remain intact.

---

## MONITOR TO RESUME
- Hugging Face / GitHub: watch for `CpuOptimization`, `GGML`, `GGUF`, ONNX CPU runtime, Wan2.1, SVD-XT, and HunyuanVideo video-generation breakthroughs.
- Pinokio Ecosystem: watch for a working "Wan2.1 CPU-Optimized Wrapper", "Serverless Video Pipeline Link", or equivalent one-click remote video pipeline.
- Cloud templates: watch Replicate, Modal, Runpod, Vast.ai, and Kaggle for stable image-to-video templates with clear API contracts.

---

## COMPLETED BEFORE HIBERNATION
- Sprint 1 foundation: `StructuredLogger`, `VideoResult`, `MotionProvider` ABC extension, `FFmpegKenBurns`, and `pipeline.py` single-shot render path.
- Prompt ledger integration: `aurora/prompts/titan_battle_60s.json` and `--prompt-ledger` multi-segment route.
- Validation command at hibernation: `python -m pytest tests\test_sprint1.py tests\test_prompt_ledgers.py -q`

---

## DEFERRED - DO NOT BUILD WHILE HIBERNATED
- LoRA / DreamBooth character training
- HunyuanVideo local execution
- Kubernetes / Docker orchestration
- Real-time subtitle generation
- Multi-character scene automation
- Auto-publishing to Instagram/YouTube
- CogVideoX-5B full precision
- AirLLM

---

## KEY DECISIONS ALREADY MADE
- FreeLLMAPI remains the universal LLM proxy.
- One source image should produce unlimited video variants.
- Cheapest tier first: local CPU fallback before free/cheap/premium cloud escalation.
- XTTS-v2 is CPU-side only.
- Depth maps are cacheable permanent assets.
- The viable future path is hybrid local CPU plus cloud GPU, unless local CPU video inference has a major breakthrough.
