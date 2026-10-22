"""Autopilot / publish queue / YouTube uploader tests. Google APIs are mocked; nothing is uploaded."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import youtube as yt
from aurora.publish.queue import PublishQueue

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
CFG = {"autopilot": {"publish": {"veto_hours": 6, "slots": ["19:00"]},
                     "channels": [{"name": "facts", "niche": "science", "reels_per_day": 2}, {"name": "hindi", "niche": "x"}]}}


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
    q.approve(it["id"])
    assert len(q.due(NOW + timedelta(days=2))) == 1
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
    def research(niche, lang, n, channel=None):
        return [{"topic": f"{channel}-{i}", "title": f"Title {i}", "hashtags": ["#x"]} for i in range(n)]

    mod = type("R", (), {"generate_ideas": staticmethod(research)})

    def renderer(cfg, ch, idea):
        p = workspace_tmp_path / f"{idea['topic']}.mp4"
        p.write_bytes(idea["topic"].encode())
        return {"status": "ok", "reel": {"path": str(p), "sha256": idea["topic"]}}

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
    assert all(not x.get("image_prompt") for x in autopilot.get_ideas(ch, 1, fb))  # topics.txt stub (or empty)


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


def test_presign_is_deterministic_and_expiring():
    from aurora.publish import hosting

    now = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
    u = hosting.presign("GET", "https://acc.r2.cloudflarestorage.com", "b", "aurora/x.mp4", "AK", "SK", now=now, expires=600)
    assert u == hosting.presign("GET", "https://acc.r2.cloudflarestorage.com", "b", "aurora/x.mp4", "AK", "SK", now=now, expires=600)
    assert "X-Amz-Expires=600" in u and "X-Amz-Signature=" in u and u.startswith("https://acc.r2.cloudflarestorage.com/b/aurora/x.mp4?")
