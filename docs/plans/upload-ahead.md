# Upload-ahead publishing (PC is off at night)
Status: approved (user said go-live for shinobi, 2026-10-23)        Date: 2026-10-23

## Goal
Reels must go public at their slot (12:30 / 18:30 IST, weekly long episode Sat 18:00 IST) even when the PC is off.
So once the veto window has passed, upload immediately as `private` + `publishAt=<slot>`; YouTube flips it public itself.

## Decisions
- Veto window unchanged (6 h). After it, `publish-due` uploads ahead: queue status `uploaded_scheduled` (video id stored).
  Slot already past -> upload public now. Instagram cannot schedule ahead: it still publishes when the PC is on at/after the slot.
- Veto after upload = `videos.update` removing `publishAt` (video stays private) + item `vetoed`. UI must call
  `autopilot.veto_item` / `approve_item`, not `queue.veto` (which now refuses uploaded items).
- Per-channel switch `publish_enabled`; global `autopilot.publish.enabled` stays false. Only `shinobi` is true; `main` is also `render_enabled: false`.
- Cadence shinobi: 2 Shorts/day, 1 long/week. Render fills only the deficit (created today vs reels_per_day, lookahead 2 days),
  so 3 render runs/day never exceed NIM cap or YouTube quota (budget ledger defers).
- Task Scheduler: "Aurora Render" (logon, 11:00, 15:00) + "Aurora Publish" (logon, hourly 09:00-23:00), StartWhenAvailable, hidden, logs/.
- Daily report outputs/reports/<date>.md after each publish run, shown in the Approvals tab.

## Files
`aurora/publish/queue.py`, `aurora/publish/youtube.py` (video status update), `aurora/autopilot.py`, `scripts/autopilot_task.ps1`,
`config.yaml`, tests, `docs/AUTOPILOT.md`.

## Risks
- YouTube may keep a scheduled video private if the API project is unaudited (earlier note) -> reconcile step flags "still private after slot".
- Long episodes need Backend's `comic.make_long_episode`; until it exists the long slot is skipped with a log line.

## Done when
Mocked tests green; coordinator's one live private+publishAt check; user sees the report in the Approvals tab.
