"""Per-day YouTube Data API quota ledger (default 10,000 units/day, resets at midnight Pacific).

Every call that costs quota goes through `QuotaBudget.spend(op)`; when the day's safety limit would be exceeded it raises
`QuotaExceeded` BEFORE the call, so callers defer instead of hitting Google's 403. Ledger: outputs/publish_state/quota_<date>.json.
YouTube Analytics API has its own, separate quota (not tracked here).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / "outputs" / "publish_state"
DAILY_LIMIT = 10000
SAFETY_LIMIT = 9000  # leave headroom for manual Studio/API use
COSTS = {
    "videos.insert": 1600, "videos.update": 50, "captions.insert": 400, "thumbnails.set": 50, "search.list": 100,
    "playlists.insert": 50, "playlists.update": 50, "playlistItems.insert": 50, "channels.update": 50,
    "comments.insert": 50, "comments.setModerationStatus": 50,
    "videos.list": 1, "playlists.list": 1, "playlistItems.list": 1, "commentThreads.list": 1, "channels.list": 1,
}


def pacific_day(now: datetime | None = None) -> str:
    """Quota day label. Pacific = UTC-8 (UTC-7 in summer); a fixed -8 is the conservative choice."""
    return ((now or datetime.now(timezone.utc)) + timedelta(hours=-8)).date().isoformat()


class QuotaBudget:
    def __init__(self, state_dir: Path = STATE_DIR, limit: int = SAFETY_LIMIT, now=None):
        self.dir, self.limit, self._now = Path(state_dir), limit, now

    @property
    def path(self) -> Path:
        return self.dir / f"quota_{pacific_day(self._now() if self._now else None)}.json"

    def used(self) -> int:
        try:
            return int(json.loads(self.path.read_text(encoding="utf-8")).get("used", 0))
        except (OSError, ValueError):
            return 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def can_afford(self, *ops: str) -> bool:
        return sum(COSTS[o] for o in ops) <= self.remaining()

    def spend(self, op: str, times: int = 1) -> None:
        from aurora.publish.youtube import QuotaExceeded

        cost = COSTS[op] * times
        used = self.used()
        if used + cost > self.limit:
            raise QuotaExceeded(f"daily budget: {used}+{cost} > {self.limit} units ({op})")
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"used": used + cost}), encoding="utf-8")
        os.replace(tmp, self.path)
