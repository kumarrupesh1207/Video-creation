import shutil
import subprocess

import pytest
from PIL import Image

from aurora.monitoring.media_validator import KNOWN_MOCK_MD5, validate_image, validate_video
from aurora.pipeline import run
from backend.app.providers.nvidia_nim import NvidiaNIMProvider

pytestmark_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


def _png(path, size=(64, 96)):
    Image.new("RGB", size, (120, 80, 200)).save(path)
    return path


def test_validate_image_ok_and_mismatch(workspace_tmp_path):
    p = _png(workspace_tmp_path / "a.png")
    assert validate_image(p, 64, 96).ok
    rep = validate_image(p, 512, 512)
    assert not rep.ok and any("width" in e for e in rep.errors)


def test_validate_image_rejects_garbage(workspace_tmp_path):
    p = workspace_tmp_path / "bad.png"
    p.write_bytes(b"not an image")
    assert not validate_image(p).ok
    assert not validate_image(workspace_tmp_path / "missing.png").ok


def test_validate_video_rejects_fake_mp4(workspace_tmp_path):
    p = workspace_tmp_path / "fake.mp4"
    p.write_bytes(b"mp4")
    rep = validate_video(p)
    assert not rep.ok and "not a valid video" in rep.errors[0]


@pytestmark_ffmpeg
def test_reel_real_dimensions_fps_duration_audio(workspace_tmp_path):
    img = _png(workspace_tmp_path / "in.png", (200, 300))
    out = run(img, workspace_tmp_path / "out.mp4", duration_secs=2, fps=24, width=270, height=480)
    rep = validate_video(out, 270, 480, fps=24, duration_secs=2, require_audio=True)
    assert rep.ok, rep.errors


@pytestmark_ffmpeg
def test_reel_custom_fps_and_audio_file(workspace_tmp_path):
    img = _png(workspace_tmp_path / "in.png", (200, 300))
    wav = workspace_tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", str(wav)], capture_output=True, check=True)
    out = run(img, workspace_tmp_path / "o30.mp4", duration_secs=2, fps=30, width=270, height=480, audio_path=wav)
    rep = validate_video(out, 270, 480, fps=30, duration_secs=2, require_audio=True)
    assert rep.ok, rep.errors


@pytestmark_ffmpeg
def test_known_mock_md5_is_rejected(workspace_tmp_path, monkeypatch):
    import aurora.monitoring.media_validator as mv

    img = _png(workspace_tmp_path / "in.png", (200, 300))
    out = run(img, workspace_tmp_path / "o.mp4", duration_secs=1, width=270, height=480)
    monkeypatch.setattr(mv, "KNOWN_MOCK_MD5", frozenset({mv._md5(out)}))
    rep = mv.validate_video(out)
    assert not rep.ok and any("mock" in e for e in rep.errors)
    assert len(KNOWN_MOCK_MD5) == 2


@pytest.mark.parametrize("req,expected", [((512, 512), (1024, 1024)), ((512, 768), (768, 1344)), ((1920, 1080), (1344, 768))])
def test_nim_size_mapping(req, expected):
    assert NvidiaNIMProvider.nearest_flux_size(*req) == expected
    assert NvidiaNIMProvider.nearest_aspect_ratio(512, 768) == "2:3"


def test_zero_byte_files_rejected(workspace_tmp_path):
    for name, fn in (("e.mp4", validate_video), ("e.png", validate_image)):
        p = workspace_tmp_path / name
        p.write_bytes(b"")
        assert not fn(p).ok


@pytestmark_ffmpeg
@pytest.mark.parametrize("w,h", [(480, 270), (360, 360)])
def test_reel_custom_aspect_and_deep_decode(workspace_tmp_path, w, h):
    img = _png(workspace_tmp_path / "in.png", (200, 300))
    out = run(img, workspace_tmp_path / "o.mp4", duration_secs=1, width=w, height=h)
    assert validate_video(out, w, h, fps=24, duration_secs=1, require_audio=True, deep=True).ok


@pytestmark_ffmpeg
def test_truncated_video_fails_deep_decode(workspace_tmp_path):
    img = _png(workspace_tmp_path / "in.png", (200, 300))
    out = run(img, workspace_tmp_path / "o.mp4", duration_secs=2, width=270, height=480)
    data = out.read_bytes()
    out.write_bytes(data[: len(data) // 2])
    assert not validate_video(out, deep=True).ok


def test_local_comfyui_defaults_schema():
    import json
    from pathlib import Path

    import yaml

    wf = json.loads(Path("src/workflows/image_gen.json").read_text(encoding="utf-8"))
    k = wf["3"]["inputs"]
    assert (k["cfg"], k["steps"], k["sampler_name"], k["scheduler"]) == (5.5, 25, "dpmpp_2m", "karras")
    assert (wf["5"]["inputs"]["width"], wf["5"]["inputs"]["height"]) == (512, 768)
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))["providers"]["local_comfyui"]
    for text in (wf["7"]["inputs"]["text"], cfg["negative_prompt"]):
        assert "photorealistic" not in text and "realistic" not in text
        assert "extra fingers" in text and "deformed" in text and "bad hands" in text


def test_output_identical_to_input_rejected(workspace_tmp_path):
    p = workspace_tmp_path / "a.mp4"
    p.write_bytes(b"x" * 100)
    rep = validate_video(p, input_path=p)
    assert any("identical" in e for e in rep.errors)
    assert rep.sha256
