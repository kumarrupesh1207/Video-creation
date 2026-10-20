import argparse
import json
import shutil
from types import SimpleNamespace

import pytest
from PIL import Image

from aurora import autorun
from aurora.motion.base import MotionResult
from aurora.motion.switchboard import MotionSwitchboard

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


def test_slug_and_topics(workspace_tmp_path):
    assert autorun.slugify("A man, in a Suit!") .startswith("a-man-in-a-suit-")
    assert autorun.slugify("x") != autorun.slugify("y")
    f = workspace_tmp_path / "t.txt"
    f.write_text("# c\n\none\n two \n", encoding="utf-8")
    assert autorun.read_topics(f) == ["one", "two"]


def test_motion_switchboard_rejects_mock_and_corrupt(workspace_tmp_path):
    bad = workspace_tmp_path / "fake.mp4"
    bad.write_bytes(b"mp4")
    with pytest.raises(RuntimeError, match="Invalid output"):
        MotionSwitchboard._require_valid_video(MotionResult("p", bad, "static_cinematic"), "p")


class FakeSwitchboard:
    """Stands in for ImageSwitchboard: writes a real portrait PNG."""

    def __init__(self, tmp):
        self.tmp, self.calls = tmp, 0

    def generate(self, prompt, style, resolution, job_id):
        self.calls += 1
        path = self.tmp / f"img{self.calls}.png"
        Image.new("RGB", tuple(resolution), (40, 90, 160)).save(path)
        return SimpleNamespace(output_path=str(path), width=resolution[0], height=resolution[1],
                               provider="fake", model="fake", seed=1, context={})


@needs_ffmpeg
def test_process_topic_writes_valid_reel_sidecar_and_is_resumable(workspace_tmp_path):
    args = argparse.Namespace(no_llm=True, style="cinematic", width=270, height=480, duration=2.0, fps=24,
                              music=None, voiceover=False)
    board = FakeSwitchboard(workspace_tmp_path)
    out_dir = workspace_tmp_path / "final" / "2026-01-01"
    info = autorun.process_topic({}, board, "a man in a navy suit", args, out_dir)
    mp4 = out_dir / f"{info['slug']}.mp4"
    side = json.loads(mp4.with_suffix(".json").read_text(encoding="utf-8"))
    assert side["status"] == "ok" and side["caption"] == "a man in a navy suit"
    assert (side["reel"]["width"], side["reel"]["height"], side["reel"]["fps"]) == (1080, 1920, 24.0)
    assert side["reel"]["has_audio"] and side["reel"]["sha256"]
    assert autorun.is_done(workspace_tmp_path / "final", info["slug"])
    assert autorun.is_done(workspace_tmp_path / "final", "other-slug") is None


@needs_ffmpeg
def test_failed_validation_leaves_no_final_file_and_is_not_skipped(workspace_tmp_path, monkeypatch):
    args = argparse.Namespace(no_llm=True, style="cinematic", width=270, height=480, duration=2.0, fps=24,
                              music=None, voiceover=False)
    board = FakeSwitchboard(workspace_tmp_path)
    out_dir = workspace_tmp_path / "final" / "2026-01-01"

    def bad_render(image, out, *a, **k):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")  # mock/corrupt render

    monkeypatch.setattr(autorun, "render_reel", bad_render)
    with pytest.raises(RuntimeError, match="failed validation"):
        autorun.process_topic({}, board, "a woman in a saree", args, out_dir)
    assert not list(out_dir.glob("*.json"))
    assert autorun.is_done(workspace_tmp_path / "final", autorun.slugify("a woman in a saree")) is None


@needs_ffmpeg
def test_black_video_fails_deep_check(workspace_tmp_path):
    import subprocess

    from aurora.monitoring.media_validator import validate_video

    p = workspace_tmp_path / "black.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:s=64x64:d=1", str(p)], capture_output=True, check=True)
    rep = validate_video(p, deep=True)
    assert not rep.ok and any("black" in e for e in rep.errors)
