"""Instagram Reels via the Instagram Graph API (Business/Creator account linked to a Facebook Page).

Per-channel credentials in outputs/tokens/instagram_<channel>.json (git-ignored): {"ig_user_id": "...", "access_token": "..."}
Flow: POST /{ig-user}/media (REELS, public video_url) -> poll status_code=FINISHED -> POST /{ig-user}/media_publish.
Meta has no API field for the "AI info" label, so the caption carries an explicit AI-generated line.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from aurora.publish.youtube import TOKEN_DIR

GRAPH = "https://graph.facebook.com/v26.0"
RATE_LIMIT_CODES = {4, 17, 32, 613, 9}  # app/user/page call limits; 9 = Reels publish limit (about 25/day)
YOUTUBE_ONLY = re.compile(r"playlist|https?://|youtu\.?be|ai-generated", re.I)  # dropped from Meta captions


class InstagramError(Exception):
    pass


class RateLimited(InstagramError):
    pass


def account_path(channel: str) -> Path:
    return TOKEN_DIR / f"instagram_{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}.json"


def load_account(channel: str) -> dict:
    p = account_path(channel)
    if not p.exists():
        raise InstagramError(f"no Instagram account file for '{channel}': create {p} with ig_user_id + access_token")
    d = json.loads(p.read_text(encoding="utf-8"))
    if not d.get("ig_user_id") or not d.get("access_token"):
        raise InstagramError(f"{p} needs ig_user_id and access_token")
    return d


def build_caption(meta: dict) -> str:
    """Instagram/Facebook text from the YouTube metadata: no playlist lines or links; hashtags and the one disclosure line last."""
    desc = meta.get("description", "")
    lines = desc.splitlines()
    is_tags = lambda ln: bool(ln.strip()) and all(w.startswith("#") for w in ln.split())  # noqa: E731
    text = "\n".join(ln for ln in lines if not is_tags(ln) and not YOUTUBE_ONLY.search(ln))
    disclose = meta.get("ai_disclosure", True) or "ai-generated" in desc.lower()
    tags = list(dict.fromkeys(w for ln in lines if is_tags(ln) for w in ln.split())) or [
        t if t.startswith("#") else f"#{t}" for t in meta.get("tags", []) if " " not in t]
    tail = ("AI-generated content" if disclose else "", " ".join(tags))
    body = "\n\n".join(p.strip() for p in re.split(r"\n\s*\n", text) if p.strip())
    limit = 2200 - sum(len(t) + 2 for t in tail)
    return "\n\n".join(p for p in (meta.get("title", ""), body) if p)[:limit].strip() + "".join(f"\n\n{t}" for t in tail if t)


def _check(resp) -> dict:
    data = resp.json() if resp.content else {}
    err = data.get("error")
    if err or resp.status_code >= 400:
        err = err or {}
        msg = f"{err.get('code')}/{err.get('error_subcode')}: {err.get('message', resp.text[:200])}"
        raise (RateLimited if err.get("code") in RATE_LIMIT_CODES else InstagramError)(msg)
    return data


def publish_reel(account: dict, video_url: str, meta: dict, http=None, sleep=time.sleep,
                 poll_secs: float = 10, max_polls: int = 60) -> str:
    """Create container, wait until processed, publish; returns the IG media id."""
    import requests

    http = http or requests
    tok, uid = account["access_token"], account["ig_user_id"]
    c = _check(http.post(f"{GRAPH}/{uid}/media", data={
        "media_type": "REELS", "video_url": video_url, "caption": build_caption(meta),
        "share_to_feed": "true", "access_token": tok}, timeout=60))
    cid = c["id"]
    for _ in range(max_polls):
        s = _check(http.get(f"{GRAPH}/{cid}", params={"fields": "status_code,status", "access_token": tok}, timeout=60))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise InstagramError(f"container {s.get('status_code')}: {s.get('status')}")
        sleep(poll_secs)
    else:
        raise InstagramError("container not FINISHED in time")
    return _check(http.post(f"{GRAPH}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}, timeout=60))["id"]


def fetch_stats(account: dict, media_ids: list[str], http=None) -> dict[str, dict]:
    import requests

    http = http or requests
    out = {}
    for m in media_ids:
        d = _check(http.get(f"{GRAPH}/{m}", params={"fields": "like_count,comments_count",
                                                     "access_token": account["access_token"]}, timeout=60))
        out[m] = {"likeCount": int(d.get("like_count", 0)), "commentCount": int(d.get("comments_count", 0))}
    return out
