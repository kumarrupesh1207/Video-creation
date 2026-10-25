"""YouTube manager: quota budget, SEO, captions, thumbnails, playlist order, branding, comments, analytics. All Google APIs mocked."""
import json
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import analytics as an
from aurora.publish import comments as cm
from aurora.publish import seo
from aurora.publish import youtube as yt
from aurora.publish.queue import PublishQueue
from aurora.publish.quota import QuotaBudget

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
NOKEY = {"research": {"api_key_env": "NOPE_NO_SUCH_KEY"}}


@pytest.fixture(autouse=True)
def _events_in_tmp(workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "EVENTS_DIR", workspace_tmp_path / "events")  # never write the real event log


@pytest.fixture
def q(workspace_tmp_path):
    return PublishQueue(workspace_tmp_path / "q.json", {"autopilot": {"publish": {"enabled": True, "veto_hours": 6, "slots": ["19:00"]}}})


@pytest.fixture
def vid(workspace_tmp_path):
    p = workspace_tmp_path / "a.mp4"
    p.write_bytes(b"fake-video-yt-manager")
    return p


def test_quota_budget_blocks_before_the_call(workspace_tmp_path):
    b = QuotaBudget(workspace_tmp_path, limit=2000)
    b.spend("videos.insert")
    assert b.used() == 1600 and b.can_afford("thumbnails.set") and not b.can_afford("videos.insert")
    with pytest.raises(yt.QuotaExceeded):
        b.spend("videos.insert")
    assert b.used() == 1600  # the refused call spent nothing


def test_publish_due_defers_when_daily_budget_is_spent(q, vid, workspace_tmp_path):
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    up = MagicMock()
    b = QuotaBudget(workspace_tmp_path / "qb", limit=1000)
    r = autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=lambda c: object(), uploader=up, budget=b)
    up.assert_not_called()
    assert r["deferred"] == [it["id"]] and q.get(it["id"])["attempts"] == 0


def test_seo_pick_best_chapters_and_description():
    kws = ["kaelen", "anime", "aether"]
    assert seo.pick_best(["x" * 120, "Kaelen and the Aether: an anime story of loss", "Short"], kws).startswith("Kaelen and the Aether")
    ch = [{"start": 5, "title": "Intro"}, {"start": 60, "title": "Fight"}, {"start": 200, "title": "End"}]
    txt = seo.format_chapters(ch, 270)
    assert txt.splitlines()[0] == "0:00 Intro" and "3:20 End" in txt
    assert seo.format_chapters(ch[:2], 270) == "" and seo.format_chapters(ch, 40) == ""  # <3 chapters / short video
    d = seo.build_description("Hook.", "Recap.", ["anime"], ["#Shorts"], txt)
    assert "AI-generated" in d and "Chapters:" in d and d.startswith("Hook.")


def test_seo_build_metadata_with_mocked_gemini_and_search(workspace_tmp_path):
    svc = MagicMock()
    svc.search.return_value.list.return_value.execute.return_value = {"items": [{"id": {"videoId": "v1"}, "snippet": {"title": "Best anime story aether"}}]}
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"snippet": {"tags": ["Anime", "aether"]}}]}
    client = MagicMock()
    client.models.generate_content.return_value.text = json.dumps(
        {"titles": ["Short", "Kaelen Enters the Aether: Anime Story of Power"], "hook": "He sees it."})
    budget = MagicMock()
    m = seo.build_metadata("t", "Echoes of the Aether", 2, "Recap text", ["#anime"], "Plain", svc, budget, client,
                           cache_dir=workspace_tmp_path / "seo")
    assert m["title"].endswith("Part 2") and m["source"] == "gemini" and "He sees it." in m["description"]
    budget.spend.assert_any_call("search.list")
    none = seo.build_metadata("t", "S", 1, "r", fallback_title="Plain title", client=None, cfg=NOKEY)
    assert none["title"] == "Plain title" and none["source"] == "none"  # no key: the caller's title, nothing invented


