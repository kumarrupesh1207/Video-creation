"""YouTube Shorts uploader (YouTube Data API v3, OAuth installed-app flow).

One token file per channel => channels never mix. One-time consent: `python -m aurora.publish.youtube auth --channel NAME`.
client_secret.json path comes from env YOUTUBE_CLIENT_SECRET (.env). videos.insert costs ~1600 quota units (default 10000/day).
`status.containsSyntheticMedia` is the API field for YouTube's altered/synthetic-content disclosure (present in the v3 discovery doc).
After auth the token's channel is checked against the profile's `expected_handle`; a mismatch refuses to upload.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube.force-ssl"]  # force-ssl: playlists
TOKEN_DIR = ROOT / "outputs" / "tokens"  # outputs/ is git-ignored
STATE_DIR = ROOT / "outputs" / "publish_state"
RETRY_STATUS = {500, 502, 503, 504}
QUOTA_REASONS = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


class QuotaExceeded(Exception):
    """Daily quota / upload limit hit: stop uploading until tomorrow."""


class AuthRequired(Exception):
    """No valid token (or missing scopes): run the one-time consent for this channel."""


class ChannelMismatch(Exception):
    """The authorised Google account is not the channel this profile expects: refuse to upload."""


def token_path(channel: str) -> Path:
    return TOKEN_DIR / f"youtube_{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}.json"


def get_credentials(channel: str, interactive: bool = False):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tp = token_path(channel)
    reauth = f"re-run: python -m aurora.publish.youtube auth --channel {channel}"
    creds = None
    if tp.exists():
        try:
            granted = set(json.loads(tp.read_text(encoding="utf-8")).get("scopes") or [])
        except ValueError:
            granted = set()
        if set(SCOPES) <= granted:
            creds = Credentials.from_authorized_user_file(str(tp), SCOPES)
        elif not interactive:  # token made before a scope was added (e.g. force-ssl for playlists)
            raise AuthRequired(f"token for '{channel}' lacks scopes {sorted(set(SCOPES) - granted)}; {reauth}")
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            if not interactive:
                raise AuthRequired(f"token for '{channel}' expired or revoked ({e}); {reauth}") from e
            creds = None
    else:
        creds = None
    if creds is None:
        if not interactive:
            raise AuthRequired(f"no YouTube token for channel '{channel}'; run: python -m aurora.publish.youtube auth --channel {channel}")
        from google_auth_oauthlib.flow import InstalledAppFlow

        secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        if not secret or not Path(secret).exists():
            raise AuthRequired("set YOUTUBE_CLIENT_SECRET in .env to the path of client_secret.json")
        creds = InstalledAppFlow.from_client_secrets_file(secret, SCOPES).run_local_server(port=0, prompt="consent")
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _norm_handle(h: str | None) -> str:
    return "@" + str(h or "").strip().lstrip("@").lower()


def verify_channel(service, expected_handle: str | None) -> dict:
    """channels.list(mine=true): the token's channel must match the profile's expected handle (when one is set)."""
    items = service.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise ChannelMismatch("this Google account has no YouTube channel")
    sn = items[0]["snippet"]
    info = {"id": items[0].get("id"), "title": sn.get("title"), "handle": sn.get("customUrl")}
    if expected_handle and _norm_handle(info["handle"]) != _norm_handle(expected_handle):
        raise ChannelMismatch(f"authorised channel is {info['handle']} ({info['title']}) but the profile expects "
                              f"{expected_handle}; re-run auth with the right Google account")
    return info


def build_service(channel: str, interactive: bool = False, expected_handle: str | None = None):
    from googleapiclient.discovery import build

    service = build("youtube", "v3", credentials=get_credentials(channel, interactive), cache_discovery=False)
    verify_channel(service, expected_handle)
    return service


def build_body(meta: dict, privacy: str = "public", publish_at: str | None = None) -> dict:
    tags = [t.lstrip("#") for t in meta.get("tags", [])]
    desc = meta.get("description", "")
    if "#shorts" not in desc.lower():
        desc = f"{desc}\n\n#Shorts".strip()
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": bool(meta.get("ai_disclosure", True))}
    if publish_at:  # YouTube requires private + RFC3339 time
        status.update(privacyStatus="private", publishAt=publish_at)
    return {"snippet": {"title": meta["title"][:100], "description": desc[:5000], "tags": tags[:30], "categoryId": "22"},
            "status": status}


def _http_error_reason(err) -> tuple[int, str]:
    status = getattr(getattr(err, "resp", None), "status", 0)
    try:
        return status, json.loads(err.content.decode())["error"]["errors"][0]["reason"]
    except Exception:  # noqa: BLE001
        return status, ""


def upload_short(service, path: str | Path, meta: dict, privacy: str = "public", publish_at: str | None = None,
                 max_retries: int = 5, sleep=time.sleep) -> str:
    """Resumable upload; returns the video id. Raises QuotaExceeded on quota/limit errors."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    req = service.videos().insert(part="snippet,status", body=build_body(meta, privacy, publish_at),
                                  media_body=MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True))
    response, retries = None, 0
    while response is None:
        try:
            _, response = req.next_chunk()
        except HttpError as e:
            code, reason = _http_error_reason(e)
            if reason in QUOTA_REASONS:
                raise QuotaExceeded(reason) from e
            if code in RETRY_STATUS and retries < max_retries:
                retries += 1
                sleep(min(60, 2 ** retries + random.random()))
                continue
            raise
        except (ConnectionError, TimeoutError):
            if retries >= max_retries:
                raise
            retries += 1
            sleep(min(60, 2 ** retries))
    if "id" not in response:
        raise RuntimeError(f"unexpected upload response: {response}")
    return response["id"]


