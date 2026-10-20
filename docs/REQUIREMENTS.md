# Project Aurora v2 — Product Requirements Document (PRD)

| Field | Value |
|---|---|
| Owner | Rupesh Kumar (sole user and decision maker) |
| Author role | Product Manager |
| Status | Draft for review |
| Date | 2026-10-20 |
| Replaces | Requirements in `AURORA.md` (v1, hibernated May 2026) |
| Related docs | `docs/` files from the Architect, ML/GPU infra, DevOps, QA and UI/UX roles |

---

## 1. Background: where v1 stopped

Project Aurora v1 was a "local-first AI video factory" that turned **one still image** into a **9:16 vertical short** (1080x1920, 24fps, 25–30s) for Reels and Shorts. It was hibernated in May 2026 because the PC (Ryzen 5 5600G, 16 GB RAM, no NVIDIA GPU) cannot run video models.

What we found when reviewing the code:

| Finding | Impact |
|---|---|
| All "I2V success" outputs are identical mock files. | No real image-to-video generation has ever worked. |
| Only FFmpeg Ken Burns (pan/zoom on a still image) produces real output. | The only real motion today is camera movement over a still. |
| Real outputs are 5s / 25fps / no audio. | The spec asked for 24fps and 25–30s. The spec was never met. |
| `config.yaml` says "12 GB VRAM, 720x1280", but `AURORA.md` says "no GPU, 1080x1920". | The config and the docs contradict each other. |
| There are two overlapping pipelines (`src/` and `aurora/`), plus `server.py`, `streamlit_app.py`, `backend/` and `ui/`. | Unclear entry point, duplicated logic. |
| `presets.json` hard-codes topic, prompt, fps (6), frames and resolution per preset. | The user cannot freely set parameters. |
| `aurora/pipeline.py --prompt-ledger` can split a long video into segments and stitch them (60s / 1440 frames tested). | **Keep this.** It is the foundation for long videos. |

**What is worth keeping from v1:** the segment ledger (`aurora/prompts/*.json`), the provider switchboard idea, structured JSON logging (Rule 4), retry and fallback (Rule 5), the asset library and manifest (as an *option*, not a rule), and FFmpeg as an always-available CPU fallback.

### 1.1 Reuse map: extend existing code, do not rewrite

v2 is built by **extending** `aurora/`. New modules are added only where nothing exists yet.

| Need in v2 | Reuse | Change needed |
|---|---|---|
| Provider routing, "auto" tier, fallback | **Reuse `aurora/motion/switchboard.py` (`MotionSwitchboard`)** | Add T2I/T2V job types and a cost-aware order. |
| Provider contract and result | **Reuse `aurora/motion/base.py` (`MotionProvider`, `VideoResult`)** | Add width/height/fps/seed/duration fields (not fixed 9:16/5s). |
| Rented-GPU worker (Runpod/Vast/Modal/Kaggle) | **Reuse `aurora/motion/providers/remote_i2v_base.py` (`RemoteI2VProvider` + its auth/timeout/crash errors)** | Add one real HTTP/queue transport. Subclasses already exist. |
| Video models | **Reuse `wan2gp_provider.py`, `svdxt_provider.py`, `framepack_provider.py`, `liveportrait_provider.py`** | Wire them to the real transport and remove mock outputs. |
| CPU fallback and assembly | **Reuse `ffmpeg_kenburns.py`, `ffmpeg_static.py`, `_concat_segments` in `aurora/pipeline.py`** | Make size/fps/duration parameters, not constants. |
| Long videos | **Reuse `aurora/pipeline.py --prompt-ledger` and the `aurora/prompts/*.json` ledger format** | Add per-segment model/camera/seed fields and re-roll of one segment. |
| Logging (Rule 4) | **Reuse `aurora/logs/logger.py` (`StructuredLogger`) and `aurora.jsonl`** | Add the new fields in 9.3. |
| Metrics and cost totals | **Reuse `aurora/monitoring/observability.py` (`TelemetryCollector`)** | Add monthly cost total and cap check. |
| Library and reuse-vs-new | **Reuse `aurora/assets/manifest.py` (`ManifestManager`), `cache_gate.py` (`CacheGate`), `assets/images/manifest.json`** | Make reuse a per-job choice, not forced. |
| Text→image | **Reuse the NVIDIA NIM and ComfyUI image paths (`config.yaml` `providers`, `src/workflows/image_gen.json`)** | Expose aspect ratio, size and seed. |
| API for the UI | **Reuse `aurora/api.py`** (the Architect decides whether `server.py`/`backend/` merge into it) | Add job queue and status endpoints. |
| Web UI (mobile) | **Reuse `ui/` (`index.html`, `main.js`, `style.css`)** | Replace fixed dropdowns with free controls and add a responsive layout. Retire `streamlit_app.py` later. |
| Requirements doc | **Reuse `AURORA.md`** | Update its status and rules so they point to this PRD. Do not keep two sources of truth. |

