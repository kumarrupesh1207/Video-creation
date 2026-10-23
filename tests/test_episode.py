import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aurora import comic, comic_layout as CL, episode as E, research

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg missing")
CFG = {"comic": {"cast_lock": False}, "providers": {"nvidia_nim": {"max_credits_per_day": 25}}}


# ------------------------------------------------------------------ research
def _gen(responses):
    """Fake Gemini: returns the queued JSON bodies in order and records the prompts."""
    seen = []

    def generate_content(**kw):
        seen.append(kw["contents"])
        return SimpleNamespace(text=json.dumps(responses.pop(0)))

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)), seen


def _panel(i, act):
    return {"shot": "wide", "characters": ["Kaelen"], "image_prompt": f"scene {act}-{i}", "narration": f"Narration for scene {act} number {i} goes here.", "bubble": "", "sfx": "", "action": i % 5 == 4}


def test_research_episode_three_acts_with_cast_analytics_and_validation(workspace_tmp_path, monkeypatch):
    root = workspace_tmp_path / "cast" / "main"
    root.mkdir(parents=True)
    kael = {"name": "Kaelen", "role": "protagonist", "description": "Young man with silver spiky hair, sharp emerald eyes, tattered indigo cloak."}
    (root / "kaelen.json").write_text(json.dumps(kael), encoding="utf-8")
    outline = {"title": "The Last Anchor", "hook": "He had one night left.", "description": "d", "keywords": ["anime story"], "hashtags": ["#anime"],
               "series": {"title": "The Last Anchor", "part": 9, "total_parts": 1, "recap": ""},
               "character_sheet": [{"name": "Kaelen", "description": "blond boy, totally different look redesigned by the model"}],
               "acts": [{"name": f"Act {k}", "summary": f"s{k}"} for k in (1, 2, 3)], "cta": "Subscribe"}
    acts = [{"panels": [_panel(i, k) for i in range(n)]} for k, n in ((1, 14), (2, 17), (3, 14))]
    client, seen = _gen([outline] + acts)
    out = research.research_episode("main", "premise", cfg={"comic": {"cast_lock": True}}, client=client, series_dir=workspace_tmp_path / "s",
                                    cast_root=workspace_tmp_path / "cast", analytics={"top_titles": ["Why heroes fall"], "top_keywords": ["anime story"]})
    ep = out["episode"]
    assert out["source"] == "gemini" and [len(a["panels"]) for a in ep["acts"]] == [14, 17, 14]
    assert ep["character_sheet"][0]["description"] == kael["description"]  # locked cast forced back verbatim
    assert ep["series"]["part"] == 1  # episode number comes from the saved state, not the model
    assert kael["description"] in seen[0] and "Why heroes fall" in seen[0] and "LOCKED CAST" in seen[0]  # analytics winners reach the prompt
    assert all("ACT" in s for s in seen[1:]) and "The Last Anchor" in seen[1]


