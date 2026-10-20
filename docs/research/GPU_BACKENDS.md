# GPU Backends & Open-Weight Models: Research for Project Aurora

Author role: ML / GPU Infrastructure Engineer. Date: 2026-10-20.
Scope: compute and model facts for resuming Aurora. This is research only; nothing was signed up for or paid for.

**How to read the evidence.** Every price or capability has a source URL. Tags used:
- **[official]**: read on the vendor's own page or model card on 2026-10-20.
- **[3rd-party]**: aggregator or blog. Treat as indicative and re-check before spending.
- **[ASSUMPTION]**: my arithmetic or estimate, not a measured number.
Marketplace prices (Vast.ai) and many speed figures vary by day and by host.

---

## 0. Executive summary

1. **Primary stack: ComfyUI + Wan 2.2 on Runpod Serverless.** Use the TI2V-5B model on the 24 GB tier as the cheap default. Use Wan 2.2 A14B on the 48 GB and 80 GB tiers for quality. Wan 2.2 is Apache-2.0, has a ComfyUI workflow, and has no provider-side content filter.
2. **Fallback:** Modal (Python-native, $30/month free credit) or a Runpod/Vast pod for batch runs. Hosted APIs (fal, Replicate) are the last tier. They are the easiest to call but have content policies, and they are the least creative-control.
3. **Cost, 5 s clip at 720p:** about **$0.05–0.17** self-hosted on a 4090 with TI2V-5B (derived from the official "under 9 min" figure). Hosted Wan 2.2 A14B on fal is about **$0.40**.
4. **CPU/APU path (exit criterion #1): not viable.** I found no evidence of Wan or HunyuanVideo running usefully on a Ryzen 5 5600G. The AMD/Vulkan image-to-video path in stable-diffusion.cpp has an open crash bug. Keep criterion #1 closed.
5. **Gap to flag:** `runpod/worker-comfyui` documents image output only (`output.images`). Video output is undocumented, so a thin custom handler or an S3 upload is needed (section 7).

---

## 1. Open-weight models

### 1.1 Text → image

| Model | License / commercial use | VRAM | Notes | ComfyUI |
|---|---|---|---|---|
| FLUX.2 [klein] 4B | Apache-2.0, commercial OK | ~8 GB claimed (repo), ~13 GB (model card), sources conflict | Fast, interactive. BFL claims it matches Qwen quality at lower latency (vendor claim). | Yes |
| FLUX.2 [klein] 9B | FLUX non-commercial license | ~24 GB estimate (unconfirmed) | **Not usable commercially.** | Yes |
| SDXL / Z-Image / Qwen-Image | Not verified in this research | not verified | Not researched. Verify licenses before adoption. | Yes (widely used) |

Sources: https://bfl.ai/blog/flux2-klein-towards-interactive-visual-intelligence [official]. VRAM conflict is via https://developer.puter.com/ai/black-forest-labs/flux-2-klein-4b/ [3rd-party].
Recommendation: FLUX.2 klein 4B (Apache-2.0) for the T2I stage if commercial use matters. Aurora also has `LocalComfyUIProvider` and `NvidiaNIMProvider`, so the existing image switchboard can stay as is.

### 1.2 Image → video / text → video

| Model | License | VRAM | Max clip / resolution | Speed (reported) | Quality / notes | ComfyUI |
|---|---|---|---|---|---|---|
| **Wan 2.2 TI2V-5B** | Apache-2.0 | 24 GB with offload flags (official); ComfyUI claims ~8 GB with offload [3rd-party] | 720p (1280×704) at 24 fps; about 5 s | "5 s of 720p in under 9 min" on a single consumer GPU (4090) | The only Wan 2.2 variant that fits 24 GB officially. It does T2V and I2V. | Yes |
| **Wan 2.2 A14B** (I2V/T2V, MoE) | Apache-2.0 | **≥80 GB official** single-GPU; FP8 ≈ 14.3 GB per expert puts it on 24 GB (community); Q5 GGUF reported on 16 GB (community) | 480p and 720p; about 5 s per clip; chain clips for longer | No verified numbers (see §3) | Best open quality. lightx2v distilled models exist for fewer steps. | Yes (also Kijai WanVideoWrapper) |
| **HunyuanVideo 1.5** (8.3B) | `tencent-hunyuan-community` (territory/user-count limits likely; **read the file**) | ≥14 GB with offload; Wan2GP claims 6 GB | 480p and 720p, with 1080p super-resolution; default 121 frames (~5 s at 24 fps) | 480p I2V step-distilled: "within 75 seconds" on a 4090 (frame count not stated) | Strongest option on a single prosumer card. | Yes |
| **LTX-2.3** (22B, audio+video) | LTX-2 Community License (reported: free under $10M ARR; **full text not verified**) | 32 GB documented for full precision (3rd-party); FP8/GGUF smaller | Up to 20 s with native audio; 1080p+ (fal lists 1080p–2160p) | fal hosted: $0.06/s at 1080p | Fast distilled (8-step) variants. Native audio is a plus. Needs a Gemma text encoder. | Yes |
| **FramePack / F1** (13B) | Apache-2.0 | **≥6 GB** | Up to 60 s at 30 fps (1800 frames) | 4090: 2.5 s/frame unoptimized, 1.5 s/frame with TeaCache | Best for **long** clips. TeaCache is "not really lossless". Standalone Gradio app; no official ComfyUI note. | Not documented (standalone) |
| SVD-XT | Stability community license (not re-verified here) | ~12–16 GB | ~25 frames, short | Fast | Old and weak by 2026 standards. Already in Aurora. | Yes |
| CogVideoX, Mochi, Wan 2.5/2.7/3.0, MiniMax H3 | Not researched. Roundups say Wan 3.0 is API-only and MiniMax H3 excludes US/EU/UK. | n/a | n/a | n/a | Treat Wan 3.0 as closed. | n/a |

Sources:
- Wan 2.2: https://github.com/Wan-Video/Wan2.2 [official]; license, TI2V-5B 24 GB, A14B ≥80 GB. Community quantization: https://www.mindstudio.ai/blog/what-is-wan-2-2-video-open-source and https://huggingface.co/Rob1221rib/wan22-fp16-i2v-gguf [3rd-party].
- HunyuanVideo 1.5: https://huggingface.co/tencent/HunyuanVideo-1.5 [official]. ComfyUI: https://docs.comfy.org/tutorials/video/hunyuan/hunyuan-video-1-5.md
- LTX-2.x: https://huggingface.co/Lightricks/LTX-2.3 [official; license id `ltx-2-community-license-agreement`]. The $10M ARR and 32 GB figures come from https://www.opensourceforu.com/2026/01/ltx-2-from-lightricks-delivers-native-4k-audio-video-with-fully-open-weights/ and https://comfy.org/workflows/model/ltx/ [3rd-party]. fal specs: https://fal.ai/ltx-2.3 [official].
- FramePack: https://github.com/lllyasviel/FramePack [official].
- Newer-model roundup: https://presenc.ai/research/best-open-weight-video-generation-models-2026 [3rd-party].

### 1.3 Talking head and lip-sync

| Model | License | Notes |
|---|---|---|
| LivePortrait | Code is permissive, but the **InsightFace detector weights are non-commercial research**. The official recommendation is to replace them for commercial use. | Drives a portrait from a driving video. Already in Aurora. https://ph.end.dedyn.io/u/catch88@sh.itjust.works [3rd-party; verify at the LivePortrait repo] |
| MuseTalk 1.5 | GitHub shows "other"; **terms not verified** | Audio-driven lip sync. Inference and training code and weights are released. |
| SadTalker | **Not verified** | Older and lower quality. |
| InfiniteTalk (on Wan 2.1) | **Not verified** | Audio-driven talking video. ComfyUI workflow exists. Heavy 14B model. https://comfyui-wiki.com/en/models/wan/infinitetalk [3rd-party] |
| Wan 2.2 S2V-14B | Apache-2.0 (same repo) | Needs ≥80 GB. https://github.com/Wan-Video/Wan2.2 [official] |

VRAM, length, and speed for these were not established in this research. Action: before building on LivePortrait for commercial output, read its LICENSE and replace the InsightFace dependency, or accept non-commercial use only.

### 1.4 TTS

| Model | License | Notes |
|---|---|---|
| XTTS-v2 (current Aurora choice) | **CPML, non-commercial.** Coqui shut down in Jan 2024, so there is no commercial license to buy. | Cloning from ~6 s of audio, 17 languages, 4–6 GB VRAM. **Replace it if you monetize.** |
| Chatterbox | MIT | Voice cloning; adds an audio watermark. Best open-license cloner. |
| Kokoro (82M) | Apache-2.0 | No voice cloning, very fast. |
| Piper | MIT | Real-time on CPU (even a Pi 5), no cloning. Best for CPU-only. |
| F5-TTS | Reported CC-BY-NC weights | Non-commercial. |
| MOSS-TTS 1.5 / CosyVoice2 | Apache-2.0 reported (single-source) | Unverified. |

Sources: https://www.promptquorum.com/power-local-llm/local-tts-voice-cloning-piper-coqui-xtts , https://tts.ai/blog/open-source-text-to-speech-guide-2026/ , https://aiweekly.co/alerts/openmoss-ships-tts-v15-with-multi-speaker-cloning [all 3rd-party; verify repos before shipping].
Aurora's Rule 6 says XTTS runs on CPU. The sources I found say XTTS needs 4–6 GB VRAM, so test this claim. Piper or Kokoro is the safe CPU option.

### 1.5 Music

| Model | License | VRAM / speed | ComfyUI |
|---|---|---|---|
| ACE-Step 1.5 | MIT (mirror pages and Gigazine; verify at the official repo) | <4 GB for small variants; XL (4B) ≥12 GB with offload. <2 s per song on an A100, <10 s on an RTX 3090 | Official support |

Sources: https://comfyui-wiki.com/en/news/2026-01-23-ace-step-1-5-music-generation , https://gigazine.net/news/20260204-ace-step-music-generation/ .
MusicGen (reportedly CC-BY-NC), Stable Audio Open, and YuE were **not verified** in this research.

---

## 2. Compute options

### 2.1 Price per GPU-hour by class

| Provider | 24 GB class | 48 GB class | 80 GB class | Billing |
|---|---|---|---|---|
| **Runpod Pods** (Community / Secure) | RTX 4090: $0.34 / $0.89 | L40S: $0.79 / $1.09. A6000: $0.33 / $0.59 | A100 PCIe: $1.19 / $1.79. H100 PCIe: $1.99 / $2.89 | Per hour or per second |
| **Runpod Serverless** (one rate per group) | 4090: $1.10/hr. L4/3090/A5000: $0.69/hr | L40/L40S/6000 Ada: $1.75/hr. A6000/A40: $1.22/hr | A100: $2.72/hr. H100: $4.79/hr | Per second, rounded up; cold start is billed |
| **Modal** (per second) | L4: $0.80/hr. A10: $1.10/hr. (no 4090) | L40S: $1.95/hr | A100 80GB: $2.50/hr. H100: $3.95/hr | Per second, scale to zero |
| **Vast.ai** | Marketplace. 3rd-party range for a 4090: $0.15–0.45/hr | n/a | A100 80GB ~$0.70–2.00/hr (3rd-party range) | Per second; interruptible is "50%+ cheaper" |
| **Replicate** (own hardware rates) | n/a | L40S: $3.51/hr | A100: $5.04/hr. H100: $5.49/hr | Per second, public models only pay for run time |
| **fal serverless compute** (own app) | n/a | n/a | H100: $4.50 list, $2.49 "as low as" | Per hour |
| **Lightning AI** | L4: $0.79/hr (post Sep-2026 reprice, 3rd-party tracker) | n/a | A100 80GB ~$2.71/hr; H100 $4.50/hr (3rd-party) | Credits |
| **Kaggle** | Free T4 or P100 (≈16 GB); no 24 GB | n/a | n/a | Free quota |

Sources:
- Runpod pods and serverless, storage: https://www.runpod.io/pricing and https://docs.runpod.io/serverless/pricing [official; page showed "Updated October 8, 2026"]. Community pod prices come from the page's structured data. Serverless Flex versus Active rates are not shown separately; Active has "discounts available through sales inquiry". The per-second figures are my division of the hourly rate.
- Modal: https://modal.com/pricing [official; per-second rates shown, hourly values are my ×3600 calculation]. Modal region selection costs 1.15–1.75× and non-preemptible costs 3×, so the headline rate is the preemptible, any-region rate. See https://www.spheron.network/blog/modal-gpu-pricing-2026-per-second-billing/ [3rd-party].
- Vast.ai: https://vast.ai/pricing and https://docs.vast.ai/guides/pricing [official; **the pages show no GPU prices**, hosts set prices, so check live at https://cloud.vast.ai/create/ ]. Ranges are from https://costbench.com/software/ai-gpu-cloud/vast-ai/ and https://www.promptquorum.com/prompt-bites/cloud-gpu-cost-per-hour [3rd-party].
- Replicate and fal: https://replicate.com/pricing and https://fal.ai/pricing [official].
- Lightning: https://www.usagepricing.com/blueprint/activity/lightning-ai-2026-09-21-gpu-rate-card-reprice [3rd-party tracker; the free tier reportedly changed to a one-time grant of up to 30 credits; verify at lightning.ai].
- Kaggle: https://gpuperhour.com/blog/free-cloud-gpus-and-credits [3rd-party]. Weekly GPU quota is not published (commonly ~30 h/week); sessions up to 12 h per the docs summary. The official TI2V-5B guidance is 24 GB, and Kaggle's T4/P100 are about 16 GB, so **TI2V-5B at 720p will not fit comfortably on Kaggle**. Use Kaggle for small models only.

### 2.2 Cold start, API, and storage

| Provider | Cold start | API ergonomics | Weight storage |
|---|---|---|---|
| Runpod Serverless | Docs give no figures. Cold start (model load) is billed. Third-party measurements: 10–15 s for later FlashBoot starts, 8–30 s for infrequent endpoints, and ~53 s for a first start of a 7.5 GB model on a 4090 (https://www.premai.io/blog/serverless-llm-deployment-runpod-vs-modal-vs-lambda-2026/ [3rd-party]). A 14B video model will be slower. | HTTP: `/run`, `/runsync`, `/health`, `/status`. Official ComfyUI worker. Request limit ~10 MB on `/run`, 20 MB on `/runsync`. | Network volume $0.07/GB-month (under 1 TB). High-performance $0.14. Container disk $0.10. Mounted at `/runpod-volume` |
| Runpod Pods | Minutes to boot, but no per-request cold start | SSH / ComfyUI UI. Great for experiments. | Volume disk $0.10/GB-month running, $0.20 idle |
| Modal | Not measured in this research | Python decorators; `modal deploy` gives an HTTPS endpoint. Needs no Docker if you use its image builder. | Volumes $0.09/GiB-month (1 TiB/month free per the pricing page footnote) |
| Vast.ai | Instance boot (minutes); no scale-to-zero serverless in the sources I read | Marketplace CLI/API. Host quality varies. | Billed while the instance exists, even stopped |
| Replicate / fal | Not stated on pricing pages | One HTTP call per model. Simplest. | Provider-managed |
| Kaggle | Notebook start | Notebook, no stable HTTP endpoint (needs a tunnel). It is the current Aurora "file-drop" pattern. | Datasets; 12 h session |

### 2.3 Hosted APIs (own content policies)

| Provider | Model and price | Content policy |
|---|---|---|
| fal | Wan 2.2 A14B I2V: $0.08/s at 720p, $0.06/s at 580p, $0.04/s at 480p (16 fps video seconds), lists "Commercial use". LTX-2.3: from $0.06/s at 1080p ($0.04/s fast). https://fal.ai/models/fal-ai/wan/v2.2-a14b/image-to-video , https://fal.ai/ltx-2.3 | https://fal.ai/legal/trust-and-safety : automated CSAM hash matching and OpenAI moderation. Whether a per-model safety-checker toggle exists is **unconfirmed**. |
| Replicate | wavespeedai/wan-2.1-i2v-480p: $0.09/s; 720p: $0.25/s. Wan 2.2 and 2.5–2.7 models exist (https://replicate.com/wan-video) but per-output prices were not shown. | Not shown on the pricing page; check Replicate terms. |

**Policy note.** Self-hosted open weights have **no provider-side content filter**; whatever the prompt asks, the model attempts. Hosted APIs filter and can ban accounts. Self-hosting also means the legal and platform-policy responsibility for outputs (Instagram/YouTube rules, likeness, copyright) sits entirely with the user.

---

## 3. Cost per clip

### Method and caveats
- Cost = (generation seconds ÷ 3600) × $/hr. Cold start adds a flat amount, so batch jobs to reduce it.
- **Only a few speed figures are verified:** TI2V-5B ("under 9 min" for 5 s at 720p on a 4090), HunyuanVideo 1.5 480p I2V distilled (75 s on a 4090), and FramePack (1.5–2.5 s/frame on a 4090). Everything else is [ASSUMPTION].
- Linear scaling to longer clips is [ASSUMPTION]. Wan 2.2 clips are about 5 s, so 10 s and 30 s mean chaining clips (last frame becomes the next input), which brings drift risk.
- 1080p: neither Wan 2.2 5B (720p max) nor FramePack documents native 1080p. The 1080p column below assumes a 720p render plus a separate upscale pass (not costed). LTX-2.3 is the only one with native 1080p, via hosted pricing.

### Combo A: Wan 2.2 TI2V-5B on RTX 4090 (24 GB)
Time basis: 9 min per 5 s at 720p (official upper bound, so costs are ceilings).

| Clip | Gen time (720p) | Runpod Serverless 4090 @ $1.10/hr | Runpod Community Pod 4090 @ $0.34/hr |
|---|---|---|---|
| 5 s | ≤9 min | ≤$0.17 | ≤$0.05 |
| 10 s | ≤18 min [ASSUMPTION] | ≤$0.33 | ≤$0.10 |
| 30 s | ≤54 min [ASSUMPTION] | ≤$0.99 | ≤$0.31 |
| 1080p | not native; render 720p, then upscale | add upscale cost | add upscale cost |

### Combo B: FramePack on RTX 4090 (long clips)
Time basis: 30 fps, 1.5–2.5 s/frame (TeaCache on to off). Resolution not documented in the README I read.

| Clip | Frames | Gen time | Serverless 4090 @ $1.10/hr | Community Pod @ $0.34/hr |
|---|---|---|---|---|
| 5 s | 150 | 3.75–6.25 min | $0.07–0.11 | $0.02–0.04 |
| 10 s | 300 | 7.5–12.5 min | $0.14–0.23 | $0.04–0.07 |
| 30 s | 900 | 22.5–37.5 min | $0.41–0.69 | $0.13–0.21 |

### Combo C: Wan 2.2 A14B on an 80 GB A100, serverless $2.72/hr
**No verified timing exists.** Break-even table, with minutes per 5 s of 720p as the unknown (assumed 4–10 min with a distilled 4-step workflow; unverified):

| Minutes per 5 s clip | 5 s | 10 s | 30 s |
|---|---|---|---|
| 4 min | $0.18 | $0.36 | $1.09 |
| 10 min | $0.45 | $0.91 | $2.72 |

Run one benchmark job before committing to A14B.

### Combo D: HunyuanVideo 1.5 480p on 4090
75 s per clip (frame count not stated, probably ~5 s). Serverless: 75 s × $1.10/3600 ≈ **$0.023** per clip; community pod ≈ $0.007. 720p timing was not found. Cost per second would grow, but I will not guess.

### Hosted comparison (720p unless noted)

| Clip | fal Wan 2.2 A14B @ $0.08/s | Replicate wan-2.1 720p @ $0.25/s | fal LTX-2.3 1080p @ $0.06/s |
|---|---|---|---|
| 5 s | $0.40 | $1.25 | $0.30 |
| 10 s | $0.80 | $2.50 | $0.60 |
| 30 s | $2.40 (chained) | $7.50 (chained) | n/a: 20 s max, so chain two clips ≈ $1.80 |

Note: fal counts video seconds at 16 fps for Wan, so a "5 s" API clip has fewer frames than a 24 fps native clip.
Takeaway: self-hosted TI2V-5B/FramePack on a 4090 is roughly 3–10× cheaper per second than hosted APIs. The price is engineering effort and idle risk.

---

## 4. CPU-only / AMD APU viability (Ryzen 5 5600G, 16 GB RAM) — exit criterion #1

**Verdict: not viable. Do not unpause on this path.**

Evidence:
- A GGUF build of Wan 2.2 TI2V-5B exists for stable-diffusion.cpp (https://huggingface.co/offgrid-ai/Wan2.2-TI2V-5B-GGUF), but I found **no benchmark** on a 5600G, a Vega iGPU, or any CPU-only machine. The few timings shown come from NVIDIA GPUs.
- Bugs: stable-diffusion.cpp issue #2009 reports a division-by-zero crash in image-to-video mode, confirmed on **both ROCm/HIP and Vulkan** builds (https://github.com/leejet/stable-diffusion.cpp/issues/2009). An older Windows Vulkan report failed with a buffer allocation error (https://github.com/leejet/stable-diffusion.cpp/issues/942, from the same search).
- Reasoning [ASSUMPTION]: the 5600G's Vega 7 iGPU shares system RAM, and even the 5B video model plus VAE and text encoder leave little headroom in 16 GB. Even if it ran, the official 24 GB-GPU figure ("under 9 min" per 5 s) implies hours on this APU for one clip. I have no measurement and did not try.
- What does work on CPU: FFmpeg (Mode 1), Piper/Kokoro TTS, light image tasks, and possibly ACE-Step small variants (<4 GB, though CPU speed unverified).

Re-check trigger (cheap): watch the stable-diffusion.cpp issue above, and any Wan2GP or ONNX release that reports iGPU numbers.

---

## 5. Recommendation

### Primary (self-hosted, full creative control)
- **Model:** Wan 2.2 via ComfyUI.
  - Default tier: **TI2V-5B on a 24 GB GPU** (4090-class). Cheapest, Apache-2.0, T2V and I2V.
  - Quality tier: **A14B** on 48/80 GB. Benchmark first; official guidance says ≥80 GB, with FP8/GGUF community workarounds.
  - Long shots: FramePack (up to 60 s, 6 GB minimum), switched in as a second workflow in the same worker.
- **Provider:** **Runpod Serverless** with a network volume for weights.
  - Matches AURORA.md's "Runpod serverless template" criterion.
  - Official ComfyUI worker, scale to zero, per-second billing.
  - A 4090-class worker is $1.10/hr serverless versus $0.34/hr on a Community pod. Use serverless for interactive jobs and a pod for large batches.
- **Serving method:** `runpod/worker-comfyui` (base image) plus Wan nodes and models on a network volume, calling `/run` then `/status`. Output goes to S3 or the volume (see §7).
- **Cheapest-tier-first mapping for Aurora Rule 3:** LOCAL (FFmpeg only) → FREE (Kaggle: LivePortrait, small models only) → CHEAP (Runpod/Vast pods at 4090 Community rates) → PREMIUM (Runpod Serverless 48/80 GB, Modal, then hosted fal/Replicate).

### Fallback
1. **Modal** for a Python-native endpoint: no Docker, `$30/month` free credits on Starter, A100 80GB at $2.50/hr.
2. **Vast.ai interruptible 4090** for cheap batch runs, with a retry-on-preempt wrapper.
3. **fal (Wan 2.2 A14B, LTX-2.3)** only when GPUs are unavailable. It has a content policy and costs more per second.

### Risks to put in the requirements doc
- **Licensing:** Wan 2.2 and FramePack are Apache-2.0. HunyuanVideo and LTX are custom licenses; read them before commercial use. **XTTS-v2 and the LivePortrait/InsightFace weights are non-commercial.** MuseTalk, SadTalker, and MusicGen were not verified.
- **Video output on worker-comfyui** is not documented (§7).
- **Cold start:** model load for a 14B model can take a minute or more; there are no official numbers. Plan for a warm worker (Active) or accept the first-call latency.
- **Speed data:** most per-GPU timings above are unverified. A one-hour benchmark pod ($0.34–$1.79/hr) will settle them cheaply.

---

## 6. What the current code expects

Read from `aurora/motion/providers/remote_i2v_base.py` and `scripts/remote_gpu_worker.py`:
- `RemoteI2VProvider.generate(...)` builds a JSON payload with these fields: `request_id`, `asset_key`, `source_image` (absolute local path), `reference_image_path`, `reference_anchor_asset`, `generation_seed`/`seed`, `identity_lock`, `expected_output`, `response_status_path`, `duration_sec`, `motion_mode: "full_i2v_motion"`, `provider`, `visual_complexity`, `motion_prompt`, `template_name`, `status: "queued"`.
- The base class is an **abstract hook set**: `_submit_remote_job`, `_poll_remote_status`, `_download_remote_artifact`. All three return `None` in the base. Today the providers fall back to a **file-drop** pattern: write the request JSON into `outputs/i2v_requests/` and wait for `<id>_<provider>.mp4` and `<id>_<provider>.json` to appear in `outputs/i2v_responses/`. A sync tool or shared mount must move the files, and `scripts/remote_gpu_worker.py` polls the request dir.
- `_wait_for_remote_completion` polls every 2 s until `wait_for_remote_response_sec` (default 120 s in `config.yaml`). It raises on status `PROVIDER_CRASH` or `FAILED`, and **returns only on status `COMPLETED`**.
- **Mismatch to fix:** `scripts/remote_gpu_worker.py` writes `status: "SUCCESS"` (`STATUS_SUCCESS`), not `COMPLETED`. The file-drop path (`wait_for_response`) does not check for it, but the HTTP-hook path does. Any new HTTP provider must **map remote success to `"COMPLETED"`**, or `_wait_for_remote_completion` will time out.
- `config.yaml` sets `wait_for_remote_response_sec: 120`. A cold Wan job takes several minutes, so this must rise (e.g. 900–1800 s) for the serverless path.
- Error statuses in the worker: `FAILED` and `PROVIDER_CRASH` (OOM maps to crash), with `error`, `error_type`, `exit_code`, `stderr_tail`.

I did not edit any code; the points above go into the architecture doc.

---

## 7. Setup checklist (Runpod Serverless + ComfyUI + Wan 2.2)

Nothing below has been run. Estimated one-time cost is a few dollars of pod time plus volume storage.

**A. Accounts and storage**
1. Create a Runpod account and an API key (user action, not done here). Set the account spend limit low (the default is $80/hr).
2. Create a Network Volume in one region. Size ≈ 150 GB for Wan 2.2 TI2V-5B plus A14B (FP8) plus VAE/text encoders. About $10.50/month at $0.07/GB (under 1 TB).
3. Attach the same volume to a cheap pod (RTX A6000 Community at $0.33/hr or a 4090 at $0.34/hr) and download weights into `/workspace/models/...`. Serverless sees the same volume at `/runpod-volume/models/...`. Folders: `diffusion_models`, `text_encoders`, `vae`, `loras`.
4. Weights to download (confirm exact repo ids in the ComfyUI Wan 2.2 docs: https://docs.comfy.org/tutorials/video/wan/wan2_2): Wan2.2 TI2V-5B, Wan2.2 I2V A14B high/low-noise FP8 files, the Wan VAE, the UMT5 text encoder, and optionally the lightx2v distill LoRAs (https://huggingface.co/lightx2v/Wan2.2-Distill-Models).

**B. Image**
5. `FROM runpod/worker-comfyui:5.11.0-base` (latest release seen in the repo, https://github.com/runpod-workers/worker-comfyui). Then `RUN comfy-node-install <Wan nodes: ComfyUI-WanVideoWrapper, VideoHelperSuite>`. **Bake custom nodes into the image**: the README says network volumes are not suitable for custom nodes.
6. Add a small custom `handler.py` wrapper (or extend the worker) that returns the **mp4**. The stock worker documents only `output.images` as base64 or `s3_url`. Verify in `handler.py` whether it passes video files through; if not, write the mp4 to S3 and return a presigned URL. Note: the worker repo is **AGPL-3.0**; check implications if you modify and distribute it.
7. Optional S3: set `BUCKET_ENDPOINT_URL`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY` on the endpoint.
8. Push the image to a registry, create the Serverless Endpoint: GPU group 24 GB PRO (4090) for TI2V-5B, a separate endpoint on the 80 GB group for A14B. Attach the network volume. Max workers 1–2, idle timeout ≥30 s, FlashBoot on (https://docs.runpod.io/serverless/endpoints/endpoint-configurations#flashboot).

**C. Workflow**
9. Build the I2V workflow in ComfyUI, then **Export (API)**. Parameterize these node inputs: `LoadImage` (the source image), the prompt text, seed, frames, width, height, and fps. Keep one JSON file per mode (`wan22_ti2v5b_720p`, `wan22_a14b_i2v`, `framepack_long`).

**D. Contract**
Request to `POST https://api.runpod.ai/v2/<endpoint_id>/run` with `Authorization: Bearer <RUNPOD_API_KEY>` (from env, never stored in the repo):
```json
{"input": {
  "workflow": { "<API-format ComfyUI graph with placeholders filled>" },
  "images": [{"name": "source.png", "image": "<base64>"}]
}}
```
Request limit is ~10 MB, so downscale or compress the image. Poll `GET /v2/<endpoint_id>/status/<job_id>` until `COMPLETED`, `FAILED`, or timeout.

Mapping to Aurora's contract (what the new provider class must implement):

| Aurora payload field | Remote use |
|---|---|
| `source_image` | read the local file, base64, send as `images[0]` |
| `motion_prompt`, `duration_sec`, `seed` | fill the workflow placeholders. Convert `duration_sec` × fps to frames (Wan wants 4n+1 frames) |
| `expected_output` | download target for the mp4 |
| `response_status_path` | write the status JSON here, using the same keys as `remote_gpu_worker.py` |
| `request_id` | keep as the idempotency key. Note it is not a Runpod job id; store the Runpod `id` in the status JSON |

Status mapping: Runpod `IN_QUEUE` → `QUEUED`, `IN_PROGRESS` → `PROCESSING`, `COMPLETED` → **`COMPLETED`** (not `SUCCESS`), `FAILED`/`TIMED_OUT` → `FAILED`, OOM in `error`/`stderr_tail` → `PROVIDER_CRASH` with `error_type: "CUDA_OOM"`. Include `cost_usd` (from `executionTime` × rate) and `gen_time_secs` so Aurora's JSON logging rule (Rule 4) is satisfied.

**E. Validation before unpausing**
10. Run one 5 s TI2V-5B job end-to-end and record: cold-start time, generation time, VRAM, and cost. Put the numbers into this doc to replace the assumptions in §3.
11. Run one A14B job on the 80 GB tier and the same on a pod. Record time and cost.
12. Check that the provider surfaces a timeout and a crash as `RemoteQueueTimeoutError` and `RemoteWorkerCrashError`, and falls back per Rule 5.
13. Set `wait_for_remote_response_sec` to ≥900 in `config.yaml` when this path is enabled.

---

## 7b. Reuse, don't rebuild

| Need | Reuse | Change |
|---|---|---|
| Remote I2V provider | Subclass `RemoteI2VProvider` in `aurora/motion/providers/remote_i2v_base.py`; the existing `wan2gp_provider.py`, `framepack_provider.py`, `svdxt_provider.py` already use it | Implement only `_submit_remote_job`, `_poll_remote_status`, `_download_remote_artifact` (HTTP to Runpod). Do not add a new framework. |
| Routing / tier order | `aurora/motion/switchboard.py` and Rule 3 in `AURORA.md` | Add the Runpod endpoint as the CHEAP/PREMIUM entry; no new router |
| Config | `motion.i2v` block in `config.yaml` | Add endpoint id and a longer `wait_for_remote_response_sec` |
| Status vocabulary, error types, GPU metrics | `scripts/remote_gpu_worker.py` (`FAILED`, `PROVIDER_CRASH`, `CUDA_OOM`, `GPUMonitor`) | Fix the `SUCCESS` vs `COMPLETED` mismatch in one place |
| Weight download | `scripts/gpu_provision.sh` (already uses `huggingface_hub.snapshot_download` into `MODEL_ROOT`) | Add the Wan 2.2 repos; run it on the pod that fills the network volume |
| Logging | `aurora.jsonl` fields from Rule 4 | Fill `cost_usd` and `gen_time_secs` from Runpod's `executionTime` |
| Image generation | Existing `LocalComfyUIProvider`; same ComfyUI worker can serve it | Add a T2I workflow JSON, no new provider |
| Docs | Fold the final decision into `AURORA.md` (exit criterion #2) | Mark criterion #1 as checked and closed (§4) |
| UI | `ui/` already exists | Expose provider/tier choice there; no new frontend |

## 8. Open questions
- Exact LTX-2 license text ($10M ARR clause) and HunyuanVideo 1.5 license text: not read.
- MuseTalk, SadTalker, MusicGen, Stable Audio Open, YuE licenses: not verified.
- Wan 2.2 A14B timings on A100/H100/4090 with distilled LoRAs: no verified numbers.
- Vast.ai live prices: pages are dynamic; check the console.
- Whether the fal Wan 2.2 endpoint exposes a safety-checker toggle.
- Lightning's current free-tier terms (trackers disagree).
