# YouTube SEO guidelines → rules for `aurora/publish/seo.py`
Researched 2026-10-25 from YouTube's official Help Center (pages are not dated 2026; re-check yearly).

## What YouTube officially says
| Topic | Official guidance | Source |
|---|---|---|
| Hashtags | > 60 hashtags → ALL ignored; only the 3 most engaging show above the title; no spaces (#TwoWords); profanity can get the video restricted; "ordinary descriptive tags or repetitive sentences in the description" are prohibited | [Hashtags help](https://support.google.com/youtube/answer/6390658?hl=en) |
| Tags | Tags play a **minimal role** in discovery; useful mainly for commonly misspelled words; excessive tags in the description violate the spam policy | [Tags help](https://support.google.com/youtube/answer/146402?hl=en) |
| Titles & descriptions | Pick 1-2 main words that describe the video and feature them prominently in BOTH title and description; every video should have a **unique** description | [Description tips](https://support.google.com/youtube/answer/12948449?hl=en) |
| Misleading metadata | Titles/thumbnails/descriptions must not promise content the video doesn't have (spam & deceptive practices) | [Spam policy](https://support.google.com/youtube/answer/2801973?hl=en) |
| Shorts discovery | Ranking factors include how well metadata matches the search and whether viewers click and keep watching; Shorts can't hurt long-video performance | [Shorts search & discovery](https://support.google.com/youtube/answer/11914225), [Shorts guide](https://blog.youtube/creator-and-artist-stories/your-guide-to-getting-started-with-youtube-shorts/) |

## Rules for our SEO module (derived)
1. **Title:** "<Series> – Ch.N Part M: <hook>" stays, but the hook must contain 1 main keyword when natural; ≤ 70 chars preferred (≤ 100 hard); never promise events not in the part.
2. **Description line 1–2 = hook + main keyword** (this is what shows before "more").
3. **Unique body per part:** 2–3 sentences specific to THIS part's events (from its panels/narration). Shared boilerplate ≤ 2 short lines (next-part line + playlist line).
4. **No keyword lists in prose.** Remove lines like "Perfect if you like anime story, original anime and dark fantasy anime" and any "Keywords: …" line (reads as stuffing / repetitive sentences).
5. **Hashtags: 3 only**, placed at the end, most relevant first so the 3 shown above the title are useful: series hashtag (#EchoesOfTheAether), #anime, #animestory. #Shorts is optional (vertical ≤ 3 min is classified as a Short automatically); keep at most one.
6. **Tags: 5–10**, relevant phrases + common misspellings of the series name (e.g. "echos of the aether"); no tag stuffing.
7. **Metadata fields:** categoryId 1 (Film & Animation), defaultLanguage + defaultAudioLanguage "en", captions uploaded (already built), part added to the series playlist in order; playlist must be **public** when videos are public.
8. **One disclosure line only** (project rule: AI/synthetic disclosure on every upload).
9. **Retention matters more than text:** first 1–2 s of the Short must hook (cover card ≤ 1 s, then action) — note for the render side.
10. Re-check this page against the live Help Center before major SEO changes.
