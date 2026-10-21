import json
from pathlib import Path

import pytest
from PIL import Image

from backend.app.providers import local_comfyui as lc
from backend.app.providers.local_comfyui import LocalComfyUIProvider

CFG = {"paths": {"base_output": ""}, "models": {"comfyui": {"api_url": "http://x"}},
       "providers": {"local_comfyui": {"steps": 25, "cfg": 5.5, "sampler_name": "dpmpp_2m", "scheduler": "karras"}}}


class FakeOrch:
    last = None

    def __init__(self, cfg):
        self.out = cfg["paths"]["images_dir"]

    def run_workflow(self, wf, overrides):
        FakeOrch.last = (wf, overrides)
        p = Path(self.out) / "out.png"
        Image.new("RGB", (512, 768), (90, 60, 40)).save(p)
        return [p]


@pytest.fixture
def provider(workspace_tmp_path, monkeypatch):
    import src.modules.video_engine as ve

    cfg = json.loads(json.dumps(CFG))
    cfg["paths"]["base_output"] = str(workspace_tmp_path / "out")
    monkeypatch.setattr(lc, "ComfyOrchestrator", FakeOrch)
    monkeypatch.setattr(ve, "upload_image", lambda path, api_url="": "uploaded_name.png")
    return LocalComfyUIProvider(cfg)


def test_workflow_structure():
    wf = json.loads(Path("src/workflows/img2img.json").read_text(encoding="utf-8"))
    assert wf["12"]["class_type"] == "LoadImage" and wf["11"]["class_type"] == "VAEEncode"
    assert wf["3"]["inputs"]["latent_image"] == ["11", 0] and wf["3"]["inputs"]["denoise"] == 0.45
    assert wf["9"]["class_type"] == "VAEDecode" and "5" not in wf


def test_generate_from_image(provider, workspace_tmp_path):
    src = workspace_tmp_path / "photo.jpg"
    Image.new("RGB", (300, 200), (200, 100, 50)).save(src)
    res = provider.generate_from_image(src, "same man in a navy suit", "cinematic", [512, 768], "j1", denoise=0.4, seed=5)
    wf, ov = FakeOrch.last
    assert wf.endswith("img2img.json")
    assert ov["12"] == {"image": "uploaded_name.png"} and ov["3"]["denoise"] == 0.4 and ov["3"]["seed"] == 5
    assert ov["3"]["steps"] == 25 and ov["3"]["sampler_name"] == "dpmpp_2m"
    assert "realistic" not in ov["7"]["text"]
    assert (res.width, res.height, res.provider) == (512, 768, "local_comfyui")
    assert res.context["mode"] == "img2img" and res.context["denoise"] == 0.4
    prepared = Path(res.output_path).parent / "source_512x768.png"
    assert Image.open(prepared).size == (512, 768)


def test_missing_source_raises(provider):
    with pytest.raises(FileNotFoundError):
        provider.generate_from_image("nope.jpg", "x", "cinematic", [512, 768], "j2")


def test_facelock_workflow_and_override(provider, workspace_tmp_path):
    wf = json.loads(Path("src/workflows/img2img_facelock.json").read_text(encoding="utf-8"))
    assert wf["15"]["class_type"] == "IPAdapterAdvanced" and wf["3"]["inputs"]["model"] == ["15", 0]
    assert wf["15"]["inputs"]["image"] == ["12", 0] and wf["15"]["inputs"]["weight"] == 0.8
    src = workspace_tmp_path / "photo.jpg"
    Image.new("RGB", (300, 200), (200, 100, 50)).save(src)
    res = provider.generate_from_image(src, "same man in a suit", "cinematic", [512, 768], "j3", face_lock=True)
    wf_path, ov = FakeOrch.last
    assert wf_path.endswith("img2img_facelock.json") and ov["15"] == {"weight": 0.8}
    assert ov["3"]["denoise"] == 0.45 and res.context["face_lock"] is True