---

## 2. Vision and goal

**Vision:** A personal AI image and video studio where Rupesh can create **any image or video they imagine**, with full creative control, from their PC or phone.

**Goal of v2:**

- The user decides everything: format, length, style, model, and content. The product imposes **no hard-coded presets, fixed formats or creative filters**.
- Real generation runs on **open-weight models on rented GPU** (pay per minute, no hardware purchase).
- The local PC works as the controller and CPU fallback. It is never required to run heavy models.
- The web UI works fully from a **phone browser**.

**Non-goals (for now):** a multi-user SaaS, a public product, auto-publishing to social media, training custom models from scratch.

---

## 3. Users and use cases

There is one primary user: **Rupesh**, a technical solo creator. Rupesh uses a Windows PC at home and an Android/iOS phone when away. Rupesh makes content for Reels/Shorts/YouTube and personal creative projects (for example, anime-style fight scenes, talking-head explainers, product shots and cinematic clips).

---

## 4. User stories

Priority: **P0** = MVP, **P1** = next phase, **P2** = later.

### 4.1 Creation

| ID | Story | Priority |
|---|---|---|
| US-01 | **Text → Image.** As a user, I type a prompt (and optional negative prompt), pick an aspect ratio and size, and get one or more images. | P0 |
| US-02 | **Text → Video.** As a user, I type a prompt and get a video clip of the duration, fps and aspect ratio I choose. | P0 |
| US-03 | **Image → Video.** As a user, I upload an image (or pick one from my library, or one I just generated) and animate it using a prompt and camera motion. | P0 |
| US-04 | **Video → Video.** As a user, I upload a video and restyle or transform it (style transfer, re-render with a prompt, extend, or upscale). | P1 |
| US-05 | **Long video from a segment ledger.** As a user, I write (or let an LLM draft) a list of segments, each with its own prompt, duration and camera motion. The system generates each segment and stitches them into one video of any total length. | P0 (manual ledger) / P1 (LLM-drafted ledger) |
| US-06 | **Continuity across segments.** As a user, I want characters and scenes to stay consistent between segments, using the last frame of segment N as the start of segment N+1, plus a shared character description and seed. | P1 |
| US-07 | **Re-roll one segment.** As a user, I can regenerate only one bad segment without regenerating the whole video. | P0 |

### 4.2 Audio (all optional)

| ID | Story | Priority |
|---|---|---|
| US-08 | **Voiceover.** As a user, I type a script, pick a voice (or clone my reference voice), and add narration to the video. | P1 |
| US-09 | **Music.** As a user, I add a music track (uploaded or generated) with volume and fade controls. | P1 |
| US-10 | **Lip-sync.** As a user, I make a face in the video speak the voiceover. | P2 |
| US-11 | **Silent by default.** If I add no audio, the output has no audio track (or a silent track). This is not an error. | P0 |

### 4.3 Control, library and mobile

