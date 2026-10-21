import pytest
from pydantic import ValidationError

import server


def test_create_request_validation():
    for bad in ({"aspect": "4:3"}, {"aspect": "custom", "width": 99999}, {"prompt": "x" * 1001}, {"provider": "evil"}):
        with pytest.raises(ValidationError):
            server.CreateRequest(**{"prompt": "x", **bad})


def test_create_rejects_empty_prompt_and_foreign_music():
    assert server.create_single(server.CreateRequest(prompt="  "))["status"] == "error"
    r = server.create_single(server.CreateRequest(prompt="x", music_path="C:/Windows/win.ini"))
    assert r["status"] == "error"


def test_files_endpoint_blocks_traversal():
    with pytest.raises(server.HTTPException):
        server.get_output_file("../config.yaml")
    assert server.gallery()["items"] is not None


def test_ai_motion_job_falls_back_and_reports(monkeypatch):
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from PIL import Image

    import aurora.motion.providers.kaggle_provider as kp
    import backend.app.services.image_switchboard as sb

    tmp = tempfile.TemporaryDirectory()
    tmp_path = Path(tmp.name)
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "img.png"
    Image.effect_noise((512, 768), 60).convert("RGB").save(img)
    monkeypatch.setattr(sb.ImageSwitchboard, "generate", lambda self, *a, **k: SimpleNamespace(
        output_path=img, width=512, height=768, provider="fake", model="m", seed=1, context={}))
    seen = {}

    class FakeKaggle:
        def __init__(self, cfg):
            seen["cfg"] = cfg

        def generate(self, *a, **k):
            raise RuntimeError("kaggle offline")

    monkeypatch.setattr(kp, "KaggleProvider", FakeKaggle)
    req = server.CreateRequest(prompt="a man", mode="reel", motion="ai", action="he runs", duration=3)
    server._create_jobs["t1"] = {"status": "running", "stage": "queued"}
    server._run_create_job("t1", req)
    job = server._create_jobs["t1"]
    assert job["status"] == "done", job.get("error")
    assert seen["cfg"]["_motion_prompt"] == "a man"
    assert seen["cfg"]["motion"]["i2v"]["kaggle"]["motion_suffix"].startswith("Static camera. he runs.")
    assert any("used camera motion" in e for e in job["events"])
    monkeypatch.undo()
    tmp.cleanup()


def test_upload_requires_uploads_path():
    with pytest.raises(server.HTTPException) as e:
        server.create_single(server.CreateRequest(prompt="", source="upload", image_path="C:/Windows/win.ini"))
    assert e.value.status_code == 422
    with pytest.raises(ValidationError):
        server.CreateRequest(prompt="x", source="upload", denoise=0.9)


@pytest.mark.parametrize("restyle", [False, True])
def test_upload_job_asis_and_restyle(monkeypatch, restyle):
    import tempfile
    from pathlib import Path
    from types import SimpleNamespace

    from PIL import Image

    import backend.app.providers.local_comfyui as lc
    import backend.app.services.image_switchboard as sb

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    monkeypatch.chdir(root)
    photo = root / "photo.png"
    Image.effect_noise((512, 768), 60).convert("RGB").save(photo)
    calls = {}
    monkeypatch.setattr(sb.ImageSwitchboard, "generate", lambda *a, **k: calls.setdefault("switchboard", True))
    monkeypatch.setattr(lc.LocalComfyUIProvider, "generate_from_image", lambda self, source_path, prompt, style, resolution, job_id, negative_prompt=None, denoise=0.55, face_lock=False, **k: (calls.update(denoise=denoise, face_lock=face_lock, source_path=source_path) or SimpleNamespace(
        output_path=photo, width=512, height=768, provider="local_comfyui", model="m", seed=1)), raising=False)
    req = server.CreateRequest(prompt="restyled" if restyle else "", source="upload", restyle=restyle, denoise=0.6,
                               image_path=str(photo), mode="image")
    server._create_jobs["u1"] = {"status": "running", "stage": "queued"}
    server._run_create_job("u1", req)
    job = server._create_jobs["u1"]
    assert job["status"] == "done", job.get("error")
    assert "switchboard" not in calls
    assert job["outputs"]["provider"] == ("local_comfyui" if restyle else "upload")
    assert (calls.get("denoise") == 0.6) == restyle
    assert calls.get("face_lock", True) is True
    monkeypatch.undo()
    tmp.cleanup()
