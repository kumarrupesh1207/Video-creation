# CLAUDE.md — Project Aurora

## What this is
Local AI video factory for vertical reels (YouTube Shorts / Instagram). Image (NVIDIA NIM or local ComfyUI)
→ motion (FFmpeg camera, free Kaggle Wan AI motion, optional paid Google Veo, or a motion-comic of panels)
→ voice (Edge-TTS / XTTS / Piper), music (MusicGen), captions → validated MP4 → autopilot publish queue.
Runs on a CPU-only Windows PC (Ryzen 5 5600G, 16 GB, no NVIDIA GPU). History/status: `AURORA.md`.

## Folder map
- `aurora/` — main engine: `pipeline.py` (reel render, AI-motion path), `autorun.py` (topics → reels),
  `autopilot.py` (research → render → queue → publish), `comic.py` (motion comics), `research.py` (Gemini ideas),
  `audio.py` (voice/music/mix), `costs.py`, `motion/` (switchboard + providers: ffmpeg, kaggle, google…),
  `publish/` (YouTube/Instagram uploaders, veto queue), `monitoring/media_validator.py`, `assets/`, `logs/`.
- `backend/app/` — image providers (`nvidia_nim.py`, `local_comfyui.py`) + `services/image_switchboard.py`.
- `src/` — legacy pipeline; still used: `modules/comfy_orchestrator.py`, `workflows/*.json` (ComfyUI graphs).
- `server.py` — FastAPI on :8000 (`/api/create`, `/api/jobs`, uploads, publish queue, login).
- `ui/` — Vite mobile UI on :5173. `scripts/` — start/stop, Kaggle worker, autopilot task, TTS setup.
- `tests/` — pytest suite. `docs/` — see index below. `outputs/` — all generated media/tokens (git-ignored).

## How to run
- Start everything: double-click `start_aurora.bat` (ComfyUI :8188, API :8000, UI :5173, supervised).
  Stop: `stop_aurora.bat`. Logs: `logs/supervisor.log`, `logs/<svc>.log`.
- Tests: `python -m pytest -q -p no:cacheprovider`
- Reels from topics: `python -m aurora.autorun --topics topics.txt [--motion camera|ai] [--voiceover] [--music auto]`
- Autopilot: `python -m aurora.autopilot render --channel main --limit 1`, `status`, `veto <id>`,
  `publish-due --dry-run`. YouTube consent: `python -m aurora.publish.youtube auth --channel <name>`.
- Long episode (16:9, ~45 panels, 3 acts) + Shorts: `python -m aurora.episode --channel main [--shorts 4]` — resumable: generates panels under the
  daily NIM cap (25/day, ComfyUI off), saves progress in `outputs/episodes/<slug>/`, renders when complete. Never uploads.
- Offline voice (XTTS) setup: `scripts/setup_tts.ps1` (separate `.venv-tts`, Python 3.11).
- Secrets live in `.env` (see `.env.example`); never print or commit them.

## Rules (each confirmed in code/commits)
- Tests never call live Gemini: `tests/conftest.py` autouse fixture removes `GEMINI_API_KEY` (7adc20c).
- ComfyUI `image_gen*` / `img2img*` workflows need a long CPU timeout (3600 s) — `comfy_orchestrator.py` (65e825f).
- Comic `image_mode: nim` never touches ComfyUI (`comic.nim_comfy_fallback: false`); optional ComfyUI use is preflighted (`/system_stats`) and skipped with a warning, never a crash.
- Comic `image_mode: comfy` never falls back to NIM; any NIM fallback must be explicit, capped and logged (65e825f).
- Autopilot publish kill-switch `autopilot.publish.enabled` stays `false` until the user says go (ecf6a44).
- Every upload carries the AI/synthetic disclosure (`containsSyntheticMedia: true`) — docs/AUTOPILOT.md.
- Never stop or restart ComfyUI while its `/queue` has running/pending jobs; `stop_aurora.bat` enforces this.
- Supervisor must run detached (WMI), never as a child of the launching shell (98c7c37).
- Kaggle chained segments run in child processes of the worker; in-process loops leak GPU/RAM (ae7cf81).
- When mixing voice over music, pad the voice (`apad`) before `sidechaincompress` or the music cuts out (2a57670).
- Episode panels are never regenerated and the daily NIM cap is never exceeded; a paused episode resumes with the same command (`aurora/episode.py`).
- Comic panel prompts forbid image-drawn text; final encodes stay phone-sized (~≤10 Mbps) (56f21ce).
- Write files as UTF-8 with the Write/Edit tools — PowerShell `>`/Out-File wrote UTF-16 NULs into a test (daf2fc0).
- Tokens/client secrets live in git-ignored `outputs/tokens/` (c16483c).
- Stories are Series -> Chapter N -> Part M, >= 10 parts per chapter (`comic.chapter_parts`); the finished chapter is compiled to ONE 16:9 video from cached parts, 0 NIM credits (`aurora/chapter.py`); the weekly `aurora.episode` long video is off.
- Stories are CONTENT, not code: a new series, chapter or character must never need a code change. Story packs (series meta, characters, chapter scripts, world rules) are data files read by a generic engine and validated before render (user requirement, 2026-10-25; see docs/STORY_PACKS.md once built).
- VEER (manga series, `aurora/manga.py`): docs/stories/veer respect charter + style guide are hard rules (`manga.check_charter`); never queue/publish it until the user says go; it only renders with NIM credits left after Echoes (`reserve_credits`).
- Core architecture rules (cheapest tier first, structured JSON logs, fail loud) — `AURORA.md` "CORE ARCHITECTURE RULES".

## Workflow (Compound loop)
1. **PLAN** — for any big change, first read this file + `docs/solutions/`; write `docs/plans/<name>.md`
   (template in `docs/plans/README.md`) and get the user's approval before coding.
2. **WORK** — follow the plan; after each step run the related tests (full suite before finishing).
3. **REVIEW** — run `/code-review medium` on the diff and fix what it finds.
4. **COMPOUND** — record what was learned in `docs/solutions/` (update an existing note instead of adding a
   duplicate) and add a one-line rule above if it applies every time.

## Docs index
- `AURORA.md` — project status, core architecture rules, how to start servers.
- `docs/REQUIREMENTS.md` — product requirements (full creative control, phases).
- `docs/ARCHITECTURE.md` — target architecture, interfaces, migration plan, ADRs.
- `docs/AUTOPILOT.md` — autopilot config, safety rules, YouTube/Instagram one-time setup.
- `docs/UI_SPEC.md` — mobile-first UI spec.
- `docs/TEST_PLAN.md` — test strategy and acceptance criteria.
- `docs/SETUP_AUDIT.md` — dependencies/config/secrets audit.
- `docs/PERF_BASELINE.md` — performance before/after numbers.
- `docs/research/GPU_BACKENDS.md` — models, GPU providers, cost per clip.
- `docs/solutions/` — notes on solved problems (index in its README).
- `docs/plans/` — plans for upcoming work (template in its README).