| ID | Story | Priority |
|---|---|---|
| US-12 | **Mobile use.** As a user, I open the app in my phone browser, start a job, close the phone, and later see the result and download it. | P0 |
| US-13 | **Job queue and status.** As a user, I see each job's status (queued / running / done / failed), its provider, its cost so far and its estimated time. | P0 |
| US-14 | **Cost preview.** Before I start a job, I see its estimated cost and time. I can set a per-job and a monthly spending cap. | P0 |
| US-15 | **Library.** As a user, I browse past images and videos with their full settings (prompt, seed, model) and can "remix" any of them. | P0 |
| US-16 | **Saved presets (user-made only).** As a user, I can save my own favourite settings as a preset and reuse them. The product ships with no forced presets. | P1 |
| US-17 | **Reuse or regenerate.** For image→video and long videos, I choose per job whether to reuse an existing image or generate a new one. | P0 |
| US-18 | **GPU on/off control.** As a user, I can start and stop the rented GPU from the UI, so I never pay for an idle GPU by mistake. | P0 |

---

## 5. User-controllable parameters

Every parameter below is set by the user per job. "Default" is only the starting value shown in the UI. Nothing is locked.

| Parameter | Allowed values | Default | Notes |
|---|---|---|---|
| Prompt | Free text | — | Required (except pure video→video upscale). |
| Negative prompt | Free text | Empty | Passed through as-is if the model supports it. |
| Aspect ratio | **Any.** Quick picks: 9:16, 16:9, 1:1, 4:5, 21:9, plus custom W:H | 9:16 | The system snaps to the nearest size the model supports (for example, multiples of 8 or 16) and tells the user the real size used. |
| Resolution | Any width x height up to the chosen model's limit | Model's native size | Option to generate at native size and then upscale to a target (for example, 1080x1920 or 4K). |
| Duration | **Any.** From 1 second to unlimited | 5s single clip | If the duration is longer than the model's maximum clip length, the system splits it into segments automatically and stitches them (see US-05). |
| FPS | Any, for example 8–60 | 24 | Generation fps is the model's native fps. Output fps is reached by frame interpolation (for example, RIFE) or resampling. |
| Seed | Integer, or "random" | Random | The seed used is always saved, so results can be reproduced. |
| Style | Free text, or a user-saved style | None | No built-in style list is required. Optional LoRA selection (P1). |
| Camera motion | None, pan, tilt, zoom in/out, orbit, dolly, crane, handheld shake, or custom text | Model decides | Sent as model controls where supported, and added to the prompt otherwise. |
| Model | A specific model, or **"auto"** | Auto | "Auto" picks the cheapest tier that can do the job (see section 7). |
| Source image | Generate new / reuse from library / upload | Generate new for T2V; required for I2V | Replaces the old forced reuse rule. |
| Number of outputs | 1–N variants | 1 | For images and short clips. |
| Quality / steps | Fast / Balanced / High, or raw steps and CFG | Balanced | Advanced settings are hidden behind an "Advanced" toggle on mobile. |
| Audio | None / voiceover / music / both, plus optional lip-sync | None | |
| Output format | MP4 (H.264), WebM, GIF; PNG/JPG/WebP for images | MP4 / PNG | |

**Rule:** if the user's setting cannot be done exactly (for example, the model only supports 81 frames), the system does the nearest valid thing **and clearly shows what it changed**. It never silently overrides the user.

---

## 6. What gets removed from v1

| v1 behaviour | Why it is removed | v2 replacement |
|---|---|---|
| Fixed 9:16 vertical only (1080x1920 / 720x1280) | Blocks landscape, square and cinematic formats. | Any aspect ratio (section 5). 9:16 is just the default. |
| Fixed 5s clips (and 25–30s as the only "real" target) | Too limiting for both short and long content. | Any duration. Long videos are stitched from segments. |
| Rule 2 "Asset First, Generate Never" (forced reuse, `reuse_existing_asset: true`, `--force-new-image` needed to generate) | Forces old images into new ideas. | Reuse is a per-job **choice** (US-17). The asset library stays as a convenience. |
| Hard-coded preset chains in `presets.json` (fixed topic, prompt, fps 6, frames, resolution) | Hides control and bakes in content choices. | User-made presets only (US-16). Built-in examples may exist, but they are optional and fully editable. |
| Fixed UI dropdowns (for example, "Talking Head" only, "30 Seconds" only, "Cinematic / Animated" mode) | The UI only offers what was hard-coded. | Free parameter controls (section 5). |
| Mock I2V outputs counted as "success" | They hid the fact that nothing worked. | A job only succeeds if a real file is produced and passes checks (section 9.4). |
| "GPU modules never run anywhere but Kaggle/Vast" (Rule 6, strict form) | Too narrow. | GPU work runs on any configured GPU backend. CPU/GPU separation is kept. |

