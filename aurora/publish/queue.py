"""Publish queue with a veto window. Persisted to outputs/publish_queue.json (atomic writes).

Statuses: pending_approval (manual mode) -> scheduled -> publishing -> published | failed
          scheduled/pending_approval -> vetoed (user tapped stop; `approve` restores it).
Approval modes: veto (default; publishes after veto_hours unless vetoed), manual (needs approve()), auto (next slot).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PATH = ROOT / "outputs" / "publish_queue.json"
IST = timezone(timedelta(hours=5, minutes=30), "IST")
DEFAULTS = {"enabled": False, "upload_ahead": False, "approval": "veto", "veto_hours": 6.0, "slots": ["19:00"], "timezone": "Asia/Kolkata", "max_attempts": 3}
EXTRA_KEYS = ("captions_srt", "thumbnail", "duration_secs", "part", "chapters", "chapter", "playlist", "story", "engagement_question")  # render outputs the publisher uses
ACTIVE = {"pending_approval", "scheduled", "uploaded_scheduled", "publishing", "published"}  # statuses that block re-enqueueing a sha
_lock = threading.RLock()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - tzdata missing on Windows
        return IST if name == "Asia/Kolkata" else timezone.utc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def settings(cfg: dict | None) -> dict:
    return {**DEFAULTS, **((cfg or {}).get("autopilot") or {}).get("publish", {})}


class PublishQueue:
    def __init__(self, path: Path = QUEUE_PATH, cfg: dict | None = None):
        self.path = Path(path)
        self.s = settings(cfg)
        # YouTube handle each channel profile must authorise as (config autopilot.channels[].expected_handle)
        self.channel_cfg = {c["name"]: c for c in ((cfg or {}).get("autopilot") or {}).get("channels", []) if c.get("name")}
        self.expected_handles = {c["name"]: c["expected_handle"]
                                 for c in ((cfg or {}).get("autopilot") or {}).get("channels", [])
                                 if c.get("name") and c.get("expected_handle")}

    # ---- storage -------------------------------------------------
    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8")).get("items", [])
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def list(self, channel: str | None = None, status: str | None = None) -> list[dict]:
        with _lock:
            return [i for i in self._load() if (not channel or i["channel"] == channel) and (not status or i["status"] == status)]

    def get(self, item_id: str) -> dict | None:
        return next((i for i in self.list() if i["id"] == item_id), None)

    def _update(self, item_id: str, **fields) -> dict:
        with _lock:
            items = self._load()
            for i in items:
                if i["id"] == item_id:
                    i.update(fields)
                    self._save(items)
                    return i
        raise KeyError(item_id)

    def publish_enabled(self, channel: str) -> bool:
        """Per-channel `publish_enabled` wins; otherwise the global autopilot.publish.enabled (default false)."""
        flag = self.channel_cfg.get(channel, {}).get("publish_enabled")
        return bool(self.s.get("enabled", False)) if flag is None else bool(flag)

    def series_entry(self, channel: str, story: str | None) -> dict | None:
        for e in self.channel_cfg.get(channel, {}).get("series") or []:
            e = {"slug": e} if isinstance(e, str) else e
            if e.get("slug") == story:
                return e
        return None

    def story_enabled(self, channel: str, story: str | None) -> bool:
        """A series switched off in config (`enabled: false` or `per_day: 0`) is never published, even if parts exist."""
        e = self.series_entry(channel, story)
        return e is None or (e.get("enabled", True) is not False and int(e.get("per_day", 1)) > 0)

    # ---- scheduling ----------------------------------------------
    def next_slot(self, channel: str, now: datetime | None = None, taken: list[datetime] | None = None,
                  kind: str = "short", story: str | None = None) -> datetime:
        """Earliest configured slot >= now + veto_hours that this channel hasn't used yet (UTC).
        Per-channel `slots` override the global ones; kind "long" uses the channel's weekly `long_episode` slot."""
        now = now or _now()
        earliest = now + timedelta(hours=float(self.s["veto_hours"]) if self.s["approval"] == "veto" else 0)
        tz = _tz(self.s["timezone"])
        ccfg = self.channel_cfg.get(channel, {})
        if kind == "long" and ccfg.get("long_episode"):
            return self._next_weekly(ccfg["long_episode"], earliest, tz, taken)
        slots = (self.series_entry(channel, story) or {}).get("slots") or ccfg.get("slots") or self.s["slots"]
        used = {t.astimezone(timezone.utc).replace(second=0, microsecond=0) for t in (taken or [])}
        day = earliest.astimezone(tz).date()
        for offset in range(0, 60):
            for slot in sorted(slots):
                hh, mm = (int(x) for x in slot.split(":"))
                cand = datetime(2000, 1, 1, hh, mm).replace(year=day.year, month=day.month, day=day.day, tzinfo=tz)
                cand = (cand + timedelta(days=offset)).astimezone(timezone.utc)
                if cand >= earliest and cand not in used:
                    return cand
        return earliest

    @staticmethod
    def _next_weekly(spec: dict, earliest: datetime, tz, taken: list[datetime] | None) -> datetime:
        names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        wd = spec.get("weekday", "saturday")
        wd = names.index(str(wd).lower()) if str(wd).lower() in names else int(wd)
        hh, mm = (int(x) for x in str(spec.get("time", "18:00")).split(":"))
        used = {t.astimezone(timezone.utc).replace(second=0, microsecond=0) for t in (taken or [])}
        day = earliest.astimezone(tz).date()
        for offset in range(0, 60):
            d = day + timedelta(days=offset)
            if d.weekday() != wd:
                continue
            cand = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).astimezone(timezone.utc)
            if cand >= earliest and cand not in used:
                return cand
        return earliest

    def enqueue(self, channel: str, video_path: str | Path, meta: dict, now: datetime | None = None,
                platform: str = "youtube") -> dict | None:
        """Add a finished reel for one platform. Returns None if the same file (sha256) is already queued/published there."""
        path = Path(video_path)
        sha = meta.get("sha256") or sha256_file(path)
        now = now or _now()
        with _lock:
            items = self._load()
            if any(i["sha256"] == sha and i["channel"] == channel and i.get("platform", "youtube") == platform
                   and i["status"] in ACTIVE for i in items):
                return None
            taken = [datetime.fromisoformat(i["publish_at"]) for i in items
                     if i["channel"] == channel and i.get("platform", "youtube") == platform
                     and i["status"] in ("scheduled", "pending_approval", "uploaded_scheduled", "publishing")]
            mode = self.s["approval"]
            item = {
                "id": uuid.uuid4().hex[:12], "channel": channel, "platform": platform, "path": str(path), "sha256": sha,
                "title": (meta.get("title") or path.stem)[:100], "description": meta.get("description", ""),
                "tags": list(meta.get("tags", [])), "ai_disclosure": bool(meta.get("ai_disclosure", True)),
                "privacy": meta.get("privacy", "public"), "approval": mode, "series": meta.get("series"),
                **{k: meta[k] for k in EXTRA_KEYS if meta.get(k) is not None},
                "status": "pending_approval" if mode == "manual" else "scheduled",
                "kind": meta.get("kind", "short"),
                "publish_at": self.next_slot(channel, now, taken, meta.get("kind", "short"), meta.get("story")).isoformat(),
                "created_at": now.isoformat(),
                "veto_until": (now + timedelta(hours=float(self.s["veto_hours"]) if mode == "veto" else 0)).isoformat(),
                "attempts": 0, "video_id": None, "error": None, "stats": None,
            }
            if meta.get("publish_at_hint"):  # e.g. chapter compilation: Saturday 18:00 IST, never inside the veto window
                earliest = now + timedelta(hours=float(self.s["veto_hours"]) if mode == "veto" else 0)
                item["publish_at"] = max(datetime.fromisoformat(meta["publish_at_hint"]), earliest).isoformat()
            items.append(item)
            self._save(items)
            return item

    # ---- user actions --------------------------------------------
    def veto(self, item_id: str) -> dict:
        it = self.get(item_id)
        if it and it["status"] == "uploaded_scheduled":
            raise ValueError("already uploaded: use aurora.autopilot.veto_item (removes publishAt on YouTube)")
        if not it or it["status"] not in ("scheduled", "pending_approval"):
            raise ValueError("only scheduled/pending items can be vetoed")
        return self._update(item_id, status="vetoed")

    def approve(self, item_id: str) -> dict:
        """manual mode: approve -> publish at the next slot. Also restores a vetoed item."""
        it = self.get(item_id)
        if not it or it["status"] not in ("pending_approval", "vetoed"):
            raise ValueError("nothing to approve")
        taken = [datetime.fromisoformat(i["publish_at"]) for i in self.list(it["channel"])
                 if i["status"] == "scheduled" and i.get("platform", "youtube") == it.get("platform", "youtube")]
        return self._update(item_id, status="scheduled", error=None,
                            publish_at=self.next_slot(it["channel"], taken=taken, story=it.get("story")).isoformat())

    def reschedule(self, item_id: str, when: datetime) -> dict:
        return self._update(item_id, publish_at=when.astimezone(timezone.utc).isoformat())

    # ---- publisher side ------------------------------------------
    def due(self, now: datetime | None = None, channel: str | None = None) -> list[dict]:
        now = now or _now()
        return sorted((i for i in self.list(channel, "scheduled") if datetime.fromisoformat(i["publish_at"]) <= now),
                      key=lambda i: i["publish_at"])

    def uploadable(self, now: datetime | None = None, channel: str | None = None) -> list[dict]:
        """Scheduled items whose veto window has passed (upload-ahead: they may be uploaded before their slot)."""
        now = now or _now()
        return sorted((i for i in self.list(channel, "scheduled")
                       if datetime.fromisoformat(i.get("veto_until") or i["publish_at"]) <= now), key=lambda i: i["publish_at"])

    def mark_uploaded_scheduled(self, item_id: str, video_id: str) -> dict:
        return self._update(item_id, status="uploaded_scheduled", video_id=video_id, error=None,
                            uploaded_at=_now().isoformat())

    def mark_publishing(self, item_id: str) -> dict:
        it = self.get(item_id)
        return self._update(item_id, status="publishing", attempts=it["attempts"] + 1)

    def mark_published(self, item_id: str, video_id: str) -> dict:
        return self._update(item_id, status="published", video_id=video_id, error=None,
                            published_at=_now().isoformat())

    def mark_failed(self, item_id: str, error: str, retry_in: timedelta | None = timedelta(hours=1)) -> dict:
        """Retry later until max_attempts, then park as failed."""
        it = self.get(item_id)
        if retry_in is not None and it["attempts"] < int(self.s["max_attempts"]):
            return self._update(item_id, status="scheduled", error=error[:500],
                                publish_at=(_now() + retry_in).isoformat())
        return self._update(item_id, status="failed", error=error[:500])

    def hold(self, item_id: str, error: str) -> dict:
        """Not uploaded this run (daily budget used up): stays scheduled with its slot untouched, no attempt counted."""
        it = self.get(item_id)
        return self._update(item_id, status="scheduled", attempts=max(0, it["attempts"] - 1), error=error[:500])

    def defer(self, item_id: str, delta: timedelta, error: str) -> dict:
        """Quota hit: put back without counting an attempt."""
        it = self.get(item_id)
        return self._update(item_id, status="scheduled", attempts=max(0, it["attempts"] - 1), error=error[:500],
                            publish_at=(_now() + delta).isoformat())

    def set_stats(self, item_id: str, stats: dict) -> dict:
        return self._update(item_id, stats=stats)
