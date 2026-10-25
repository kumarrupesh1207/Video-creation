import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aurora import chapter, comic, research

IST = timezone(timedelta(hours=5, minutes=30))


class FakeClient:
    """Mock Gemini: returns a 10-beat outline for the outline prompt and a valid Comic for a part prompt; records the prompts."""

    def __init__(self, parts=10):
        self.parts, self.prompts = parts, []

    def generate(self, prompt, schema):
        self.prompts.append(prompt)
        if schema is research.ChapterOutline:
            return research.ChapterOutline(series_title="Echoes of the Aether", chapter_title="Chapter 1: Awakening", summary="Kaelen wakes the relic.",
                                           beats=[research.Beat(role="setup" if i < 2 else "rising stakes", summary=f"BEAT{i + 1} happens") for i in range(self.parts)])
        n = 6
        return research.Comic.model_validate({
            "series": {"title": "X", "part": 1, "total_parts": 1, "recap": "recap line"}, "hook": "The Relic Wakes.", "cta": "x",
            "character_sheet": [{"name": "Kaelen", "description": "silver hair, blue cloak"}],
            "panels": [{"shot": "medium", "characters": ["Kaelen"], "image_prompt": f"scene {i}", "narration": f"Panel {i} narration words here"} for i in range(n)]})


@pytest.fixture(autouse=True)
def fake_call_json(monkeypatch):
    monkeypatch.setattr(research, "_call_json", lambda client, model, prompt, schema: client.generate(prompt, schema))


def _cfg(parts=10):
    return {"comic": {"chapter_parts": parts, "cast_lock": False}, "research": {}}


def test_chapter_parts_minimum_is_ten():
    assert research.chapter_parts({"comic": {"chapter_parts": 4}}) == 10
    assert research.chapter_parts({"comic": {"chapter_parts": 12}}) == 12
    assert research.chapter_parts({}) == 10


def test_titles_and_end_cards():
    assert research.part_title("Echoes of the Aether", 1, 3, "The Relic Wakes.") == "Echoes of the Aether – Ch.1 Part 3: The Relic Wakes"
    assert research.part_title("S", 2, 1) == "S – Ch.2 Part 1"
    assert research.end_card_for(1, 3, 10) == ("To be continued...", "Part 4 tomorrow")
    assert research.end_card_for(2, 10, 10) == ("Chapter 2 finale", "The next chapter is coming")


def test_outline_first_then_each_part_uses_its_beat_and_the_recaps(workspace_tmp_path):
    client, sd = FakeClient(), workspace_tmp_path / "s"
    r1 = research.research_part("c", "premise", cfg=_cfg(), client=client, series_dir=sd)
    assert r1["chapter"]["number"] == 1 and r1["chapter"]["part"] == 1 and r1["chapter"]["parts"] == 10 and not r1["chapter"]["final"]
    assert "BEAT1 happens" in client.prompts[1] and len(client.prompts) == 2  # outline prompt, then part prompt
    assert r1["comic"]["series"]["part"] == 1 and r1["comic"]["series"]["total_parts"] == 10 and r1["comic"]["cta"] == "Part 2 tomorrow"
    state = research.load_series("c", sd)
    assert len(state["chapter"]["outline"]) == 10 and state["chapter"]["parts_done"] == 0  # outline persisted, nothing advanced yet
    research.record_part("c", r1["comic"], r1["chapter"], {"part": 1, "video": "v1", "sidecar": "s1"}, sd)
    r2 = research.research_part("c", "premise", cfg=_cfg(), client=client, series_dir=sd)
    assert r2["chapter"]["part"] == 2 and len(client.prompts) == 3  # no new outline
    assert "BEAT2 happens" in client.prompts[2] and "recap line" in client.prompts[2]


def test_failed_part_does_not_advance_and_chapter_rolls_over(workspace_tmp_path):
    client, sd = FakeClient(), workspace_tmp_path / "s"
    for m in range(1, 11):
        r = research.research_part("c", "p", cfg=_cfg(), client=client, series_dir=sd)
        assert r["chapter"]["part"] == m and r["chapter"]["number"] == 1
        if m == 5:  # a failed render never calls record_part: the same part comes back
            again = research.research_part("c", "p", cfg=_cfg(), client=client, series_dir=sd)
            assert again["chapter"]["part"] == 5
        research.record_part("c", r["comic"], r["chapter"], {"part": m, "video": f"v{m}", "sidecar": f"s{m}"}, sd)
    assert r["chapter"]["final"] and r["comic"]["cta"] == "Chapter 1 finale"
    state = research.load_series("c", sd)
    assert state["chapters_done"] == 1 and state["chapters"]["1"]["complete"]
    nxt = research.research_part("c", "p", cfg=_cfg(), client=client, series_dir=sd)
    assert nxt["chapter"]["number"] == 2 and nxt["chapter"]["part"] == 1  # Chapter 2 starts with its own outline
    assert "Ch.1" in client.prompts[-2]  # earlier chapters are passed to the new outline


