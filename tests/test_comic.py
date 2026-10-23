import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aurora import comic, research
from aurora.monitoring.media_validator import validate_video

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg missing")


def test_panel_prompt_embeds_character_sheet_verbatim():
    sheet = [{"name": "Ren", "description": "boy, silver hair, blue eyes, black coat"},
             {"name": "Mira", "description": "woman, crimson ponytail, gold armor"}]
    p = research.panel_prompt({"shot": "action", "characters": ["Mira"], "image_prompt": "slashes a sword"}, sheet)
    assert "cel-shaded anime comic panel" in p and "crimson ponytail, gold armor" in p and "silver hair" not in p
    assert "action shot" in p and "slashes a sword" in p


def test_fallback_comic_series_state_continues(workspace_tmp_path):
    d = workspace_tmp_path / "series"
    one = research.research_comic("ch", "a swordsman guards a floating city", cfg={}, series_dir=d)
    assert one["source"] == "topic_fallback" and one["comic"]["series"]["part"] == 1 and len(one["comic"]["panels"]) == 6
    research.save_series("ch", one["comic"], d)
    two = research.research_comic("ch", "ignored", panels=8, cfg={}, series_dir=d)["comic"]
    assert two["series"]["part"] == 2 and two["series"]["title"] == one["comic"]["series"]["title"]
    assert two["character_sheet"] == one["comic"]["character_sheet"] and len(two["panels"]) == 8
    assert json.loads((d / "ch.json").read_text(encoding="utf-8"))["seed"]


def test_research_comic_validates_gemini_json(workspace_tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    good = {"series": {"title": "T", "part": 9, "recap": "r"}, "character_sheet": [{"name": "A", "description": "tall boy, red hair, green eyes"}],
            "panels": [{"image_prompt": "a city", "narration": "It began tonight."}] * 5, "cliffhanger": "c", "cta": "Follow for Part 2"}
    client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text=json.dumps(good))))
    out = research.research_comic("ch", "p", cfg={}, client=client, series_dir=workspace_tmp_path)
    assert out["source"] == "gemini" and out["comic"]["series"]["part"] == 1  # part number is set by code, not the model
    bad = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text="{}")))
    assert research.research_comic("ch", "p", cfg={}, client=bad, series_dir=workspace_tmp_path)["source"] == "topic_fallback"


def test_overlay_png_draws_border_box_and_bubble(workspace_tmp_path):
    p = comic.make_overlay(workspace_tmp_path / "o.png", narration="Then a warrior fell from the sky.", bubble="Give me that crystal!")
    im = Image.open(p)
    assert im.size == (1080, 1920) and im.getpixel((1, 1))[3] == 0 and im.getpixel((20, 960))[3] == 255  # border painted
    assert im.getpixel((540, 1300))[3] > 200  # bubble filled
    assert comic.make_overlay(workspace_tmp_path / "h.png", narration="मंदिर की शाम और शांति", title="शीर्षक").exists()


@needs_ffmpeg
def test_render_comic_durations_and_validation(workspace_tmp_path, monkeypatch):
    c = research.research_comic("ch", "a swordsman guards a floating city", panels=5, cfg={}, series_dir=workspace_tmp_path / "s")["comic"]
    imgs = []
    for i in range(5):
        p = workspace_tmp_path / f"i{i}.png"
        Image.new("RGB", (576, 1024), (30 * i, 80, 140)).save(p)
        imgs.append(p)

    def fake_tts(text, out, lang=None, options=None):
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=1.5", str(wav)], check=True)
        return wav, "fake"

    monkeypatch.setattr(comic, "synthesize_voiceover", fake_tts)
    out = workspace_tmp_path / "comic.mp4"
    info = comic.render_comic(shutil.which("ffmpeg"), c, imgs, out, workspace_tmp_path / "w", music=False)
    n_clips = 5 + 2  # cover + panels + end card
    expected = comic.COVER_S + 5 * max(1.5 + comic.PAD, comic.PANEL_MIN_S) + comic.END_S - comic.XF * (n_clips - 1)
    assert abs(info["seconds"] - expected) < 0.05  # each panel = max(voice + 0.4 s, minimum hold)
    rep = validate_video(out, 1080, 1920, fps=24, duration_secs=expected, require_audio=True, deep=True)
    assert rep.ok, rep.errors


def test_story_finishes_then_new_story_and_end_card_rule(workspace_tmp_path):
    d = workspace_tmp_path / "s"
    p1 = research.research_comic("c", "a swordsman guards a floating city", cfg={}, series_dir=d)["comic"]
    assert p1["series"]["part"] == 1 and p1["series"]["total_parts"] == 2 and "coming soon" in p1["cta"]
    research.save_series("c", p1, d)
    p2 = research.research_comic("c", "x", cfg={}, series_dir=d)["comic"]
    assert p2["series"]["part"] == 2 and p2["cta"] == "The End" and not p2["cliffhanger"]
    research.save_series("c", p2, d)
    p3 = research.research_comic("c", "a new story about a sky pirate", cfg={}, series_dir=d)["comic"]
    assert p3["series"]["part"] == 1  # finished story -> a new one starts


