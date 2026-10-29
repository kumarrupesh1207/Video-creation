"""Hook-first titles, engagement-question first line, ONE engagement comment, public series playlist. Mocked; nothing is uploaded."""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import comments, seo
from aurora.publish.queue import PublishQueue

NOKEY = {"research": {"api_key_env": "NOPE_NO_SUCH_KEY"}}
NOW = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
CFG = {"autopilot": {"publish": {"enabled": False, "upload_ahead": True, "veto_hours": 6, "slots": ["19:00"]},
                     "channels": [{"name": "shinobi", "publish_enabled": True, "slots": ["12:30"], "reels_per_day": 1, "platforms": ["youtube"]}]}}
SERIES = "Echoes of the Aether"


@pytest.fixture(autouse=True)
def _events_in_tmp(workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "EVENTS_DIR", workspace_tmp_path / "events")


def _meta(workspace_tmp_path, **kw):
    args = dict(hashtags=["#anime", "#animestory"], fallback_title=f"{SERIES} – Ch.1 Part 2: x", service=None, cfg=NOKEY, duration_secs=30,
                chapter=1, beat="Kaelen finds a sphere.", events=["Shadows form a claw."], caption="He runs. Part 3 tomorrow", parts=10,
                playlist=SERIES, hook="Kaelen awakens the ruin", cache_dir=workspace_tmp_path, main_keyword="dark fantasy anime",
                series_hashtag_="#EchoesOfTheAether")
    args.update(kw)
    return seo.build_metadata("t", SERIES, 2, "recap", **args)


def test_title_leads_with_the_hook_and_series_follows_within_70_chars(workspace_tmp_path):
    m = _meta(workspace_tmp_path, hook_title="The ruin was never empty", engagement_question="Would you have opened it?")
    assert m["title"] == f"The ruin was never empty | {SERIES} Pt 2" and len(m["title"]) <= 70
    assert "Ch." not in m["title"] and m["hook_title_used"]
    long = _meta(workspace_tmp_path, hook_title="An extremely long hook title that keeps going and going well past any sane limit")["title"]
    assert len(long) <= 70 and long.endswith(f" | {SERIES} Pt 2") and not long.startswith(" ")


def test_description_line_one_is_the_question_then_chapter_in_body(workspace_tmp_path):
    m = _meta(workspace_tmp_path, hook_title="The ruin was never empty", engagement_question="Would you have opened it?")
    d = m["description"]
    assert d.splitlines()[0] == "Would you have opened it? 👇"
    assert "Chapter 1, Part 2 of 10." in d and "Watch Part 3 next" in d
    assert d.count("AI-generated") == 1 and d.rstrip().endswith("#EchoesOfTheAether #anime #animestory")
    assert m["engagement_question"] == "Would you have opened it?"


def test_missing_hook_title_and_question_fall_back_with_a_warning(workspace_tmp_path):
    m = _meta(workspace_tmp_path)
    assert m["title"] == f"Kaelen awakens the ruin | {SERIES} Pt 2"
    assert m["description"].splitlines()[0] == f"{seo.FALLBACK_QUESTION} 👇"
    assert any("hook_title" in w for w in m["warnings"]) and any("engagement_question" in w for w in m["warnings"])


def test_non_series_video_keeps_the_old_title_style(workspace_tmp_path):
    m = seo.build_metadata("A topic", None, None, "r", fallback_title="A topic", cfg=NOKEY, cache_dir=workspace_tmp_path)
    assert not m["hook_title_used"] and m["engagement_question"] == "" and "👇" not in m["description"]


