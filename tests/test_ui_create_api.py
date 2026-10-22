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


def test_preflight_blocks_when_comfyui_down_or_kaggle_keys_missing(monkeypatch):
    import json as _json

    import aurora

    monkeypatch.setattr(server, "_comfy_up", lambda cfg: False)
    r = server.create_single(server.CreateRequest(prompt="x", provider="local_comfyui"))
    assert r.status_code == 503 and "start_aurora.bat" in _json.loads(r.body)["message"]
    monkeypatch.setattr(aurora, "load_env", lambda: None)
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    r = server.create_single(server.CreateRequest(prompt="x", provider="nvidia_nim", motion="ai"))
    assert r.status_code == 503 and "Kaggle" in _json.loads(r.body)["message"]


def test_jobs_persist_and_running_becomes_interrupted(monkeypatch):
    import json as _json
    import tempfile
    from pathlib import Path

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.chdir(tmp.name)
    job = {"job_id": "create_20990101_000000", "status": "running", "stage": "image", "prompt": "p", "timeline": []}
    server._save_job(job)
    server._create_jobs.pop(job["job_id"], None)
    server._load_jobs()
    got = server._create_jobs.pop(job["job_id"])
    assert got["status"] == "interrupted" and "restarted" in got["error"]
    saved = _json.loads((Path(tmp.name) / "outputs" / "jobs" / f"{job['job_id']}.json").read_text(encoding="utf-8"))
    assert saved["status"] == "interrupted"
    monkeypatch.undo()
    tmp.cleanup()


def _req(path="/api/jobs", cookie=None, ip="9.9.9.9", method="GET"):
    from types import SimpleNamespace
    return SimpleNamespace(url=SimpleNamespace(path=path), method=method, cookies={"aurora_session": cookie} if cookie else {},
                           client=SimpleNamespace(host=ip), headers={})


def test_login_flow_token_and_rate_limit(monkeypatch):
    import asyncio
    import time

    monkeypatch.setenv("AURORA_PASSWORD", "s3cret")
    monkeypatch.setenv("AURORA_SECRET", "x" * 64)
    server._login_hits.clear()

    async def nxt(_):
        return "passed"

    # no cookie -> 401 on /api/*, exempt paths pass
    assert asyncio.run(server._require_login(_req(), nxt)).status_code == 401
    assert asyncio.run(server._require_login(_req("/api/health"), nxt)) == "passed"
    # wrong password -> 401 and no cookie; right password -> signed HttpOnly SameSite=Strict cookie
    assert server.login(server.LoginRequest(password="nope"), _req()).status_code == 401
    ok = server.login(server.LoginRequest(password="s3cret"), _req())
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    token = cookie.split("aurora_session=")[1].split(";")[0]
    assert asyncio.run(server._require_login(_req(cookie=token), nxt)) == "passed"
    # tampered / expired tokens are rejected
    assert not server._valid_token(token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert not server._valid_token(server._sign(int(time.time()) - 5))
    # 5 attempts/min/IP -> 429 on the next one
    server._login_hits.clear()
    codes = [server.login(server.LoginRequest(password="bad"), _req(ip="5.5.5.5")).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    # no password configured -> everything open (localhost mode)
    monkeypatch.delenv("AURORA_PASSWORD")
    assert asyncio.run(server._require_login(_req(), nxt)) == "passed"
    assert server.auth_state(_req()) == {"auth_required": False, "authenticated": True}


def test_publish_endpoints_veto_restore_reschedule(monkeypatch):
    import functools
    import tempfile
    from pathlib import Path

    import aurora.publish.queue as pq

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setattr(pq, "PublishQueue", functools.partial(pq.PublishQueue, path=Path(tmp.name) / "q.json"))
    clip = Path(tmp.name) / "clip.mp4"
    clip.write_bytes(b"not really a video")
    item = pq.PublishQueue().enqueue("test", clip, {"title": "T"})
    assert [i["id"] for i in server.publish_queue()["items"]] == [item["id"]]
    assert server.publish_veto(item["id"])["item"]["status"] == "vetoed"
    assert server.publish_approve(item["id"])["item"]["status"] == "scheduled"
    assert server.publish_reschedule(item["id"], server.RescheduleRequest(publish_at="2030-01-01T10:00:00+05:30"))["status"] == "success"
    with pytest.raises(server.HTTPException) as e:
        server.publish_reschedule(item["id"], server.RescheduleRequest(publish_at="2030-01-01T10:00:00"))
    assert e.value.status_code == 400
    with pytest.raises(server.HTTPException) as e:
        server.publish_approve(item["id"])  # already scheduled
    assert e.value.status_code == 400
    assert server.publish_health()["channels"]["test"]["token"] in (True, False)
    tmp.cleanup()
