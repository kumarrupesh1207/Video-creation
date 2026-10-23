# YouTube API: optional scopes, quota budget, Windows file locks
**Problem:** adding the analytics scope to the required scope list would have broken uploads on every existing token; a 1600-unit upload can silently exhaust the 10k daily quota; captions built from a temp file crashed on Windows (`MediaFileUpload` keeps the file open).
**Fix:** required scopes (upload, readonly, force-ssl) vs optional (`yt-analytics.readonly` via `get_credentials(need=...)`); `auth` always asks for all. Every quota-costing call goes through `QuotaBudget.spend()` (9000/10000 safety limit, Pacific day) and defers instead of failing. SRT text is uploaded from memory (`MediaIoBaseUpload`), never via a temp file.
**Rule:** a new API scope is optional until the user has re-consented; budget before calling, not after the 403.

## Playlist 404 right after creation (eventual consistency)
`playlistItems.list` on a playlist created seconds earlier returned 404, so Part 1 was not added (Part 2 was). `add_to_series_playlist` now retries 404 with 2/5/10 s backoff on lookup, size check and insert, always inserts at an explicit `position` = part-1 (clamped to the playlist size), and prints a loud "NOT added" error if it still fails (the item's `playlist_error` records it).
