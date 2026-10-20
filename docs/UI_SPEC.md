# Project Aurora — UI/UX Spec (single mobile-first web UI)

Status: draft v1 · Author: UI/UX Designer session · Date: 2026-10-20
Replaces: `ui/` (index.html, main.js, style.css), `streamlit_app.py`, server-rendered pages in `server.py`.
`docs/ARCHITECTURE.md` did not exist when this was written — the endpoint list in §12 is a *proposal*; reconcile with the architect's doc.

## 0. Design principles

1. **Phone first.** Designed at 360–430 px wide, one hand, thumb zone. Desktop = same screens with more columns.
2. **Total creative freedom.** Every creative parameter is a free field with sensible *defaults*, never a fixed list. No hard-coded presets. Presets exist only as **user-saved templates**.
3. **Money and time are always visible.** GPU jobs are slow and cost money. Every submit shows an estimate *before* the run; every running job shows spend so far; a budget cap is always one tap away.
4. **Async by default.** Submitting never blocks. The user can close the phone and come back; the queue is the source of truth (server-side), not the browser.
5. **Honest states.** Every screen defines empty / loading / error / success. Every error says what happened and what to do next.
6. **Hinglish-friendly.** UI strings in English with i18n keys so Hindi/Hinglish labels can be added; prompt inputs accept Devanagari and Roman Hindi (UTF-8 everywhere, no prompt clipping).

## 0.1 Reuse map (extend, don't rewrite)

| Need | Reuse | Change |
|---|---|---|
| Frontend build | `ui/` (Vite already installed), `ui/style.css` tokens/dark theme | add Preact + `vite-plugin-pwa`; port screens |
| Progress/log streaming | `/api/logs/stream` SSE in `server.py` | keep; add multiplexed `/api/events` on top |
| Jobs, assets, status, providers | existing `/api/jobs`, `/api/assets`, `/api/status`, `/api/providers/status`, `/api/smoke-test` | extend fields (cost, provider, tier, params) |
| Provider routing ("Auto (cheapest)", tier) | `aurora/motion/switchboard.py` (`MotionSwitchboard`) | expose its choice via `/api/jobs/estimate` + `/api/capabilities` |
| Remote GPU errors → UI error states | `remote_i2v_base.py` (`RemoteAuthenticationError`, `RemoteQueueTimeoutError`, `RemoteWorkerCrashError`) | map to error codes/messages in §5.5 / §12 |
| Provider list in Settings | `aurora/motion/providers/*` (wan2gp, framepack, svdxt, liveportrait) | provider rows come from registry, not hard-coded |
| Settings/budget storage | `config.yaml` | add budget keys; keys stay server-side |
| Streamlit sidebar metrics | `streamlit_app.py` (copy metric names only) | becomes Settings status card, then delete file |
| Project docs | `AURORA.md`, `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md` | link, don't duplicate |

## 1. Review of existing UIs

### 1.1 `ui/` (vanilla HTML/JS, ~340 + 294 + 244 lines)
**Works / keep**
- Live pipeline-progress view with stage list and log stream (SSE `/api/logs/stream`).
- Status polling pattern and a system-status block (backend online, provider health).
- Dark theme, big primary "Start" button.
- Job details + outputs content — right content, wrong layout.

**Drop**
- Desktop-only multi-column dashboard; unusable at 360 px.
- Hard-coded `<select>` options: Content Type "Talking Head", Duration "30 Seconds (25-35s)", Mode "Cinematic/Animated". This is exactly the preset-lock the new goal removes.
- "Pause / Stop / Open Output Folder": opening a server folder is meaningless remotely (`/api/open-output`); Pause/Stop are global, not per-job.
- Decorative controls (video player chrome with static buttons, "View Logs" without behaviour).
- No cost, no queue, no auth, no library reuse.
- Numbered-section layout ("1. CREATE NEW PROJECT … 6. OUTPUTS") assumes one job at a time.

### 1.2 `streamlit_app.py` (385 lines)
**Works / keep**
- Sidebar "System Pulse": backend heartbeat, provider quota %, asset count, cache hits / "credit savings" → Settings status card and spend chip.
- Single Topic vs **Batch Upload** tabs → keep the capability as "variations / queue several" in Create.
- Recent Jobs, Asset Gallery, Batch Status → map to Queue, Library, grouped jobs.
- Smoke-test button → Settings ("Test provider").

