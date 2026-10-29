"""SEO quality: phrase keywords, no stop-word/franchise tags, one disclosure line, hashtags once, loud fallback. Mocked."""
import json
from unittest.mock import MagicMock

import pytest

from aurora import autopilot
from aurora.publish import seo
from aurora.publish import youtube as yt
from aurora.publish.queue import PublishQueue

NOKEY = {"research": {"api_key_env": "NOPE_NO_SUCH_KEY"}}


def test_keywords_are_phrases_without_stopwords_series_words_or_other_shows():
    texts = ["Best Dark Fantasy Anime Fight 2026", "Spy x Family anime spyxfamily edit", "The Aether of the Echoes", "dark fantasy anime story",
             "cgi animation", "football anime"]
    kws = seo.keywords_from(texts, exclude={"echoes", "the", "aether"}, niche=["anime story", "original anime"])
    assert kws[:2] == ["anime story", "original anime"]
    assert all(" " in k for k in kws) and not {"the", "echoes", "aether", "of"} & set(" ".join(kws).split())
    assert not any("spyxfamily" in k or "cgi" in k or "football" in k for k in kws)  # off-genre / other franchises never become keywords


def test_exactly_three_hashtags_series_first_and_shorts_optional():
    tags = seo.clean_hashtags(["#EchoesOfTheAether", "#Anime", "#anime", "#animestory", "#extra"], True)
    assert tags == ["#EchoesOfTheAether", "#Anime", "#animestory"]
    assert seo.clean_hashtags(["#a", "#b", "#c"], True, shorts_tag=True) == ["#a", "#b", "#c"]  # cap 3 even with #Shorts allowed
    assert seo.clean_hashtags(["#a", "#shorts"], True) == ["#a"]  # #Shorts only when asked for
    assert seo.series_hashtag("Echoes of the Aether") == "#EchoesOfTheAether"


def test_misspellings_from_pack_or_automatic():
    assert seo.misspellings("Echoes of the Aether", ["Echos of the Aether"]) == ["echos of the aether"]
    auto = seo.misspellings("Echoes of the Aether")
    assert auto and "echoes of the aether" not in auto and all(a == a.lower() for a in auto)


def test_template_follows_youtube_guidelines_when_gemini_and_search_are_down(capsys, workspace_tmp_path):
    m = seo.build_metadata("t", "Echoes of the Aether", 2, "Kaelen enters the sky city.",
                           hashtags=["#anime", "#animestory"], fallback_title="Echoes of the Aether – Ch.1 Part 2: x",
                           service=None, cfg=NOKEY, duration_secs=30, chapter=1, beat="Kaelen finds a pulsating obsidian sphere.",
                           events=["The shadows form a claw.", "It reaches for Kaelen.", "He runs."], caption="He runs. Part 3 tomorrow",
                           parts=10, playlist="Echoes of the Aether", hook="Kaelen awakens the ruin", cache_dir=workspace_tmp_path,
                           main_keyword="dark fantasy anime", series_hashtag_="#EchoesOfTheAether",
                           misspell=["echos of the aether", "echoes of aether"])
    d = m["description"]
    assert d.split("\n\n")[0].endswith("👇")  # line 1 = engagement question (generic fallback here: none supplied)
    first_two = " ".join(d.split("\n\n")[1].splitlines())
    assert first_two.startswith("Kaelen awakens the ruin") and "dark fantasy anime" in first_two.lower()  # hook + main keyword up top
    assert "Kaelen finds a pulsating obsidian sphere." in d and "The shadows form a claw." in d  # unique body from THIS part's events
    assert "Watch Part 3 next" in d and "playlist" in d
    for banned in ("Perfect if you like", "Keywords:", "Follow for", "Part 3 tomorrow"):
        assert banned not in d
    assert d.count("AI-generated") == 1
    assert d.rstrip().endswith("#EchoesOfTheAether #anime #animestory") and sum(1 for w in d.split() if w.startswith("#")) == 3
    assert 5 <= len(m["tags"]) <= 10 and "echos of the aether" in m["tags"] and "echoes of the aether" in m["tags"]
    assert not {"the", "aether", "echoes", "of"} & set(m["tags"])
    assert m["source"] == "none" and any("Gemini" in w for w in m["warnings"])


