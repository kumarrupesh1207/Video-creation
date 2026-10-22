import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from aurora import costs, research
from aurora.motion.providers import google_video_provider as gv
from aurora.pipeline import run

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg missing")


def test_budget_cap_and_ledger(workspace_tmp_path):
    d = workspace_tmp_path / "costs"
    costs.check_budget(0.5, monthly_budget_inr=85, usd_inr=85, cost_dir=d)  # $0.5 <= $1
    costs.log_cost("google_video", "m", 8, 0.8, "j1", cost_dir=d)
    assert costs.month_spend_usd(d) == 0.8
    with pytest.raises(costs.BudgetExceeded):
        costs.check_budget(0.5, monthly_budget_inr=85, usd_inr=85, cost_dir=d)
    with pytest.raises(costs.BudgetExceeded):  # budget 0 = never spend
        costs.check_budget(0.01, monthly_budget_inr=0, cost_dir=workspace_tmp_path / "empty")


def _cfg(tmp, budget=1000, enabled=True):
    return {"motion": {"i2v": {"google": {"enabled": enabled, "model": "gemini-omni-1.1-flash", "clip_seconds": 8,
                                           "rate_usd_per_sec": 0.10, "monthly_budget_inr": budget, "usd_inr": 85,
                                           "cost_dir": str(tmp / "costs")}}}}


def test_rates_and_estimate(workspace_tmp_path):
    assert gv.default_rate("veo-3.1-lite-x") == 0.05 and gv.default_rate("veo-3.1-fast") == 0.10 and gv.default_rate("veo-3.1") == 0.40
    assert gv.GoogleVideoProvider(_cfg(workspace_tmp_path)).estimated_cost_usd(16) == 1.6


def test_refuses_when_disabled_missing_key_or_over_budget(workspace_tmp_path, monkeypatch):
    img = workspace_tmp_path / "i.png"
    img.write_bytes(b"x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(gv.GoogleVideoUnavailable, match="disabled"):
        gv.GoogleVideoProvider(_cfg(workspace_tmp_path, enabled=False)).generate(img, 8)
    with pytest.raises(gv.GoogleVideoUnavailable, match="GEMINI_API_KEY"):
        gv.GoogleVideoProvider(_cfg(workspace_tmp_path)).generate(img, 8)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(gv.GoogleVideoProvider, "_client", lambda self: object())
    with pytest.raises(costs.BudgetExceeded):
        gv.GoogleVideoProvider(_cfg(workspace_tmp_path, budget=0)).generate(img, 8)


@needs_ffmpeg
def test_chained_clips_are_costed_logged_and_concatenated(workspace_tmp_path, monkeypatch):
    from PIL import Image

    img = workspace_tmp_path / "i.png"
    Image.new("RGB", (180, 320), (20, 80, 140)).save(img)
    seen = []

    def fake_clip(self, client, image, prompt, out):
        seen.append(image.name)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=180x320:d=2:r=24", "-f", "lavfi", "-i",
                        "sine=d=2", "-shortest", str(out)], check=True)
        return out

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(gv.GoogleVideoProvider, "_client", lambda self: object())
    monkeypatch.setattr(gv.GoogleVideoProvider, "_clip", fake_clip)
    prov = gv.GoogleVideoProvider(_cfg(workspace_tmp_path))
    res = prov.generate(img, duration=16, job_id="j")
    assert res.context["clips"] == 2 and seen == ["i.png", "last0.png"]  # 2nd clip starts from the last frame
    assert costs.month_spend_usd(workspace_tmp_path / "costs") == 1.6


@needs_ffmpeg
def test_google_motion_falls_back_to_camera_on_billing_error(workspace_tmp_path, monkeypatch):
    from PIL import Image

    from aurora.monitoring.media_validator import validate_video

    img = workspace_tmp_path / "i.png"
    Image.new("RGB", (200, 300), (60, 60, 90)).save(img)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from aurora.motion.providers import kaggle_provider

    monkeypatch.setattr(kaggle_provider.KaggleProvider, "__init__", lambda self, cfg: None)
    monkeypatch.setattr(kaggle_provider.KaggleProvider, "generate", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    out = run(img, workspace_tmp_path / "o.mp4", 3, 24, 270, 480, motion="google", config=_cfg(workspace_tmp_path))
    assert validate_video(out, 270, 480, fps=24, duration_secs=3, require_audio=True).ok


def _idea(i=0):
    return {"title": f"Idea {i}", "hook": "Wait for it", "description": "d", "hashtags": ["#a"], "ai_disclosure": True,
            "scenes": [{"image_prompt": "a man in a suit, city", "motion_prompt": "push in", "voiceover": "Namaste"}] * 3}


class FakeModels:
    def __init__(self, text):
        self.text, self.calls = text, []

    def generate_content(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text=self.text, parsed=None)


def test_research_valid_json_and_schema(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    client = SimpleNamespace(models=FakeModels(json.dumps({"ideas": [_idea(0), _idea(1)]})))
    out = research.research_ideas("street food", "Hindi", "Reels", n=2, cfg={}, client=client)
    assert out["source"] == "gemini" and len(out["ideas"]) == 2 and out["ideas"][0]["ai_disclosure"] is True
    assert client.models.calls[0]["model"] == "gemini-3.1-flash-lite"


def test_research_invalid_json_and_no_key_fall_back(monkeypatch):
    monkeypatch.setattr(research, "_local_llm", lambda *a, **k: None)
    bad = SimpleNamespace(models=FakeModels(json.dumps({"ideas": [{"title": "x"}]})))
    out = research.research_ideas("street food", client=bad, cfg={})
    assert out["source"] == "topic_fallback" and "invalid" in out["note"]
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = research.research_ideas("street food", cfg={})
    assert out["source"] == "topic_fallback" and out["ideas"][0]["scenes"]
