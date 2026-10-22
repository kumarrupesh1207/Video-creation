"""YouTube Shorts uploader (YouTube Data API v3, OAuth installed-app flow).

One token file per channel => channels never mix. One-time consent: `python -m aurora.publish.youtube auth --channel NAME`.
client_secret.json path comes from env YOUTUBE_CLIENT_SECRET (.env). videos.insert costs ~1600 quota units (default 10000/day).
`status.containsSyntheticMedia` is the API field for YouTube's altered/synthetic-content disclosure (present in the v3 discovery doc).
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
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_DIR = ROOT / "outputs" / "tokens"  # outputs/ is git-ignored
RETRY_STATUS = {500, 502, 503, 504}
QUOTA_REASONS = {"quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded", "rateLimitExceeded"}


class QuotaExceeded(Exception):
    """Daily quota / upload limit hit: stop uploading until tomorrow."""


class AuthRequired(Exception):
    """No valid token: run the one-time consent for this channel."""


def token_path(channel: str) -> Path:
    return TOKEN_DIR / f"youtube_{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}.json"


def get_credentials(channel: str, interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    tp = token_path(channel)
    creds = Credentials.from_authorized_user_file(str(tp), SCOPES) if tp.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
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


def build_service(channel: str, interactive: bool = False):
    from googleapiclient.discovery import build

    return build("youtube", "v3", credentials=get_credentials(channel, interactive), cache_discovery=False)


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
    a = ap.parse_args(argv)
    get_credentials(a.channel, interactive=True)
    print(f"[youtube] token saved: {token_path(a.channel)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