**Drop**
- Streamlit itself: full-script reruns, websocket session that dies on mobile sleep, flickering auto-refresh, poor touch layout, a second process to run and secure.
- `visual_complexity` and other fixed choice lists.

### 1.3 `server.py` API
The JSON API is a good base: `POST/GET /api/jobs`, `GET /api/jobs/{id}`, `GET /api/providers/status`, `GET /api/assets`, `GET /api/assets/image/{f}`, `GET /api/logs/stream`, `GET /api/status`, `GET /api/metrics`, `GET /api/batch`, `POST /api/smoke-test`, `/api/pause|resume|stop`. Keep the shape, extend (§12). Drop `/api/open-output`, `/api/start` (legacy alias of jobs), `/api/latest-output` (replaced by per-job outputs).

## 2. Information architecture & navigation

| # | Screen | Mobile nav | Purpose |
|---|---|---|---|
| 1 | Create | bottom tab (centre, emphasised) | compose a generation |
| 2 | Queue | bottom tab (badge = running+queued) | live jobs, cost, cancel |
| 3 | Library | bottom tab | results + uploads, reuse |
| 4 | Settings | bottom tab | providers, budget, templates, account |
| — | Segment Ledger | sub-screen of Create (long video) | segment editor |
| — | Job detail | sub-screen of Queue/Library | full result & parameters |
| — | Login | gate | auth |

- **Mobile (<768 px):** bottom tab bar (4 tabs, ≥48 px tall, safe-area padded); top app bar with a **spend chip** ("$3.20 / 20.00 today") that opens Settings → Budget.
- **≥768 px:** tab bar becomes a left rail (72 px, icons + labels). At ≥1100 px Create shows form (left, ~480 px) + live preview / queue peek (right).
- Currency: provider-native USD, optional INR conversion rate in Settings.

## 3. Global components

| Component | Spec |
|---|---|
| Spend chip | App bar, 44 px high. `spent_today / cap`. Amber ≥80 %, red at cap. Tap → Budget. |
| Connection banner | Sticky under app bar when API unreachable: "Can't reach server. Retrying…" + Retry. Cached data stays visible, greyed, read-only. |
| Toast | Above tab bar, 4 s, with Undo where relevant. |
| Bottom sheet | Pickers, advanced drawer, confirmations. Drag handle, max 90 vh, dismiss by swipe/scrim/Back. |
| Number field | Free numeric input with stepper (± 44×44 px) *and* typing; unit suffix; never clamps silently — shows a warning if the provider can't honour it. |
| Chips | ≥44 px tap area (visual size may be smaller with padding). |
| Cost estimate pill | "≈ $0.42 · ~6 min · RunPod 4090"; (i) expands breakdown. Always above Submit. |

Global rules
- **Touch targets ≥ 44×44 px**, ≥ 8 px gap between adjacent targets. Primary actions 52–56 px.
- Inputs ≥ 16 px font (prevents iOS zoom); body 15–16 px.
- WCAG AA contrast; dark theme default, light via `prefers-color-scheme` + manual toggle.
- Respect `prefers-reduced-motion`; no autoplay with sound.
- No hover-only affordances; overflow menus (⋯) and long-press are secondary, never the only path.

## 4. Screen: Create

