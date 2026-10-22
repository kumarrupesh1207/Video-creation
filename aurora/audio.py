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


MUSIC_CACHE = ROOT / "outputs" / "music_cache"
MOOD_PROMPTS = {
    "epic": "epic cinematic orchestral music, powerful drums, dramatic strings",
    "calm": "soft ambient piano and warm pads, calm and peaceful",
    "happy": "upbeat happy acoustic pop instrumental, bright and cheerful",
    "emotional": "emotional cinematic piano and strings, warm and heartfelt",
    "devotional": "serene indian devotional instrumental, flute, tabla and tanpura",
}
DEFAULT_MUSIC_PROMPT = "soft cinematic background music, gentle piano, warm and uplifting"
_musicgen = {}


def music_prompt(text: str) -> str:
    """MusicGen prompt from the reel text via the same mood keywords as the library picker."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    scores = {m: len(words & set(kw.split())) for m, kw in MOODS.items()}
    best = max(scores, key=scores.get)
    return MOOD_PROMPTS[best] if scores[best] > 0 else DEFAULT_MUSIC_PROMPT


def generate_music(text: str, seconds: float, out_wav: Path, cache_dir: Path = MUSIC_CACHE) -> Path:
    """AI background music (MusicGen small, CPU). Cached by prompt+length so repeat moods are instant."""
    prompt = music_prompt(text)
    seconds = min(max(float(seconds), 4.0), 30.0)
    cached = Path(cache_dir) / f"{hashlib.sha1(f'{prompt}|{round(seconds)}'.encode()).hexdigest()[:16]}.wav"
    if not cached.exists():
        import numpy as np
        import torch
        from scipy.io import wavfile
        from transformers import AutoProcessor, MusicgenForConditionalGeneration

        if "model" not in _musicgen:
            _musicgen["proc"] = AutoProcessor.from_pretrained("facebook/musicgen-small")
            _musicgen["model"] = MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small").eval()
        model = _musicgen["model"]
        torch.set_num_threads(max(1, (torch.get_num_threads() or 4) - 2))  # keep the PC usable
        inputs = _musicgen["proc"](text=[prompt], padding=True, return_tensors="pt")
        with torch.no_grad():
            audio = model.generate(**inputs, do_sample=True, guidance_scale=3.0, max_new_tokens=int(seconds * 50))
        wav = audio[0, 0].cpu().numpy().astype(np.float32)
        cached.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(str(cached), model.config.audio_encoder.sampling_rate, wav)
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_wav.write_bytes(cached.read_bytes())
    return out_wav


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


HINGLISH = set("hai hain nahi nahin kya aur ke ka ki ko mein main tum hum aap yeh ye woh tha thi se par bahut accha acha zindagi dil pyaar".split())
EDGE_VOICES = {("hi", "female"): "hi-IN-SwaraNeural", ("hi", "male"): "hi-IN-MadhurNeural",
               ("en", "female"): "en-IN-NeerjaNeural", ("en", "male"): "en-IN-PrabhatNeural"}
XTTS_SPEAKERS = {"female": "Claribel Dervla", "male": "Damien Black"}
XTTS_PY = ROOT / ".venv-tts" / "Scripts" / "python.exe"
XTTS_SCRIPT = ROOT / "scripts" / "xtts_say.py"
ENGINE_ORDER = ["edge", "xtts", "piper"]


def detect_lang(text: str) -> str:
    """hi for Devanagari or romanised Hindi (Hinglish), else en."""
    if re.search(r"[ऀ-ॿ]", text):
        return "hi"
    return "hi" if len(set(re.findall(r"[a-z]+", text.lower())) & HINGLISH) >= 2 else "en"


def audio_settings() -> dict:
    """config.yaml `audio:` block (voice_engine auto|edge|xtts|piper, voice_gender, voice, speaker_wav, rate, pitch)."""
    try:
        import yaml

        return dict((yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}).get("audio", {}))
    except Exception:  # noqa: BLE001 - config is optional
        return {}


def _tts_edge(text, out, lang, o):
    import asyncio

    import edge_tts

    voice = o.get("voice") or EDGE_VOICES[(lang, o.get("gender", "female"))]
    mp3 = Path(out).with_suffix(".mp3")
    asyncio.run(edge_tts.Communicate(text, voice, rate=o.get("rate", "+0%"), pitch=o.get("pitch", "+0Hz")).save(str(mp3)))
    if not mp3.exists() or mp3.stat().st_size == 0:
        raise RuntimeError("edge-tts returned no audio")
    return mp3, f"edge {voice}"


def _tts_xtts(text, out, lang, o):
    if not XTTS_PY.exists():
        raise RuntimeError("XTTS venv missing (run scripts/setup_tts.ps1)")
    wav = o.get("speaker_wav") or "-"
    cmd = [str(XTTS_PY), str(XTTS_SCRIPT), text, lang, str(out), str(wav), XTTS_SPEAKERS[o.get("gender", "female")]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not Path(out).exists():
        raise RuntimeError("xtts failed: " + (proc.stderr.strip().splitlines() or ["?"])[-1])
    return Path(out), "xtts" + (" cloned voice" if o.get("speaker_wav") else "")


def _tts_piper(text, out, lang, o):
    voice_file = PIPER_DIR / f"{PIPER_VOICES[lang]}.onnx"
    if not voice_file.exists():
        raise RuntimeError(f"missing voice model {voice_file.name} (models/piper/)")
    from piper import PiperVoice

    with wave.open(str(out), "wb") as wf:
        PiperVoice.load(str(voice_file)).synthesize_wav(text, wf)
    return Path(out), f"piper {PIPER_VOICES[lang]}"


def synthesize_voiceover(text: str, out_wav: Path, lang: str | None = None, options: dict | None = None) -> tuple[Path | None, str]:
    """Voiceover with fallbacks: edge (natural, online) -> xtts (offline, cloning) -> piper (basic). Never raises."""
    o = {**audio_settings(), **(options or {})}
    lang = lang or o.get("language") or detect_lang(text)
    o["gender"] = o.get("gender") or o.get("voice_gender") or "female"
    choice = str(o.get("voice_engine", "auto")).lower()
    order = ENGINE_ORDER if choice in ("auto", "") else [choice] + [e for e in ENGINE_ORDER if e != choice]
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    for engine in order:
        fn = {"edge": _tts_edge, "xtts": _tts_xtts, "piper": _tts_piper}.get(engine)
        if fn is None:
            continue
        try:
            path, label = fn(text, out_wav, lang, o)
            return path, f"voiceover ({label}, {lang})" + (f" [fallback: {'; '.join(failed)}]" if failed else "")
        except Exception as exc:  # noqa: BLE001 - TTS must never break a render
            failed.append(f"{engine}: {str(exc)[:80]}")
    return None, "voiceover skipped: " + "; ".join(failed)


def resolve_audio(
    ffmpeg: str,
    duration: float,
    work_dir: Path,
    slug: str,
    music: str | Path | None = None,
    prompt: str = "",
    voiceover_text: str | None = None,
    music_dir: Path = MUSIC_DIR,
    voice_options: dict | None = None,
) -> tuple[Path | None, str]:
    """music: 'auto' (library pick by mood), 'none'/None (no music), or a file path. Returns (audio, note)."""
    track, mood, ai_note = None, "", ""
    if music and str(music).lower() == "auto":
        track, mood = pick_music(prompt, music_dir)
        if track is None and audio_settings().get("ai_music", True):  # library empty -> MusicGen (cached by mood)
            try:
                track, mood = generate_music(prompt, duration, Path(work_dir) / f"{slug}_aimusic.wav"), "ai-musicgen"
            except Exception as exc:  # noqa: BLE001 - never break a render for music
                ai_note = f"ai music skipped: {str(exc)[:80]}"
    elif music and str(music).lower() != "none":
        track = Path(music)
        if not track.is_file():
            raise FileNotFoundError(f"music file not found: {track}")
    voice, vnote = (synthesize_voiceover(voiceover_text, Path(work_dir) / f"{slug}_voice.wav", options=voice_options)
                    if voiceover_text else (None, ""))
    if not track and not voice:
        return None, vnote or ai_note or "silent"
    mixed = mix_audio(ffmpeg, Path(work_dir) / f"{slug}_audio.wav", duration, track, voice)
    parts = ([f"music:{track.name}" + (f" ({mood})" if mood else "")] if track else []) + ([vnote] if voice else [])
    return mixed, " + ".join(parts) + (f" | {vnote}" if vnote and not voice else "")


def voice_duration(work_dir: Path, slug: str, ffprobe: str = "ffprobe") -> float | None:
    """Length of the generated voiceover file for this slug, if any (used to time captions)."""
    for f in Path(work_dir).glob(f"{slug}_voice.*"):
        proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(f)],
                              capture_output=True, text=True, timeout=30)
        try:
            return float(proc.stdout.strip())
        except ValueError:
            return None
    return None
