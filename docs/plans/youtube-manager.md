# YouTube channel manager + long-form stories (Shinobi Mindset)
Status: approved (user, 2026-10-23)           Date: 2026-10-23

## Goal
Manage @ShinobiMindset1 end to end, not just uploads: SEO-researched metadata, subtitles, thumbnails,
series playlists, comment moderation with approved replies, analytics reports and a learning loop.
Add 4-5 min horizontal story episodes, with 30 s vertical Shorts cut from the same panels.

## Decisions (best practice, user said "proceed")
- Long story: 16:9 1920×1080 episode (~4-5 min, ~40-50 panels), plus 3-5 vertical Shorts from its panels
  (each links to the full episode). Shorts drive discovery; episodes drive watch time.
- Comments: spam/abuse auto-held; reply drafts need user approval (no auto-replies).
- Uploads: kill-switch stays off; first uploads private; schedule only after the user says go.
- Same locked cast (Kaelen) and comic format; English.

## Files to change
- `aurora/publish/seo.py` (new) — YouTube search.list for the topic → competitor titles/tags; Gemini proposes
  3 titles + description (hook, recap, keywords, chapters for long videos, hashtags, AI note); pick best by
  keyword coverage/length rules.
- `aurora/publish/youtube.py` — captions.insert (SRT from narration timings), thumbnails.set (long videos),
  playlist title/description SEO + part ordering, channels.update branding (description/keywords).
- `aurora/publish/comments.py` (new) — commentThreads.list, spam hold (setModerationStatus), reply drafts queue.
- `aurora/publish/analytics.py` (new) — YouTube Analytics API (views, watch time, avg view %, subs) → weekly report;
  feed top performers into research.
- `aurora/comic.py` — `aspect: 16:9` long-episode mode + Shorts cutter from the same panels; thumbnail frame export.
- `aurora/research.py` — long-episode schema (40-50 panels, acts), uses analytics winners.
- `server.py`/`ui/` — Approvals tab: queue items, comment reply drafts, weekly report.
- New OAuth scope for Analytics (`yt-analytics.readonly`) → one more consent click.

## Risks
- NIM credits: a 45-panel episode ≈ 45+ credits (daily cap 25) → render over 2 days or reuse panels; cap stays.
- API quota 10k/day: upload 1600, captions ~400, thumbnails 50, search 100 each → budget per day, defer on quota.
- Custom thumbnails need the channel phone-verified (user, once in Studio).
- Not possible via API: end screens, cards, community posts, impressions/CTR, monetization settings.

## Test plan
- Unit tests with mocked Google APIs for SEO, captions, thumbnails, playlists, comments, analytics.
- One private upload of a long episode + its Shorts after the user's explicit approval.

## Done when
- Private test videos show SEO title/description, subtitles, thumbnail (long), correct playlist and order.
- Weekly analytics report reaches the phone; comment drafts appear for approval.
- Full suite green; docs/AUTOPILOT.md updated; user approves going public.