**Kept from v1:** Rule 1 (provider-agnostic LLM through FreeLLMAPI) as the default for LLM tasks such as prompt expansion and drafting ledgers; Rule 3 (cheapest tier first) as the "auto" behaviour; Rule 4 (structured logging); Rule 5 (fail loud, recover gracefully).

---

## 7. Backend tiers

The switchboard routes each job to a backend. In **"auto"** mode it uses the cheapest tier that can do the job. The user can always force a specific tier or model.

| Tier | What it is | What it can do | Role |
|---|---|---|---|
| **T0 — CPU fallback (local)** | FFmpeg on the home PC: Ken Burns pan/zoom, stitching, interpolation, audio mixing, encoding, upscaling (simple) | Motion over still images, assembling final videos | Always available and free. Used for assembly in every job and as the last fallback. It must be **clearly labelled** "still-image motion", not "AI video". |
| **T1 — Self-hosted open-weight models on rented GPU (PRIMARY)** | ComfyUI or a small worker on Runpod / Vast.ai / Modal; Kaggle free GPU for batch and testing | T2I (for example, FLUX-class, SDXL-class), T2V and I2V (for example, Wan 2.x, HunyuanVideo, LTX-Video, FramePack-class), V2V, upscaling, interpolation, TTS, lip-sync | **Main path.** Gives full control: our own models, settings, LoRAs and no provider content filter. Spun up on demand and shut down when idle. |
| **T2 — Hosted APIs (optional)** | For example, Replicate, fal.ai, NVIDIA NIM (already in `config.yaml`), and others | Fast access to models without managing a GPU | Optional add-on. **Each API applies its own content policy** and may refuse prompts. The app must show the provider's refusal message clearly and offer to retry on T1. |

**Auto routing order:** T0 (only if the job is still-image motion or assembly) → Kaggle free (if the job is not urgent) → cheapest rented GPU → hosted API. This keeps v1's Rule 3. The exact models per tier are decided by the ML/GPU infra role.

---

## 8. Content policy

- This is a personal creative tool. The product **does not add its own creative filters, keyword blocklists, or forced presets**. The user decides what to create.
- The only hard limits are legal ones, and the system will not support them:
  - No sexual content involving minors, in any form.
  - No non-consensual intimate imagery of real people, and no deceptive deepfakes of real people (for example, fake statements, fraud or impersonation).
- Hosted APIs (tier T2) have **their own** content policies. These are outside our control.
- The user is responsible for published output. It must follow the rules of the platform it is posted on (Instagram, YouTube, and others) and respect copyright and trademark law. For example, fan content using existing characters such as those in `titan_battle_60s.json` may be limited by the rights holder or the platform.
- Optional (P1): add an "AI-generated" label or metadata to exports, because some platforms require AI disclosure.

---

## 9. Non-functional requirements

### 9.1 Cost targets (rented GPU, approximate, to be checked by ML/GPU infra)

| Output | Target cost |
|---|---|
| 1 image (T1) | ≤ $0.01 |
| 5s video clip, 720p (T1) | ≤ $0.10 |
| 30s video, 720p, stitched (T1) | ≤ $0.75 |
| 60s video, 720p, stitched (T1) | ≤ $1.50 |
| Idle GPU cost | $0 — GPU auto-shuts down after N minutes idle (default 10) |
| Monthly budget | User-set cap (suggested start: $20/month). Jobs stop with a clear message when the cap is reached. |

Every job logs its real `cost_usd`, and the UI shows the running monthly total.

