"""Chapter publishing: per-series playlist ordered by (chapter, part), chapter compilation queued at its hint. Mocked."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import seo
from aurora.publish import youtube as yt
from aurora.publish.queue import PublishQueue

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc)  # Monday
CH = {"name": "shinobi", "platforms": ["youtube"], "format": "comic", "reels_per_day": 2}
CFG = {"autopilot": {"publish": {"veto_hours": 6, "slots": ["12:30", "18:30"]}, "channels": [CH]}}


@pytest.fixture(autouse=True)
def _events_in_tmp(workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot, "EVENTS_DIR", workspace_tmp_path / "events")


@pytest.fixture
def q(workspace_tmp_path):
    return PublishQueue(workspace_tmp_path / "q.json", CFG)


def mp4(tmp, name):
    p = tmp / f"{name}.mp4"
    p.write_bytes(name.encode())
    return p


def test_order_key_puts_the_compilation_after_its_parts():
    parts = [{"chapter": 1, "part": 10}, {"chapter": 1, "part": 2}, {"chapter": 2, "part": 1}]
    comp = {"chapter": 1, "kind": "chapter"}
    keys = sorted([autopilot.order_key(i) for i in parts] + [autopilot.order_key(comp)])
    assert keys == [(1, 2), (1, 10), (1, 10000), (2, 1)]


def test_position_counts_unknown_pilot_videos_as_earlier():
    svc = MagicMock()
    svc.playlistItems.return_value.list.return_value.execute.return_value = {"items": [
        {"snippet": {"resourceId": {"videoId": "BvQaD-dpGvI"}}},  # pilot video: not in the queue, never a part
        {"snippet": {"resourceId": {"videoId": "p1"}}}, {"snippet": {"resourceId": {"videoId": "p3"}}}]}
    keys = {"p1": (1, 1), "p3": (1, 3)}
    assert yt.compute_position(svc, "PL", (1, 2), keys) == 2  # pilot + part 1 come first
    assert yt.compute_position(svc, "PL", (1, 4), keys) == 3
    assert yt.compute_position(svc, "PL", (1, 0), keys) == 1  # the pilot still sits above everything


def test_playlist_insert_uses_chapter_part_order(q, workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(yt, "STATE_DIR", workspace_tmp_path / "state")
    for n in (1, 2):  # parts 1 and 2 already uploaded
        it = q.enqueue("shinobi", mp4(workspace_tmp_path, f"p{n}"), {"title": f"P{n}", "series": "Echoes", "playlist": "Echoes",
                                                                    "chapter": 1, "part": n}, now=NOW)
        q.mark_published(it["id"], f"v{n}")
    new = q.enqueue("shinobi", mp4(workspace_tmp_path, "p3"), {"title": "P3", "series": "Echoes", "playlist": "Echoes", "chapter": 1, "part": 3}, now=NOW)
    svc = MagicMock()
    svc.playlists.return_value.list.return_value.execute.return_value = {"items": []}
    svc.playlists.return_value.insert.return_value.execute.return_value = {"id": "PL1"}
    pl_items = svc.playlistItems.return_value.list.return_value.execute
    pl_items.side_effect = [{"items": []},  # dedupe lookup
                            {"items": [{"snippet": {"resourceId": {"videoId": "pilot"}}}, {"snippet": {"resourceId": {"videoId": "v1"}}},
                                       {"snippet": {"resourceId": {"videoId": "v2"}}}]}]
    autopilot._add_to_playlist(q, new, "v3", svc, None, "public", None)
    snippet = svc.playlistItems.return_value.insert.call_args.kwargs["body"]["snippet"]
    assert snippet["position"] == 3 and snippet["playlistId"] == "PL1"
    assert q.get(new["id"])["playlist_id"] == "PL1"


def test_enqueue_hint_is_never_inside_the_veto_window(q, workspace_tmp_path):
    hint = datetime(2026, 10, 31, 12, 30, tzinfo=timezone.utc)  # Saturday 18:00 IST
    far = q.enqueue("shinobi", mp4(workspace_tmp_path, "a"), {"title": "a", "kind": "chapter", "publish_at_hint": hint.isoformat()}, now=NOW)
    assert datetime.fromisoformat(far["publish_at"]) == hint and far["kind"] == "chapter"
    soon_hint = NOW + timedelta(hours=1)
    soon = q.enqueue("shinobi", mp4(workspace_tmp_path, "b"), {"title": "b", "kind": "chapter", "publish_at_hint": soon_hint.isoformat()}, now=NOW)
    assert datetime.fromisoformat(soon["publish_at"]) == NOW + timedelta(hours=6)  # veto window wins


def _chapter_json(root, n, ok=True):
    d = root / f"ch{n}"
    d.mkdir(parents=True)
    video = d / "v.mp4"
    video.write_bytes(f"chapter{n}".encode())
    (d / "chapter.json").write_text(json.dumps({
        "status": "ok" if ok else "failed", "kind": "chapter", "title": f"Echoes – Chapter {n}: Dawn", "series": "Echoes", "playlist": "Echoes",
        "chapter": n, "description": "full chapter\n\n0:00 Part 1", "keywords": ["anime story"], "hashtags": ["#anime"],
        "chapters": [{"start": 0, "title": "Part 1"}], "captions_srt": "c.srt", "thumbnail": "t.jpg",
        "video": {"path": str(video), "sha256": f"sha{n}", "duration_secs": 300}, "publish_at_hint": "2026-10-31T12:30:00+00:00"}), encoding="utf-8")


def test_render_chapters_compiles_pending_then_queues_each_once(q, workspace_tmp_path):
    root = workspace_tmp_path / "chapters"
    _chapter_json(root, 1)
    _chapter_json(root, 2, ok=False)
    compiled = []
    out = autopilot.render_chapters(CFG, q, CH, compile_fn=lambda c, n, cfg: compiled.append(n), pending_fn=lambda c: [3], root=root)
    assert compiled == [3] and len(out) == 1
    it = out[0]
    assert it["kind"] == "chapter" and it["chapter"] == 1 and it["playlist"] == "Echoes" and it["series"] == "Echoes"
    assert it["publish_at"].startswith("2026-10-31T12:30") and it["thumbnail"] == "t.jpg" and it["duration_secs"] == 300
    assert autopilot.render_chapters(CFG, q, CH, compile_fn=lambda *a: None, pending_fn=lambda c: [], root=root) == []  # not twice


def test_failed_chapter_compile_does_not_stop_queueing(q, workspace_tmp_path):
    def boom(c, n, cfg):
        raise RuntimeError("ffmpeg failed")

    assert autopilot.render_chapters(CFG, q, CH, compile_fn=boom, pending_fn=lambda c: [1], root=workspace_tmp_path / "none") == []


def test_seo_keeps_chapter_markers_in_titles():
    client = MagicMock()
    client.models.generate_content.return_value.text = json.dumps({"titles": ["Kaelen Faces the Storm: Anime Story of Courage"], "hook": "h"})
    part = seo.build_metadata("t", "Echoes", 3, "recap", client=client, chapter=1)
    assert part["title"].endswith("Ch.1 Part 3")
    whole = seo.build_metadata("t", "Echoes", None, "recap", client=client, chapter=1)
    assert whole["title"].endswith("Chapter 1")
