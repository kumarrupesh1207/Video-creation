"""Upload-ahead publishing, per-channel switches, cadence, weekly long slot, daily report. Mocked; nothing is uploaded."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import youtube as yt
from aurora.publish.queue import PublishQueue

NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)  # Thursday 11:30 IST
CHANNELS = [
    {"name": "shinobi", "publish_enabled": True, "slots": ["12:30", "18:30"], "reels_per_day": 2, "platforms": ["youtube"],
     "long_episode": {"weekday": "saturday", "time": "18:00"}, "niche": "anime"},
    {"name": "main", "publish_enabled": False, "render_enabled": False, "reels_per_day": 3, "platforms": ["youtube"]},
]
CFG = {"autopilot": {"publish": {"enabled": False, "upload_ahead": True, "veto_hours": 6, "slots": ["19:00"]}, "channels": CHANNELS}}


@pytest.fixture(autouse=True)
def _events_in_tmp(workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "EVENTS_DIR", workspace_tmp_path / "events")  # never write the real event log


@pytest.fixture
def q(workspace_tmp_path):
    return PublishQueue(workspace_tmp_path / "q.json", CFG)


def mp4(tmp, name):
    p = tmp / f"{name}.mp4"
    p.write_bytes(name.encode())
    return p


def test_slots_per_channel_and_weekly_long_slot(q, workspace_tmp_path):
    a = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "a"}, now=NOW)
    b = q.enqueue("shinobi", mp4(workspace_tmp_path, "b"), {"title": "b"}, now=NOW)
    # 11:30 IST + 6 h veto = 17:30 IST -> 18:30 IST today, then 12:30 IST tomorrow
    assert datetime.fromisoformat(a["publish_at"]) == datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc)
    assert datetime.fromisoformat(b["publish_at"]) == datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc)
    long_ = q.enqueue("shinobi", mp4(workspace_tmp_path, "L"), {"title": "ep", "kind": "long"}, now=NOW)
    assert datetime.fromisoformat(long_["publish_at"]) == datetime(2026, 10, 24, 12, 30, tzinfo=timezone.utc)  # Sat 18:00 IST
    assert long_["kind"] == "long"


def test_upload_ahead_uploads_private_with_publish_at_after_veto_window(q, workspace_tmp_path):
    it = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "T", "series": "Echoes", "part": 2}, now=NOW)
    up = MagicMock(return_value="VID1")
    pl = MagicMock(return_value="PL1")
    early = NOW + timedelta(hours=3)  # veto window (6 h) still open
    autopilot.publish_due(q, now=early, service_factory=lambda c: object(), uploader=up, playlist_fn=pl)
    up.assert_not_called()
    r = autopilot.publish_due(q, now=NOW + timedelta(hours=6, minutes=30), service_factory=lambda c: object(), uploader=up, playlist_fn=pl)
    assert r["scheduled"] == [it["id"]] and not r["published"]
    kw = up.call_args.kwargs
    assert kw["privacy"] == "private" and kw["publish_at"] == "2026-10-22T13:00:00.000Z"
    got = q.get(it["id"])
    assert got["status"] == "uploaded_scheduled" and got["video_id"] == "VID1" and got["playlist_id"] == "PL1"
    assert pl.call_args.args[-1] == "public"  # the series playlist is public: the video will be


def test_slot_already_past_publishes_public_now(q, workspace_tmp_path):
    it = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "T"}, now=NOW)
    up = MagicMock(return_value="VID2")
    r = autopilot.publish_due(q, now=NOW + timedelta(days=1), service_factory=lambda c: object(), uploader=up)
    assert r["published"] == [it["id"]] and up.call_args.kwargs["privacy"] == "public" and "publish_at" not in up.call_args.kwargs


def test_main_never_publishes_and_all_disabled_returns_disabled(q, workspace_tmp_path):
    q.enqueue("main", mp4(workspace_tmp_path, "m"), {"title": "M"}, now=NOW)
    s = q.enqueue("shinobi", mp4(workspace_tmp_path, "s"), {"title": "S"}, now=NOW)
    up = MagicMock(return_value="V")
    r = autopilot.publish_due(q, now=NOW + timedelta(days=2), service_factory=lambda c: object(), uploader=up)
    assert [c.args for c in up.call_args_list] == [] or up.call_count == 1  # only shinobi's item
    assert q.get(s["id"])["status"] in ("published", "uploaded_scheduled") and len(r["skipped_disabled"]) == 1
    off = PublishQueue(q.path.with_name("off.json"), {"autopilot": {"publish": {}, "channels": [{"name": "main", "publish_enabled": False}]}})
    off.enqueue("main", mp4(workspace_tmp_path, "m2"), {"title": "M"}, now=NOW)
    up2 = MagicMock()
    assert autopilot.publish_due(off, now=NOW + timedelta(days=2), uploader=up2).get("disabled")
    up2.assert_not_called()


def test_veto_uploaded_item_removes_publish_at_on_youtube(q, workspace_tmp_path):
    it = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "T"}, now=NOW)
    q.mark_uploaded_scheduled(it["id"], "VID1")
    with pytest.raises(ValueError, match="veto_item"):
        q.veto(it["id"])  # the plain queue veto must not silently skip YouTube
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"status": {
        "privacyStatus": "private", "publishAt": "2026-10-22T13:00:00Z", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}]}
    out = autopilot.veto_item(q, it["id"], service_factory=lambda c: svc)
    body = svc.videos.return_value.update.call_args.kwargs["body"]
    assert out["status"] == "vetoed" and body["id"] == "VID1"
    assert body["status"]["privacyStatus"] == "private" and "publishAt" not in body["status"] and body["status"]["containsSyntheticMedia"] is True


def test_approve_vetoed_uploaded_item_reschedules_without_reupload(q, workspace_tmp_path):
    it = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "T"}, now=NOW)
    q.mark_uploaded_scheduled(it["id"], "VID1")
    q._update(it["id"], status="vetoed")
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"status": {"privacyStatus": "private"}}]}
    out = autopilot.approve_item(q, it["id"], service_factory=lambda c: svc, now=NOW + timedelta(hours=7))
    st = svc.videos.return_value.update.call_args.kwargs["body"]["status"]
    assert out["status"] == "uploaded_scheduled" and st["privacyStatus"] == "private" and st["publishAt"].endswith("Z")


def test_reconcile_marks_public_scheduled_videos_published(q, workspace_tmp_path):
    it = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "T"}, now=NOW)
    q.mark_uploaded_scheduled(it["id"], "VID1")
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"status": {"privacyStatus": "public"}}]}
    assert autopilot.reconcile(q, NOW + timedelta(days=1), lambda c: svc, {}, set()) == 1
    assert q.get(it["id"])["status"] == "published"
    assert autopilot.reconcile(q, NOW, lambda c: svc, {}, set()) == 0  # nothing left uploaded_scheduled


def test_shorts_needed_fills_only_the_days_deficit(q, workspace_tmp_path):
    ch = CHANNELS[0]
    assert autopilot.shorts_needed(q, ch, NOW) == 2
    q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "a"}, now=NOW)
    assert autopilot.shorts_needed(q, ch, NOW) == 1
    q.enqueue("shinobi", mp4(workspace_tmp_path, "b"), {"title": "b"}, now=NOW)
    assert autopilot.shorts_needed(q, ch, NOW) == 0  # three render runs a day make 2 reels, not 6


def test_render_skips_disabled_channel_and_queues_long_episode(q, workspace_tmp_path):
    calls = []

    def research(niche, lang, plat, n):
        calls.append(n)
        return {"source": "gemini", "ideas": [{"title": "Idea", "hook": "h", "description": "d", "hashtags": [], "scenes": []}] * n}

    mod = type("R", (), {"research_ideas": staticmethod(research)})
    ep = workspace_tmp_path / "ep.mp4"
    ep.write_bytes(b"episode")
    side = {"title": "Echoes - Part 1", "episode": 1, "hook": "h", "description": "d", "hashtags": ["#anime"], "chapters": [], "chapters_text": "",
            "captions_srt": "x.srt", "thumbnail": "t.jpg", "video": {"path": str(ep), "sha256": "e1", "duration_secs": 270}}
    out = autopilot.run_render(CFG, q, research=mod, renderer=lambda *a: {"status": "bad"}, now=NOW,
                               episode_fn=lambda *a: {"status": "rendered", "episode": side, "shorts": []})
    assert calls  # shinobi researched, main never
    assert [i["channel"] for i in out] == ["shinobi"] and out[0]["kind"] == "long"
    assert datetime.fromisoformat(out[0]["publish_at"]).weekday() == 5  # Saturday
    again = autopilot.render_long(CFG, q, CHANNELS[0], lambda *a: pytest.fail("long episode already queued"), NOW)
    assert again == []
    paused = autopilot.render_long(CFG, PublishQueue(q.path.with_name("e.json"), CFG), CHANNELS[0],
                                   lambda *a: {"status": "paused", "remaining": 20}, NOW)
    assert paused == []


def test_daily_report_lists_everything(q, workspace_tmp_path):
    a = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "Rendered one"}, now=NOW)
    b = q.enqueue("shinobi", mp4(workspace_tmp_path, "b"), {"title": "Scheduled one"}, now=NOW)
    q.mark_uploaded_scheduled(b["id"], "VIDB")
    q._update(a["id"], error="boom")
    budget = MagicMock()
    budget.used.return_value = 1650
    autopilot._event("upload_failed", "shinobi", "something broke", NOW)
    out = autopilot.write_daily_report(q, budget, NOW + timedelta(hours=1), nim_credits=12, report_dir=workspace_tmp_path / "r",
                                       events_dir=workspace_tmp_path / "events")
    text = out.read_text(encoding="utf-8")
    assert out.name == "2026-10-22.md" and "NIM credits used today: 12" in text and "1650 / 10000" in text
    assert "Rendered one" in text and "Scheduled on YouTube (1)" in text and "VIDB" in text and "boom" in text and "something broke" in text


def test_tee_logs_writes_utf8_copy(workspace_tmp_path, monkeypatch, capsys):
    import sys

    monkeypatch.delenv("AURORA_NO_LOG_TEE", raising=False)
    out0, err0 = sys.stdout, sys.stderr
    try:
        path = autopilot.tee_logs("render", workspace_tmp_path)
        print("héllo – Part 1 ✓")
        print("oops", file=sys.stderr)
    finally:
        sys.stdout, sys.stderr = out0, err0
    raw = path.read_bytes()
    assert b"\x00" not in raw and not raw.startswith((b"\xff\xfe", b"\xfe\xff"))  # not UTF-16
    text = raw.decode("utf-8")
    assert "héllo – Part 1 ✓" in text and "oops" in text and "autopilot render" in text
