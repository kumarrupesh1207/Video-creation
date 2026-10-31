"""Facebook Reels via the Graph API (Pages only). Uses the same Meta error handling and caption as Instagram.

Per-channel credentials in outputs/tokens/facebook_<channel>.json (git-ignored): {"page_id": "...", "access_token": "..."}
Flow: POST /{page}/video_reels (start) -> POST rupload.facebook.com (file bytes, no public hosting needed)
-> POST /{page}/video_reels (finish, PUBLISHED) -> poll GET /{video}?fields=status.
Meta limits: 3-90 s, 9:16, 30 API posts per 24 h.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from aurora.publish.instagram import GRAPH, InstagramError, _check, build_caption
from aurora.publish.youtube import TOKEN_DIR

RUPLOAD = GRAPH.replace("graph.facebook.com", "rupload.facebook.com/video-upload")
MIN_SECS, MAX_SECS = 3, 90


class ReelOutOfRange(InstagramError):
    """Meta rejects this length: retrying can never work."""


def account_path(channel: str) -> Path:
    return TOKEN_DIR / f"facebook_{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}.json"


def load_account(channel: str) -> dict:
    p = account_path(channel)
    if not p.exists():
        raise InstagramError(f"no Facebook account file for '{channel}': create {p} with page_id + access_token")
    d = json.loads(p.read_text(encoding="utf-8"))
    if not d.get("page_id") or not d.get("access_token"):
        raise InstagramError(f"{p} needs page_id and access_token")
    return d


def check_duration(meta: dict) -> None:
    secs = meta.get("duration_secs")
    if secs is not None and not MIN_SECS <= float(secs) <= MAX_SECS:
        raise ReelOutOfRange(f"Facebook Reels must be {MIN_SECS}-{MAX_SECS} s, this one is {float(secs):.0f} s")


def _published(status: dict) -> bool:
    if status.get("video_status") in ("error", "expired", "upload_failed") or \
            any((status.get(p) or {}).get("status") == "error" for p in ("uploading_phase", "processing_phase", "publishing_phase")):
        raise InstagramError(f"reel failed: {status}")
    phase = status.get("publishing_phase") or {}
    return phase.get("publish_status") == "published" or phase.get("status") in ("complete", "completed")


def publish_reel(account: dict, path: str | Path, meta: dict, http=None, sleep=time.sleep,
                 poll_secs: float = 10, max_polls: int = 60) -> str:
    """Upload and publish; returns the video id."""
    import requests

    check_duration(meta)
    http = http or requests
    tok, page = account["access_token"], account["page_id"]
    vid = _check(http.post(f"{GRAPH}/{page}/video_reels", data={"upload_phase": "start", "access_token": tok}, timeout=60))["video_id"]
    with open(path, "rb") as f:
        _check(http.post(f"{RUPLOAD}/{vid}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(os.path.getsize(path)),
            "Content-Type": "application/octet-stream"}))
    _check(http.post(f"{GRAPH}/{page}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED", "description": build_caption(meta),
        "is_ai_generated": str(bool(meta.get("ai_disclosure", True))).lower(), "access_token": tok}, timeout=60))
    # finish was accepted, so a retry would post it twice: only a failed status raises from here on
    for _ in range(max_polls):
        try:
            s = _check(http.get(f"{GRAPH}/{vid}", params={"fields": "status", "access_token": tok}, timeout=60))
        except Exception as e:  # noqa: BLE001 - a failed check says nothing about the Reel
            print(f"[facebook] {vid}: status check failed: {e}")
            s = {}
        if _published(s.get("status") or {}):
            return vid
        sleep(poll_secs)
    print(f"[facebook] {vid}: publish accepted, still processing after {max_polls} checks")
    return vid


def fetch_stats(account: dict, video_ids: list[str], http=None) -> dict[str, dict]:
    import requests

    http = http or requests
    out = {}
    for v in video_ids:
        d = _check(http.get(f"{GRAPH}/{v}", params={"fields": "likes.summary(true).limit(0),comments.summary(true).limit(0)",
                                                     "access_token": account["access_token"]}, timeout=60))
        out[v] = {"likeCount": int(d.get("likes", {}).get("summary", {}).get("total_count", 0)),
                  "commentCount": int(d.get("comments", {}).get("summary", {}).get("total_count", 0))}
    return out