def test_srt_captions_and_thumbnail(workspace_tmp_path):
    srt = yt.srt_from_cues([{"start": 0, "end": 1.25, "text": "Hello"}, {"start": 1.25, "end": 3, "text": " "}, {"start": 3, "end": 4, "text": "Bye"}])
    assert "00:00:01,250" in srt and srt.count("-->") == 2 and srt.startswith("1\n")
    svc = MagicMock()
    svc.captions.return_value.insert.return_value.execute.return_value = {"id": "CAP1"}
    assert yt.upload_captions(svc, "V", srt, budget=MagicMock()) == "CAP1"
    assert svc.captions.return_value.insert.call_args.kwargs["body"]["snippet"]["videoId"] == "V"
    big = workspace_tmp_path / "t.png"
    big.write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        yt.set_thumbnail(svc, "V", big)
    ok = workspace_tmp_path / "ok.jpg"
    ok.write_bytes(b"1234")
    yt.set_thumbnail(svc, "V", ok, budget=MagicMock())
    assert svc.thumbnails.return_value.set.call_args.kwargs["videoId"] == "V"


def _svc_with_playlists():
    svc = MagicMock()
    svc.playlists.return_value.list.return_value.execute.return_value = {"items": []}
    svc.playlists.return_value.insert.return_value.execute.return_value = {"id": "PL_NEW"}
    return svc


def test_playlist_position_follows_part_and_is_clamped(workspace_tmp_path):
    svc = _svc_with_playlists()
    svc.playlistItems.return_value.list.return_value.execute.side_effect = [{"items": []}, {"pageInfo": {"totalResults": 1}}]
    yt.add_to_series_playlist(svc, "shinobi", "Echoes", "V2", "public", workspace_tmp_path, part=5)
    assert svc.playlistItems.return_value.insert.call_args.kwargs["body"]["snippet"]["position"] == 1
    assert svc.playlists.return_value.insert.call_args.kwargs["body"]["snippet"]["description"]


def test_branding_is_draft_only_until_applied(workspace_tmp_path):
    f = yt.draft_branding("shinobi", "Anime stories", ["anime", "story time"], workspace_tmp_path)
    assert "draft" in f.read_text(encoding="utf-8")
    svc = MagicMock()
    svc.channels.return_value.list.return_value.execute.return_value = {"items": [{"brandingSettings": {"channel": {"title": "T"}}}]}
    yt.apply_branding(svc, "UC1", "New desc", ["anime", "story time"])
    body = svc.channels.return_value.update.call_args.kwargs["body"]["brandingSettings"]["channel"]
    assert body["title"] == "T" and body["keywords"] == 'anime "story time"'


def _threads(*items):
    return {"items": [{"snippet": {"totalReplyCount": 0, "topLevelComment": {"id": i, "snippet": {
        "authorDisplayName": "Fan", "textDisplay": t, "videoId": "V", "authorChannelId": {"value": "OTHER"}}}}} for i, t in items]}


def test_comments_hold_spam_draft_reply_and_send_only_approved(workspace_tmp_path):
    svc = MagicMock()
    svc.commentThreads.return_value.list.return_value.execute.return_value = _threads(
        ("c1", "Loved this story! Kaelen is great"), ("c2", "Join my telegram t.me/xyz for crypto"))
    out = cm.process(svc, "shinobi", "UC1", client=None, cfg=NOKEY, state_dir=workspace_tmp_path)
    assert out == {"new": 2, "held": 1, "drafted": 1}
    assert svc.comments.return_value.setModerationStatus.call_args.kwargs == {"id": "c2", "moderationStatus": "heldForReview"}
    assert [c["id"] for c in cm.pending("shinobi", workspace_tmp_path)] == ["c1"]
    assert cm.send_approved(svc, "shinobi", state_dir=workspace_tmp_path) == []  # nothing approved -> nothing posted
    svc.comments.return_value.insert.assert_not_called()
    cm.approve("shinobi", "c1", "Thank you! https://spam.example more soon", workspace_tmp_path)  # links are stripped
    assert cm.send_approved(svc, "shinobi", state_dir=workspace_tmp_path) == ["c1"]
    assert "http" not in svc.comments.return_value.insert.call_args.kwargs["body"]["snippet"]["textOriginal"]
    assert cm.send_approved(svc, "shinobi", state_dir=workspace_tmp_path) == []  # never twice
    assert cm.process(svc, "shinobi", "UC1", cfg=NOKEY, state_dir=workspace_tmp_path)["new"] == 0  # seen ones are not re-drafted