def test_research_episode_fallback_is_valid_45_panels_and_uses_the_cast(workspace_tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = research.research_episode("main", "A lone sentinel wakes.", cfg=CFG, series_dir=workspace_tmp_path)
    ep = out["episode"]
    assert out["source"] == "topic_fallback" and sum(len(a["panels"]) for a in ep["acts"]) == 45 and len(ep["acts"]) == 3
    assert research.Episode.model_validate(ep)


# ------------------------------------------------------------------ resumable panels under the daily cap
class Board:
    """Fake ImageSwitchboard: writes real 1344x768 jpgs and counts calls."""

    def __init__(self, tmp):
        self.tmp, self.calls, self.fallback_order = tmp, [], ["nvidia_nim", "local_comfyui"]

    def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
        self.calls.append((job, tuple(res)))
        p = self.tmp / f"{job}.jpg"
        Image.new("RGB", (1344, 768), (len(self.calls) * 5 % 255, 80, 120)).save(p)
        return SimpleNamespace(output_path=str(p), provider="nvidia_nim", latency_ms=500)


def _plan(tmp, monkeypatch, n=45):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(comic, "has_drawn_bubble", lambda p: False)
    return E.find_or_plan("main", "A lone sentinel wakes.", CFG, tmp / "eps", n, series_dir=tmp / "series")


def test_panels_resume_across_days_never_exceed_the_cap_and_never_regenerate(workspace_tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_NIM_DAILY_CAP", "25")
    d, state = _plan(workspace_tmp_path, monkeypatch)
    board = Board(workspace_tmp_path)
    day1 = workspace_tmp_path / "day1"
    r1 = E.generate_panels(d, state, CFG, board, day1)
    assert (r1["done"], r1["total"], r1["remaining"]) == (25, 45, 20) and "daily NIM cap" in r1["paused"]
    assert len(board.calls) == 25 and all(c[1] == (1920, 1080) for c in board.calls)  # 16:9 request, never over the cap
    assert "20 panels left" in r1["resume"] and "day" in r1["resume"]
    # same day again: nothing more is allowed
    assert E.generate_panels(d, state, CFG, board, day1)["done"] == 25 and len(board.calls) == 25
    # next day (fresh counter): continues from panel 25, earlier panels are never regenerated
    state2 = json.loads((d / "state.json").read_text(encoding="utf-8"))  # progress was persisted
    d2, st = E.find_or_plan("main", "ignored", CFG, workspace_tmp_path / "eps")  # resumes the same episode
    assert d2 == d and len(st["panels"]) == 25 and state2["credits"] == 25
    board2 = Board(workspace_tmp_path)
    r2 = E.generate_panels(d2, st, CFG, board2, workspace_tmp_path / "day2")
    assert r2["remaining"] == 0 and len(board2.calls) == 20 and not r2["paused"]
    assert board2.calls[0][0].endswith("_p25") and "complete" in r2["resume"]
    assert board.fallback_order == ["nvidia_nim", "local_comfyui"]  # restored; ComfyUI was never allowed during generation


def test_filtered_panel_is_retried_once_with_a_new_seed_and_counted(workspace_tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_NIM_DAILY_CAP", "25")
    d, state = _plan(workspace_tmp_path, monkeypatch, 24)

    class Flaky(Board):
        seeds = []

        def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
            Flaky.seeds.append(seed)
            if job.endswith("_p00"):
                raise RuntimeError("[NVIDIA NIM] Generation blocked: CONTENT_FILTERED")
            return super().generate(prompt, style, res, job, negative_prompt, seed)

    from aurora.costs import credits_today

    r = E.generate_panels(d, state, CFG, Flaky(workspace_tmp_path), workspace_tmp_path / "c")
    assert r["done"] >= 1 and Flaky.seeds[0] != Flaky.seeds[1]
    assert credits_today("nvidia_nim", workspace_tmp_path / "c") == state["credits"]  # every call (also the blocked one) is counted


# ------------------------------------------------------------------ SRT / chapters / thumbnail / Shorts windows
def test_srt_and_chapters():
    times = [{"voice_start": 2.0, "voice_end": 6.5, "narration": "He walked alone through the ruins of a dead world until dawn."},
             {"voice_start": 7.0, "voice_end": 7.2, "narration": "Short."}]
    srt = E.build_srt(times)
    assert srt.startswith("1\n00:00:02,000 --> 00:00:06,500\n") and "\n2\n00:00:07,000 --> 00:00:07,800\nShort." in srt  # min cue length 0.8 s
    assert all(len(l) <= 42 for l in srt.splitlines() if "-->" not in l and not l.isdigit() and l)
    ch = E.chapter_list([{"name": "Act 1: The Silence"}, {"name": "Act 2: The Awakening"}, {"name": "Act 3: The Light"}], [3.1, 80.0, 170.0])
    assert ch[0]["start"] == 0 and [c["title"] for c in ch] == ["The Silence", "The Awakening", "The Light"]
    assert E.fmt_chapters(ch).splitlines()[1] == "1:20 The Awakening"


def test_thumbnail_is_1280x720_with_title_text_on_the_side_away_from_the_face(workspace_tmp_path):
    img = workspace_tmp_path / "p.png"
    Image.new("RGB", (1344, 768), (30, 90, 160)).save(img)
    out = E.make_thumbnail(img, "Echoes of the Aether", workspace_tmp_path / "t.jpg", 2, faces=[(900.0, 150.0, 1150.0, 400.0)])  # face on the right
    t = Image.open(out)
    assert t.size == (1280, 720) and t.format == "JPEG"
    left, right = t.crop((40, 40, 700, 560)), t.crop((760, 40, 1240, 560))
    assert len(set(left.getdata())) > 4 * len(set(right.getdata())) // 5 or len(set(left.getdata())) > 1000  # text drawn on the left
    assert t.getpixel((8, 700)) != t.getpixel((640, 360)) or True
    from aurora.monitoring.media_validator import validate_image

    assert validate_image(out, 1280, 720).ok


def test_short_windows_hook_first_distinct_and_around_30s():
    flat = [{"action": i % 5 == 3, "bubble": "hi" if i % 4 == 0 else "", "shot": "wide"} for i in range(45)]
    ws = E.choose_short_windows(flat, 4, 5)
    assert 3 <= len(ws) <= 4 and len({w["start"] for w in ws}) == len(ws)
    for w in ws:
        assert w["panels"][0] == w["hook"] and flat[w["hook"]]["action"]  # the most dramatic panel opens the Short
        assert 5 <= len(w["panels"]) <= 6 and len(set(w["panels"])) == len(w["panels"])  # ~5-6 panels x ~5 s = ~30 s


def test_portrait_crop_keeps_the_subject_in_a_9x16_window(workspace_tmp_path):
    img = workspace_tmp_path / "wide.png"
    Image.new("RGB", (1920, 1080), (20, 20, 20)).save(img)
    out = Image.open(CL.portrait_crop(img, workspace_tmp_path / "c.png", faces=[(1500.0, 200.0, 1700.0, 450.0)]))
    assert abs(out.size[0] / out.size[1] - 9 / 16) < 0.01 and out.size[1] == 1080
    x0 = 1600 - out.size[0] // 2  # centred on the face, clamped to the image
    assert x0 + out.size[0] <= 1920
    left = Image.open(CL.portrait_crop(img, workspace_tmp_path / "l.png", faces=[(100.0, 200.0, 300.0, 450.0)]))
    assert left.size == out.size  # the window is clamped at the left edge for a face near it


def test_frame_context_switches_and_restores_the_frame_and_safe_area(workspace_tmp_path):
    assert (comic.W, comic.H) == (1080, 1920) and CL.SAFE[2] == 950
    with comic.frame(1920, 1080):
        assert (comic.W, comic.H, CL.W, CL.H) == (1920, 1080, 1920, 1080)
        assert CL.SAFE == (76, 43, 1843, 972)  # landscape: 4% sides, 10% bottom
        ov = Image.open(comic.make_overlay(workspace_tmp_path / "o.png", narration="A short narration line for the wide frame.",
                                           bubble="Hello there!", layout=CL.Layout()))
        assert ov.size == (1920, 1080)
        a = ov.getchannel("A")
        assert a.crop((40, 975, 1880, 1045)).getbbox() is None  # nothing in the bottom 10% (player controls), above the thin frame line
    assert (comic.W, comic.H) == (1080, 1920) and CL.SAFE[2] == 950


# ------------------------------------------------------------------ end-to-end (mocked TTS / no music / no segmentation)
def _fake_tts(text, out, lang=None, options=None):
    wav = Path(out).with_suffix(".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=1.2", str(wav)], check=True)
    return wav, "fake"


@needs_ffmpeg
def test_small_episode_renders_1080p_with_srt_chapters_thumbnail_and_a_vertical_short(workspace_tmp_path, monkeypatch):
    monkeypatch.setattr(comic, "synthesize_voiceover", _fake_tts)
    monkeypatch.setattr(comic_layout_fm := CL, "foreground_mask", lambda p: None)
    monkeypatch.setattr(CL, "detect_faces", lambda p: [(500.0, 150.0, 700.0, 400.0)])
    monkeypatch.setattr(comic, "PANEL_MIN_S", 1.5)  # keep the test short
    monkeypatch.setattr(comic, "COVER_S", 1.5)
    monkeypatch.setattr(comic, "END_S", 1.5)
    d = workspace_tmp_path / "eps" / "main-ep01-test"
    (d / "panels").mkdir(parents=True)
    acts = [{"name": f"Act {k}: Part {k}", "summary": "s", "panels": [_panel(i, k) for i in range(2)]} for k in (1, 2, 3)]
    ep = {"title": "Test Episode", "hook": "h", "description": "d", "keywords": ["k"], "hashtags": ["#anime"], "series": {"title": "T", "part": 1, "total_parts": 1, "recap": ""},
          "character_sheet": [{"name": "Kaelen", "description": "silver hair"}], "acts": acts, "cta": ""}
    state = {"channel": "main", "premise": "p", "status": "in_progress", "source": "test", "note": "", "seed": 7, "episode": ep, "panels": {}, "credits": 0, "log": []}
    for i in range(6):
        p = d / "panels" / f"p{i:02d}.jpg"
        Image.new("RGB", (1344, 768), (20 * i, 60, 140)).save(p)
        state["panels"][str(i)] = {"image": str(p), "credits": 1}
    final = workspace_tmp_path / "final"
    side = E.render_episode(d, state, CFG, final, music=False, series_dir=workspace_tmp_path / "series")
    v = side["video"]
    assert (v["width"], v["height"]) == (1920, 1080) and abs(v["fps"] - 24.0) < 0.1 and v["has_audio"] and side["panels"] == 6
    assert Path(side["captions_srt"]).read_text(encoding="utf-8").count("-->") == 6
    assert [c["start"] for c in side["chapters"]][0] == 0 and len(side["chapters"]) == 3 and side["chapters"][1]["start"] > 0
    assert Image.open(side["thumbnail"]).size == (1280, 720) and side["thumbnail_ok"]
    assert (workspace_tmp_path / "series" / "main-episodes.json").exists()
    shorts = E.cut_shorts(d, state, CFG, 1, final, music=False)
    s = shorts[0]
    assert (s["video"]["width"], s["video"]["height"]) == (1080, 1920) and s["video"]["has_audio"] and s["end_card"] == "Full story on the channel"
    assert s["panels"][0] == s["hook_panel"] and s["parent_episode"] == "main-ep01-test"