### 4.1 Wireframe (mobile 390 px)
```
┌──────────────────────────────────────┐
│ Aurora                    [$3.20/20] │ app bar (56)
├──────────────────────────────────────┤
│ What do you want to make?            │
│ ┌────────┬────────┬────────┐         │ mode picker: horizontally
│ │ Text→  │ Text→  │ Image→ │ ›       │ scrollable cards
│ │ Image  │ Video  │ Video  │         │ (Text→Image | Text→Video |
│ └────────┴────────┴────────┘         │  Image→Video | Video→Video |
│                                      │  Long video)
│ ┌──────────────────────────────────┐ │
│ │ INPUT (varies per mode)          │ │
│ │ ┌─────────┐ ┌──────────────┐     │ │ e.g. Image→Video:
│ │ │ + Upload│ │ From library │     │ │
│ │ └─────────┘ └──────────────┘     │ │
│ │ [ thumbnail ✕ ]                  │ │
│ └──────────────────────────────────┘ │
│ Prompt                               │
│ ┌──────────────────────────────────┐ │
│ │                                  │ │ auto-grow textarea, ≥3 rows
│ │                                  │ │
│ └──────────────────────────────────┘ │
│ 0 chars                       [🎤]   │
│ ▸ Negative prompt                    │ collapsed row (48)
│                                      │
│ Current settings (summary chips)     │
│ [9:16] [720p] [5 s] [24 fps] [Auto]  │ tap → opens Advanced sheet
│ [⚙ Advanced]   [💾 Save as template] │
│ Variations: [ − 1 + ]                │
│ ┌──────────────────────────────────┐ │
│ │ ≈ $0.42 · ~6 min · Auto(cheapest)│ │ estimate pill
│ └──────────────────────────────────┘ │
│ [        ▶  Generate          ]  56  │ sticky above tab bar
├──────────────────────────────────────┤
│ Create │ Queue(2) │ Library │ Settings│
└──────────────────────────────────────┘
```
Desktop ≥1100 px: left column form, right column live preview of the running job and the user's template list.

### 4.2 Mode picker
- Five modes as ≥96×72 px cards. Mode changes the Input block and which Advanced fields show.
- **Text→Image:** prompt only.
- **Text→Video:** prompt only.
- **Image→Video:** 1 source image (upload / library / paste URL); optional end-frame image.
- **Video→Video:** source video + prompt describing the change; optional strength (free number 0–1).
- **Long video from segments:** prompt = global story/style; the **Segment Ledger** (§6) is the main input; summary shows segment count and total duration.
- Modes come from `GET /api/capabilities`, not hard-coded; if a backend gains/loses a mode the picker updates.

### 4.3 Fields
| Field | Control | Notes |
|---|---|---|
| Prompt | textarea, no hard max (soft hint at model token limit) | multi-line, Hindi/Roman Hindi, paste-from-clipboard |
| Negative prompt | collapsible textarea | stored per template, not globally |
| Source image / video | Upload (gallery/camera via `<input type=file accept>`), Library picker, drag-drop on desktop | shows thumbnail, size, resolution; optional client downscale of big phone photos with "keep original" toggle |
| Variations | stepper ≥1 | each = one job; estimate multiplies |

### 4.4 Advanced drawer (bottom sheet on mobile, side panel on desktop)
All optional; empty = "Auto".

| Field | Control | Details |
|---|---|---|
| Aspect ratio | Two free number boxes W : H plus "Quick fill" chips (1:1, 9:16, 16:9, 4:5, 21:9…) that only *populate* the boxes. Any ratio can be typed. | live shape preview. "Custom" is the same two fields, not a special mode. |
| Resolution | Width × height (px) or "long edge" | auto-rounds to model multiple and says so ("rounded 1000→1024"); warns above backend max |
| Duration | Free number, seconds (decimals ok) | shows frame count. If provider caps shorter: "This model makes ≤5 s clips; will be built from N segments" + [Switch to Long video] |
| FPS | Free number | |
| Seed | Number + 🎲 + 🔒 lock | shown in result metadata |
| Style | **Free text** + user-saved snippets as chips | no built-in style list |
| Camera motion | **Free text** + user-saved snippets | appended to prompt or mapped to a model param when supported |
| Expert | steps / CFG / sampler / strength | shown only if backend advertises them |
| Model | Dropdown: **"Auto (cheapest)"** (default) · "Auto (best quality)" · "Auto (fastest)" · each concrete model/provider with price/min | choosing a model recomputes estimate; unsupported fields are greyed with a reason, never silently hidden |
| Provider / tier | Read-only under Auto; selectable under manual model. Tiers: local/free, spot, on-demand | tier labelled with cost and interruption risk |
| Budget guard | "Stop if this job exceeds $__" | defaults to remaining daily cap |

"Reset to Auto" per field and "Reset all". Advanced values persist per mode in localStorage so nothing is retyped.

### 4.5 Estimate & submit
- Estimate recomputed (debounce 400 ms) via `POST /api/jobs/estimate` on relevant changes.
- Pill states: loading (skeleton), ok, **over-budget** (red; button becomes "Generate anyway…" with a confirm), **no capable provider** (Generate disabled with reason + link to Settings).
- Confirm sheet when estimate > user-set "ask above $X" (default $1): cost, time, provider, parameter summary, [Cancel] [Confirm & run].
- Submit: spinner ≤2 s, toast "Queued · View in Queue"; form stays so the user can tweak and run again.

