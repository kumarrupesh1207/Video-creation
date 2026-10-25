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


def test_hashtags_deduped_and_limited():
    tags = seo.clean_hashtags(["#anime", "#Anime", "#shorts", "#animeshorts", "#a", "#b", "#c", "#d"], True)
    assert tags[0] == "#Shorts" and len(tags) == 5 and len({t.lower() for t in tags}) == 5
    assert "#Shorts" not in seo.clean_hashtags(["#anime", "#shorts"], False)


def test_template_description_when_gemini_and_search_are_down(capsys, workspace_tmp_path):
    m = seo.build_metadata("t", "Echoes of the Aether", 2, "Kaelen discovers an obsidian sphere in a ruined altar.",
                           hashtags=["#anime", "#animeshorts", "#shorts", "#anime"], fallback_title="Echoes of the Aether – Ch.1 Part 2: x",
                           service=None, cfg=NOKEY, duration_secs=30, chapter=1, beat="He locates a pulsating sphere.", caption="The shadows form a claw. Part 3 tomorrow",
                           parts=10, playlist="Echoes of the Aether", hook="Kaelen awakens the ruin", cache_dir=workspace_tmp_path)
    d = m["description"]
    assert d.startswith("Kaelen awakens the ruin") and "Watch Part 3 next" in d and 'playlist' in d and "Follow for the next part" in d
    assert d.count("AI-generated") == 1 and d.lower().count("#anime ") + d.lower().count("#anime\n") <= 1 and d.count("#Shorts") == 1
    assert "Keywords:" not in d and "Part 3 tomorrow" not in d  # end-card teaser is not description text
    assert sum(1 for w in d.split() if w.startswith("#")) <= 5
    assert 8 <= len(m["tags"]) <= 15 and not {"the", "aether", "echoes", "of"} & set(m["tags"])
    assert m["source"] == "none" and any("Gemini" in w for w in m["warnings"])
    assert "[seo]" in capsys.readouterr().err or m["warnings"]


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


def test_build_body_adds_shorts_hashtag_only_to_shorts():
    assert "#Shorts" in yt.build_body({"title": "T", "description": "d", "duration_secs": 30})["snippet"]["description"]
    assert "#Shorts" not in yt.build_body({"title": "T", "description": "d", "kind": "chapter", "duration_secs": 300})["snippet"]["description"]


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
