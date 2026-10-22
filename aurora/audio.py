"""Reel audio: music library auto-pick, loop/trim + fades + loudness, optional voiceover with ducking.

Single entry point for autorun / server / CLI:  resolve_audio(...) -> (audio file or None, note).
No music and no voiceover -> (None, "silent"): the reel keeps today's silent AAC track.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUSIC_DIR = ROOT / "assets" / "music"
PIPER_DIR = ROOT / "models" / "piper"
PIPER_VOICES = {"en": "en_US-lessac-medium", "hi": "hi_IN-pratham-medium"}
EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
MOODS = {
    "epic": "epic battle fight war hero warrior titan action power dragon storm cinematic army",
    "calm": "calm peace sunset beach ocean sea forest nature morning mountain lake river quiet relax garden",
    "happy": "happy party festival fun celebration birthday dance smile joy holiday friends colorful",
    "emotional": "love romantic romance sad wedding memory emotional rain tears nostalgia family",
    "devotional": "temple god puja prayer krishna shiva ganesh mandir aarti devotional bhajan diwali saree",
}


def list_tracks(library: Path = MUSIC_DIR) -> dict[str, list[Path]]:
    tracks: dict[str, list[Path]] = {}
    if Path(library).is_dir():
        for sub in sorted(p for p in Path(library).iterdir() if p.is_dir()):
            files = sorted(f for f in sub.iterdir() if f.suffix.lower() in EXTS)
            if files:
                tracks[sub.name.lower()] = files
    return tracks


def pick_music(prompt: str, library: Path = MUSIC_DIR) -> tuple[Path | None, str]:
    """Choose a track by mood keywords in the prompt; fall back to any track; None if the library is empty."""
    tracks = list_tracks(library)
    if not tracks:
        return None, ""
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    scores = {mood: len(words & set(kw.split())) for mood, kw in MOODS.items() if mood in tracks}
    best = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else None
    pool = tracks[best] if best else [f for files in tracks.values() for f in files]
    pick = pool[int(hashlib.sha1(prompt.encode("utf-8")).hexdigest(), 16) % len(pool)]
    return pick, best or "any"


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg audio step failed: " + "\n".join(proc.stderr.splitlines()[-5:]))


def mix_audio(ffmpeg: str, out: Path, duration: float, music: Path | None = None, voice: Path | None = None) -> Path:
    """Music looped/trimmed to duration (1 s fades, ~-14 LUFS); voice (-16 LUFS) ducks the music."""
    fade = f"afade=t=in:d=1,afade=t=out:st={max(duration - 1, 0):.2f}:d=1"
    d = f"{duration:.2f}"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if music and voice:
        # apad: sidechaincompress ends with its shorter input, which cut the music when the voice finished
        graph = ("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[m];[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,apad,asplit[v1][v2];"
                 "[m][v1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=600[duck];"
                 f"[duck][v2]amix=inputs=2:duration=longest:normalize=0,atrim=0:{d},{fade}")
        _run([ffmpeg, "-y", "-stream_loop", "-1", "-i", str(music), "-i", str(voice), "-filter_complex", graph,
              "-t", d, str(out)])
    elif music:
        _run([ffmpeg, "-y", "-stream_loop", "-1", "-i", str(music), "-af",
              f"loudnorm=I=-14:TP=-1.5:LRA=11,{fade}", "-t", d, str(out)])
    elif voice:
        _run([ffmpeg, "-y", "-i", str(voice), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-t", d, str(out)])
    else:
        raise ValueError("nothing to mix")
    return out


def synthesize_voiceover(text: str, out_wav: Path, lang: str | None = None) -> tuple[Path | None, str]:
    """Offline CPU TTS with Piper (English + Hindi). Returns (wav or None, note). Never raises."""
    lang = lang or ("hi" if re.search(r"[ऀ-ॿ]", text) else "en")
    voice_file = PIPER_DIR / f"{PIPER_VOICES[lang]}.onnx"
    if not voice_file.exists():
        return None, f"voiceover skipped: missing voice model {voice_file.name} (models/piper/)"
    try:
        from piper import PiperVoice

        voice = PiperVoice.load(str(voice_file))
        out_wav = Path(out_wav)
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(out_wav), "wb") as wf:
            voice.synthesize_wav(text, wf)
        return out_wav, f"voiceover ({PIPER_VOICES[lang]})"
    except Exception as exc:  # noqa: BLE001 - TTS must never break a render
        return None, f"voiceover skipped: {exc}"


def resolve_audio(
    ffmpeg: str,
    duration: float,
    work_dir: Path,
    slug: str,
    music: str | Path | None = None,
    prompt: str = "",
    voiceover_text: str | None = None,
    music_dir: Path = MUSIC_DIR,
) -> tuple[Path | None, str]:
    """music: 'auto' (library pick by mood), 'none'/None (no music), or a file path. Returns (audio, note)."""
    track, mood = None, ""
    if music and str(music).lower() == "auto":
        track, mood = pick_music(prompt, music_dir)
    elif music and str(music).lower() != "none":
        track = Path(music)
        if not track.is_file():
            raise FileNotFoundError(f"music file not found: {track}")
    voice, vnote = (synthesize_voiceover(voiceover_text, Path(work_dir) / f"{slug}_voice.wav")
                    if voiceover_text else (None, ""))
    if not track and not voice:
        return None, vnote or "silent"
    mixed = mix_audio(ffmpeg, Path(work_dir) / f"{slug}_audio.wav", duration, track, voice)
    parts = ([f"music:{track.name}" + (f" ({mood})" if mood else "")] if track else []) + ([vnote] if voice else [])
    return mixed, " + ".join(parts) + (f" | {vnote}" if vnote and not voice else "")