def test_panel_images_use_comfy_reference_then_fall_back_to_nim(workspace_tmp_path, monkeypatch):
    from backend.app.providers import local_comfyui as lc

    spec = research.research_comic("c", "a swordsman", panels=5, cfg={}, series_dir=workspace_tmp_path)["comic"]
    calls, timeouts = [], []

    class FakeProv:
        def __init__(self, cfg):
            pass

        def generate_with_params(self, prompt, style, res, job, negative_prompt=None, seed=None, steps=None, timeout=None):
            calls.append(("ref", seed, steps))
            return SimpleNamespace(output_path=str(workspace_tmp_path / "ref.png"))

        def generate_with_reference(self, ref, prompt, style, res, job, negative_prompt=None, seed=None, steps=None, timeout=None):
            assert "speech bubble" in negative_prompt
            timeouts.append(timeout)
            calls.append(("panel", ref, seed))
            return SimpleNamespace(output_path=str(workspace_tmp_path / f"{job}.png"))

    class Board:
        def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
            assert "speech bubble" in negative_prompt
            calls.append(("nim", seed))
            return SimpleNamespace(output_path=str(workspace_tmp_path / f"{job}.png"))

    monkeypatch.setattr(lc, "LocalComfyUIProvider", FakeProv)
    monkeypatch.setattr(comic, "_comfy_up", lambda cfg: True)
    imgs, timings, note, mode = comic._panel_images({}, Board(), spec, 77, "j", (576, 1024), "auto")
    assert mode == "comfy_ipadapter" and len(imgs) == 5 and len(timings) == 6
    assert calls[0][0] == "ref" and all(c[0] == "panel" and c[2] == 77 for c in calls[1:])
    assert all(c[1] == str(workspace_tmp_path / "ref.png") for c in calls[1:])  # every panel conditioned on one reference
    calls.clear()
    monkeypatch.setattr(comic, "_comfy_up", lambda cfg: False)
    assert all(t and t >= 1800 for t in timeouts)  # per-image timeout = max(floor, 3 x median so far)
    cfg = {"comic": {"max_nim_credits_per_reel": 10}, "cost_dir": str(workspace_tmp_path / "costs")}
    imgs, timings, note, mode = comic._panel_images(cfg, Board(), spec, 77, "j", (576, 1024), "auto")
    assert mode == "nim_fallback" and "consistency may drop" in note and "5 credits" in note and [c[0] for c in calls] == ["nim"] * 5
    from aurora.costs import credits_today

    assert credits_today("nvidia_nim", workspace_tmp_path / "costs") == 5  # fallback is logged, never silent


@needs_ffmpeg
def test_final_encode_is_phone_sized(workspace_tmp_path, monkeypatch):
    c = research.research_comic("ch", "a swordsman", panels=5, cfg={}, series_dir=workspace_tmp_path / "s")["comic"]
    imgs = []
    for i in range(5):
        p = workspace_tmp_path / f"i{i}.png"
        Image.effect_noise((576, 1024), 90).convert("RGB").save(p)  # noisy frames = worst case for bitrate
        imgs.append(p)

    def fake_tts(text, out, lang=None, options=None):
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=1.5", str(wav)], check=True)
        return wav, "fake"

    monkeypatch.setattr(comic, "synthesize_voiceover", fake_tts)
    out = workspace_tmp_path / "c.mp4"
    info = comic.render_comic(shutil.which("ffmpeg"), c, imgs, out, workspace_tmp_path / "w", music=False)
    assert out.stat().st_size * 8 / info["seconds"] <= 10_000_000  # about 10 Mbps overall at most


def test_ipadapter_and_img2img_workflows_get_the_long_cpu_timeout(monkeypatch):
    from src.modules.comfy_orchestrator import ComfyOrchestrator

    seen = []
    orch = ComfyOrchestrator.__new__(ComfyOrchestrator)
    monkeypatch.setattr(ComfyOrchestrator, "_execute", lambda self, wf, ov, timeout: seen.append(timeout) or [])
    for name in ("image_gen.json", "image_gen_ipadapter.json", "img2img.json", "img2img_facelock.json"):
        orch.run_workflow(f"src/workflows/{name}", {})
    orch.run_workflow("src/workflows/svd_video.json", {})
    assert seen == [3600, 3600, 3600, 3600, 900]


def test_explicit_comfy_mode_does_not_fall_back_to_nim(workspace_tmp_path, monkeypatch):
    from backend.app.providers import local_comfyui as lc

    spec = research.research_comic("c", "a swordsman", panels=5, cfg={}, series_dir=workspace_tmp_path)["comic"]

    class Boom:
        def __init__(self, cfg):
            pass

        def generate_with_params(self, *a, **k):
            raise RuntimeError("timed out")

    monkeypatch.setattr(lc, "LocalComfyUIProvider", Boom)
    monkeypatch.setattr(comic, "_comfy_up", lambda cfg: True)
    with pytest.raises(RuntimeError, match="timed out"):
        comic._panel_images({}, SimpleNamespace(generate=lambda *a, **k: pytest.fail("NIM must not be called")), spec, 1, "j", (576, 1024), "comfy")


def test_nim_fallback_is_off_by_default_and_daily_capped(workspace_tmp_path, monkeypatch):
    spec = research.research_comic("c", "a swordsman", panels=5, cfg={}, series_dir=workspace_tmp_path)["comic"]
    board = SimpleNamespace(generate=lambda *a, **k: pytest.fail("NIM must not be called"))
    monkeypatch.setattr(comic, "_comfy_up", lambda cfg: False)
    with pytest.raises(RuntimeError, match="max_nim_credits_per_reel=0"):  # default cap 0 -> explicit failure, no spend
        comic._panel_images({"cost_dir": str(workspace_tmp_path / "c")}, board, spec, 1, "j", (576, 1024), "auto")
    from aurora.costs import log_credits

    log_credits("nvidia_nim", 8, workspace_tmp_path / "c")
    cfg = {"comic": {"max_nim_credits_per_reel": 10}, "providers": {"nvidia_nim": {"max_credits_per_day": 10}},
           "cost_dir": str(workspace_tmp_path / "c")}
    with pytest.raises(RuntimeError, match="daily cap"):  # 8 used + 5 needed > 10
        comic._panel_images(cfg, board, spec, 1, "j", (576, 1024), "auto")