def test_weekly_report_and_winners_feed(workspace_tmp_path):
    def query(**kw):
        r = MagicMock()
        by_video = bool(kw.get("dimensions"))
        cols = [{"name": n} for n in (["video"] if by_video else []) + an.COLS]
        row = (["V1"] if by_video else []) + [100, 50, 30, 72.5, 3, 9, 2]
        r.execute.return_value = {"columnHeaders": cols, "rows": [row]}
        return r

    analytics = MagicMock()
    analytics.reports.return_value.query.side_effect = lambda **kw: query(**kw)
    rep = an.weekly_report(analytics, "shinobi", date(2026, 10, 26), titles_by_id={"V1": "Echoes - Part 1"}, state_dir=workspace_tmp_path)
    assert rep["views"] == 100 and rep["top"][0]["title"] == "Echoes - Part 1" and "Best retention" in rep["insight"]
    assert (workspace_tmp_path / "reports" / "shinobi_2026-10-25.md").exists()
    assert an.load_winners("shinobi", state_dir=workspace_tmp_path)[0]["avg_view_pct"] == 72.5
    assert an.latest_report("shinobi", workspace_tmp_path)["views"] == 100


def test_analytics_scope_is_optional_for_uploads(workspace_tmp_path, monkeypatch):
    tp = workspace_tmp_path / "t.json"
    tp.write_text(json.dumps({"scopes": yt.SCOPES, "refresh_token": "r"}), encoding="utf-8")
    monkeypatch.setattr(yt, "token_path", lambda c: tp)
    with pytest.raises(yt.AuthRequired, match="yt-analytics"):  # analytics needs the extra consent
        yt.get_credentials("x", need=(yt.ANALYTICS_SCOPE,))
    try:  # uploads keep working on a token without it: the scope check must not complain
        yt.get_credentials("x")
    except yt.AuthRequired as e:
        assert "lacks scopes" not in str(e)
    except Exception:  # noqa: BLE001 - the fake token cannot refresh offline; only the scope check matters
        pass


def test_post_upload_steps_fail_independently(q, vid):
    it = q.enqueue("facts", vid, {"title": "T", "series": "Echoes", "captions_srt": "1\n0 --> 1\nhi\n", "thumbnail": "t.png",
                                  "duration_secs": 270, "part": 2}, now=NOW)
    cap, thumb, pl = MagicMock(side_effect=RuntimeError("cap fail")), MagicMock(), MagicMock(return_value="PL")
    autopilot._post_upload(q, q.get(it["id"]), "V1", object(), pl, "private", None, cap, thumb)
    got = q.get(it["id"])
    assert "cap fail" in got["captions_error"] and got["thumbnail_done"] and got["playlist_id"] == "PL"


def test_thumbnail_skipped_for_shorts(q, vid):
    it = q.enqueue("facts", vid, {"title": "S", "thumbnail": "t.png", "duration_secs": 20}, now=NOW)
    thumb = MagicMock()
    autopilot._post_upload(q, q.get(it["id"]), "V1", object(), None, None, None, None, thumb)
    thumb.assert_not_called()


def test_approvals_summary_lists_queue_drafts_report(q, vid, workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "STATE_DIR", workspace_tmp_path)
    monkeypatch.setattr(an, "STATE_DIR", workspace_tmp_path)
    it = q.enqueue("facts", vid, {"title": "T"}, now=NOW)
    s = autopilot.approvals_summary(q, ["facts"])
    assert [i["id"] for i in s["queue"]] == [it["id"]] and s["comment_drafts"] == {"facts": []} and "facts" in s["weekly_report"]


def _http_404():
    from googleapiclient.errors import HttpError

    return HttpError(MagicMock(status=404), b'{"error": {"errors": [{"reason": "playlistNotFound"}]}}')


def test_new_playlist_404_is_retried_then_added_at_position(workspace_tmp_path):
    svc = _svc_with_playlists()
    ex = svc.playlistItems.return_value.list.return_value.execute
    ex.side_effect = [_http_404(), _http_404(), {"items": []}, {"pageInfo": {"totalResults": 0}}]
    slept = []
    yt.add_to_series_playlist(svc, "shinobi", "Echoes", "V1", "private", workspace_tmp_path, part=1, sleep=slept.append)
    assert slept == [2, 5]
    assert svc.playlistItems.return_value.insert.call_args.kwargs["body"]["snippet"]["position"] == 0


def test_playlist_404_that_never_clears_fails_loudly(workspace_tmp_path, capsys):
    from googleapiclient.errors import HttpError

    svc = _svc_with_playlists()
    svc.playlistItems.return_value.list.return_value.execute.side_effect = _http_404()
    with pytest.raises(HttpError):
        yt.add_to_series_playlist(svc, "shinobi", "Echoes", "V1", "private", workspace_tmp_path, part=1, sleep=lambda s: None)
    assert "NOT added" in capsys.readouterr().err
    svc.playlistItems.return_value.insert.assert_not_called()
