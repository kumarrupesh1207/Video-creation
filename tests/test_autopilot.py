"""Autopilot / publish queue / YouTube uploader tests. Google APIs are mocked; nothing is uploaded."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import youtube as yt
from aurora.publish.queue import PublishQueue

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
CFG = {"autopilot": {"publish": {"enabled": True, "veto_hours": 6, "slots": ["19:00"]},
                     "channels": [{"name": "facts", "niche": "science", "reels_per_day": 2}, {"name": "hindi", "niche": "x"}]}}


@pytest.fixture(autouse=True)
def _events_in_tmp(workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "EVENTS_DIR", workspace_tmp_path / "events")  # never write the real event log


@pytest.fixture
def q(workspace_tmp_path):
    return PublishQueue(workspace_tmp_path / "q.json", CFG)


@pytest.fixture
def vid(workspace_tmp_path):
    p = workspace_tmp_path / "a.mp4"
    p.write_bytes(b"fake-video-1")
    return p


def test_enqueue_publish_time_and_dedupe(q, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    assert it["status"] == "scheduled" and it["ai_disclosure"] is True
    # 11:30 IST + 6h = 17:30 IST -> slot 19:00 IST = 13:30 UTC
    assert datetime.fromisoformat(it["publish_at"]) == datetime(2026, 10, 22, 13, 30, tzinfo=timezone.utc)
    assert q.enqueue("facts", vid, {"title": "T"}, now=NOW) is None  # same sha, same channel
    assert q.enqueue("hindi", vid, {"title": "T"}, now=NOW) is not None  # channels separate


def test_second_reel_takes_next_day_slot(q, workspace_tmp_path):
    a, b = workspace_tmp_path / "1.mp4", workspace_tmp_path / "2.mp4"
    a.write_bytes(b"1"); b.write_bytes(b"2")
    i1, i2 = q.enqueue("facts", a, {"title": "a"}, now=NOW), q.enqueue("facts", b, {"title": "b"}, now=NOW)
    assert datetime.fromisoformat(i2["publish_at"]) - datetime.fromisoformat(i1["publish_at"]) == timedelta(days=1)


def test_veto_blocks_publish_and_manual_mode(q, workspace_tmp_path, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    q.veto(it["id"])
    assert q.due(NOW + timedelta(days=2)) == []
    q.approve(it["id"])  # approve schedules relative to the real clock
    assert len(q.due(datetime.now(timezone.utc) + timedelta(days=3))) == 1
    mq = PublishQueue(workspace_tmp_path / "m.json", {"autopilot": {"publish": {"approval": "manual"}}})
    m = mq.enqueue("facts", vid, {"title": "T"}, now=NOW)
    assert m["status"] == "pending_approval" and mq.due(NOW + timedelta(days=2)) == []


def test_publish_due_publishes_once_and_never_reuploads(q, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    up = MagicMock(return_value="VID123")
    later = NOW + timedelta(days=1)
    assert autopilot.publish_due(q, now=later, service_factory=lambda c: object(), uploader=up)["published"] == [it["id"]]
    assert q.get(it["id"])["video_id"] == "VID123"
    autopilot.publish_due(q, now=later, service_factory=lambda c: object(), uploader=up)
    assert up.call_count == 1
    assert q.enqueue("facts", vid, {"title": "T"}, now=later) is None


def test_not_due_and_vetoed_not_uploaded(q, vid):
    q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    up = MagicMock()
    autopilot.publish_due(q, now=NOW, service_factory=lambda c: object(), uploader=up)
    up.assert_not_called()


def test_quota_defers_without_attempt_and_failure_retries(q, workspace_tmp_path, vid):
    other = workspace_tmp_path / "o.mp4"; other.write_bytes(b"zz")
    a = q.enqueue("facts", vid, {"title": "A"}, now=NOW)
    q.enqueue("facts", other, {"title": "B"}, now=NOW)
    up = MagicMock(side_effect=yt.QuotaExceeded("quotaExceeded"))
    r = autopilot.publish_due(q, now=NOW + timedelta(days=3), service_factory=lambda c: object(), uploader=up)
    assert up.call_count == 1 and len(r["deferred"]) == 1  # channel blocked after first quota hit
    assert q.get(a["id"])["attempts"] == 0 and q.get(a["id"])["status"] == "scheduled"
    up2 = MagicMock(side_effect=RuntimeError("boom"))
    for _ in range(3):
        q._update(a["id"], publish_at=NOW.isoformat())
        autopilot.publish_due(q, only="facts", now=NOW + timedelta(days=1), service_factory=lambda c: object(), uploader=up2)
    assert q.get(a["id"])["status"] == "failed"


def test_auth_missing_leaves_item_scheduled(q, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)

    def boom(c):
        raise yt.AuthRequired("no token")

    autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=boom, uploader=MagicMock())
    assert q.get(it["id"])["status"] == "scheduled"


def test_build_body_synthetic_flag_and_shorts():
    b = yt.build_body({"title": "T", "description": "d", "tags": ["#a"], "ai_disclosure": True})
    assert b["status"]["containsSyntheticMedia"] is True and b["status"]["privacyStatus"] == "public"
    assert "#Shorts" in b["snippet"]["description"] and b["snippet"]["tags"] == ["a"]
    s = yt.build_body({"title": "T"}, publish_at="2026-10-23T13:30:00Z")["status"]
    assert s["privacyStatus"] == "private" and s["publishAt"].startswith("2026")


def test_upload_short_resumable_with_retry(vid):
    from googleapiclient.errors import HttpError

    req = MagicMock()
    err = HttpError(MagicMock(status=503), b"{}")
    req.next_chunk.side_effect = [err, (None, {"id": "ABC"})]
    svc = MagicMock()
    svc.videos.return_value.insert.return_value = req
    assert yt.upload_short(svc, vid, {"title": "T"}, sleep=lambda s: None) == "ABC"


def test_upload_short_quota_error(vid):
    from googleapiclient.errors import HttpError

    body = b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}'
    req = MagicMock()
    req.next_chunk.side_effect = HttpError(MagicMock(status=403), body)
    svc = MagicMock()
    svc.videos.return_value.insert.return_value = req
    with pytest.raises(yt.QuotaExceeded):
        yt.upload_short(svc, vid, {"title": "T"}, sleep=lambda s: None)


def test_run_render_enqueues_per_channel_with_stub_research(q, workspace_tmp_path):
    def research(niche, lang, plat, n):
        return {"source": "gemini", "ideas": [{"title": f"Title {i}", "hook": "h", "description": "d", "hashtags": ["#x"],
                                               "scenes": [{"image_prompt": f"img {niche} {i}", "motion_prompt": "m", "voiceover": "v"}]}
                                              for i in range(n)]}

    mod = type("R", (), {"research_ideas": staticmethod(research)})

    def renderer(cfg, ch, idea):
        key = f"{ch['name']}-{idea['topic']}"
        p = workspace_tmp_path / f"{key}.mp4"
        p.write_bytes(key.encode())
        return {"status": "ok", "reel": {"path": str(p), "sha256": key}}

    out = autopilot.run_render(CFG, q, research=mod, renderer=renderer)
    assert sorted(i["channel"] for i in out) == ["facts", "facts", "hindi"]  # reels_per_day 2 / default 1
    assert autopilot.run_render(CFG, q, research=mod, renderer=renderer) == []  # dedupe: nothing re-queued


def test_analytics_stores_stats(q, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    q.mark_published(it["id"], "V1")
    n = autopilot.collect_analytics(q, service_factory=lambda c: object(),
                                    stats_fn=lambda s, ids: {"V1": {"viewCount": 5, "likeCount": 1, "commentCount": 0}})
    assert n == 1 and q.get(it["id"])["stats"]["viewCount"] == 5


def test_research_mapping_and_fallback_to_topics(workspace_tmp_path):
    ch = {"name": "c", "niche": "space", "language": "hi", "platforms": ["youtube"]}
    sc = [{"image_prompt": "a planet", "motion_prompt": "push in", "voiceover": "line one"},
          {"image_prompt": "b", "motion_prompt": "m", "voiceover": "line two"}]
    good = type("R", (), {"research_ideas": staticmethod(lambda niche, lang, plat, n: {
        "source": "gemini", "ideas": [{"title": "Cool", "hook": "Wow", "description": "d", "hashtags": ["#s"],
                                       "scenes": sc, "ai_disclosure": False}]})})
    i = autopilot.get_ideas(ch, 1, good)[0]
    assert i["image_prompt"] == "a planet" and i["voiceover"] == "Wow line one line two" and i["ai_disclosure"] is False
    fb = type("R", (), {"research_ideas": staticmethod(lambda *a: {"source": "topic_fallback", "ideas": [{}]})})
    assert autopilot.get_ideas(ch, 1, fb, sleep=lambda s: None) == []  # no off-niche topics.txt fallback


def test_publish_due_dry_run_sends_nothing(q, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    up, sf = MagicMock(), MagicMock()
    r = autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=sf, uploader=up, dry_run=True)
    assert r["would_publish"] == [it["id"]]
    up.assert_not_called(); sf.assert_not_called()
    assert q.get(it["id"])["status"] == "scheduled"


def test_compose_meta_series_title_hashtags_disclosure():
    m = autopilot.compose_meta({"topic": "t", "title": "x", "series": "Space Cats", "part": 3, "hook": "They escaped!",
                                "series_hashtags": ["#SpaceCats"], "hashtags": ["#shorts"], "description": "d"}, "sha")
    assert m["title"] == "Space Cats – Part 3: They escaped!" and m["tags"] == ["#SpaceCats", "#shorts"]
    assert "#SpaceCats" in m["description"] and "AI-generated" in m["description"]
    assert autopilot.compose_meta({"topic": "t"}, "s")["title"] == "t"


def test_platforms_are_queued_separately_and_instagram_publishes(q, vid, monkeypatch):
    from aurora.publish import instagram as ig

    monkeypatch.setattr(ig, "load_account", lambda ch: {})
    y = q.enqueue("facts", vid, {"title": "T"}, now=NOW, platform="youtube")
    i = q.enqueue("facts", vid, {"title": "T"}, now=NOW, platform="instagram")
    assert y and i and q.enqueue("facts", vid, {"title": "T"}, now=NOW, platform="instagram") is None
    assert y["publish_at"] == i["publish_at"]  # slots are per platform
    pub = MagicMock(return_value="IG99")
    r = autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=lambda c: object(),
                              uploader=MagicMock(return_value="YT1"), ig_publisher=pub)
    assert sorted(r["published"]) == sorted([y["id"], i["id"]]) and q.get(i["id"])["video_id"] == "IG99"
    n = autopilot.collect_analytics(q, service_factory=lambda c: object(),
                                    stats_fn=lambda s, ids: {"YT1": {"viewCount": 1}},
                                    ig_stats_fn=lambda a, ids: {"IG99": {"likeCount": 2}})
    assert n == 2


def test_instagram_flow_and_errors():
    from aurora.publish import instagram as ig

    calls = []

    def resp(js, code=200):
        r = MagicMock(); r.json.return_value = js; r.content = b"x"; r.status_code = code; r.text = str(js)
        return r

    http = MagicMock()
    http.post.side_effect = lambda url, **k: (calls.append(url), resp({"id": "C1"} if url.endswith("/media") else {"id": "M1"}))[1]
    http.get.side_effect = [resp({"status_code": "IN_PROGRESS"}), resp({"status_code": "FINISHED"})]
    acct = {"ig_user_id": "U", "access_token": "T"}
    assert ig.publish_reel(acct, "https://x/v.mp4", {"title": "T", "tags": ["a"]}, http=http, sleep=lambda s: None) == "M1"
    assert http.post.call_args_list[0].kwargs["data"]["media_type"] == "REELS"
    assert "AI-generated" in http.post.call_args_list[0].kwargs["data"]["caption"]
    http.post.side_effect = None
    http.post.return_value = resp({"error": {"code": 4, "message": "limit"}}, 400)
    with pytest.raises(ig.RateLimited):
        ig.publish_reel(acct, "u", {"title": "T"}, http=http)


def _meta_resp(js, code=200):
    r = MagicMock(); r.json.return_value = js; r.content = b"x"; r.status_code = code; r.text = str(js)
    return r


def test_instagram_uploads_a_local_file_without_hosting(vid):
    from aurora.publish import instagram as ig

    http = MagicMock()
    http.post.side_effect = [_meta_resp({"id": "C1"}), _meta_resp({"success": True}), _meta_resp({"id": "M1"})]
    http.get.side_effect = [_meta_resp({"status_code": "IN_PROGRESS"}), _meta_resp({"status_code": "FINISHED"})]
    acct = {"ig_user_id": "U", "access_token": "TOK"}
    assert ig.publish_reel_file(acct, vid, {"title": "T"}, http=http, sleep=lambda s: None) == "M1"
    create, upload, publish = http.post.call_args_list
    assert create.kwargs["data"]["upload_type"] == "resumable" and create.kwargs["data"]["media_type"] == "REELS"
    assert "video_url" not in create.kwargs["data"] and "AI-generated" in create.kwargs["data"]["caption"]
    assert upload.args[0].startswith("https://rupload.facebook.com/ig-api-upload/") and upload.args[0].endswith("/C1")
    assert upload.kwargs["headers"]["Authorization"] == "OAuth TOK" and upload.kwargs["headers"]["offset"] == "0"
    assert publish.args[0].endswith("/U/media_publish") and publish.kwargs["data"]["creation_id"] == "C1"
    http.post.side_effect = [_meta_resp({"id": "C2"}), _meta_resp({"success": False, "debug_info": {"retriable": False}})]
    with pytest.raises(ig.InstagramError):
        ig.publish_reel_file(acct, vid, {"title": "T"}, http=http, sleep=lambda s: None)


def test_meta_caption_drops_youtube_lines_and_keeps_one_disclosure():
    from aurora.publish import instagram as ig

    desc = ("Who lived there? \U0001F447\n\nThe city falls.\n\nChapter 1, Part 10 of 10.\nAll parts in order: the \"Echoes\" playlist.\n\n"
            "This story and its visuals are AI-generated (synthetic media).\n\n#EchoesOfTheAether #anime #animestory")
    cap = ig.build_caption({"title": "T | Echoes Pt 10", "description": desc, "tags": ["echoes of the aether", "anime story"]})
    assert cap.startswith("T | Echoes Pt 10\n\nWho lived there?") and "The city falls." in cap and "Chapter 1, Part 10 of 10." in cap
    assert "playlist" not in cap and "synthetic media" not in cap and cap.lower().count("ai-generated") == 1
    assert cap.endswith("AI-generated content\n\n#EchoesOfTheAether #anime #animestory")
    assert "#echoes of" not in cap  # keyword tags with spaces are not hashtags
    old = ig.build_caption({"title": "T", "description": "Story.\n\nPerfect if you like anime story.\n\n#Shorts #anime"})
    assert "Perfect if" not in old and "#Shorts" not in old and old.endswith("#anime")
    prose = ig.build_caption({"title": "T", "description": "Rule #2 holds.\n\n#anime"})
    assert "Rule #2 holds." in prose and prose.endswith("AI-generated content\n\n#anime")
    long = ig.build_caption({"title": "T", "description": "x" * 5000 + "\n\n#tag"})
    assert len(long) <= 2200 and long.endswith("AI-generated content\n\n#tag")


def test_facebook_flow_flags_ai_and_uploads_directly(vid):
    from aurora.publish import facebook as fb

    http = MagicMock()
    http.post.side_effect = [_meta_resp({"video_id": "V1"}), _meta_resp({"success": True}), _meta_resp({"success": True})]
    http.get.side_effect = [_meta_resp({"status": {"video_status": "processing", "publishing_phase": {"status": "in_progress"}}}),
                            _meta_resp({"status": {"video_status": "ready", "publishing_phase": {"publish_status": "published"}}})]
    acct = {"page_id": "P", "access_token": "TOK"}
    assert fb.publish_reel(acct, vid, {"title": "T", "duration_secs": 30}, http=http, sleep=lambda s: None) == "V1"
    start, upload, finish = http.post.call_args_list
    assert start.kwargs["data"]["upload_phase"] == "start"
    assert upload.args[0].startswith("https://rupload.facebook.com/video-upload/") and upload.args[0].endswith("/V1")
    assert upload.kwargs["headers"]["Authorization"] == "OAuth TOK" and upload.kwargs["headers"]["offset"] == "0"
    assert finish.kwargs["data"]["upload_phase"] == "finish" and finish.kwargs["data"]["video_state"] == "PUBLISHED"
    assert finish.kwargs["data"]["is_ai_generated"] == "true" and "AI-generated" in finish.kwargs["data"]["description"]


def test_facebook_errors_and_slow_processing_never_double_post(vid):
    from aurora.publish import facebook as fb, instagram as ig

    acct = {"page_id": "P", "access_token": "T"}
    http = MagicMock()
    with pytest.raises(fb.ReelOutOfRange):
        fb.publish_reel(acct, vid, {"title": "T", "duration_secs": 91}, http=http)
    assert not http.post.called
    http.post.side_effect = [_meta_resp({"video_id": "V"}), _meta_resp({"success": True}), _meta_resp({"success": True})]
    http.get.return_value = _meta_resp({"status": {"video_status": "processing"}})
    assert fb.publish_reel(acct, vid, {"title": "T"}, http=http, sleep=lambda s: None, max_polls=2) == "V"  # accepted: no retry
    http.post.side_effect = [_meta_resp({"video_id": "V"}), _meta_resp({"success": True}), _meta_resp({"success": True})]
    http.get.side_effect = [TimeoutError("net"), _meta_resp({"status": {"publishing_phase": {"status": "complete"}}})]
    assert fb.publish_reel(acct, vid, {"title": "T"}, http=http, sleep=lambda s: None) == "V"  # a failed check is not a failed Reel
    http.get.side_effect = None
    http.post.side_effect = [_meta_resp({"video_id": "V"}), _meta_resp({"success": True}), _meta_resp({"success": True})]
    http.get.return_value = _meta_resp({"status": {"video_status": "error"}})
    with pytest.raises(fb.InstagramError):
        fb.publish_reel(acct, vid, {"title": "T"}, http=http, sleep=lambda s: None)
    http.post.side_effect = [_meta_resp({"error": {"code": 4, "message": "limit"}}, 400)]
    with pytest.raises(ig.RateLimited):
        fb.publish_reel(acct, vid, {"title": "T"}, http=http)


def test_facebook_dispatch_parks_too_long_reels_and_reports_platform(q, vid, workspace_tmp_path, monkeypatch):
    from aurora.publish import facebook as fb

    ok = q.enqueue("facts", vid, {"title": "Ok", "sha256": "a"}, now=NOW, platform="facebook")
    long = q.enqueue("facts", vid, {"title": "Long", "sha256": "b"}, now=NOW, platform="facebook")

    def pub(it):
        if it["id"] == long["id"]:
            raise fb.ReelOutOfRange("too long")
        return "FB1"

    r = autopilot.publish_due(q, now=NOW + timedelta(days=2), fb_publisher=pub, service_factory=lambda c: object())
    assert r["published"] == [ok["id"]] and r["failed"] == [long["id"]]
    assert q.get(ok["id"])["video_id"] == "FB1" and q.get(long["id"])["status"] == "failed"
    monkeypatch.setattr(fb, "load_account", lambda ch: {})
    n = autopilot.collect_analytics(q, fb_stats_fn=lambda a, ids: {"FB1": {"likeCount": 3}}, service_factory=lambda c: object())
    assert n == 1 and q.get(ok["id"])["stats"]["likeCount"] == 3
    md = autopilot.write_daily_report(q, now=NOW + timedelta(days=2), nim_credits=0, report_dir=workspace_tmp_path,
                                      events_dir=workspace_tmp_path).read_text(encoding="utf-8")
    assert "[facts/facebook] Long: too long" in md


def test_backfill_queues_from_part_one_and_refuses_disabled_series(workspace_tmp_path, vid):
    cfg = {"autopilot": {"publish": {"enabled": True, "slots": ["12:30", "18:30"]}, "channels": [
        {"name": "c", "series": [{"slug": "good"}, {"slug": "veer", "enabled": False}]}]}}
    q3 = PublishQueue(workspace_tmp_path / "b.json", cfg)
    made = {}
    for part, story, series in ((2, "good", "Good"), (1, "good", "Good"), (3, None, "Good"), (4, None, "Other")):
        it = q3.enqueue("c", vid, {"title": f"P{part}", "sha256": f"s{part}", "kind": "short", "chapter": 1, "part": part, "duration_secs": 30,
                                   "story": story, "series": series}, now=NOW)
        q3.mark_published(it["id"], f"Y{part}")
        made[part] = it
    q3.veto(q3.enqueue("c", vid, {"title": "Vetoed", "sha256": "v", "kind": "short", "chapter": 1, "part": 9, "story": "good", "series": "Good"}, now=NOW)["id"])
    assert len(autopilot.backfill(q3, "c", "instagram", "good", dry_run=True, now=NOW)) == 3
    assert not [i for i in q3.list() if i["platform"] == "instagram"]
    out = autopilot.backfill(q3, "c", "instagram", "good", limit=2, now=NOW)
    assert [i["part"] for i in out] == [1, 2] and out[0]["publish_at"] < out[1]["publish_at"] and out[0]["story"] == "good"
    assert [i["part"] for i in autopilot.backfill(q3, "c", "instagram", "good", dry_run=True, now=NOW)] == [3]
    assert [i["part"] for i in autopilot.backfill(q3, "c", "instagram", "good", now=NOW)] == [3]  # resumes, never twice
    with pytest.raises(PermissionError):
        autopilot.backfill(q3, "c", "instagram", "veer", now=NOW)
    with pytest.raises(ValueError):
        autopilot.backfill(q3, "c", "youtube", "good", now=NOW)


def test_presign_is_deterministic_and_expiring():
    from aurora.publish import hosting

    now = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
    u = hosting.presign("GET", "https://acc.r2.cloudflarestorage.com", "b", "aurora/x.mp4", "AK", "SK", now=now, expires=600)
    assert u == hosting.presign("GET", "https://acc.r2.cloudflarestorage.com", "b", "aurora/x.mp4", "AK", "SK", now=now, expires=600)
    assert "X-Amz-Expires=600" in u and "X-Amz-Signature=" in u and u.startswith("https://acc.r2.cloudflarestorage.com/b/aurora/x.mp4?")


def test_kill_switch_blocks_publish_unless_enabled(workspace_tmp_path, vid):
    q2 = PublishQueue(workspace_tmp_path / "k.json", {"autopilot": {"publish": {}}})  # default: disabled
    it = q2.enqueue("facts", vid, {"title": "T"}, now=NOW)
    up, sf = MagicMock(), MagicMock()
    r = autopilot.publish_due(q2, now=NOW + timedelta(days=1), service_factory=sf, uploader=up)
    assert r.get("disabled") and not r["published"]
    up.assert_not_called(); sf.assert_not_called()
    assert q2.get(it["id"])["status"] == "scheduled"
    assert autopilot.publish_due(q2, now=NOW + timedelta(days=1), dry_run=True)["would_publish"] == [it["id"]]


def test_research_failure_skips_instead_of_off_niche_topic():
    ch = {"name": "c", "niche": "anime", "language": "en", "platforms": ["youtube"]}
    fb = type("R", (), {"research_ideas": staticmethod(lambda *a: {"source": "topic_fallback", "ideas": [{}], "note": "503"})})
    slept = []
    assert autopilot.get_ideas(ch, 1, fb, sleep=slept.append) == [] and len(slept) == 2  # retried, then skipped
    calls = iter([{"source": "topic_fallback", "ideas": [{}]}, {"source": "gemini", "ideas": [{"title": "Ok", "hook": "h", "scenes": []}]}])
    flaky = type("R", (), {"research_ideas": staticmethod(lambda *a: next(calls))})
    assert autopilot.get_ideas(ch, 1, flaky, sleep=lambda s: None)[0]["title"] == "Ok"


# ---- shinobi: scopes, channel check, playlists, publish-one -------------------------
def _svc_with_playlists(existing=None, items=None):
    svc = MagicMock()
    svc.playlists.return_value.list.return_value.execute.return_value = {"items": existing or []}
    svc.playlists.return_value.insert.return_value.execute.return_value = {"id": "PL_NEW"}
    svc.playlistItems.return_value.list.return_value.execute.return_value = {"items": items or []}
    return svc


def test_old_token_scopes_ask_for_reauth(workspace_tmp_path, monkeypatch):
    tp = workspace_tmp_path / "youtube_x.json"
    tp.write_text('{"scopes": ["https://www.googleapis.com/auth/youtube.upload"], "refresh_token": "r"}', encoding="utf-8")
    monkeypatch.setattr(yt, "token_path", lambda c: tp)
    with pytest.raises(yt.AuthRequired, match="re-run"):
        yt.get_credentials("x")


def test_verify_channel_matches_or_refuses():
    svc = MagicMock()
    svc.channels.return_value.list.return_value.execute.return_value = {"items": [{"id": "UC1", "snippet": {"title": "Shinobi", "customUrl": "@shinobimindset1"}}]}
    assert yt.verify_channel(svc, "@ShinobiMindset1")["id"] == "UC1"
    with pytest.raises(yt.ChannelMismatch):
        yt.verify_channel(svc, "@SomeoneElse")


def test_playlist_created_once_cached_and_privacy(workspace_tmp_path):
    svc = _svc_with_playlists()
    pid = yt.add_to_series_playlist(svc, "shinobi", "Echoes of the Aether", "V1", "private", workspace_tmp_path)
    assert pid == "PL_NEW"
    assert svc.playlists.return_value.insert.call_args.kwargs["body"]["status"]["privacyStatus"] == "private"
    yt.add_to_series_playlist(svc, "shinobi", "Echoes of the Aether", "V2", "private", workspace_tmp_path)
    assert svc.playlists.return_value.insert.call_count == 1  # second video reuses the cached playlist
    assert svc.playlistItems.return_value.insert.call_count == 2
    yt.add_to_series_playlist(svc, "shinobi", "Echoes of the Aether", "V3", "public", workspace_tmp_path)
    assert svc.playlists.return_value.update.call_args.kwargs["body"]["status"]["privacyStatus"] == "public"


def test_playlist_reuses_existing_and_skips_duplicate_item(workspace_tmp_path):
    svc = _svc_with_playlists(existing=[{"id": "PL_OLD", "snippet": {"title": "echoes of the aether"}, "status": {"privacyStatus": "public"}}],
                              items=[{"id": "already"}])
    assert yt.add_to_series_playlist(svc, "shinobi", "Echoes of the Aether", "V1", "public", workspace_tmp_path) == "PL_OLD"
    svc.playlists.return_value.insert.assert_not_called()
    svc.playlistItems.return_value.insert.assert_not_called()


def test_publish_one_requires_confirm_and_bypasses_kill_switch(workspace_tmp_path, vid):
    q2 = PublishQueue(workspace_tmp_path / "p.json", {"autopilot": {"publish": {}}})  # kill-switch off
    it = q2.enqueue("shinobi", vid, {"title": "T", "series": "Echoes"}, now=NOW)
    q2.veto(it["id"])
    up, pl = MagicMock(return_value="VID9"), MagicMock(return_value="PLX")
    with pytest.raises(PermissionError):
        autopilot.publish_one(q2, it["id"], "private", False, service_factory=lambda c: object(), uploader=up)
    up.assert_not_called()
    done = autopilot.publish_one(q2, it["id"], "private", True, service_factory=lambda c: object(), uploader=up, playlist_fn=pl)
    assert done["status"] == "published" and done["video_id"] == "VID9" and done["privacy"] == "private" and done["playlist_id"] == "PLX"
    assert up.call_args.kwargs["privacy"] == "private" and pl.call_args.args[-1] == "private"
    with pytest.raises(ValueError):  # never twice
        autopilot.publish_one(q2, it["id"], "private", True, service_factory=lambda c: object(), uploader=up)


def test_wrong_channel_blocks_publish_due(q, vid):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)

    def wrong(c):
        raise yt.ChannelMismatch("authorised channel is @other")

    up = MagicMock()
    autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=wrong, uploader=up)
    up.assert_not_called()
    assert q.get(it["id"])["status"] == "scheduled"


def test_series_playlist_step_runs_and_failure_does_not_unpublish(q, vid):
    it = q.enqueue("facts", vid, {"title": "T", "series": "Echoes"}, now=NOW)
    pl = MagicMock(side_effect=RuntimeError("playlist quota"))
    autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=lambda c: object(),
                          uploader=MagicMock(return_value="V1"), playlist_fn=pl)
    got = q.get(it["id"])
    assert got["status"] == "published" and "playlist quota" in got["playlist_error"]
