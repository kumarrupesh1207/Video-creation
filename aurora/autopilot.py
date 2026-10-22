"""Hands-off loop: research -> render -> validate -> enqueue (veto window) -> publish-due -> analytics.

    python -m aurora.autopilot render       [--channel NAME] [--limit N]
    python -m aurora.autopilot publish-due  [--channel NAME]
    python -m aurora.autopilot analytics
    python -m aurora.autopilot status | veto ID | approve ID

Config (config.yaml) `autopilot:` section, see docs/AUTOPILOT.md. Channels never mix: own token file + queue tag.
Research interface (aurora/research.py, built by Backend): generate_ideas(niche, language, n, channel=None) ->
  [{"topic","title","description","hashtags","ai_disclosure","voiceover"?}]; until it exists a topics.txt stub is used.
"""
from __future__ import annotations

import argparse
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from aurora.publish.queue import PublishQueue

ROOT = Path(__file__).resolve().parents[1]
QUOTA_BACKOFF = timedelta(hours=12)


def channels_from(cfg: dict) -> list[dict]:
    chans = (cfg.get("autopilot") or {}).get("channels") or []
    return [{"platforms": ["youtube"], "reels_per_day": 1, "motion": "camera", "language": "en", **c} for c in chans if c.get("name")]


# ---- research --------------------------------------------------------
def stub_ideas(channel: dict, n: int, topics_file: Path = ROOT / "topics.txt") -> list[dict]:
    """Fallback until aurora.research lands: take unused lines from topics.txt."""
    try:
        lines = [t.strip() for t in topics_file.read_text(encoding="utf-8").splitlines() if t.strip() and not t.startswith("#")]
    except OSError:
        return []
    return [{"topic": t, "title": t[:95], "description": t, "hashtags": [], "ai_disclosure": True} for t in lines[:n]]


def get_ideas(channel: dict, n: int, research=None) -> list[dict]:
    if research is None:
        try:
            from aurora import research as research  # noqa: PLC0414 - optional module
        except ImportError:
            return stub_ideas(channel, n)
    return list(research.generate_ideas(channel.get("niche", ""), channel.get("language", "en"), n, channel=channel["name"]))[:n]


# ---- render ----------------------------------------------------------
def render_idea(cfg: dict, switchboard, channel: dict, idea: dict) -> dict:
    """Render one reel via autorun.process_topic; returns its sidecar (validated, has reel.path + reel.sha256)."""
    from aurora import autorun

    a = types.SimpleNamespace(
        style="cinematic", width=512, height=768, duration=15.0, fps=24, music="auto", voiceover=True,
        captions=True, motion=channel.get("motion", "camera"), no_llm=False, voice=channel.get("voice"),
        voice_engine=None, voice_gender=None, speaker_wav=None, voiceover_text=idea.get("voiceover"))
    out_dir = ROOT / "outputs" / "final" / channel["name"] / datetime.now().date().isoformat()
    return autorun.process_topic(cfg, switchboard, idea["topic"], a, out_dir)


def run_render(cfg: dict, queue: PublishQueue, only: str | None = None, limit: int = 0,
               research=None, renderer=None) -> list[dict]:
    """For each channel: research N ideas, render, enqueue. `renderer(cfg, channel, idea)->sidecar` is injectable."""
    queued: list[dict] = []
    switchboard = None
    for ch in channels_from(cfg):
        if only and ch["name"] != only:
            continue
        n = limit or int(ch["reels_per_day"])
        try:
            ideas = get_ideas(ch, n, research)
        except Exception as e:  # noqa: BLE001 - one channel's failure must not stop others
            print(f"[autopilot] {ch['name']}: research failed: {e}", file=sys.stderr)
            continue
        for idea in ideas:
            try:
                if renderer:
                    sidecar = renderer(cfg, ch, idea)
                else:
                    if switchboard is None:
                        from backend.app.services.image_switchboard import ImageSwitchboard

                        switchboard = ImageSwitchboard(cfg)
                    sidecar = render_idea(cfg, switchboard, ch, idea)
                if sidecar.get("status") != "ok":
                    raise RuntimeError(f"render status {sidecar.get('status')}")
            except Exception as e:  # noqa: BLE001
                print(f"[autopilot] {ch['name']}: '{idea.get('topic')}' failed: {e}", file=sys.stderr)
                continue
            meta = {"title": idea.get("title") or idea["topic"], "description": idea.get("description", ""),
                    "tags": idea.get("hashtags", []), "ai_disclosure": idea.get("ai_disclosure", True),
                    "sha256": sidecar["reel"]["sha256"]}
            item = queue.enqueue(ch["name"], sidecar["reel"]["path"], meta)
            if item:
                queued.append(item)
                print(f"[autopilot] {ch['name']}: queued {item['id']} ({item['status']}, publish_at {item['publish_at']})")
    return queued


