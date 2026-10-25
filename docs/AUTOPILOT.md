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
4. Credentials -> Create credentials -> **OAuth client ID** -> type **Desktop app** -> Download JSON, save as e.g. `C:\Rupesh\App\Video creation\outputs\tokens\client_secret.json` (outputs/ is git-ignored).
5. In `.env` add: `YOUTUBE_CLIENT_SECRET=C:\Rupesh\App\Video creation\outputs\tokens\client_secret.json`
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

## Instagram Reels (code ready, never posted live in tests)
Add `instagram` to a channel's `platforms:`; `render` then queues one item per platform (own slot + veto, same file, dedupe per platform).
Code: `aurora/publish/instagram.py` (Graph API v21.0: create REELS container -> poll `status_code=FINISHED` -> `media_publish`),
`aurora/publish/hosting.py` (temporary public HTTPS URL), dispatch in `autopilot.publish_due`. Rate-limit errors (about 25 Reels/day) defer 12 h;
missing account/hosting config keeps the item scheduled and retries hourly. Meta has no API flag for the "AI info" label, so the caption gets an
"AI-generated content" line; also switch on Instagram's auto-label in the app.

Why hosting: Meta downloads the video itself from a public HTTPS URL. Aurora uploads the file to a private bucket, gives Meta a presigned URL that expires in 1 h, then deletes the object.
Free option: **Cloudflare R2** (10 GB free, no egress fees). Alternative without hosting code: **Metricool** API (paid plan) which handles hosting + tokens for IG/FB/TikTok/YouTube; say so and an adapter replaces `instagram.py`.

