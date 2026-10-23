"""Hands-off loop: research -> render -> validate -> enqueue (veto window) -> publish-due -> analytics.

    python -m aurora.autopilot render       [--channel NAME] [--limit N]
    python -m aurora.autopilot publish-due  [--channel NAME]
    python -m aurora.autopilot analytics
    python -m aurora.autopilot status | veto ID | approve ID
    python -m aurora.autopilot publish-one ID --privacy private --i-confirm   (bypasses the kill-switch for ONE item)

Config (config.yaml) `autopilot:` section, see docs/AUTOPILOT.md. Channels never mix: own token file + queue tag.
Research interface (aurora/research.py, built by Backend): generate_ideas(niche, language, n, channel=None) ->
  [{"topic","title","description","hashtags","ai_disclosure","voiceover"?}]; until it exists a topics.txt stub is used.
"""
from __future__ import annotations

import argparse
import sys
import time
import types
from unittest import mock
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


LANGS = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish"}


def _from_research(channel: dict, n: int, research) -> list[dict]:
    """Map aurora.research.research_ideas() output to autopilot ideas; [] if it only produced the niche-text fallback."""
    lang = LANGS.get(str(channel.get("language", "en")).lower(), channel.get("language", "English"))
    platform = "YouTube Shorts" if "youtube" in channel.get("platforms", ["youtube"]) else "Instagram Reels"
    res = research.research_ideas(channel.get("niche", ""), lang, platform, n)
    if res.get("source") == "topic_fallback":
        return []
    out = []
    for i in res.get("ideas", [])[:n]:
        sc = i.get("scenes") or []
        out.append({
            "topic": i["title"], "title": i["title"], "description": i.get("description", ""),
            "hashtags": i.get("hashtags", []), "ai_disclosure": i.get("ai_disclosure", True),
            "image_prompt": sc[0]["image_prompt"] if sc else None, "motion_prompt": sc[0]["motion_prompt"] if sc else None,
            "hook": i.get("hook"), "scenes": sc,
            "voiceover": " ".join([i.get("hook", "")] + [x["voiceover"] for x in sc]).strip() or None,
        })
    return out


def get_ideas(channel: dict, n: int, research=None, sleep=time.sleep, retries: int = 3) -> list[dict]:
    """On-niche research ideas only (Gemini 503s are retried with backoff). Never an off-niche topic: when research
    fails we log loudly and return [] (skip), unless the channel explicitly opts in with `topics_file:`."""
    from aurora import load_env

    load_env()
    if channel.get("format") == "comic":  # one idea per reel; aurora.comic researches the actual story + series continuity
        return [{"topic": channel.get("niche", "an original anime story"), "title": channel["name"], "description": "",
                 "hashtags": ["#anime", "#animeshorts", "#originalstory", "#shorts"], "ai_disclosure": True} for _ in range(n)]
    err = "no ideas"
    for attempt in range(retries):
        try:
            if research is None:
                from aurora import research
            ideas = _from_research(channel, n, research)
            if ideas:
                return ideas
            err = "research returned only its niche-text fallback (Gemini/local LLM failed)"
        except Exception as e:  # noqa: BLE001 - no key, network, import error
            err = f"{type(e).__name__}: {e}"
        if attempt < retries - 1:
            sleep(30 * (attempt + 1))
    print(f"[autopilot] !!! {channel['name']}: RESEARCH FAILED after {retries} tries ({err}); "
          f"skipping this channel run, nothing rendered", file=sys.stderr)
    if channel.get("topics_file"):
        print(f"[autopilot] {channel['name']}: using opt-in topics_file {channel['topics_file']}", file=sys.stderr)
        return stub_ideas(channel, n, ROOT / channel["topics_file"])
    return []