### 4.6 States
| State | Behaviour |
|---|---|
| Empty | Mode picker + "Describe anything. No presets, no limits." Saved template chips if any. |
| Loading (capabilities/estimate) | Skeleton chips; Generate disabled only if capabilities unavailable |
| Validation error | Inline under field (red text + icon), first error scrolled into view |
| Submit error | Persistent banner above button with reason ("Provider key invalid", "Server unreachable") + [Retry] / [Open Settings]; form data retained |
| Success | Toast + Queue badge increments |
| Offline | Drafts allowed; Generate disabled; "Save draft" stores locally |

## 5. Screen: Job queue / progress

### 5.1 Wireframe (mobile)
```
┌──────────────────────────────────────┐
│ Queue                       [Filter] │
│ [Active 2][Done][Failed][All]        │ segmented, 44 px
├──────────────────────────────────────┤
│ ┌──────────────────────────────────┐ │
│ │▒▒ thumb ▒▒ Text→Video   ● Running│ │ job card
│ │ "neon rain city, slow dolly…"    │ │
│ │ ███████████░░░░░ 62%  step 31/50 │ │
│ │ RunPod · 4090 · on-demand        │ │
│ │ $0.21 so far · est $0.34 · ~2m   │ │
│ │ Stage: sampling                  │ │
│ │        [View]        [Cancel ✕]  │ │
│ └──────────────────────────────────┘ │
│ ┌──────────────────────────────────┐ │
│ │ Image→Video          ◔ Queued #2 │ │
│ │ waiting for GPU · est $0.40      │ │
│ │                      [Cancel ✕]  │ │
│ └──────────────────────────────────┘ │
│ ┌──────────────────────────────────┐ │
│ │ Text→Image  ✓ Done 1m ago  $0.03 │ │
│ │ [thumb][thumb][thumb]            │ │
│ │ [Open] [Reuse] [⋯]               │ │
│ └──────────────────────────────────┘ │
├──────────────────────────────────────┤
│ Create │ Queue(2) │ Library │ Settings│
└──────────────────────────────────────┘
```

### 5.2 Job card
- Thumbnail/placeholder, mode, prompt (2-line clamp), status pill (Queued / Starting GPU / Running / Assembling / Done / Failed / Cancelled / Paused-budget), progress bar (`role=progressbar`, numeric %), stage, step n/N, **provider, GPU, tier**, **spend so far / estimate / cap**, elapsed + ETA, queue position.
- Actions: **Cancel** (visible while active; confirm sheet: "You'll be charged for time already used: $0.21"), **Retry** (failed; offers "retry on different provider"), **Open**, **Reuse params**, **Delete**.
- Long-video jobs show a segment strip `[✓][✓][▶][ ][ ]`; tap → ledger.

### 5.3 Job detail (full screen)
Preview player (poster → `<video controls playsinline>`), parameters table (all inputs incl. seed, model, provider), event timeline (queued → GPU allocated → model loaded → sampling → upload → done) with timestamps and per-stage cost, collapsible **live logs** (auto-scroll toggle, copy), actions: Download, Share, Reuse, Make variations, Delete.

### 5.4 Behaviour
- **Live updates:** one SSE stream `GET /api/events` (multiplexed); fallback to 3 s polling; reconnect with `Last-Event-ID`; re-sync on `visibilitychange` (phone sleep).
- **Notifications:** Web Push for done/failed/budget-hit (opt-in; iOS needs the installed PWA).
- Sort: running → queued → recent. Pull-to-refresh. Swipe-left = Cancel with same confirm (never the only path).
- Spend is *measured* when the provider reports it, else shown with "≈" and labelled "estimated".

### 5.5 States
| State | Behaviour |
|---|---|
| Empty | "No jobs yet." + [Create something] |
| Loading | 3 skeleton cards |
| Error (list) | Inline card "Couldn't load jobs" + Retry; cached list stays visible greyed |
| Failed | Red pill + short reason ("Out of GPU memory at 1080p — try 720p"), [Retry] [Edit & retry] [View logs]; cost incurred shown |
| Cancelled | Grey; final charge shown |
| Success | Green pill, thumbnails, final cost, duration |
| Stuck (no progress > N min) | Amber "No progress for 4 min" + [Cancel] [Keep waiting] |