def test_offline_fallback_still_gives_a_full_chapter_plan(workspace_tmp_path):
    r = research.research_part("c", "a swordsman", cfg=_cfg(), series_dir=workspace_tmp_path / "s")
    assert r["source"] == "topic_fallback" and r["chapter"]["parts"] == 10
    assert len(research.load_series("c", workspace_tmp_path / "s")["chapter"]["outline"]) == 10


def test_reset_for_chapters_backs_up_pilots_and_keeps_cast(workspace_tmp_path):
    sd = workspace_tmp_path / "s"
    sd.mkdir()
    (sd / "c.json").write_text(json.dumps({"title": "Old", "part": 2, "last_summary": "old lore", "characters": [{"name": "Kaelen", "look": "x"}], "seed": 5}), encoding="utf-8")
    st = research.reset_for_chapters("c", series_dir=sd)
    assert st["part"] == 0 and st["chapter"] is None and st["characters"][0]["name"] == "Kaelen" and st["background"] == "old lore" and st["seed"] == 5
    assert json.loads((sd / "c.pilot-backup.json").read_text(encoding="utf-8"))["part"] == 2
    research.reset_for_chapters("c", series_dir=sd)  # the backup is never overwritten
    assert json.loads((sd / "c.pilot-backup.json").read_text(encoding="utf-8"))["part"] == 2


def test_saturday_slot_math():
    sat = datetime(2026, 10, 24, 9, 0, tzinfo=IST)  # a Saturday
    assert sat.weekday() == 5
    assert chapter.next_saturday_1800(sat) == datetime(2026, 10, 24, 18, 0, tzinfo=IST)  # same day, 9 h ahead
    assert chapter.next_saturday_1800(datetime(2026, 10, 24, 14, 0, tzinfo=IST)) == datetime(2026, 10, 31, 18, 0, tzinfo=IST)  # < 6 h lead -> next week
    assert chapter.next_saturday_1800(datetime(2026, 10, 25, 10, 0, tzinfo=IST)) == datetime(2026, 10, 31, 18, 0, tzinfo=IST)
    assert chapter.next_saturday_1800(datetime(2026, 10, 25, 10, 0)).hour == 18  # naive = IST
    assert chapter.next_saturday_1800(datetime(2026, 10, 24, 6, 0, tzinfo=timezone.utc)) == datetime(2026, 10, 24, 18, 0, tzinfo=IST)  # 11:30 IST


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def _fake_part(tmp, m):
    """A tiny stand-in for a rendered Short: 1080x1920, 24 fps, stereo audio, 8 s; story window 2.0 s .. 6.0 s."""
    vid = tmp / f"p{m}.mp4"
    _ff("-f", "lavfi", "-i", "testsrc=s=1080x1920:r=24:d=8", "-f", "lavfi", "-i", "sine=f=330:d=8", "-ar", "44100", "-ac", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(vid))
    imgs = []
    for i in range(2):
        p = tmp / f"p{m}_{i}.png"
        Image.effect_noise((576, 1024), 60).convert("RGB").save(p)
        imgs.append(str(p))
    side = {"hook": f"Hook {m}", "panels": [{"shot": "medium"}, {"action": True}], "panel_images": imgs, "cover_s": 2.4,
            "panel_times": [{"i": 0, "start": 2.0, "voice_start": 2.05, "voice_end": 3.5, "end": 4.0, "narration": f"narration {m} a"},
                            {"i": 1, "start": 4.0, "voice_start": 4.05, "voice_end": 5.5, "end": 6.0, "narration": f"narration {m} b"}]}
    sc = tmp / f"p{m}.json"
    sc.write_text(json.dumps(side), encoding="utf-8")
    return {"part": m, "video": str(vid), "sidecar": str(sc), "title": f"t{m}"}


