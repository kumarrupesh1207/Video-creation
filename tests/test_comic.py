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
    assert "cel-shaded anime" in p and "comic panel" not in p and "crimson ponytail, gold armor" in p and "silver hair" not in p
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



class _Prov:
    """Fake LocalComfyUIProvider recording the reference + weight every panel is conditioned on."""

    calls = []

    def __init__(self, cfg):
        pass

    def generate_with_params(self, prompt, style, res, job, negative_prompt=None, seed=None, steps=None, timeout=None):
        _Prov.calls.append(("ref", seed, timeout))
        return SimpleNamespace(output_path=f"{job}.png")

    def generate_with_reference(self, ref, prompt, style, res, job, negative_prompt=None, seed=None, steps=None, weight=None, timeout=None):
        assert "speech bubble" in negative_prompt
        _Prov.calls.append(("panel", ref, weight, timeout))
        return SimpleNamespace(output_path=f"{job}.png")


class _Board:
    def __init__(self, tmp, fail_at=None):
        self.tmp, self.fail_at, self.n, self.fallback_order = tmp, fail_at, 0, ["nvidia_nim", "local_comfyui"]
        self.orders = []

    def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
        assert "speech bubble" in negative_prompt and "no text" in prompt
        self.orders.append(list(self.fallback_order))
        if self.fail_at is not None and self.n == self.fail_at:
            raise RuntimeError("HTTP 402: insufficient credits")
        self.n += 1
        return SimpleNamespace(output_path=str(self.tmp / f"{job}.jpg"), provider="nvidia_nim", latency_ms=900)


def _spec(tmp, panels=5):
    return research.research_comic("c", "a swordsman", panels=panels, cfg={}, series_dir=tmp)["comic"]


def test_nim_is_the_default_image_source_and_credits_are_logged(workspace_tmp_path):
    from aurora.costs import credits_today

    board = _Board(workspace_tmp_path)
    cfg = {"cost_dir": str(workspace_tmp_path / "costs")}
    imgs, timings, note, mode = comic._panel_images(cfg, board, _spec(workspace_tmp_path), 7, "j", (576, 1024))
    assert mode == "nim" and len(imgs) == 5 and all(o == ["nvidia_nim"] for o in board.orders)  # NIM only, no silent ComfyUI
    assert board.fallback_order == ["nvidia_nim", "local_comfyui"]  # restored afterwards
    assert credits_today("nvidia_nim", workspace_tmp_path / "costs") == 5


def test_nim_failure_continues_on_comfy_ip_adapter_at_low_weight(workspace_tmp_path, monkeypatch, capsys):
    from backend.app.providers import local_comfyui as lc

    _Prov.calls = []
    monkeypatch.setattr(lc, "LocalComfyUIProvider", _Prov)
    board = _Board(workspace_tmp_path, fail_at=2)  # quota runs out on the 3rd panel
    cfg = {"cost_dir": str(workspace_tmp_path / "costs"), "providers": {"local_comfyui": {"reference_weight": 0.4}}}
    imgs, timings, note, mode = comic._panel_images(cfg, board, _spec(workspace_tmp_path), 7, "j", (576, 1024))
    assert mode == "nim+comfy_fallback" and len(imgs) == 5 and "NIM failed after 2/5" in note and "insufficient credits" in note
    panels = [c for c in _Prov.calls if c[0] == "panel"]
    assert len(panels) == 3 and all(str(c[1]) == str(workspace_tmp_path / "j_p0.jpg") and c[2] == 0.4 for c in panels)  # 1st NIM panel = reference
    assert not [c for c in _Prov.calls if c[0] == "ref"]
    assert "FALLBACK to ComfyUI" in capsys.readouterr().out  # never silent


def test_daily_cap_sends_remaining_panels_to_comfy(workspace_tmp_path, monkeypatch):
    from aurora.costs import log_credits
    from backend.app.providers import local_comfyui as lc

    _Prov.calls = []
    monkeypatch.setattr(lc, "LocalComfyUIProvider", _Prov)
    log_credits("nvidia_nim", 10, workspace_tmp_path / "costs")  # today's cap already used
    board = _Board(workspace_tmp_path)
    cfg = {"cost_dir": str(workspace_tmp_path / "costs")}
    imgs, timings, note, mode = comic._panel_images(cfg, board, _spec(workspace_tmp_path), 7, "j", (576, 1024))
    assert mode == "nim+comfy_fallback" and "daily NIM cap" in note and board.n == 0
    assert len([c for c in _Prov.calls if c[0] == "ref"]) == 1 and len(imgs) == 5


def test_comfy_modes_and_auto_fallback_caps(workspace_tmp_path, monkeypatch):
    from backend.app.providers import local_comfyui as lc

    _Prov.calls = []
    monkeypatch.setattr(lc, "LocalComfyUIProvider", _Prov)
    monkeypatch.setattr(comic, "_comfy_up", lambda cfg: True)
    spec = _spec(workspace_tmp_path)
    cfg = {"cost_dir": str(workspace_tmp_path / "costs")}
    board = SimpleNamespace(generate=lambda *a, **k: pytest.fail("NIM must not be called"))
    imgs, timings, note, mode = comic._panel_images(cfg, board, spec, 7, "j", (576, 1024), "comfy")
    assert mode == "comfy_ipadapter" and len(timings) == 6
    assert all(c[3] >= 1800 for c in _Prov.calls if c[0] == "panel")  # per-image timeout >= floor
    monkeypatch.setattr(comic, "_comfy_up", lambda cfg: False)
    with pytest.raises(RuntimeError, match="not reachable"):  # explicit comfy never falls back to NIM
        comic._panel_images(cfg, board, spec, 7, "j", (576, 1024), "comfy")
    small = {"cost_dir": str(workspace_tmp_path / "costs"), "comic": {"max_nim_credits_per_reel": 3}}
    with pytest.raises(RuntimeError, match="reel cap 3"):  # auto + ComfyUI down: NIM fallback is capped
        comic._panel_images(small, board, spec, 7, "j", (576, 1024), "auto")


def test_soften_prompt_and_filtered_panel_is_retried(workspace_tmp_path):
    out = comic.soften_prompt("hero slashes a monster, blood everywhere, the dying beast", 2)
    assert "blood" not in out and "slash" not in out and "dying" not in out and "safe for all ages" in out

    class FlakyBoard(_Board):
        def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
            if self.n == 0 and "non-violent" not in prompt:
                self.n += 0
                raise RuntimeError("[ImageSwitchboard] All image providers failed. nvidia_nim: [NVIDIA NIM] Generation blocked: CONTENT_FILTERED")
            return super().generate(prompt, style, res, job, negative_prompt, seed)

    spec = _spec(workspace_tmp_path)
    spec["panels"][0]["image_prompt"] = "hero slashes a monster, blood everywhere"
    board = FlakyBoard(workspace_tmp_path)
    cfg = {"cost_dir": str(workspace_tmp_path / "costs")}
    imgs, timings, note, mode = comic._panel_images(cfg, board, spec, 7, "j", (576, 1024))
    assert mode == "nim" and len(imgs) == 5  # panel 0 succeeded on the softened retry
    from aurora.costs import credits_today

    assert credits_today("nvidia_nim", workspace_tmp_path / "costs") == 6  # the filtered attempt is counted too