def test_apply_seo_uses_hook_title_for_chapter_parts_and_stores_the_question(workspace_tmp_path):
    ch = {"name": "shinobi"}
    idea = {"topic": "t", "series": SERIES, "part": 2, "chapter": 1, "parts": 10, "hook": "h", "hook_title": "The ruin was never empty",
            "engagement_question": "Would you have opened it?", "recap": "r"}
    meta = {"title": f"{SERIES} – Ch.1 Part 2: h", "description": "", "tags": [], "duration_secs": 30}
    out = autopilot.apply_seo(NOKEY, ch, idea, meta, service_factory=lambda c: None)
    assert out["title"].startswith("The ruin was never empty | ") and out["engagement_question"] == "Would you have opened it?"
    out2 = autopilot.apply_seo(NOKEY, ch, {**idea, "hook_title": "", "hook": ""}, meta, service_factory=lambda c: None)
    assert out2["title"] == meta["title"]  # no hook at all: Backend's fixed chapter-part title stays


def test_idea_from_sidecar_reads_the_new_fields():
    i = autopilot.idea_from_sidecar({"title": "t", "hook_title": "H", "engagement_question": "Q?"})
    assert i["hook_title"] == "H" and i["engagement_question"] == "Q?"


def test_engagement_comment_is_posted_once_when_public_and_counts_quota():
    svc = MagicMock()
    svc.commentThreads.return_value.insert.return_value.execute.return_value = {"snippet": {"topLevelComment": {"id": "C1"}}}
    budget = MagicMock()
    assert comments.post_engagement_comment(svc, "VID", " Would you? ", budget) == "C1"
    budget.spend.assert_called_once_with("commentThreads.insert")
    body = svc.commentThreads.return_value.insert.call_args.kwargs["body"]["snippet"]
    assert body["videoId"] == "VID" and body["topLevelComment"]["snippet"]["textOriginal"] == "Would you?"


def test_reconcile_posts_comment_makes_playlist_public_and_reports_pin(workspace_tmp_path):
    q = PublishQueue(workspace_tmp_path / "q.json", CFG)
    f = workspace_tmp_path / "a.mp4"
    f.write_bytes(b"a")
    it = q.enqueue("shinobi", f, {"title": "T", "series": SERIES, "playlist": SERIES, "part": 2, "engagement_question": "Would you?"}, now=NOW)
    q.mark_uploaded_scheduled(it["id"], "VID1")
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"status": {"privacyStatus": "public"}}]}
    posted, pub = [], []
    kw = dict(comment_fn=lambda s, v, t, budget=None: posted.append((v, t)) or "C1", playlist_public_fn=lambda s, c, n, p: pub.append((c, n, p)) or "PL")
    later = NOW + timedelta(days=3)
    assert autopilot.reconcile(q, later, lambda c: svc, {}, set(), **kw) == 1
    assert posted == [("VID1", "Would you?")] and pub == [("shinobi", SERIES, "public")]
    got = q.get(it["id"])
    assert got["engagement_comment_id"] == "C1"
    autopilot.went_public(q, got, svc, **kw)  # a second call never comments twice
    assert len(posted) == 1
    rep = autopilot.write_daily_report(q, now=datetime.now(timezone.utc), nim_credits=0, report_dir=workspace_tmp_path / "r",
                                       events_dir=workspace_tmp_path / "events").read_text(encoding="utf-8")
    assert "Pin these comments in YouTube Studio (1)" in rep and "Would you?" in rep


def test_comment_failure_never_unpublishes_and_is_flagged(workspace_tmp_path):
    q = PublishQueue(workspace_tmp_path / "q.json", CFG)
    f = workspace_tmp_path / "a.mp4"
    f.write_bytes(b"a")
    it = q.enqueue("shinobi", f, {"title": "T", "engagement_question": "Q?"}, now=NOW)
    q.mark_uploaded_scheduled(it["id"], "VID1")
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"status": {"privacyStatus": "public"}}]}

    def boom(*a, **k):
        raise RuntimeError("quota")

    assert autopilot.reconcile(q, NOW + timedelta(days=3), lambda c: svc, {}, set(), comment_fn=boom) == 1
    got = q.get(it["id"])
    assert got["status"] == "published" and "quota" in got["engagement_comment_error"] and not got.get("engagement_comment_id")