# ---- render ----------------------------------------------------------
def render_idea(cfg: dict, switchboard, channel: dict, idea: dict) -> dict:
    """Render one reel via autorun.process_topic; returns its sidecar (validated, has reel.path + reel.sha256)."""
    from aurora import autorun

    a = types.SimpleNamespace(
        style="cinematic", width=512, height=768, duration=15.0, fps=24, music="auto", voiceover=True,
        captions=True, motion=channel.get("motion", "camera"), no_llm=False, voice=channel.get("voice"),
        voice_engine=None, voice_gender=None, speaker_wav=None, voiceover_text=idea.get("voiceover"))
    out_dir = ROOT / "outputs" / "final" / channel["name"] / datetime.now().date().isoformat()
    if channel.get("format") == "comic":  # motion comic: image panels + voice + music + series state
        import json as _json

        from aurora.comic import make_comic_reel

        res = make_comic_reel(channel["name"], idea["topic"], ROOT / "outputs" / "final" / channel["name"] / "comic", cfg=cfg,
                              language=LANGS.get(str(channel.get("language", "en")).lower(), "English"),
                              panels=int(channel.get("panels", 6)), voice=channel.get("voice", "en-US-AvaNeural"),
                              switchboard=switchboard)
        side = _json.loads(Path(res["sidecar"]).read_text(encoding="utf-8"))
        idea.update(series=side["series"]["title"], part=side["part"], description=side["caption"],
                    hook=None)  # compose_meta -> "<Series> – Part N"
        return side
    if not idea.get("image_prompt"):  # topics.txt stub: let autorun's own LLM step write the prompt
        return autorun.process_topic(cfg, switchboard, idea["topic"], a, out_dir)
    text = {"image_prompt": idea["image_prompt"] + autorun.QUALITY_SUFFIX, "caption": idea.get("hook") or idea["title"],
            "source": "research"}
    with mock.patch.object(autorun, "write_prompt_and_caption", lambda _cfg, _topic: text):
        return autorun.process_topic(cfg, switchboard, idea["topic"], a, out_dir)


def run_render(cfg: dict, queue: PublishQueue, only: str | None = None, limit: int = 0,
               research=None, renderer=None, seo_service_factory=None) -> list[dict]:
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
            meta = compose_meta(idea, sidecar["reel"]["sha256"])
            meta.update(_sidecar_extras(sidecar))
            if ch.get("seo"):
                meta = apply_seo(cfg, ch, idea, meta, seo_service_factory)
            for platform in ch["platforms"]:
                item = queue.enqueue(ch["name"], sidecar["reel"]["path"], meta, platform=platform)
                if item:
                    queued.append(item)
                    print(f"[autopilot] {ch['name']}/{platform}: queued {item['id']} ({item['status']}, publish_at {item['publish_at']})")
    return queued


def _sidecar_extras(sidecar: dict) -> dict:
    """Render outputs the publisher needs: SRT captions (path or text), thumbnail, duration, part number."""
    return {"captions_srt": sidecar.get("captions_srt"), "thumbnail": sidecar.get("thumbnail"),
            "duration_secs": (sidecar.get("reel") or {}).get("duration_secs"), "part": sidecar.get("part"),
            "chapters": sidecar.get("chapters")}


def apply_seo(cfg: dict, ch: dict, idea: dict, meta: dict, service_factory=None, budget=None, client=None) -> dict:
    """Replace title/description/tags with SEO-researched ones. Never raises: any failure keeps the plain metadata."""
    try:
        from aurora.publish import seo, youtube as yt

        service = None
        try:
            service = (service_factory or (lambda c: yt.build_service(c, expected_handle=ch.get("expected_handle"))))(ch["name"])
        except Exception as e:  # noqa: BLE001 - no token yet: SEO still works without competitor data
            print(f"[autopilot] {ch['name']}: SEO without competitor search ({e})", file=sys.stderr)
        res = seo.build_metadata(idea.get("topic", ""), idea.get("series"), idea.get("part"), meta.get("description", "")[:600],
                                 hashtags=meta.get("tags"), fallback_title=meta["title"], service=service, budget=budget,
                                 client=client, cfg=cfg, duration_secs=meta.get("duration_secs"), chapters=meta.get("chapters"))
        return {**meta, "title": res["title"], "description": res["description"], "tags": res["tags"] or meta["tags"]}
    except Exception as e:  # noqa: BLE001
        print(f"[autopilot] {ch['name']}: SEO failed, using plain metadata: {e}", file=sys.stderr)
        return meta


