# Aurora Autopilot (research → create → veto → publish → analytics)

```
nightly:  aurora.autopilot render      research ideas -> render reels (autorun pipeline + media_validator) -> queue
hourly :  aurora.autopilot publish-due uploads items whose publish_at has passed and are not vetoed
daily  :  aurora.autopilot analytics   views/likes/comments written back into the queue items
```

Files: `aurora/autopilot.py`, `aurora/publish/queue.py` (outputs/publish_queue.json), `aurora/publish/youtube.py`,
`scripts/autopilot_task.ps1` (prints the Task Scheduler commands; does not register anything).

## Config (add to config.yaml; nothing is required — defaults shown)
```yaml
autopilot:
  publish:
    approval: veto        # veto = auto-publish after veto_hours unless stopped | manual = needs approve | auto = next slot
    veto_hours: 6
    slots: ["19:00"]      # best posting times, local to `timezone`; one reel per channel per slot
    timezone: Asia/Kolkata
    max_attempts: 3
  channels:
    - {name: facts, niche: "amazing science facts", language: hi, platforms: [youtube], reels_per_day: 1, motion: camera, voice: hi-IN-SwaraNeural}
```
Publish time = max(now + veto_hours, next free slot). Reels beyond the slots of a day roll to the next day.
Each channel has its own token (`outputs/tokens/youtube_<name>.json`, git-ignored) and its queue items are tagged with the channel, so channels never mix.

## Safety rules built in
- Never uploads the same file twice (sha256 per channel; `publishing` state is persisted before the upload).
- AI/synthetic disclosure: every upload sets `status.containsSyntheticMedia: true` (YouTube Data API v3 field, confirmed in the API discovery doc) unless research says otherwise.
- Quota hit (`quotaExceeded`): channel is paused, item deferred 12 h without counting an attempt. Other errors retry hourly, max 3, then `failed`.
- YouTube default quota is 10,000 units/day and one upload costs about 1,600, so about 6 uploads/day/project. Ask for more quota only if needed.
- Missing token -> items stay `scheduled` and nothing is lost; run the consent step.

## One-time setup (YouTube) — do this once per Google project
1. Go to console.cloud.google.com, create a project (e.g. "Aurora").
2. APIs & Services -> Library -> enable **YouTube Data API v3**.
3. APIs & Services -> OAuth consent screen: User type External, fill app name + your email, and under **Test users** add your own Gmail (the one that owns the YouTube channel). Keep it in "Testing" mode. (Tokens of Testing apps expire after 7 days; click "Publish app" to remove that limit; it stays private to you.)
4. Credentials -> Create credentials -> **OAuth client ID** -> type **Desktop app** -> Download JSON, save as e.g. `C:\Rupesh\App\Video creation\secrets\client_secret.json` (keep it out of git).
5. In `.env` add: `YOUTUBE_CLIENT_SECRET=C:\Rupesh\App\Video creation\secrets\client_secret.json`
6. For each channel run: `python -m aurora.publish.youtube auth --channel facts`. A browser opens; sign in, pick that channel's account, Allow. Done; the token is saved and refreshes itself.
7. Unverified apps show "Google hasn't verified this app": click Advanced -> Go to app. Normal for your own project.
8. Videos uploaded by a not-yet-audited API project may be forced **private** by YouTube. If that happens, apply for the YouTube API Services audit (form linked from the API docs) — one-time.

Test without publishing: `python -m aurora.autopilot render --limit 1` then `python -m aurora.autopilot status`; nothing uploads until `publish-due` runs.

## Frontend / server spec (server.py + ui/, owned by Frontend/Backend)
Import `PublishQueue` from `aurora.publish.queue` (thread-safe, file-backed).
| API | Behaviour |
|---|---|
| `GET /api/publish/queue?channel=` | `PublishQueue().list(channel)` — items: id, channel, title, status, publish_at (UTC ISO), error, video_id, stats |
| `POST /api/publish/{id}/veto` | `.veto(id)` — "Stop" button; 400 if already publishing/published |
| `POST /api/publish/{id}/approve` | `.approve(id)` — approves manual items / restores vetoed |
| `POST /api/publish/{id}/reschedule` `{publish_at}` | `.reschedule(id, dt)` |
| `GET /api/publish/health` | per-channel: token file exists (`youtube.token_path(ch).exists()`), count scheduled/failed |
UI "Queue" tab: card per item with video preview (`path`), title, countdown to `publish_at` (show IST), big **Stop** button while `scheduled`, **Restore** when `vetoed`, red banner for `failed`/missing token, and views/likes once published. Auth-protected like the other endpoints. Push notification to the phone when an item is queued is optional (item's `created_at`).

## Instagram / Facebook (design only, no code yet)
- Meta Graph API: needs an Instagram **Business/Creator** account linked to a Facebook Page, a Meta developer app (use dev mode with yourself as tester; no app review needed for your own accounts) and a long-lived token (60 days, refreshable).
- Reels publish = 2 calls: `POST /{ig-user-id}/media` with `media_type=REELS` + `video_url` (must be a **public HTTPS URL**, so the file needs temporary hosting: Cloudflare R2/S3 presigned URL or the tunnel Aurora already uses), poll the container `status_code` until FINISHED, then `POST /{ig-user-id}/media_publish`. Facebook Reels: `/{page-id}/video_reels` start/upload/finish.
- AI label: Meta has no reliable API flag yet for the "AI info" label; put "AI-generated" in caption and enable auto-label on the account.
- Fit: add `aurora/publish/meta.py` with the same `upload(...)` shape and a `platform` field per queue item (queue already stores channel; add `platform`). Reuse the same veto/slot logic.
- Alternative with less code: Metricool API/MCP (paid plan) schedules IG/FB/TikTok/YouTube from one call and handles tokens and hosting. Recommended once a channel earns or reaches ~1k followers; start with direct YouTube (free).
