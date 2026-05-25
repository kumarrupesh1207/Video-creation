from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from aurora.motion.base import ExecutionTier, MotionMode, VideoResult
from aurora.pipeline import run_prompt_ledger


@pytest.fixture
def workspace_tmp_path() -> Path:
    """Create a temp directory inside the workspace for locked-down Windows hosts."""
    path = Path(".test_tmp") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_titan_battle_ledger_timing_and_pipeline_metadata() -> None:
    ledger_path = Path("aurora/prompts/titan_battle_60s.json")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    assert ledger["duration_secs"] == 60
    assert ledger["fps"] == 24
    assert ledger["total_frames"] == 1440
    assert ledger["target_resolution"] == "1080x1920"
    assert ledger["seed"] == 9001
    assert ledger["visual_complexity"] == "high action"
    assert ledger["motion_mode"] == "full_i2v_motion"
    assert ledger["ledger_name"] == "goku_vs_saitama_climax_60s"
    assert "global_character_specs" in ledger

    segments = ledger["segments"]
    assert [segment["frame_start"] for segment in segments] == [0, 360, 840]
    assert [segment["frame_end"] for segment in segments] == [359, 839, 1439]
    assert [segment["duration_secs"] for segment in segments] == [15, 20, 25]
    assert sum(segment["frame_end"] - segment["frame_start"] + 1 for segment in segments) == 1440


def test_run_prompt_ledger_generates_segments_and_concats(workspace_tmp_path: Path, monkeypatch) -> None:
    source_ledger = json.loads(Path("aurora/prompts/titan_battle_60s.json").read_text(encoding="utf-8"))
    source_ledger["id"] = "test_ledger"
    ledger_path = workspace_tmp_path / "test_ledger.json"
    ledger_path.write_text(json.dumps(source_ledger), encoding="utf-8")
    image_path = workspace_tmp_path / "image.png"
    image_path.write_bytes(b"png")
    output_path = workspace_tmp_path / "master.mp4"
    generated_durations: list[float] = []

    def fake_generate(self, image_path: Path, output_path: Path, duration_secs: float, fps: int, job_id: str) -> VideoResult:
        del self, image_path, fps, job_id
        generated_durations.append(duration_secs)
        output_path.write_bytes(b"segment")
        return VideoResult(
            output_path=output_path,
            provider="ffmpeg_kenburns",
            mode=MotionMode.STATIC_CINEMATIC,
            tier=ExecutionTier.LOCAL,
            job_id="job-test",
            image_hash="abc123",
            duration_secs=duration_secs,
            gen_time_secs=0.01,
            cost_usd=0.0,
        )

    class Completed:
        returncode = 0

        def check_returncode(self) -> None:
            return None

    def fake_run(cmd: list[str], capture_output: bool, text: bool, timeout: int) -> Completed:
        del capture_output, text, timeout
        Path(cmd[-1]).write_bytes(b"master")
        return Completed()

    monkeypatch.setattr("aurora.motion.providers.ffmpeg_kenburns.FFmpegKenBurns.generate", fake_generate)
    monkeypatch.setattr("subprocess.run", fake_run)

    result_path = run_prompt_ledger(image_path=image_path, output_path=output_path, prompt_ledger=ledger_path)

    assert result_path == output_path
    assert result_path.read_bytes() == b"master"
    assert generated_durations == [15.0, 20.0, 25.0]