def test_competitor_failure_is_logged_loudly_not_silent(capsys, workspace_tmp_path):
    svc = MagicMock()
    svc.search.return_value.list.return_value.execute.side_effect = RuntimeError("quota")
    m = seo.build_metadata("t", "S", 1, "r", service=svc, cfg=NOKEY, cache_dir=workspace_tmp_path)
    assert "[seo] !!! competitor research failed" in capsys.readouterr().err
    assert any("competitor" in w for w in m["warnings"]) and m["tags"]  # curated niche keywords still produce tags


def test_competitor_results_are_cached_for_a_day(workspace_tmp_path):
    svc = MagicMock()
    svc.search.return_value.list.return_value.execute.return_value = {"items": [{"id": {"videoId": "v"}, "snippet": {"title": "Dark Fantasy Anime"}}]}
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"snippet": {"tags": ["anime fight"]}}]}
    seo.competitor_research(svc, "anime story shorts", cache_dir=workspace_tmp_path)
    seo.competitor_research(svc, "anime story shorts", cache_dir=workspace_tmp_path)
    assert svc.search.return_value.list.call_count == 1


def test_build_body_fields_and_hashtag_rule():
    b = yt.build_body({"title": "T", "description": "d", "duration_secs": 30})
    assert "#Shorts" in b["snippet"]["description"]  # only when the text has no hashtags at all
    assert "#Shorts" not in yt.build_body({"title": "T", "description": "d #a #b #c", "duration_secs": 30})["snippet"]["description"]
    assert "#Shorts" not in yt.build_body({"title": "T", "description": "d", "kind": "chapter", "duration_secs": 300})["snippet"]["description"]
    assert b["snippet"]["categoryId"] == "1" and b["snippet"]["defaultLanguage"] == "en" and b["snippet"]["defaultAudioLanguage"] == "en"
    assert b["status"]["containsSyntheticMedia"] is True


def test_playlist_that_cannot_be_made_public_does_not_block_and_is_flagged(workspace_tmp_path, capsys):
    svc = MagicMock()
    svc.playlists.return_value.update.return_value.execute.side_effect = RuntimeError("forbidden")
    (workspace_tmp_path / "shinobi_playlists.json").write_text(json.dumps({"Echoes": {"id": "PL", "privacy": "private"}}), encoding="utf-8")
    assert yt.ensure_playlist(svc, "shinobi", "Echoes", "public", workspace_tmp_path) == "PL"  # video still gets added
    assert "could not be made public" in capsys.readouterr().err
    assert "still private" in yt.playlist_privacy_problem("shinobi", "Echoes", workspace_tmp_path)


def test_reseo_regenerates_from_sidecar_and_refuses_uploaded(workspace_tmp_path):
    cfg = {"autopilot": {"publish": {}, "channels": [{"name": "shinobi", "platforms": ["youtube"]}]}}
    q = PublishQueue(workspace_tmp_path / "q.json", cfg)
    mp4 = workspace_tmp_path / "p.mp4"
    mp4.write_bytes(b"x")
    mp4.with_suffix(".json").write_text(json.dumps({
        "title": "Echoes of the Aether – Ch.1 Part 1: Awakening", "part": "1", "caption": "A guardian turns on Kaelen. Part 2 tomorrow",
        "hook": "Awakening", "playlist": "Echoes of the Aether", "story": "echoes-of-the-aether",
        "chapter": {"number": 1, "parts": 10, "beat": {"summary": "Kaelen crosses the ruins."}, "series_title": "Echoes of the Aether"},
        "series": {"title": "Echoes of the Aether", "recap": "Kaelen enters the sky city."}}), encoding="utf-8")
    it = q.enqueue("shinobi", mp4, {"title": "old", "description": "Keywords: the", "tags": ["the"], "series": "Echoes of the Aether", "part": 1,
                                    "duration_secs": 29}, now=None)
    q.veto(it["id"])
    out = autopilot.reseo_item(q, it["id"], cfg, service_factory=lambda c: None, client=None, apply=False)
    assert q.get(it["id"])["description"] == "Keywords: the" and "Watch Part 2 next" in out["after"]["description"]
    autopilot.reseo_item(q, it["id"], cfg, service_factory=lambda c: None)
    got = q.get(it["id"])
    assert "Keywords:" not in got["description"] and "the" not in got["tags"] and 8 <= len(got["tags"]) <= 15
    q._update(it["id"], status="uploaded_scheduled")
    with pytest.raises(ValueError):
        autopilot.reseo_item(q, it["id"], cfg)


