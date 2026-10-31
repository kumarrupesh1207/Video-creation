# Distribution and earning plan (Instagram + other platforms)
Status: approved        Date: 2026-10-31

## Goal
Take Aurora's reels beyond YouTube, grow reach, and reach real income at $0 budget.
Stays automatic, and every post keeps the AI disclosure.

## Where we stand (measured 2026-10-31)
| | YouTube @ShinobiMindset1 | Instagram shinobimindset1 |
|---|---|---|
| Followers | 5 subscribers | 1 follower |
| Content | 13 videos | 3 Reels, none since 20 Oct |
| Views | 408 total | ~138 Reel views |

- Echoes parts run ~30 s; the Ch.1 compilation runs 4.5 min.
- 85 % of YouTube views come from the Shorts feed (AUTOPILOT.md).
- Metricool brand 7524973 has Instagram + YouTube connected. No Facebook Page is connected.
- `aurora/publish/instagram.py` + `hosting.py` exist but were never posted live.

## Final platform list
| Tier | Platform | Why | Automation |
|---|---|---|---|
| Now | YouTube Shorts + long | Live; the only proven income route | Built |
| Now | Instagram Reels | Same 9:16 file, big India audience | Built, needs setup; API limit 100/24 h (Meta's page also says 50) |
| Now | Facebook Reels (Page) | Same Meta app, cheap to add | To build; 30/24 h, **3-90 s only**, Pages only |
| Later | Telegram channel | Audience we own, funnel for Patreon/e-book | Bot API; 50 MB limit (unverified) |
| Later | Threads | Text funnel to new parts, weak for video | Same Meta app |
| Later | Pinterest | Long-lived pins | Needs a review for video pins |
| Later | Dailymotion | Second home for 16:9 chapters | Unverified |
| Skip | TikTok | Banned in India | - |
| Skip | X, Reddit | Paid API / hostile to self-promotion | - |
| Skip | Snapchat | Spotlight API is allowlist-only | - |
| Skip | ShareChat, Moj, Josh, LinkedIn, Rumble, Likee | No API found or wrong audience | - |
| Skip | Webtoon, Tapas | Image episodes, AI art disliked | - |

Posting route: use the native Meta APIs. Metricool's free plan allows 20 posts a month, and 2 parts/day on one
network is about 60; Starter costs money (from $20/month). Keep Metricool for analytics.

## What drives reach (vidIQ, measured; conclusions inferred)
- Biggest channel to study: STOICSTIC (44.3K subs, 26.7M views), top Short 8.3M. Matio and Dark Age Datacore
  went from zero to 20-40K subs since July 2026, so a new channel can still grow here.
- STOICSTIC's top Shorts run 1-2.5 min, and their titles are one-line premises ("When GODS mourn a JANITOR"), not lore.
- Daily posting beats volume: CATWOLF AI posts 124 Shorts/30 d and averages ~1.2K views per video.
- Keywords: "ai anime story" 64 volume / 24 competition; "anime story" 72/35; "anime summary" growing fast.
- Language: English first. Hindi demand is nearly all recaps of existing anime, not original stories.
  Dub a single English winner only after it passes 50K views.

## Earning: the facts (YouTube official pages, read 2026-10-31)
- Today: 1,000 subs + (4,000 watch hours in 12 months or 10M Shorts views in 90 days). Review takes about a month.
- From 1 Feb 2027: new applicants need 1,000 subs + (8,000 watch hours in 365 days or 20M Shorts views in 90 days).
- From 1 Feb 2027: a monthly share of the Shorts pool needs 10M Shorts views in the last 90 days, even inside YPP.
- Unchanged: fan funding and Shopping at 500 subs + (3,000 hours or 3M Shorts views). India's eligibility is unverified.
- Shorts share is 45 %. Shorts-feed watch hours do not count toward the hours target.
- **Conclusion:** income comes from long videos. Third-party RPM figures put long-form 10-100x above Shorts; treat them as rough.
- Estimates, not a forecast: 1M Shorts views is about $10-50; 1M long views about $500-4,000.
- Reaching 1,000 subs + 4,000 hours by 1 Feb from 5 subs is unlikely. Plan for the 8,000-hour bar and apply early if the old bar is hit.
- 8,000 hours is 480,000 minutes. The 4.5-min chapter needs ~267K views (at 1.8 min watched per view); a 45-min
  compilation needs ~71K views (at 6.75 min, assumed 15 % retention). Long compilations are the cheapest route: 0 NIM credits.

## Income ladder
| Milestone | Turn on |
|---|---|
| Now | W-8BEN and AdSense prep only when YPP opens; have a CA confirm tax |
| 500 IG followers | Instagram Gifts (secondary source) |
| 500 subs + 3,000 hours or 3M Shorts views | YouTube fan funding, if India qualifies |
| 2 finished chapters | Kindle/Gumroad comic e-book; KDP requires AI image disclosure |
| 1,000 subs + YPP bar | Apply for YPP |
| 5-10K subs | Patreon / Ko-fi: early access to parts |
| 10K+ | Merch, affiliate |
| 50K+ | Sponsors, licensing |

Check Facebook Content Monetization in Meta Business Suite once the Page exists: its help page was unreachable, so India status is unverified.

## Inauthentic-content policy (biggest income risk)
YouTube reviews the whole channel. It bans "templated storylines" and AI made with "generic or unoriginal templates",
and allows "using AI to visualize a unique character and narrative you invented". Echoes fits the allowed side if:
- each part has its own storyline and varied scenes, never the same situation repeated;
- a human edits the scripts, and the edits are kept in git as proof of creative work;
- off-niche uploads are removed before applying.

## Phases
**Phase 1 - clean-up (this week, user).** Decide on the two Hindi motivation Shorts: nTN0r4rcMZ8 and kVszeCbWsLA are
off-niche, and Metricool records them with the AI flag off, which breaks our disclosure rule. Set them private or
switch the flag on in Studio.

**Phase 2 - Meta setup (user, ~1 hour).** Create a Facebook Page, link Instagram, create the Meta app and token, create
the Cloudflare R2 bucket (steps in docs/AUTOPILOT.md). Confirm Meta App Review needs while doing this: unverified.

**Phase 3 - build (Claude, after approval).** See "Files to change".

**Phase 4 - backfill.** Post Echoes Ch.1 from Part 1 on Instagram and Facebook, so new viewers can start at the beginning.

**Phase 5 - 2-week length test.** Run one chapter at 1 part/day of 60-90 s against the current 2 parts/day of 30 s.
This fits the NIM cap (25/day), is the most Facebook allows, and lowers the template risk. Compare views per Short and
average % viewed. Premise-style hook titles run in the same test.

**Phase 6 - long videos.** After Ch.3, compile a 30-45 min season video from cached parts (0 NIM credits).

**Phase 7 - monthly review.** Add a Later platform only when Tier "Now" runs clean for a month. Hindi test after a 50K Short.

## Files to change
- `aurora/publish/facebook.py` (new) — Reels via `/{page_id}/video_reels`; reject files over 90 s; reuse `instagram.py` patterns.
- `aurora/autopilot.py` — dispatch `facebook` beside `instagram` (`publish_due`, analytics loop); add a backfill command.
- `aurora/publish/instagram.py` — per-platform caption: series, part, disclosure, no YouTube-only lines.
- `config.yaml` — `platforms: [youtube, instagram, facebook]` for shinobi, only after a live test.
- `docs/AUTOPILOT.md` — Facebook section.
- `tests/` — new `facebook.py` and caption tests.

## Risks
- Instagram cannot schedule ahead: the PC must be on at or after the slot; a missed slot posts late.
- Meta tokens expire: use a Page token and show missing or expired tokens in the daily report.
- Parts over 90 s can't go to Facebook; the publisher skips them with a logged reason, never a crash.
- Duplicate-post penalties are unverified; post Aurora's own file with no third-party watermark.
- Render stalls: the daily report for 2026-10-31 shows 0 renders and 2 failures. A paused feed costs reach more than a late platform.
- VEER stays off all platforms until the user says go.

## Test plan
- Unit tests with mocked HTTP for the Facebook flow, the 90 s guard and captions; full pytest suite.
- `publish-due --dry-run` to read the exact captions; then one user-approved live Reel on each platform.

## Done when
- One Echoes part has posted live on Instagram and Facebook with disclosure, and the user has confirmed it.
- The daily report covers all three platforms; tests are green.
- Backfill has finished and the length test has a written result here.