def compose_meta(idea: dict, sha256: str) -> dict:
    """Queue metadata. Series ideas (comic format) get "<Series> – Part N" + hook as title; series hashtags and the
    AI-disclosure line go into the description (the platform flag is set separately at upload)."""
    series, part = idea.get("series"), idea.get("part")
    base = idea.get("title") or idea["topic"]
    title = f"{series} – Part {part}" + (f": {idea['hook']}" if idea.get("hook") else "") if series and part else base
    tags = list(dict.fromkeys(list(idea.get("series_hashtags", [])) + list(idea.get("hashtags", []))))
    ai = idea.get("ai_disclosure", True)
    desc = "\n\n".join(x for x in (idea.get("description", ""), " ".join(t if t.startswith("#") else f"#{t}" for t in tags),
                                    "AI-generated content." if ai else "") if x)
    return {"title": title[:100], "description": desc, "tags": tags, "ai_disclosure": ai, "sha256": sha256,
            "series": series if series and part else None}


# ---- publish ---------------------------------------------------------
def publish_due(queue: PublishQueue, only: str | None = None, now: datetime | None = None, service_factory=None,
                uploader=None, dry_run: bool = False, ig_publisher=None, playlist_fn=None, budget=None) -> dict:
    """Upload every due, non-vetoed item. Stops a channel on quota; failures retry up to max_attempts."""
    from aurora.publish import youtube as yt

    service_factory = service_factory or (lambda ch: yt.build_service(ch, expected_handle=queue.expected_handles.get(ch)))
    uploader = uploader or yt.upload_short
    result = {"published": [], "failed": [], "deferred": []}
    if not dry_run and not queue.s.get("enabled", False):  # global kill-switch: autopilot.publish.enabled (default false)
        print("[autopilot] publishing is DISABLED (autopilot.publish.enabled is false); nothing uploaded. "
              "Use --dry-run to preview.", file=sys.stderr)
        result["disabled"] = True
        return result
    if dry_run:  # log what WOULD be sent; no auth, no upload, queue untouched
        from aurora.publish import youtube as yt

        result["would_publish"] = []
        for it in queue.due(now, only):
            if it.get("platform", "youtube") == "instagram":
                from aurora.publish import instagram as ig

                print(f"[autopilot] DRY-RUN {it['channel']}/instagram: would host + publish {it['path']} caption={ig.build_caption(it)!r}")
                result["would_publish"].append(it["id"])
                continue
            body = yt.build_body(it, it.get("privacy", "public"))
            print(f"[autopilot] DRY-RUN {it['channel']}: would upload {it['path']} ({it['sha256'][:12]}) "
                  f"title={body['snippet']['title']!r} tags={body['snippet']['tags']} "
                  f"privacy={body['status']['privacyStatus']} containsSyntheticMedia={body['status']['containsSyntheticMedia']}")
            result["would_publish"].append(it["id"])
        return result
    services: dict[str, object] = {}
    blocked: set[str] = set()
    for it in queue.due(now, only):
        ch = it["channel"]
        if it.get("platform", "youtube") == "instagram":
            _publish_instagram(queue, it, result, ig_publisher)
            continue
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
        try:
            if budget:
                budget.spend("videos.insert")  # defer (not fail) when today's quota budget is used up
        except yt.QuotaExceeded as e:
            queue.defer(it["id"], QUOTA_BACKOFF, f"quota budget: {e}")
            blocked.add(ch)
            result["deferred"].append(it["id"])
            continue
        queue.mark_publishing(it["id"])
        try:
            vid = uploader(services[ch], it["path"], it, privacy=it.get("privacy", "public"))
            queue.mark_published(it["id"], vid)
            result["published"].append(it["id"])
            print(f"[autopilot] {ch}: published {vid}")
            _post_upload(queue, it, vid, services[ch], playlist_fn, budget=budget)
        except yt.QuotaExceeded as e:
            queue.defer(it["id"], QUOTA_BACKOFF, f"quota: {e}")
            blocked.add(ch)
            result["deferred"].append(it["id"])
        except Exception as e:  # noqa: BLE001
            queue.mark_failed(it["id"], f"{type(e).__name__}: {e}")
            result["failed"].append(it["id"])
    return result


