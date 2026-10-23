"""YouTube Analytics API (v2): weekly report + top performers that feed research.

Needs the extra scope `yt-analytics.readonly` (one more consent click: `auth --channel <name>`; tokens without it get a clear
"re-run auth" message). Analytics has its own quota; Data API calls here are budgeted via QuotaBudget.
Files: outputs/publish_state/reports/<channel>_<date>.json|.md and <channel>_winners.json (read by research via load_winners).
Not available through any API: impressions / click-through rate.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from aurora.publish.youtube import ANALYTICS_SCOPE, STATE_DIR, get_credentials

METRICS = "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,likes,comments"
COLS = METRICS.split(",")


def build_analytics_service(channel: str):
    from googleapiclient.discovery import build

    return build("youtubeAnalytics", "v2", credentials=get_credentials(channel, need=(ANALYTICS_SCOPE,)), cache_discovery=False)


def _row(headers: list[str], row: list) -> dict:
    return dict(zip(headers, row))


def query_week(analytics, start: date, end: date, max_videos: int = 25) -> dict:
    """Channel totals + per-video rows (sorted by views) for [start, end]."""
    common = dict(ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), metrics=METRICS)
    tot = analytics.reports().query(**common).execute()
    totals = _row([h["name"] for h in tot.get("columnHeaders", [])], (tot.get("rows") or [[0] * len(COLS)])[0])
    per = analytics.reports().query(**common, dimensions="video", sort="-views", maxResults=max_videos).execute()
    heads = [h["name"] for h in per.get("columnHeaders", [])]
    return {"totals": totals, "videos": [_row(heads, r) for r in per.get("rows", [])]}


def _titles(service, ids: list[str], titles_by_id: dict[str, str] | None, budget=None) -> dict[str, str]:
    titles = dict(titles_by_id or {})
    missing = [i for i in ids if i not in titles]
    if missing and service is not None:
        if budget:
            budget.spend("videos.list")
        for v in service.videos().list(part="snippet", id=",".join(missing[:50])).execute().get("items", []):
            titles[v["id"]] = v["snippet"]["title"]
    return titles


def weekly_report(analytics, channel: str, today: date | None = None, data_service=None, titles_by_id: dict | None = None,
                  budget=None, state_dir: Path = STATE_DIR) -> dict:
    """Last 7 full days vs the 7 before. Writes JSON + Markdown, refreshes the winners file. Returns the report dict."""
    today = today or date.today()
    end = today - timedelta(days=1)
    this, prev = query_week(analytics, end - timedelta(days=6), end), query_week(analytics, end - timedelta(days=13), end - timedelta(days=7), 1)
    ids = [v["video"] for v in this["videos"]]
    titles = _titles(data_service, ids, titles_by_id, budget)
    top = [{"video_id": v["video"], "title": titles.get(v["video"], v["video"]), "views": int(v["views"]),
            "avg_view_pct": round(float(v["averageViewPercentage"]), 1), "likes": int(v["likes"]),
            "subs": int(v["subscribersGained"])} for v in this["videos"][:5]]
    t, p = this["totals"], prev["totals"]

    def delta(k: str) -> str:
        a, b = float(t.get(k, 0)), float(p.get(k, 0))
        return f"{a:.0f} ({'+' if a >= b else ''}{(a - b) / b * 100:.0f}%)" if b else f"{a:.0f}"

    report = {"channel": channel, "start": (end - timedelta(days=6)).isoformat(), "end": end.isoformat(),
              "views": int(t.get("views", 0)), "watch_minutes": int(t.get("estimatedMinutesWatched", 0)),
              "avg_view_pct": round(float(t.get("averageViewPercentage", 0)), 1), "subs": int(t.get("subscribersGained", 0)),
              "top": top, "insight": _insight(top)}
    md = [f"# Weekly report - {channel} ({report['start']} to {report['end']})",
          f"- Views: {delta('views')}", f"- Watch minutes: {delta('estimatedMinutesWatched')}",
          f"- Avg % viewed: {report['avg_view_pct']}%", f"- New subscribers: {delta('subscribersGained')}", "", "## Top videos"]
    md += [f"{i}. {x['title']} - {x['views']} views, {x['avg_view_pct']}% viewed" for i, x in enumerate(top, 1)]
    md += ["", f"**Insight:** {report['insight']}"]
    d = Path(state_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stem = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_{end.isoformat()}"
    (d / f"{stem}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
    save_winners(channel, top, state_dir)
    return report


def _insight(top: list[dict]) -> str:
    if not top:
        return "No views yet this week; keep posting consistently."
    best = max(top, key=lambda x: x["avg_view_pct"])
    return f"Best retention: '{best['title']}' ({best['avg_view_pct']}% viewed) - make more like it."


def save_winners(channel: str, top: list[dict], state_dir: Path = STATE_DIR) -> Path:
    f = Path(state_dir) / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_winners.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps([{"title": x["title"], "views": x["views"], "avg_view_pct": x["avg_view_pct"]} for x in top],
                              indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)
    return f


def load_winners(channel: str, n: int = 3, state_dir: Path = STATE_DIR) -> list[dict]:
    """Top performers for research prompts: [{"title","views","avg_view_pct"}], best retention first."""
    try:
        w = json.loads((Path(state_dir) / f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_winners.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(w, key=lambda x: (x["avg_view_pct"], x["views"]), reverse=True)[:n]


def latest_report(channel: str, state_dir: Path = STATE_DIR) -> dict | None:
    files = sorted((Path(state_dir) / "reports").glob(f"{re.sub(r'[^A-Za-z0-9_-]+', '_', channel)}_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