## 6. Screen: Segment ledger (long video)

Reached from Create (mode = Long video) or from a finished job ("Extend"). A **ledger** is an ordered list of segments, each generated independently and then stitched; the user can re-run just one bad segment.

### 6.1 Wireframe (mobile)
```
┌──────────────────────────────────────┐
│ ‹ Long video       Total 24 s · $1.90│
├──────────────────────────────────────┤
│ Global story / style prompt          │
│ ┌──────────────────────────────────┐ │
│ │ A lone astronaut walks a desert… │ │
│ └──────────────────────────────────┘ │
│ Continuity: (•) last frame → next    │
│             ( ) independent          │
│             ( ) reference image [pick]│
├──────────────────────────────────────┤
│ ≡  1 [thumb] Wide shot, dusk         │ segment row
│      5 s · seed 42 · ✓ done   $0.30  │ drag handle ≡ 44×44
│      [Edit] [Regenerate] [⋯]         │
│ ≡  2 [thumb] Close on helmet         │
│      4.5 s · ⟳ running 40%           │
│ ≡  3 [  +  ] Pan to ruins            │
│      6 s · not generated    ≈$0.36   │
│ ┌──────────────────────────────────┐ │
│ │        ＋ Add segment            │ │ 48 px
│ └──────────────────────────────────┘ │
│ Transition: [cut ▾] 0.0 s            │ free seconds
│ Audio: none / upload track           │
├──────────────────────────────────────┤
│ [ Generate missing (2) ]  [Assemble] │
└──────────────────────────────────────┘
```
Segment editor (bottom sheet): prompt (inherits global + own text), duration (free number), optional start image / "use previous last frame", seed, camera motion, negative prompt, per-segment model override, [Duplicate] [Delete].

### 6.2 Behaviour
- **Reorder:** drag handle (also long-press on row); accessible alternative ⋯ → "Move up / Move down" (no drag needed).
- **Add:** at end, or ⋯ → "Insert after", "Duplicate". "Split a script" pastes text and makes one segment per paragraph.
- **Per-segment status:** not generated / queued / running / done / failed / **stale** (an earlier segment changed under last-frame continuity → amber "Regenerate downstream?").
- **Actions:** Generate missing, Regenerate one, Regenerate all (confirm with total cost), Assemble (ffmpeg stitch; cheap, often CPU-only) → final video appears as normal Library item.
- Inline preview per segment; "Play all" plays done segments back-to-back.
- Total duration and cost always at top. Autosave on every change (`PUT /api/ledgers/{id}`) with "Saved ✓"; acts as a draft.
- Desktop: table layout with inline editing, drag reorder, side preview.

### 6.3 States
Empty: "Add your first segment" [Add segment] [Split a script…]. Loading: skeleton rows. Error: per-segment inline error + Retry; ledger stays usable. Success: assembled → toast + "Open in Library".

## 7. Screen: Library / Gallery

### 7.1 Wireframe
```
┌──────────────────────────────────────┐
│ Library                   [⌕] [Select]│
│ [All][Images][Videos][Uploads][★]    │ filter chips (44)
│ Sort: Newest ▾    View: [▦][▤]       │
├──────────────────────────────────────┤
│ ┌─────┐┌─────┐                       │ 2 cols at 360–430,
│ │     ││ ▶ 5s│                       │ 3 at ≥600, 5+ desktop
│ └─────┘└─────┘                       │
│ ┌─────┐┌─────┐                       │ infinite scroll, virtualised
│ └─────┘└─────┘                    [+]│ FAB: upload
├──────────────────────────────────────┤
│ Create │ Queue │ Library │ Settings  │
└──────────────────────────────────────┘
Item sheet (tap a tile):
┌──────────────────────────────────────┐
│ [      full preview / player      ]  │
│ "neon rain city…"  9:16 · 720p · 5 s │
│ seed 42 · Wan2.2 · $0.34 · 2 d ago   │
│ [Use for new variants] [Image→Video] │
│ [Download] [Share] [★] [⋯ Delete]    │
└──────────────────────────────────────┘
```