# ---- update-metadata: new SEO on a video that is already on YouTube (snippet only) ------------------
def _uploaded_item(workspace_tmp_path, status="uploaded_scheduled"):
    cfg = {"autopilot": {"publish": {}, "channels": [{"name": "shinobi", "platforms": ["youtube"], "expected_handle": "@ShinobiMindset1"}]}}
    q = PublishQueue(workspace_tmp_path / "q.json", cfg)
    mp4 = workspace_tmp_path / "p.mp4"
    mp4.write_bytes(b"x")
    mp4.with_suffix(".json").write_text(json.dumps({
        "title": "Echoes of the Aether – Ch.1 Part 2: Awakening", "part": "2", "caption": "A claw forms. Part 3 tomorrow", "hook": "Awakening",
        "playlist": "Echoes of the Aether", "story": "echoes-of-the-aether", "panels": [{"narration": "A claw rises from the sphere."}],
        "chapter": {"number": 1, "parts": 10, "beat": {"summary": "Kaelen finds a sphere."}, "series_title": "Echoes of the Aether"},
        "series": {"title": "Echoes of the Aether", "recap": "Kaelen enters the sky city."}}), encoding="utf-8")
    it = q.enqueue("shinobi", mp4, {"title": "Echoes of the Aether – Ch.1 Part 2: Awakening", "description": "old", "tags": ["the"], "duration_secs": 30}, now=None)
    q._update(it["id"], status=status, video_id="VID2")
    return cfg, q, it


def _live_service():
    svc = MagicMock()
    svc.videos.return_value.list.return_value.execute.return_value = {"items": [{"snippet": {
        "title": "Old title", "description": "old", "tags": ["the"], "categoryId": "22", "defaultLanguage": "en-US", "channelId": "UC", "publishedAt": "x"}}]}
    return svc


def test_update_metadata_sends_snippet_only_and_never_status(workspace_tmp_path):
    cfg, q, it = _uploaded_item(workspace_tmp_path)
    svc = _live_service()
    out = autopilot.update_metadata(q, it["id"], cfg, confirm=True, service_factory=lambda c: svc)
    call = svc.videos.return_value.update.call_args.kwargs
    assert call["part"] == "snippet" and "status" not in call["body"] and call["body"]["id"] == "VID2"  # publishAt/privacy untouched
    sn = call["body"]["snippet"]
    assert sn["categoryId"] == "1" and sn["defaultLanguage"] == "en" and sn["defaultAudioLanguage"] == "en"
    assert sn["title"] == "Awakening | Echoes of the Aether Pt 2" and sn["description"].rstrip().endswith("#EchoesOfTheAether #anime #animestory") and 5 <= len(sn["tags"]) <= 10
    assert "channelId" not in sn and "publishedAt" not in sn  # read-only fields are not sent back
    got = q.get(it["id"])
    assert got["description"] == sn["description"] and got["tags"] == sn["tags"] and got["metadata_updated_at"] and got["status"] == "uploaded_scheduled"
    assert out["after"]["title"] == "Awakening | Echoes of the Aether Pt 2"


def test_update_metadata_needs_confirm_and_dry_run_touches_nothing(workspace_tmp_path):
    cfg, q, it = _uploaded_item(workspace_tmp_path)
    svc = _live_service()
    with pytest.raises(PermissionError):
        autopilot.update_metadata(q, it["id"], cfg, confirm=False, service_factory=lambda c: svc)
    preview = autopilot.update_metadata(q, it["id"], cfg, dry_run=True, service_factory=lambda c: svc)
    assert "Watch Part 3 next" in preview["after"]["description"]
    svc.videos.return_value.update.assert_not_called()
    assert q.get(it["id"])["description"] == "old"


def test_update_metadata_refuses_items_not_on_youtube(workspace_tmp_path):
    cfg, q, it = _uploaded_item(workspace_tmp_path, status="vetoed")
    with pytest.raises(ValueError):
        autopilot.update_metadata(q, it["id"], cfg, confirm=True, service_factory=lambda c: _live_service())


def test_update_video_snippet_keeps_unrelated_fields():
    svc = _live_service()
    sent = yt.update_video_snippet(svc, "V", {"title": "New", "tags": None})
    assert sent["title"] == "New" and sent["tags"] == ["the"] and sent["description"] == "old"  # None = keep what is there
