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
DEFAULTS = {"enabled": False, "approval": "veto", "veto_hours": 6.0, "slots": ["19:00"], "timezone": "Asia/Kolkata", "max_attempts": 3}
ACTIVE = {"pending_approval", "scheduled", "publishing", "published"}  # statuses that block re-enqueueing a sha
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

    # ---- scheduling ----------------------------------------------
    def next_slot(self, channel: str, now: datetime | None = None, taken: list[datetime] | None = None) -> datetime:
        """Earliest configured slot >= now + veto_hours that this channel hasn't used yet (UTC)."""
        now = now or _now()
        earliest = now + timedelta(hours=float(self.s["veto_hours"]) if self.s["approval"] == "veto" else 0)
        tz = _tz(self.s["timezone"])
        used = {t.astimezone(timezone.utc).replace(second=0, microsecond=0) for t in (taken or [])}
        day = earliest.astimezone(tz).date()
        for offset in range(0, 60):
            for slot in sorted(self.s["slots"]):
                hh, mm = (int(x) for x in slot.split(":"))
                cand = datetime(2000, 1, 1, hh, mm).replace(year=day.year, month=day.month, day=day.day, tzinfo=tz)
                cand = (cand + timedelta(days=offset)).astimezone(timezone.utc)
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
                     and i["status"] in ("scheduled", "pending_approval", "publishing")]
            mode = self.s["approval"]
            item = {
                "id": uuid.uuid4().hex[:12], "channel": channel, "platform": platform, "path": str(path), "sha256": sha,
                "title": (meta.get("title") or path.stem)[:100], "description": meta.get("description", ""),
                "tags": list(meta.get("tags", [])), "ai_disclosure": bool(meta.get("ai_disclosure", True)),
                "privacy": meta.get("privacy", "public"), "approval": mode,
                "status": "pending_approval" if mode == "manual" else "scheduled",
                "publish_at": self.next_slot(channel, now, taken).isoformat(), "created_at": now.isoformat(),
                "attempts": 0, "video_id": None, "error": None, "stats": None,
            }
            items.append(item)
            self._save(items)
            return item

    # ---- user actions --------------------------------------------
    def veto(self, item_id: str) -> dict:
        it = self.get(item_id)
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
                            publish_at=self.next_slot(it["channel"], taken=taken).isoformat())

    def reschedule(self, item_id: str, when: datetime) -> dict:
        return self._update(item_id, publish_at=when.astimezone(timezone.utc).isoformat())

    # ---- publisher side ------------------------------------------
    def due(self, now: datetime | None = None, channel: str | None = None) -> list[dict]:
        now = now or _now()
        return sorted((i for i in self.list(channel, "scheduled") if datetime.fromisoformat(i["publish_at"]) <= now),
                      key=lambda i: i["publish_at"])

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

    def defer(self, item_id: str, delta: timedelta, error: str) -> dict:
        """Quota hit: put back without counting an attempt."""
        it = self.get(item_id)
        return self._update(item_id, status="scheduled", attempts=max(0, it["attempts"] - 1), error=error[:500],
                            publish_at=(_now() + delta).isoformat())

    def set_stats(self, item_id: str, stats: dict) -> dict:
        return self._update(item_id, stats=stats)