def test_compile_chapter_builds_one_16x9_video_without_new_images(workspace_tmp_path, monkeypatch):
    tmp = workspace_tmp_path
    sd = tmp / "s"
    rendered = [_fake_part(tmp, m) for m in (1, 2, 3)]
    ch = {"number": 1, "title": "Chapter 1: Awakening", "summary": "Kaelen wakes the relic.", "parts": 3, "parts_done": 3, "complete": True,
          "rendered": rendered, "outline": [], "recaps": []}
    research._write_state("c", {"series_title": "Echoes of the Aether", "chapter": ch, "chapters": {"1": ch}, "chapters_done": 1}, sd)
    from backend.app.services import image_switchboard

    monkeypatch.setattr(image_switchboard.ImageSwitchboard, "generate", lambda *a, **k: pytest.fail("compilation must not generate images"))
    now = datetime(2026, 10, 25, 10, 0, tzinfo=IST)
    assert chapter.pending("c", sd) == [1]
    side = chapter.compile_chapter("c", 1, {}, series_dir=sd, out_root=tmp / "final", now=now)
    v = side["video"]
    assert (v["width"], v["height"], v["has_audio"]) == (1920, 1080, True)
    # 3 s open card + 3 x (6.0 - 2.0) s story + 4 s finale ~= 19 s
    assert 18.2 < v["duration_secs"] < 19.8
    assert side["title"] == "Echoes of the Aether – Chapter 1: Awakening" and side["playlist"] == "Echoes of the Aether" and side["nim_credits"] == 0
    assert [c["title"] for c in side["chapters"]] == ["Part 1: Hook 1", "Part 2: Hook 2", "Part 3: Hook 3"] and side["chapters"][0]["start"] == 0.0
    assert side["chapters"][1]["start"] == pytest.approx(3.0 + 4.0, abs=0.05)
    assert side["publish_at_hint"].startswith("2026-10-31T18:00") and "+05:30" in side["publish_at_hint"]
    srt = Path(side["captions_srt"]).read_text(encoding="utf-8")
    assert "narration 1 a" in srt and "narration 3 b" in srt and "00:00:03," in srt  # first cue after the 3 s open card
    assert Path(side["thumbnail"]).exists() and side["thumbnail_ok"]
    assert chapter.pending("c", sd) == []  # the compilation is recorded in the series state
    again = chapter.compile_chapter("c", 1, {}, series_dir=sd, out_root=tmp / "final")
    assert again["video"]["sha256"] == v["sha256"]  # idempotent: no second render


def test_compile_refuses_an_incomplete_chapter(workspace_tmp_path):
    sd = workspace_tmp_path / "s"
    research._write_state("c", {"chapter": {"number": 1, "parts": 10, "parts_done": 4, "complete": False, "rendered": []}}, sd)
    with pytest.raises(RuntimeError, match="not complete"):
        chapter.compile_chapter("c", 1, {}, series_dir=sd, out_root=workspace_tmp_path / "f")


def test_make_comic_reel_chapter_mode(workspace_tmp_path, monkeypatch):
    from aurora import comic_layout

    tmp = workspace_tmp_path

    class Board:
        n = 0

        def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
            self.n += 1
            p = tmp / f"{job}_{self.n}.jpg"
            Image.effect_noise((576, 1024), 50).convert("RGB").save(p)
            return SimpleNamespace(output_path=str(p), provider="nvidia_nim", latency_ms=1)

    def fake_tts(text, out, lang=None, options=None):
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=1.0", str(wav)], check=True)
        return wav, "fake"

    monkeypatch.setattr(comic, "synthesize_voiceover", fake_tts)
    monkeypatch.setattr(comic_layout, "foreground_mask", lambda p: None)
    for name, val in (("PANEL_MIN_S", 1.5), ("COVER_S", 1.5), ("END_S", 1.5)):
        monkeypatch.setattr(comic, name, val)
    cfg = {"comic": {"chapter_parts": 10, "cast_lock": False, "image_mode": "nim"}, "research": {}, "cost_dir": str(tmp / "costs")}
    res = comic.make_comic_reel("c", "a swordsman", tmp / "out", cfg=cfg, panels=5, switchboard=Board(), music=False, series_dir=tmp / "ser")
    assert res["chapter"]["number"] == 1 and res["chapter"]["part"] == 1
    assert res["title"].startswith("Echoes of the Aether") is False or "Ch.1 Part 1" in res["title"]
    assert "Ch.1 Part 1" in res["title"]
    side = json.loads(Path(res["sidecar"]).read_text(encoding="utf-8"))
    assert side["chapter"]["parts"] == 10 and side["playlist"] and len(side["panel_times"]) == 5 and len(side["panel_images"]) == 5
    state = research.load_series("c", tmp / "ser")
    assert state["chapter"]["parts_done"] == 1 and state["chapter"]["rendered"][0]["sidecar"] == res["sidecar"]