### 9.2 Generation time targets

| Output | Target (warm GPU) |
|---|---|
| 1 image | ≤ 30s |
| 5s clip, 720p | ≤ 5 minutes |
| 30s stitched video, 720p | ≤ 30 minutes |
| Cold start (GPU boot + model load) | ≤ 5 minutes, shown as a separate phase in the UI |
| UI response to any tap | ≤ 1s (jobs run in the background) |

### 9.3 Logging (keep AURORA Rule 4)

- Every module writes JSON lines to `aurora.jsonl` with the mandatory fields: `ts, level, component, event, job_id, provider, mode, tier, image_hash, duration_secs, gen_time_secs, cost_usd, error`.
- New fields for v2: `model`, `seed`, `width`, `height`, `fps`, `segment_id`, `fallback_provider`.
- Each finished output saves a sidecar JSON with all the settings needed to reproduce it.

### 9.4 Reliability: retries and fallback (keep AURORA Rule 5)

- Never silently swallow errors.
- Each provider gets **2 retries**, then the job falls back to the next tier. Every failure logs the provider, the error type and `fallback_provider`.
- In a long video, a failed segment is retried by itself. Completed segments are kept (the job can be resumed).
- **No fake success.** An output only counts as a success if the file exists, has the requested length/fps/size (within tolerance), and is not a duplicate of a placeholder file (check the hash).
- If the system falls back to T0 Ken Burns, the job is marked "fallback: still-image motion" and is not reported as AI video.

### 9.5 Security and secrets

- All API keys and tokens live in `.env` (never in `config.yaml`, code or git). `.env.example` lists every variable with a placeholder.
- The web UI must have a login (at least a password or token) **before** it is reachable from the phone over the internet. Remote access should use a private tunnel (for example, Tailscale or Cloudflare Tunnel), not an open port.
- Generated files are private by default.

### 9.6 Mobile and usability

- The UI works on a 375px-wide phone screen with no horizontal scroll and touch-friendly controls.
- Simple mode (prompt, aspect ratio, duration, Generate) is the default view. Advanced settings are behind a toggle.
- Results can be previewed and downloaded on the phone.
- Optional: installable as a PWA, with a notification when a job finishes (P1).

### 9.7 Maintainability

- One pipeline, one backend and one UI. The duplicate `src/` vs `aurora/` pipelines and the extra UIs (`streamlit_app.py`, `ui/`, `server.py`) are merged into a single path (the Architect role decides which).
- One config file that matches reality (fix the `config.yaml` vs `AURORA.md` contradiction).
- Automated tests run without a GPU, using a mock provider that is **clearly marked as a mock**.

---

## 10. Phases

### MVP (Phase 1) — "Real output, full control"

1. One backend and one mobile-friendly web UI, with a login. *(Reuse `aurora/api.py` and `ui/`.)*
2. T2I and T2V/I2V on **one** rented-GPU provider (T1), plus T0 FFmpeg for assembly and fallback. *(Reuse `RemoteI2VProvider`, `Wan2GPProvider`, the FFmpeg providers and `MotionSwitchboard`.)*
3. All section 5 parameters except audio and V2V: any aspect ratio, resolution, duration, fps, prompt, negative prompt, seed, camera motion, model/auto, reuse vs new image. *(Extend `VideoResult` and the provider arguments.)*
4. Long videos from a manual segment ledger, with per-segment re-roll and stitching. *(Reuse `aurora/pipeline.py --prompt-ledger`.)*
5. Job queue, status, cost estimate, spending cap, GPU auto-shutdown and a library. *(Reuse `TelemetryCollector` and `ManifestManager`.)*
6. Logging (Rule 4), retries and fallback (Rule 5), secrets in `.env`, and no fake success. *(Reuse `StructuredLogger` and `.env.example`.)*
7. Remove the v1 hard-coded items listed in section 6, and update `AURORA.md` to point to this PRD.

### Phase 2