### 7.2 Features
- **Filters:** type, mode, date range, model/provider, aspect ratio, favourites, tags, free-text search over prompts. Filter state lives in the URL query.
- **Reuse for new variants:** "Use for new variants" opens Create pre-filled with the item's params and a new seed; "Image→Video" sends the image as source; "Use as reference / end frame / segment start" for Long video. Multi-select → use first/last frame for segments.
- **Download:** `GET /api/assets/{id}/download`; on phones "Save to Photos" via Web Share with files where supported, else a download link.
- **Share:** `navigator.share` (files if supported); fallback = copy a **signed expiring link** (1 h / 1 d / 7 d, user chooses). Public links are off by default; enabling needs an explicit tap.
- **Bulk (Select mode):** delete, zip download, favourite, tag. Delete is soft (Trash 30 days); "Empty trash" requires typed confirmation.
- **Uploads:** "+" adds user images/videos to the library (badge "Upload"); usable as inputs.
- Always show metadata (prompt, negative, seed, model, provider, cost, time) so any item is reproducible.

### 7.3 States
Empty: "Nothing here yet. Generations and uploads appear here." [Create]. Loading: tile skeletons with aspect-ratio boxes (API returns width/height; no layout jump). Error: tile failure → placeholder, tap to retry; list failure → banner. Success: after delete → toast with Undo. No filter match: "No matches" + [Clear filters].

## 8. Screen: Settings

