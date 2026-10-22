import shutil
import subprocess

import pytest

from aurora.audio import list_tracks, pick_music, resolve_audio
from aurora.monitoring.media_validator import validate_video
from aurora.pipeline import run

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg missing")


def _lib(root):
    for mood in ("epic", "calm", "devotional"):
        (root / mood).mkdir(parents=True)
        (root / mood / f"{mood}1.mp3").write_bytes(b"x")
    return root


def test_pick_music_by_mood_and_fallbacks(workspace_tmp_path):
    lib = _lib(workspace_tmp_path / "lib")
    assert pick_music("a woman in a saree at a temple", lib)[1] == "devotional"
    assert pick_music("epic battle of titans", lib)[1] == "epic"
    assert pick_music("a quiet beach at sunset", lib)[1] == "calm"
    track, mood = pick_music("zzz unrelated", lib)
    assert track is not None and mood == "any"
    assert pick_music("anything", workspace_tmp_path / "empty") == (None, "")
    assert set(list_tracks(lib)) == {"epic", "calm", "devotional"}


def test_empty_library_or_none_stays_silent(workspace_tmp_path):
    assert resolve_audio("ffmpeg", 3, workspace_tmp_path, "s", music="auto", prompt="x", music_dir=workspace_tmp_path / "none")[0] is None
    assert resolve_audio("ffmpeg", 3, workspace_tmp_path, "s", music="none")[0] is None
    assert resolve_audio("ffmpeg", 3, workspace_tmp_path, "s", music=None)[1] == "silent"


@needs_ffmpeg
def test_music_is_looped_trimmed_and_muxed(workspace_tmp_path):
    tone = workspace_tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", str(tone)], capture_output=True, check=True)
    audio, note = resolve_audio("ffmpeg", 3, workspace_tmp_path, "s", music=tone)
    assert "music:tone.wav" in note
    img = workspace_tmp_path / "i.png"
    from PIL import Image
    Image.new("RGB", (200, 300), (50, 90, 130)).save(img)
    out = run(img, workspace_tmp_path / "o.mp4", 3, 24, 270, 480, audio)
    assert validate_video(out, 270, 480, fps=24, duration_secs=3, require_audio=True, deep=True).ok
    vol = subprocess.run(["ffmpeg", "-i", str(out), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    assert "mean_volume: -inf" not in vol  # real, non-silent audio


def test_voiceover_skips_gracefully_without_model(workspace_tmp_path, monkeypatch):
    from aurora import audio

    monkeypatch.setattr(audio, "PIPER_DIR", workspace_tmp_path)
    wav, note = audio.synthesize_voiceover("hello", workspace_tmp_path / "v.wav")
    assert wav is None and "skipped" in note
