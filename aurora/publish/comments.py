"""Comment moderation: spam is held for review, replies are DRAFTED and only sent after the user approves each one.

State per channel: outputs/publish_state/<channel>_comments.json
  {"comments": {id: {"author","text","video_id","status": "new|held|draft|approved|rejected|sent|ignored","reply"}}, "seen": [...]}
Nothing is ever posted automatically: `send_approved` posts only comments whose status is "approved" (set by the user via UI/CLI).
Quota: commentThreads.list 1, setModerationStatus 50 (one call for all spam ids), comments.insert 50 per reply.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from aurora.publish.youtube import STATE_DIR

SPAM_WORDS = ("whatsapp", "telegram", "t.me/", "subscribe to my", "sub4sub", "check my channel", "giveaway", "crypto", "bitcoin",
              "forex", "investment", "earn $", "dm me", "porn", "onlyfans", "free followers", "promo")
MAX_REPLY = 300


def _file(channel: str, state_dir: Path) -> Path:
    return Path(state_dir) / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_comments.json"


def load(channel: str, state_dir: Path = STATE_DIR) -> dict:
    try:
        return json.loads(_file(channel, state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"comments": {}}


def save(channel: str, state: dict, state_dir: Path = STATE_DIR) -> None:
    f = _file(channel, state_dir)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def is_spam(text: str) -> bool:
    t = text.lower()
    if any(w in t for w in SPAM_WORDS):
        return True
    if len(re.findall(r"https?://|www\.", t)) >= 1 and len(t) < 200:
        return True
    letters = [c for c in text if c.isalpha()]
    return len(text) > 25 and bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.8  # SHOUTING promo


def fetch_threads(service, channel_id: str, budget=None, max_results: int = 50) -> list[dict]:
    """Newest top-level comments across the channel."""
    if budget:
        budget.spend("commentThreads.list")
    r = service.commentThreads().list(part="snippet", allThreadsRelatedToChannelId=channel_id, order="time",
                                      maxResults=max_results, textFormat="plainText").execute()
    out = []
    for t in r.get("items", []):
        top = t["snippet"]["topLevelComment"]
        sn = top["snippet"]
        if sn.get("authorChannelId", {}).get("value") == channel_id:  # our own replies/comments
            continue
        out.append({"id": top["id"], "author": sn.get("authorDisplayName", ""), "text": sn.get("textDisplay", ""),
                    "video_id": sn.get("videoId", ""), "replies": int(t["snippet"].get("totalReplyCount", 0))})
    return out


def draft_reply(text: str, author: str, client=None, cfg: dict | None = None) -> str:
    """Short friendly reply draft (Gemini when available, else a neutral template). No links, no promises, <=300 chars."""
    template = f"Thanks for watching, {author or 'friend'}! More of the story is coming soon."
    cfg = cfg or {}
    key = os.getenv((cfg.get("research", {}) or {}).get("api_key_env", "GEMINI_API_KEY"), "").strip()
    if client is None and not key:
        return template
    try:
        if client is None:
            from google import genai

            client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=(cfg.get("research", {}) or {}).get("model", "gemini-3.1-flash-lite"),
            contents=("Write one short, warm YouTube reply (max 200 characters, English, at most one emoji, no links, no promises "
                      f"about release dates) to this comment on an anime story channel: {text[:400]!r}. Reply with the text only."))
        out = re.sub(r"https?://\S+", "", (resp.text or "").strip()).strip()
        return out[:MAX_REPLY] or template
    except Exception:  # noqa: BLE001
        return template


def process(service, channel: str, channel_id: str, budget=None, client=None, cfg: dict | None = None,
            state_dir: Path = STATE_DIR) -> dict:
    """Fetch new comments, hold spam (one API call), draft replies for the rest. Posts nothing."""
    state = load(channel, state_dir)
    known = state["comments"]
    new = [c for c in fetch_threads(service, channel_id, budget) if c["id"] not in known]
    spam = [c for c in new if is_spam(c["text"])]
    if spam:
        if budget:
            budget.spend("comments.setModerationStatus")
        service.comments().setModerationStatus(id=",".join(c["id"] for c in spam), moderationStatus="heldForReview").execute()
    spam_ids = {c["id"] for c in spam}
    for c in new:
        if c["id"] in spam_ids:
            known[c["id"]] = {**c, "status": "held", "reply": None}
        else:
            known[c["id"]] = {**c, "status": "draft", "reply": draft_reply(c["text"], c["author"], client, cfg)}
    save(channel, state, state_dir)
    return {"new": len(new), "held": len(spam), "drafted": len(new) - len(spam)}


def pending(channel: str, state_dir: Path = STATE_DIR) -> list[dict]:
    return [{"id": k, **v} for k, v in load(channel, state_dir)["comments"].items() if v["status"] == "draft"]


def approve(channel: str, comment_id: str, reply: str | None = None, state_dir: Path = STATE_DIR) -> dict:
    """User approves (optionally after editing) a drafted reply; it is sent by the next `send_approved`."""
    state = load(channel, state_dir)
    c = state["comments"].get(comment_id)
    if not c or c["status"] not in ("draft", "approved"):
        raise ValueError("no such draft")
    text = re.sub(r"https?://\S+", "", (reply if reply is not None else c["reply"]) or "").strip()[:MAX_REPLY]
    if not text:
        raise ValueError("empty reply")
    c.update(status="approved", reply=text)
    save(channel, state, state_dir)
    return c


def reject(channel: str, comment_id: str, state_dir: Path = STATE_DIR) -> dict:
    state = load(channel, state_dir)
    c = state["comments"].get(comment_id)
    if not c:
        raise ValueError("no such comment")
    c["status"] = "rejected"
    save(channel, state, state_dir)
    return c


def send_approved(service, channel: str, budget=None, state_dir: Path = STATE_DIR) -> list[str]:
    """Post replies the user approved (only those). Idempotent: a sent reply is never sent twice."""
    from aurora.publish.youtube import QuotaExceeded

    state, sent = load(channel, state_dir), []
    for cid, c in state["comments"].items():
        if c["status"] != "approved":
            continue
        try:
            if budget:
                budget.spend("comments.insert")
        except QuotaExceeded:
            break
        service.comments().insert(part="snippet", body={"snippet": {"parentId": cid, "textOriginal": c["reply"]}}).execute()
        c["status"] = "sent"
        save(channel, state, state_dir)  # persist per reply so a crash cannot double-send
        sent.append(cid)
    return sent