### One-time setup (Instagram) — user steps
1. Convert the Instagram account to **Professional (Business or Creator)**: Instagram app -> Settings -> Account type and tools.
2. Create a **Facebook Page** and link it: Instagram -> Edit profile -> Page (or Page settings -> Linked accounts).
3. Go to developers.facebook.com -> My Apps -> Create app (type **Business**). Add the product **Instagram Graph API** (Facebook Login for Business). Keep the app in **Development** mode; you are admin, so no App Review is needed for your own accounts.
4. Tools -> **Graph API Explorer**: select your app, add permissions `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, then Generate Access Token and pick your Page + Instagram account.
5. Make it long-lived (60 days): Tools -> Access Token Debugger -> "Extend Access Token" (or call `GET /oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN`). Diary reminder: renew every ~50 days (a Page token derived from a long-lived user token does not expire; prefer that: `GET /me/accounts` and use the Page's `access_token`).
6. Find your IG user id: `GET /me/accounts?fields=instagram_business_account` -> `instagram_business_account.id`.
7. Save `C:\Rupesh\App\Video creation\outputs\tokens\instagram_<channel>.json` as `{"ig_user_id": "1784...", "access_token": "EAAG..."}`.
8. Cloudflare R2: dash.cloudflare.com -> R2 -> create bucket (e.g. `aurora-tmp`, keep **private**), add a lifecycle rule "delete objects after 1 day" as a backstop; R2 -> Manage API tokens -> Object Read & Write for that bucket. Put in `.env`:
   `HOST_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com`, `HOST_BUCKET=aurora-tmp`, `HOST_ACCESS_KEY=...`, `HOST_SECRET_KEY=...`
9. Test safely: `python -m aurora.autopilot render --limit 1`, then `python -m aurora.autopilot publish-due --dry-run --at <future ISO>` shows the exact caption it would post.
Facebook Reels (`/{page-id}/video_reels`) is not built; same hosting + queue would apply.

## Series / comic titles
If an idea carries `series`, `part`, `hook`, `series_hashtags` (comic format), the title becomes "<Series> – Part N: <hook>", series hashtags + the AI-generated line go in the description, and the platform AI flag stays on.

## Research wiring and dry run
- `render` calls `aurora.research.research_ideas` (Gemini free tier, needs `GEMINI_API_KEY`). Scene 1's image prompt makes the image; hook + all scene voiceovers become the voiceover/captions; title/description/hashtags/AI-flag go to the queue item. With no key (or research returning only the niche-text fallback) it uses `topics.txt` and autorun's own prompt step (which waits ~2 min if Ollama is down).
- `python -m aurora.autopilot publish-due --dry-run [--at ISO_UTC]` logs exactly what would be uploaded (title, tags, privacy, containsSyntheticMedia) with no auth, no upload, and the queue untouched.

## Shinobi Mindset channel (@ShinobiMindset1), playlists, one-off publish
- Profile `shinobi` (config.yaml) has `expected_handle: "@ShinobiMindset1"`. Its Google account differs from `main`, so it has its own token `outputs/tokens/youtube_shinobi.json`. Cast: `assets/characters/shinobi` is a copy of main's locked cast (Kaelen); side characters saved later are per channel.
- Scopes now include `youtube.force-ssl` (playlists). An old token is not usable: uploads stay queued and log "re-run auth". Run `python -m aurora.publish.youtube auth --channel shinobi` while signed in to the Google account that owns @ShinobiMindset1 (Brand-account channels: pick the channel in the chooser). After consent the tool reads `channels.list(mine=true)`; a different channel deletes the token and exits with "WRONG CHANNEL". Uploads re-check the handle every time and refuse on mismatch.
- Series videos (title "<Series> – Part N") are added to a playlist named after the series; created on first use, public only when the video is public (else private, upgraded to public when a public video joins). Ids are cached in `outputs/publish_state/<channel>_playlists.json`; adding is idempotent. A playlist failure never un-publishes the video (`playlist_error` on the item).
- One-off publish (kill-switch stays false): `python -m aurora.autopilot publish-one <queue_id> --privacy private --i-confirm`. Refuses without `--i-confirm`; never re-uploads a published item; the item is marked published/failed like normal.

## YouTube manager (plan: docs/plans/youtube-manager.md, approved)
Modules: `publish/seo.py` (competitor search + Gemini title options + best pick + description with hook/recap/keywords/chapters/hashtags/AI note),
`publish/comments.py`, `publish/analytics.py`, `publish/quota.py`; captions/thumbnails/playlist order/branding helpers live in `publish/youtube.py`.
- **SEO** is on per channel with `seo: true` (shinobi). search.list costs 100 units; no Gemini key or any failure keeps the plain title (nothing is invented).
- **After each upload** (publish-due and publish-one): SRT captions if the sidecar has `captions_srt` (path or SRT text), thumbnail only for videos of 3 min or more (`thumbnail` path in the sidecar; Shorts use an auto frame), series playlist with the part at position part-1. Each step fails on its own and is recorded on the queue item (`captions_error`, `thumbnail_error`, `playlist_error`); the video stays published.
- **Quota budget**: `outputs/publish_state/quota_<date>.json`, safety limit 9000 of 10000 units per Pacific day. An upload that does not fit is deferred, not failed. Costs: upload 1600, captions 400, search 100, thumbnail/playlist/comment writes 50.
- **Comments** (`autopilot comments [--channel]`): spam goes to heldForReview, other comments get a reply DRAFT. Nothing is posted until you approve: `comment-approve <id> --channel shinobi [--reply "text"]`, then the next `comments` run sends approved replies once. Links are stripped from replies.
- **Branding**: `youtube.draft_branding()` writes text for you to review in `outputs/publish_state/<channel>_branding_draft.json`; `apply_branding()` is never called automatically.
- **Analytics**: `autopilot weekly-report` writes `outputs/publish_state/reports/<channel>_<date>.md/.json` (views, watch minutes, average % viewed, subscribers, top 5, one insight) and `<channel>_winners.json`; research can read it with `aurora.publish.analytics.load_winners(channel)`. Needs the extra scope `yt-analytics.readonly`: run `python -m aurora.publish.youtube auth --channel <name>` once more (uploads keep working on old tokens meanwhile). Impressions and click-through rate are not available through any API.
- `autopilot approvals [--channel]` prints everything waiting for you (queue items, comment drafts, latest report) as JSON; this is what the Approvals tab shows.

### Approvals tab spec (Frontend / server.py)
Use `aurora.autopilot.approvals_summary(queue, channels)`, `aurora.publish.comments` and `aurora.publish.analytics`.
| API | Behaviour |
|---|---|
| `GET /api/approvals?channel=` | `approvals_summary(...)`: `{queue: [...], comment_drafts: {channel: [{id, author, text, reply, video_id}]}, weekly_report: {channel: {...}}}` |
| `POST /api/publish/{id}/veto` / `approve` | as in the queue spec above |
| `POST /api/comments/{channel}/{comment_id}/approve` `{reply?}` | `comments.approve(channel, id, reply)` (user may edit the text) |
| `POST /api/comments/{channel}/{comment_id}/reject` | `comments.reject(channel, id)` |
| `GET /api/reports/{channel}/latest` | `analytics.latest_report(channel)`; Markdown at `outputs/publish_state/reports/*.md` |
Phone layout: three sections (Videos waiting: preview + Stop/Approve; Comment replies: comment, editable draft, Send/Skip; Weekly report: 4 numbers + top 3 + insight). Sending approved replies and the report are done by the scheduled `comments` / `weekly-report` runs, or add a "Send now" button that calls `run_comments`.

## Go-live: upload-ahead, cadence, scheduler, daily report (plan: docs/plans/upload-ahead.md)
- **Upload-ahead** (`autopilot.publish.upload_ahead: true`): once an item's veto window (6 h) has passed, `publish-due` uploads it right away as `private` + `publishAt=<its slot>`; YouTube makes it public at the slot even if the PC is off. Status `uploaded_scheduled` (video id stored); the next `publish-due` confirms it went public (`reconcile`) -> `published`, or flags "still private an hour after its slot". A slot already in the past is published public immediately. Captions, SEO, thumbnail (long) and the series playlist (public, with the part at its position) are added at upload time. Instagram cannot schedule ahead: it still posts when the PC is on at or after the slot.
- **Veto after upload**: always use `autopilot.veto_item` / `autopilot approve|veto <id>` (they call `videos.update` to drop `publishAt`, keeping the video private, then mark it vetoed; `approve_item` re-schedules it without re-uploading). `queue.veto` refuses uploaded items on purpose.
- **Per-channel switches**: `publish_enabled` / `render_enabled` on each channel; the global `autopilot.publish.enabled` stays false. Now: `shinobi` publishes, `main` is neither rendered nor published.
- **Cadence (shinobi)**: `reels_per_day: 2` at slots 12:30 and 18:30 IST, plus one long episode per week (Saturday 18:00 IST, `long_episode:`), made by `aurora.episode` with whatever NIM cap is left after the Shorts (the episode resumes across runs; its Shorts are queued too). Each render run only fills the day's deficit (`shorts_needed`), so 3 runs a day make 2 reels. YouTube quota is budgeted (9000/10000 units per day); an upload that does not fit is deferred.
- **Scheduler** (`scripts/autopilot_task.ps1`): "Aurora Render" at logon + 11:00 + 15:00 and "Aurora Publish" at logon + hourly 09:00-23:00; hidden, StartWhenAvailable (a run missed while the PC was off starts when it is on), logs in `logs/autopilot_render.log` / `logs/autopilot_publish.log`. `-Register` creates them, `-Unregister` removes them; without a switch it only prints.
- **Daily report**: every `publish-due` run (and `autopilot daily-report`) rewrites `outputs/reports/<date>.md`: rendered today, uploaded/published today, scheduled on YouTube, waiting for the veto window, failures/problems (render, research, quota, auth), NIM credits and YouTube quota used.
- **Logs are UTF-8**: the app itself mirrors every `render` / `publish-due` / `analytics` / `comments` / `weekly-report` / `daily-report` / `publish-one` run into `logs/autopilot_<cmd>.utf8.log` (works with the tasks as already registered, no re-register needed). The older `logs/autopilot_render.log` / `autopilot_publish.log` written by PowerShell `*>>` are UTF-16: read the `.utf8.log` files instead. New registrations (`-Register`) log through `Out-File -Encoding utf8` to `*.task.log`.