# ---------------------------------------------------------------- the series title is content: research may never rename it
class RenamingClient(FakeClient):
    def generate(self, prompt, schema):
        got = super().generate(prompt, schema)
        if schema is research.ChapterOutline:
            got.series_title = "Chronicles of the Aether Blade"
        else:
            got.series.title = "Chronicles of the Aether Blade"
        return got


def test_research_cannot_rename_a_series(workspace_tmp_path):
    sd = workspace_tmp_path / "s"
    r = research.research_part("shinobi", "p", cfg=_cfg(), client=RenamingClient(), series_dir=sd)  # channel shinobi -> pack "Echoes of the Aether"
    assert r["comic"]["series"]["title"] == "Echoes of the Aether" and research.load_series("shinobi", sd)["series_title"] == "Echoes of the Aether"
    research._write_state("shinobi", {**research.load_series("shinobi", sd), "series_title": "Chronicles of the Aether Blade", "title": "Chronicles of the Aether Blade"}, sd)
    r2 = research.research_part("shinobi", "p", cfg=_cfg(), client=RenamingClient(), series_dir=sd)  # a wrong stored title is corrected too
    assert r2["comic"]["series"]["title"] == "Echoes of the Aether" and research.load_series("shinobi", sd)["title"] == "Echoes of the Aether"


def test_part_title_has_one_marker_and_a_short_hook():
    t = research.part_title("Echoes of the Aether", 1, 2, "The Guardian Locks Onto Kaelen In The Ruined Sky City and more")
    assert t.count("Part") == 1 and t.startswith("Echoes of the Aether – Ch.1 Part 2: ") and len(t.split(": ", 1)[1]) <= 40 and len(t) <= 100
    assert not t.rstrip().endswith((" In", " The", " the", " of"))
    assert research.part_title("S", 1, 1, "") == "S – Ch.1 Part 1"


def test_seo_never_rewrites_a_chapter_part_title(monkeypatch):
    from aurora import autopilot
    from aurora.publish import seo

    monkeypatch.setattr(seo, "build_metadata", lambda *a, **k: {"title": "Clickbait Pt. 1", "description": "d", "tags": ["x"]})
    meta = {"title": "Echoes of the Aether – Ch.1 Part 1: Hook", "tags": [], "description": ""}
    fail = lambda c: (_ for _ in ()).throw(RuntimeError("no service"))  # noqa: E731
    out = autopilot.apply_seo({}, {"name": "c"}, {"chapter": 1, "part": 1, "topic": "t"}, meta, fail)
    assert out["title"] == meta["title"] and out["description"] == "d"
    out = autopilot.apply_seo({}, {"name": "c"}, {"part": 1, "topic": "t"}, meta, fail)  # plain series parts keep the SEO title
    assert out["title"] == "Clickbait Pt. 1"


def test_rerender_from_cached_panels_uses_no_images_and_does_not_advance_the_chapter(workspace_tmp_path, monkeypatch):
    from aurora import comic_layout

    tmp = workspace_tmp_path
    imgs = []
    for i in range(5):
        p = tmp / f"c{i}.png"
        Image.effect_noise((576, 1024), 50).convert("RGB").save(p)
        imgs.append(str(p))

    def tts(text, out, lang=None, options=None):
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=0.6", str(wav)], check=True)
        return wav, "fake"

    monkeypatch.setattr(comic, "synthesize_voiceover", tts)
    monkeypatch.setattr(comic_layout, "foreground_mask", lambda p: None)
    for name, val in (("PANEL_MIN_S", 1.2), ("COVER_S", 1.2), ("END_S", 1.2)):
        monkeypatch.setattr(comic, name, val)
    spec = research.research_comic("c", "a swordsman", panels=5, cfg={}, series_dir=tmp / "s0")["comic"]
    spec["series"].update(title="Echoes of the Aether", part=1, total_parts=10)
    info = {"number": 1, "title": "T", "part": 1, "parts": 10, "beat": {}, "series_title": "Echoes of the Aether", "final": False}
    sd = tmp / "ser"
    research._write_state("c", {"chapter": {"number": 1, "parts_done": 1, "parts": 10, "rendered": [], "recaps": [], "complete": False}}, sd)
    res = comic.make_comic_reel("c", "", tmp / "out", cfg={"comic": {}}, comic=spec, chapter=info, images=imgs, switchboard=object(), music=False,
                                series_dir=sd, record=False)
    side = json.loads(Path(res["sidecar"]).read_text(encoding="utf-8"))
    assert side["image_mode"] == "cached" and res["title"].startswith("Echoes of the Aether – Ch.1 Part 1")
    assert research.load_series("c", sd)["chapter"]["parts_done"] == 1  # unchanged