def _add_to_playlist(queue: PublishQueue, it: dict, video_id: str, service, playlist_fn=None, privacy: str | None = None,
                     budget=None) -> None:
    """Series videos go into a per-series playlist. A playlist failure never un-publishes the item."""
    if not it.get("series"):
        return
    from aurora.publish import youtube as yt

    try:
        extra = {"part": it.get("part"), "budget": budget} if playlist_fn is None else {}
        pid = (playlist_fn or yt.add_to_series_playlist)(service, it["channel"], it["series"], video_id,
                                                         privacy or it.get("privacy", "public"), **extra)
        queue._update(it["id"], playlist_id=pid, playlist_error=None)
    except Exception as e:  # noqa: BLE001
        queue._update(it["id"], playlist_error=f"{type(e).__name__}: {e}"[:300])
        print(f"[autopilot] {it['channel']}: playlist step failed (video is published): {e}", file=sys.stderr)


def _post_upload(queue: PublishQueue, it: dict, video_id: str, service, playlist_fn=None, privacy: str | None = None,
                 budget=None, captions_fn=None, thumbnail_fn=None) -> None:
    """After a successful upload: subtitles, thumbnail (long videos), series playlist. Each step fails independently."""
    from aurora.publish import youtube as yt
    from aurora.publish.seo import LONG_SECS

    if it.get("captions_srt"):
        try:
            (captions_fn or yt.upload_captions)(service, video_id, it["captions_srt"], budget=budget)
            queue._update(it["id"], captions_done=True, captions_error=None)
        except Exception as e:  # noqa: BLE001
            queue._update(it["id"], captions_error=f"{type(e).__name__}: {e}"[:300])
            print(f"[autopilot] {it['channel']}: captions failed (video is published): {e}", file=sys.stderr)
    if it.get("thumbnail") and (it.get("duration_secs") or 0) >= LONG_SECS:  # Shorts use an auto frame
        try:
            (thumbnail_fn or yt.set_thumbnail)(service, video_id, it["thumbnail"], budget=budget)
            queue._update(it["id"], thumbnail_done=True, thumbnail_error=None)
        except Exception as e:  # noqa: BLE001
            queue._update(it["id"], thumbnail_error=f"{type(e).__name__}: {e}"[:300])
            print(f"[autopilot] {it['channel']}: thumbnail failed (video is published): {e}", file=sys.stderr)
    _add_to_playlist(queue, it, video_id, service, playlist_fn, privacy, budget)


def publish_one(queue: PublishQueue, item_id: str, privacy: str = "private", confirm: bool = False,
                service_factory=None, uploader=None, playlist_fn=None, budget=None) -> dict:
    """Upload ONE queue item, bypassing the global kill-switch for that item only. Requires explicit confirm."""
    from aurora.publish import youtube as yt

    if not confirm:
        raise PermissionError("publish-one needs the explicit --i-confirm flag")
    if privacy not in ("private", "unlisted", "public"):
        raise ValueError(f"bad privacy {privacy!r}")
    it = queue.get(item_id)
    if not it or it.get("platform", "youtube") != "youtube":
        raise ValueError("no such YouTube queue item")
    if it["status"] in ("publishing", "published"):
        raise ValueError(f"item is already {it['status']}")
    if not Path(it["path"]).exists():
        raise FileNotFoundError(it["path"])
    ch = it["channel"]
    service = (service_factory or (lambda c: yt.build_service(c, expected_handle=queue.expected_handles.get(c))))(ch)
    queue.mark_publishing(item_id)
    try:
        vid = (uploader or yt.upload_short)(service, it["path"], it, privacy=privacy)
    except Exception as e:  # noqa: BLE001 - leave it vetoed-safe: never auto-retry a one-off
        queue._update(item_id, status="failed", error=f"{type(e).__name__}: {e}"[:500])
        raise
    queue._update(item_id, privacy=privacy)
    done = queue.mark_published(item_id, vid)
    print(f"[autopilot] {ch}: published {vid} as {privacy}")
    _post_upload(queue, done, vid, service, playlist_fn, privacy, budget)
    return queue.get(item_id)