# ---- publish ---------------------------------------------------------
def publish_due(queue: PublishQueue, only: str | None = None, now: datetime | None = None, service_factory=None,
                uploader=None) -> dict:
    """Upload every due, non-vetoed item. Stops a channel on quota; failures retry up to max_attempts."""
    from aurora.publish import youtube as yt

    service_factory = service_factory or yt.build_service
    uploader = uploader or yt.upload_short
    result = {"published": [], "failed": [], "deferred": []}
    services: dict[str, object] = {}
    blocked: set[str] = set()
    for it in queue.due(now, only):
        ch = it["channel"]
        if ch in blocked:
            continue
        if not Path(it["path"]).exists():
            queue.mark_failed(it["id"], "video file missing", retry_in=None)
            result["failed"].append(it["id"])
            continue
        try:
            if ch not in services:
                services[ch] = service_factory(ch)
        except Exception as e:  # noqa: BLE001 - AuthRequired etc.
            blocked.add(ch)
            print(f"[autopilot] {ch}: cannot authenticate: {e}", file=sys.stderr)
            continue
        queue.mark_publishing(it["id"])
        try:
            vid = uploader(services[ch], it["path"], it, privacy=it.get("privacy", "public"))
            queue.mark_published(it["id"], vid)
            result["published"].append(it["id"])
            print(f"[autopilot] {ch}: published {vid}")
        except yt.QuotaExceeded as e:
            queue.defer(it["id"], QUOTA_BACKOFF, f"quota: {e}")
            blocked.add(ch)
            result["deferred"].append(it["id"])
        except Exception as e:  # noqa: BLE001
            queue.mark_failed(it["id"], f"{type(e).__name__}: {e}")
            result["failed"].append(it["id"])
    return result


def collect_analytics(queue: PublishQueue, service_factory=None, stats_fn=None) -> int:
    from aurora.publish import youtube as yt

    service_factory = service_factory or yt.build_service
    stats_fn = stats_fn or yt.fetch_stats
    n = 0
    for ch in sorted({i["channel"] for i in queue.list(status="published")}):
        items = [i for i in queue.list(ch, "published") if i.get("video_id")]
        if not items:
            continue
        try:
            stats = stats_fn(service_factory(ch), [i["video_id"] for i in items])
        except Exception as e:  # noqa: BLE001
            print(f"[autopilot] {ch}: analytics failed: {e}", file=sys.stderr)
            continue
        for i in items:
            if i["video_id"] in stats:
                queue.set_stats(i["id"], {**stats[i["video_id"]], "at": datetime.now(timezone.utc).isoformat()})
                n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    from aurora import ensure_utf8_console, load_env

    ensure_utf8_console()
    load_env()
    ap = argparse.ArgumentParser(description="Aurora autopilot")
    ap.add_argument("cmd", choices=["render", "publish-due", "analytics", "status", "veto", "approve"])
    ap.add_argument("id", nargs="?")
    ap.add_argument("--channel")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(a.config.read_text(encoding="utf-8")) or {}
    q = PublishQueue(cfg=cfg)
    if a.cmd == "render":
        from aurora.autorun import lower_priority

        lower_priority()
        run_render(cfg, q, a.channel, a.limit)
    elif a.cmd == "publish-due":
        print(publish_due(q, a.channel))
    elif a.cmd == "analytics":
        print(f"[autopilot] stats updated for {collect_analytics(q)} videos")
    elif a.cmd == "status":
        for i in q.list(a.channel):
            print(f"{i['id']}  {i['channel']:<12} {i['status']:<17} {i['publish_at'][:16]}  {i['title'][:50]}")
    else:
        if not a.id:
            ap.error("id required")
        print((q.veto if a.cmd == "veto" else q.approve)(a.id)["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