- Voiceover (TTS and voice clone), music, and AI-generated labels.
- Video → video (restyle, extend, upscale).
- LLM-drafted segment ledgers from one idea, and continuity (last frame → next segment).
- User-saved presets and styles, LoRA selection.
- A second GPU provider and hosted APIs (T2) as optional extras.
- PWA install and push notifications.

### Phase 3 (later)

- Lip-sync and talking heads.
- Character consistency tools (reference images, LoRA training).
- Batch jobs and scheduled jobs.
- Optional auto-publish helpers (was deferred in v1; still optional).

---

## 11. Success metrics

| Metric | MVP target |
|---|---|
| Real AI video produced (not mock, not Ken Burns) | ≥ 1 real T2V and 1 real I2V clip per week of use |
| Job success rate (after retries/fallback) | ≥ 90% |
| Parameter fidelity: output matches requested aspect ratio, fps and duration | 100% (or a shown, explained adjustment) |
| Cost per 30s 720p video | ≤ $0.75 |
| Idle GPU spend | $0 (no forgotten running GPU) |
| Jobs started from the phone | ≥ 30% of all jobs |
| Time from idea to first preview (warm GPU, 5s clip) | ≤ 5 minutes |
| Silent errors found in logs | 0 |

---

## 12. Open questions for the user

1. **Monthly budget:** what is the most you want to spend on GPU per month (for example, $10, $20 or $50)?
2. **GPU provider:** do you prefer Runpod, Vast.ai or Modal for the first version? Do you already have an account with any of them?
3. **Main output:** for the first month, is your priority short vertical clips (Reels/Shorts), long videos (60s and more), or images?
4. **Quality vs cost:** is 720p generation plus upscaling acceptable, or do you need native 1080p (slower and more expensive)?
5. **Phone access:** will you use the phone only on home Wi-Fi, or also on mobile data (which needs a secure tunnel)?
6. **Hosted APIs:** do you want hosted APIs (T2) at all, knowing that they apply their own content filters?
7. **Audio:** how soon do you need voiceover in Hindi/English, music, or lip-sync?
8. **Old outputs:** can the old mock files and `outputs/` caches be deleted once v2 works?
9. **Which UI to keep:** do you have a preference between the current `ui/` (web) and `streamlit_app.py`?

---

## Summary (Hinglish)

- **Kya ban rahe hain:** Ek personal AI image + video studio, jisme aap jo chahe bana sakte ho — koi fixed preset, fixed 9:16, ya fixed 5 second ka rule nahi. Aspect ratio, resolution, duration, fps, prompt, seed, style, camera motion, model — sab aap khud choose karoge.
- **Kaise chalega:** Asli generation rented GPU (Runpod/Vast/Modal/Kaggle) pe open-weight models se hoga — yahi main raasta hai. Ghar ka PC sirf controller aur FFmpeg fallback rahega. Hosted APIs optional hain, aur unke apne content filters hote hain.
- **Lambi videos:** Segment ledger se kai clips banake jod denge, aur kharab segment ko alag se dobara bana sakte ho.
- **Mobile se bhi:** Phone browser se job start karo, baad mein result dekho aur download karo. Login aur secure tunnel zaroori hai.
- **Content policy:** App apni taraf se koi creative filter nahi lagata. Sirf legal limits hain — minors ka sexual content nahi, real logon ke non-consensual ya dhokebaaz deepfakes nahi. Publish karte waqt platform rules aur copyright follow karna aapki zimmedari hai.
- **Purana code reuse karenge:** Naya likhne ki jagah `aurora/` ke switchboard, `remote_i2v_base.py`, Wan2GP/FFmpeg providers, prompt ledger, logger, manifest aur `ui/` ko extend karenge (Section 1.1).
- **Pehle MVP:** Ek GPU provider, T2I + T2V + I2V, saare parameters, lambi video stitching, cost cap, GPU auto-shutdown, aur koi fake "success" nahi. Voiceover, music, video→video aur lip-sync baad ke phases mein.
- **Aapse sawaal:** Budget kitna hai, kaunsa GPU provider, aur pehle kya chahiye — Reels, lambi videos, ya images? (Section 12 dekhiye.)