def _publish_instagram(queue: PublishQueue, it: dict, result: dict, ig_publisher=None) -> None:
    from aurora.publish import hosting, instagram as ig

    if not Path(it["path"]).exists():
        queue.mark_failed(it["id"], "video file missing", retry_in=None)
        result["failed"].append(it["id"])
        return
    queue.mark_publishing(it["id"])
    try:
        if ig_publisher:
            media_id = ig_publisher(it)
        else:
            account = ig.load_account(it["channel"])
            with hosting.temporary_public_url(it["path"]) as url:
                media_id = ig.publish_reel(account, url, it)
        queue.mark_published(it["id"], media_id)
        result["published"].append(it["id"])
        print(f"[autopilot] {it['channel']}/instagram: published {media_id}")
    except ig.RateLimited as e:
        queue.defer(it["id"], QUOTA_BACKOFF, f"rate limit: {e}")
        result["deferred"].append(it["id"])
    except (hosting.HostingNotConfigured, ig.InstagramError) as e:  # config problem: keep scheduled, don't burn attempts
        queue.defer(it["id"], timedelta(hours=1), str(e))
        result["deferred"].append(it["id"])
    except Exception as e:  # noqa: BLE001
        queue.mark_failed(it["id"], f"{type(e).__name__}: {e}")
        result["failed"].append(it["id"])


def collect_analytics(queue: PublishQueue, service_factory=None, stats_fn=None, ig_stats_fn=None) -> int:
    from aurora.publish import youtube as yt

    service_factory = service_factory or yt.build_service
    stats_fn = stats_fn or yt.fetch_stats
    n = 0
    for ch in sorted({i["channel"] for i in queue.list(status="published")}):
        for platform in ("youtube", "instagram"):
            items = [i for i in queue.list(ch, "published") if i.get("video_id") and i.get("platform", "youtube") == platform]
            if not items:
                continue
            ids = [i["video_id"] for i in items]
            try:
                if platform == "youtube":
                    stats = stats_fn(service_factory(ch), ids)
                else:
                    from aurora.publish import instagram as ig

                    stats = (ig_stats_fn or ig.fetch_stats)(ig.load_account(ch), ids)
            except Exception as e:  # noqa: BLE001
                print(f"[autopilot] {ch}/{platform}: analytics failed: {e}", file=sys.stderr)
                continue
            for i in items:
                if i["video_id"] in stats:
                    queue.set_stats(i["id"], {**stats[i["video_id"]], "at": datetime.now(timezone.utc).isoformat()})
                    n += 1
    return n


def _channel_id(service) -> str:
    from aurora.publish import youtube as yt

    return yt.verify_channel(service, None)["id"]


def _service_for(queue: PublishQueue, channel: str):
    from aurora.publish import youtube as yt

    return yt.build_service(channel, expected_handle=queue.expected_handles.get(channel))


def run_comments(queue: PublishQueue, channel: str, service=None, budget=None, client=None, cfg: dict | None = None) -> dict:
    """Hold spam + draft replies (posts nothing); then send replies the user already approved."""
    from aurora.publish import comments

    service = service or _service_for(queue, channel)
    out = comments.process(service, channel, _channel_id(service), budget, client, cfg)
    out["sent"] = len(comments.send_approved(service, channel, budget))
    return out


