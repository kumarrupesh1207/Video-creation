from __future__ import annotations

import json
import shutil
import struct
import uuid
import zlib
from dataclasses import fields
from pathlib import Path

import pytest

from aurora.logs import StructuredLogger
from aurora.motion.base import ExecutionTier, MotionMode, VideoResult
from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns


@pytest.fixture
def workspace_tmp_path() -> Path:
    """Create a temp directory inside the workspace for locked-down Windows hosts."""
    path = Path(".test_tmp") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_test_png(path: Path, width: int = 100, height: int = 100) -> None:
    raw_rows = b"".join(b"\x00" + (b"\x20\x80\xc0" * width) for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw_rows))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def test_structured_logger_writes_valid_json(workspace_tmp_path: Path) -> None:
    log_path = workspace_tmp_path / "aurora.jsonl"
    logger = StructuredLogger(component="test", log_path=log_path, job_id="job-test")

    logger.info("unit_event", provider="ffmpeg_kenburns", mode="static_cinematic", tier="local")

    record = json.loads(log_path.read_text(encoding="utf-8").strip())
    for field_name in StructuredLogger.REQUIRED_FIELDS:
        assert field_name in record
    assert record["event"] == "unit_event"
    assert record["job_id"] == "job-test"


def test_ffmpeg_kenburns_is_available_returns_bool() -> None:
    provider = FFmpegKenBurns()

    assert isinstance(provider.is_available(), bool)


def test_video_result_fields_are_present(workspace_tmp_path: Path) -> None:
    field_names = {field.name for field in fields(VideoResult)}
    expected = {
        "output_path",
        "provider",
        "mode",
        "tier",
        "job_id",
        "image_hash",
        "duration_secs",
        "gen_time_secs",
        "cost_usd",
        "error",
        "fps",
        "width",
        "height",
        "metadata",
    }

    assert expected.issubset(field_names)
    result = VideoResult(
        output_path=workspace_tmp_path / "output.mp4",
        provider="ffmpeg_kenburns",
        mode=MotionMode.STATIC_CINEMATIC,
        tier=ExecutionTier.LOCAL,
        job_id="job-test",
        image_hash="abc123",
        duration_secs=25.0,
        gen_time_secs=1.0,
        cost_usd=0.0,
    )
    assert result.width == 1080
    assert result.height == 1920
    assert result.fps == 24


def test_generate_uses_ffmpeg_with_100x100_png(workspace_tmp_path: Path, monkeypatch) -> None:
    png_path = workspace_tmp_path / "test.png"
    output_path = workspace_tmp_path / "output.mp4"
    _write_test_png(png_path)

    class Completed:
        returncode = 0
        stderr = ""

        def check_returncode(self) -> None:
            return None

    def fake_run(cmd: list[str], capture_output: bool, text: bool, timeout: int) -> Completed:
        del cmd, capture_output, text, timeout
        output_path.write_bytes(b"mp4")
        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)
    result = FFmpegKenBurns(logger=StructuredLogger("test", workspace_tmp_path / "aurora.jsonl")).generate(
        image_path=png_path,
        output_path=output_path,
    )

    assert result.output_path == output_path
    assert output_path.exists()