```
┌──────────────────────────────────────┐
│ Settings                             │
│ ── GPU providers ─────────────────── │
│ ● RunPod        key ✓ •••• 9f2a   ›  │ row 56 px
│   balance $12.40 · tested 2m ago     │
│ ○ Vast.ai       no key            ›  │
│ ● Local ComfyUI http://… offline  ›  │
│ [ Test all ]                         │
│ ── Budget ─────────────────────────  │
│ Daily cap   [ 20.00 ] USD            │
│ Monthly cap [ 200.00 ]               │
│ Ask before jobs above [ 1.00 ]       │
│ When cap reached: (•) stop & ask     │
│                   ( ) block all      │
│ Spent today $3.20  ▓▓░░░░░░░░ 16%    │
│ ── Templates (user-saved) ─────────  │
│ "Reels portrait"  [Edit][Delete]     │
│ ── Appearance / language ──────────  │
│ ── Notifications ──────────────────  │
│ ── System status ──────────────────  │
│ Backend ● · worker ● · disk 41 GB    │
│ [Run smoke test]                     │
│ ── Account ────────────────────────  │
│ Signed in as rupesh · [Log out]      │
│ [Change password] [Active devices]   │
└──────────────────────────────────────┘
```
- **Provider row → detail sheet:** paste API key (password field, show/hide, Paste button), [Save & test], status (valid / invalid / rate-limited / no balance), endpoint URL for self-hosted. **Keys are write-only**: afterwards only the last 4 chars show; stored server-side, never in localStorage, never returned by the API.
- **Budget:** hard cap enforced **server-side** (UI only edits it). 80 % → toast; 100 % → running jobs finish/pause per policy, new jobs need explicit override.
- **Templates:** user-saved Create configurations (any subset of fields). Created via "Save as template". This is the *only* preset mechanism; a fresh install has zero.
- **System status:** from Streamlit's System Pulse (heartbeat, API version, worker alive, disk) + smoke test.
- States: loading skeleton rows; provider test error inline with reason; save → toast; no providers → top callout "Add a GPU provider to start generating" (Create's Generate links here).

## 9. Auth (remote access)

Threat model: single owner reaching a server exposed to the internet from a phone; GPU keys and spend are at stake.

```
┌──────────────────────────────────────┐
│            Aurora                    │
│  Username [______________]           │
│  Password [______________] 👁        │
│  [x] Keep me signed in 30 days       │
│  [          Sign in          ] 52    │
│  error: "Wrong username or password" │
│  (after 5 fails: "Try again in 5 min")│
└──────────────────────────────────────┘
```
- Username + password, password-manager friendly (`autocomplete="username"` / `current-password`).
- **HttpOnly, Secure, SameSite=Lax session cookie** (not JWT in localStorage — not readable by XSS) + CSRF header on mutating requests.
- Rate-limit + lockout on login. v2: TOTP and passkeys (WebAuthn — best phone UX).
- First run: server prints a one-time setup token to the console to create the owner password; no default credentials.
- "Active devices" list with revoke. API 401 → Login, then return to the previous screen with unsent form drafts preserved.
- HTTPS is mandatory for remote use (PWA, cookies, share, camera all need it) via the tunnel/reverse proxy. Recommend **Tailscale or Cloudflare Access** in front as defence-in-depth; app auth is still required.
- States: loading (button spinner), error (inline), locked-out (countdown), offline banner.

## 10. Accessibility, responsive, performance

- Breakpoints: 360–599 phone (1 col); 600–899 large phone / tablet portrait; 900–1099 tablet landscape (rail nav); ≥1100 desktop (2-pane Create, Library with detail pane).
- Icons carry text labels or `aria-label`; visible focus rings; full keyboard use on desktop (`Ctrl+Enter` = Generate); landmarks; a live region announces job completion.
- Server-made WebP thumbnails (~320 px); full files only on open. Videos: `preload="metadata"`, `playsinline`, poster frame, HTTP Range. Lazy grid.
- Chunked, resumable uploads with progress and Cancel; warn > 50 MB on mobile data.
- Budget: < 150 KB gzip JS first load; interactive < 2 s on a mid Android over 4G.

## 11. Tech recommendation

**PWA built with Preact + Vite, plain CSS with custom properties, served as static files by the existing FastAPI app.**

| Option | Verdict | Reasons |
|---|---|---|
| Keep vanilla JS | Possible, not recommended | Live queue, segment drag-reorder, URL-backed filters, optimistic UI and a ~20-field interdependent form become hand-rolled state spaghetti; current `main.js` already pokes the DOM directly. Hard to test. |
| **Preact + Vite** | **Chosen** | ~4 KB runtime (fast on phones), component model fits job cards/segments, outputs plain static files, no extra server (unlike Streamlit/Next), easy for a solo developer; `sortablejs` for reorder, `wouter` for routing. |
| React/Next, Svelte | Not needed | Heavier, or SSR we don't need. Svelte is an acceptable swap; the spec is framework-agnostic. |
| Streamlit / Gradio | Reject | Rerun model, websocket dies on sleep, poor mobile UX, extra process, hard to secure. |
| Native (Flutter/RN) | Reject for now | Store friction, second codebase; PWA gives install, push, share. |

**PWA requirements** (installs on the phone home screen):
- `manifest.webmanifest` (name, 192/512 + maskable icons, `display: standalone`, `theme_color`, `start_url=/`).
- Service worker (Workbox via `vite-plugin-pwa`): precache the app shell (offline shows cached Library metadata + drafts); stale-while-revalidate thumbnails with a size cap; **never cache `/api/*` mutations or auth responses**.
- Web Push (VAPID) for job-finished notices. iOS needs the app installed first — show a one-time "Add to Home Screen" hint in Safari.
- Web Share (+ files), `<input type=file accept="image/*,video/*">` for camera roll/capture.
- Same origin: FastAPI `StaticFiles` at `/`, API under `/api` → no CORS; remove the permissive `CORSMiddleware` in `server.py` when migrating.

Layout: **reuse `ui/`** (it already has Vite 8, `package.json`, `src/`, `public/`, `style.css`). Add `ui/src/screens/{Create,Queue,Library,Ledger,Settings,Login}`, `ui/src/api/`, `ui/src/components/`; build to `ui/dist`, served by FastAPI. No new `web/` folder. Retire old `index.html`/`main.js` DOM code and `streamlit_app.py` once parity is reached.

Design tokens (start): spacing 4/8/12/16/24, radius 12 px, tap min 44 px, base font 16 px, one accent hue; status colours (queued grey, running blue, done green, failed red, budget-paused amber) always paired with icon + text.

## 12. API the UI needs

Proposal; reconcile with `docs/ARCHITECTURE.md`. JSON, cookie auth, `/api` prefix, errors as `{error:{code,message,hint}}`. "exists" = already in `server.py`.

| Purpose | Method & path | Notes |
|---|---|---|
| Auth | `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | NEW; cookie session |
| Devices | `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}` | NEW |
| Capabilities | `GET /api/capabilities` | NEW: modes, per-model supported fields, limits (max res/duration/fps) — no hard-coded lists in the UI |
| Models/providers | `GET /api/models`; `GET /api/providers/status` (exists) | price/min, VRAM, tier |
| Provider keys | `PUT /api/providers/{id}/key` (write-only), `POST /api/providers/{id}/test`; `POST /api/smoke-test` (exists) | key never returned |
| Estimate | `POST /api/jobs/estimate` | NEW; same body as create → `{cost_usd, cost_range, eta_s, provider, tier, warnings[]}` |
| Create job(s) | `POST /api/jobs` (exists, CHANGE) | `{mode, prompt, negative, inputs:[asset_id], params:{aspect or width/height, duration_s, fps, seed, style, camera, model:"auto:cheapest"…}, variations, max_cost_usd, ledger_id?}` → `{job_ids[]}`; params free-form, backend returns warnings rather than rejecting values |
| List jobs | `GET /api/jobs?status=&limit=&cursor=` (exists) | cursor pagination |
| Job detail | `GET /api/jobs/{id}` (exists) | + progress, stage, cost_so_far, provider, events[] |
| Cancel / retry | `POST /api/jobs/{id}/cancel`, `/retry` | NEW per-job (replaces global stop/pause) |
| Live events | `GET /api/events` (SSE, `Last-Event-ID`) | NEW; job + budget events. Per-job logs: `GET /api/jobs/{id}/logs/stream` |
| Assets | `GET /api/assets?type=&q=&aspect=&model=&from=&to=&cursor=` (exists, CHANGE) | w/h, duration, thumb URL, params, cost |
| Asset files | `GET /api/assets/{id}/file`, `/thumb`, `/download` | Range support for video |
| Upload | `POST /api/uploads` (init) + `PATCH` chunks + complete | resumable → `asset_id` |
| Asset ops | `PATCH /api/assets/{id}` (favourite, tags), `DELETE` (soft), `POST …/restore`, `POST …/share` → signed URL with TTL | |
| Ledgers | `POST /api/ledgers`, `GET/PUT /api/ledgers/{id}`, `POST …/generate` (missing / all / segment_id), `POST …/assemble` | NEW; segments `[{id, prompt, duration_s, seed, start_asset, params}]`, global prompt, continuity, transitions, audio |
| Templates | `GET/POST /api/templates`, `PUT/DELETE /api/templates/{id}` | NEW; user data only, none shipped |
| Budget | `GET/PUT /api/budget` → `{daily_cap, monthly_cap, ask_above, spent_today, spent_month, policy}`; `GET /api/spend?from=&to=` | enforced server-side |
| Push | `POST/DELETE /api/push/subscribe` | NEW |
| Status | `GET /api/status`, `GET /api/metrics` (exist) | Settings health block |

Drop: `/api/start`, `/api/latest-output`, `/api/open-output`, global `/api/pause|resume|stop` (replace with per-job cancel, plus an optional queue-level pause if the architect wants it).

Error contract the UI relies on: `401` → login; `402`/`409 budget_exceeded` → budget sheet with override; `422` → `errors:{field:msg}`; `503 no_capable_provider`; `429` with `retry_after`.

## 13. No-presets checklist (for reviewers)

- [ ] No fixed enum lists shipped for duration, aspect ratio, resolution, fps, style, camera, content type.
- [ ] Quick-fill chips (e.g. 9:16) only populate free numeric fields, labelled "Quick fill"; any value can be typed.
- [ ] Style/camera: free text + only snippets the user saved.
- [ ] "Auto (cheapest)" is a *routing policy*, not a creative preset.
- [ ] Templates are created only by the user; fresh install has zero.
- [ ] Backend limits come from `/api/capabilities` and appear as warnings, not as truncated choices.

## 14. Open questions (for PM / Architect)

1. Currency: USD only, or INR conversion? (Spec assumes optional conversion.)
2. Multi-user needed? Spec assumes a single owner; multi-user adds per-user budgets and library scoping.
3. Can the backend do true last-frame continuity between segments, or only independent clips + crossfade? (Affects ledger continuity options.)
4. Audio/voice (old pipeline had a TTS "Voice" field): in scope for v1? Spec reserves only an audio-track slot in the ledger.
5. Are signed public share links acceptable, or strictly private/auth-only?
6. Reachability choice (Tailscale vs Cloudflare Tunnel vs VPS) — impacts HTTPS and Push setup docs.