def run_weekly_report(queue: PublishQueue, channel: str, analytics=None, data_service=None, today=None, budget=None) -> dict:
    from aurora.publish import analytics as an

    analytics = analytics or an.build_analytics_service(channel)
    data_service = data_service or _service_for(queue, channel)
    titles = {i["video_id"]: i["title"] for i in queue.list(channel, "published") if i.get("video_id")}
    return an.weekly_report(analytics, channel, today, data_service, titles, budget)


def approvals_summary(queue: PublishQueue, channels: list[str] | None = None) -> dict:
    """Everything waiting for the user: queue items (vetoable/approvable), comment reply drafts, latest weekly report."""
    from aurora.publish import analytics as an, comments

    names = channels or sorted({i["channel"] for i in queue.list()})
    return {
        "queue": [i for i in queue.list() if i["status"] in ("pending_approval", "scheduled", "vetoed")
                  and (not channels or i["channel"] in channels)],
        "comment_drafts": {c: comments.pending(c) for c in names},
        "weekly_report": {c: an.latest_report(c) for c in names},
    }


def main(argv: list[str] | None = None) -> int:
    from aurora import ensure_utf8_console, load_env

    ensure_utf8_console()
    load_env()
    ap = argparse.ArgumentParser(description="Aurora autopilot")
    ap.add_argument("cmd", choices=["render", "publish-due", "publish-one", "analytics", "status", "veto", "approve", "comments",
                                    "comment-approve", "comment-reject", "weekly-report", "approvals"])
    ap.add_argument("--reply", help="comment-approve: edited reply text")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"], help="publish-one only")
    ap.add_argument("--i-confirm", action="store_true", help="publish-one: required; bypasses the kill-switch for this ONE item")
    ap.add_argument("id", nargs="?")
    ap.add_argument("--channel")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="publish-due: log what would be uploaded, send nothing")
    ap.add_argument("--at", help="publish-due: evaluate as of this ISO time (UTC), e.g. to preview a future run")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    a = ap.parse_args(argv)
    cfg = yaml.safe_load(a.config.read_text(encoding="utf-8")) or {}
    q = PublishQueue(cfg=cfg)
    if a.cmd == "render":
        from aurora.autorun import lower_priority

        lower_priority()
        run_render(cfg, q, a.channel, a.limit)
    elif a.cmd == "publish-due":
        from aurora.publish.quota import QuotaBudget

        print(publish_due(q, a.channel, now=datetime.fromisoformat(a.at).astimezone(timezone.utc) if a.at else None,
                          dry_run=a.dry_run, budget=QuotaBudget()))
    elif a.cmd == "publish-one":
        if not a.id:
            ap.error("queue id required")
        try:
            from aurora.publish.quota import QuotaBudget

            it = publish_one(q, a.id, a.privacy, a.i_confirm, budget=QuotaBudget())
        except PermissionError as e:
            print(f"[autopilot] refused: {e}", file=sys.stderr)
            return 2
        print(f"[autopilot] done: https://youtu.be/{it['video_id']} ({it['privacy']})")
    elif a.cmd in ("comments", "weekly-report"):
        from aurora.publish.quota import QuotaBudget

        for ch in [c["name"] for c in channels_from(cfg) if not a.channel or c["name"] == a.channel]:
            try:
                print(ch, run_comments(q, ch, budget=QuotaBudget(), cfg=cfg) if a.cmd == "comments"
                      else run_weekly_report(q, ch, budget=QuotaBudget())["insight"])
            except Exception as e:  # noqa: BLE001 - AuthRequired etc.: one channel must not stop the rest
                print(f"[autopilot] {ch}: {a.cmd} failed: {e}", file=sys.stderr)
    elif a.cmd in ("comment-approve", "comment-reject"):
        from aurora.publish import comments

        if not (a.id and a.channel):
            ap.error("needs <comment id> and --channel")
        print((comments.approve(a.channel, a.id, a.reply) if a.cmd == "comment-approve" else comments.reject(a.channel, a.id))["status"])
    elif a.cmd == "approvals":
        import json as _json

        print(_json.dumps(approvals_summary(q, [a.channel] if a.channel else None), indent=2, ensure_ascii=False))
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