# ---- playlists: one per series, ids cached so repeated runs are idempotent --------------
def _state_file(channel: str, state_dir: Path) -> Path:
    return Path(state_dir) / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_playlists.json"


def _save_cache(sf: Path, cache: dict) -> None:
    sf.parent.mkdir(parents=True, exist_ok=True)
    tmp = sf.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, sf)


def ensure_playlist(service, channel: str, title: str, video_privacy: str = "public", state_dir: Path = STATE_DIR) -> str:
    """Find or create the playlist `title`; public only when the video is public, otherwise private."""
    sf = _state_file(channel, state_dir)
    try:
        cache = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    want = "public" if video_privacy == "public" else "private"
    entry = cache.get(title)
    if entry:
        if want == "public" and entry.get("privacy") != "public":  # a public video must not sit in a private list
            service.playlists().update(part="snippet,status", body={
                "id": entry["id"], "snippet": {"title": title}, "status": {"privacyStatus": "public"}}).execute()
            entry["privacy"] = "public"
            _save_cache(sf, cache)
        return entry["id"]
    pid, privacy, token = None, want, None
    while pid is None:  # reuse an existing playlist with this title (cache lost, or made by hand)
        r = service.playlists().list(part="snippet,status", mine=True, maxResults=50, pageToken=token).execute()
        for p in r.get("items", []):
            if p["snippet"]["title"].strip().lower() == title.strip().lower():
                pid, privacy = p["id"], p.get("status", {}).get("privacyStatus", want)
                break
        token = r.get("nextPageToken")
        if pid or not token:
            break
    if pid is None:
        pid = service.playlists().insert(part="snippet,status", body={
            "snippet": {"title": title, "description": f"{title} - AI-generated series."},
            "status": {"privacyStatus": want}}).execute()["id"]
    cache[title] = {"id": pid, "privacy": privacy}
    _save_cache(sf, cache)
    return pid


def add_to_series_playlist(service, channel: str, series: str, video_id: str, video_privacy: str = "public",
                           state_dir: Path = STATE_DIR) -> str:
    """Add the video to its series playlist unless it is already there; returns the playlist id."""
    pid = ensure_playlist(service, channel, series, video_privacy, state_dir)
    have = service.playlistItems().list(part="id", playlistId=pid, videoId=video_id, maxResults=1).execute().get("items", [])
    if not have:
        service.playlistItems().insert(part="snippet", body={"snippet": {
            "playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}).execute()
    return pid


def fetch_stats(service, video_ids: list[str]) -> dict[str, dict]:
    """views/likes/comments per video id (1 quota unit per call, 50 ids max)."""
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        r = service.videos().list(part="statistics", id=",".join(video_ids[i:i + 50])).execute()
        for v in r.get("items", []):
            s = v.get("statistics", {})
            out[v["id"]] = {k: int(s.get(k, 0)) for k in ("viewCount", "likeCount", "commentCount")}
    return out


def main(argv: list[str] | None = None) -> int:
    from aurora import load_env

    load_env()
    ap = argparse.ArgumentParser(description="YouTube one-time consent")
    ap.add_argument("cmd", choices=["auth"])
    ap.add_argument("--channel", required=True)
    ap.add_argument("--expected-handle", help="default: expected_handle of this channel in config.yaml")
    a = ap.parse_args(argv)
    expected = a.expected_handle
    if expected is None:
        try:
            import yaml

            cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
            expected = next((c.get("expected_handle") for c in (cfg.get("autopilot") or {}).get("channels", [])
                             if c.get("name") == a.channel), None)
        except OSError:
            expected = None
    get_credentials(a.channel, interactive=True)
    try:
        info = verify_channel(build_service(a.channel), expected)
    except ChannelMismatch as e:
        token_path(a.channel).unlink(missing_ok=True)  # never keep a token for the wrong channel
        print(f"[youtube] WRONG CHANNEL, token deleted: {e}")
        return 2
    print(f"[youtube] token saved: {token_path(a.channel)} -> {info['handle']} ({info['title']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
