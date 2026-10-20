"""One-command automation: topics -> LLM prompt+caption -> image -> 15s reel -> validated MP4 + JSON sidecar.

    python -m aurora.autorun --topics topics.txt [--music song.mp3] [--voiceover] [--providers local_comfyui]

Sequential, low priority (PC stays usable) and resumable: topics with an ok sidecar are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yaml

from aurora import ensure_utf8_console
from aurora.monitoring.media_validator import validate_image, validate_video
from aurora.pipeline import run as render_reel

ROOT = Path(__file__).resolve().parents[1]
LLM_PROMPT = (
    'Topic: "{topic}"\nWrite one detailed text-to-image prompt for a vertical portrait photo '
    '(subject, setting, lighting, camera) and a short social media caption (max 120 chars, may include 2 hashtags).\n'
    'Reply ONLY with JSON: {{"image_prompt": "...", "caption": "..."}}'
)
QUALITY_SUFFIX = ", masterpiece, best quality, sharp focus, natural lighting"


def slugify(topic: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:48] or "topic"
    return f"{base}-{hashlib.sha1(topic.encode('utf-8')).hexdigest()[:6]}"


def read_topics(path: Path) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def is_done(out_root: Path, slug: str) -> Path | None:
    for sidecar in sorted(out_root.glob(f"*/{slug}.json")):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("status") == "ok" and sidecar.with_suffix(".mp4").exists():
            return sidecar
    return None


def write_prompt_and_caption(cfg: dict, topic: str) -> dict:
    """LLM (Ollama via LLMManager) writes prompt+caption; falls back to the topic text if unavailable."""
    fallback = {"image_prompt": topic + QUALITY_SUFFIX, "caption": topic, "source": "topic_fallback"}
    try:
        from src.modules.llm_manager import LLMManager

        llm = LLMManager(cfg)
        if not llm._server_alive():
            print("[autorun] LLM not running -> using topic text as prompt")
            return fallback
        import requests

        tags = requests.get(llm.endpoint.rsplit("/api/", 1)[0] + "/api/tags", timeout=3).json().get("models", [])
        names = [m.get("name", "") for m in tags]
        if names and llm.model_name not in names:
            print(f"[autorun] model {llm.model_name} not installed -> using {names[0]}")
            llm.model_name = names[0]
        data = llm._call_ollama(LLM_PROMPT.format(topic=topic))
        prompt, caption = str(data.get("image_prompt", "")).strip(), str(data.get("caption", "")).strip()
        if len(prompt) < 10:
            return fallback
        return {"image_prompt": prompt, "caption": caption or topic, "source": "llm"}
    except Exception as exc:  # noqa: BLE001 - any LLM failure must not stop the batch
        print(f"[autorun] LLM failed ({exc}) -> using topic text as prompt")
        return fallback


def make_voiceover(cfg: dict, caption: str, slug: str, music: Path | None, ffmpeg: str) -> tuple[Path | None, str]:
    """Optional XTTS voiceover (CPU). Returns (audio path or music, note). Skips gracefully."""
    try:
        from src.modules.audio_engine import AudioEngine

        if not AudioEngine.check_installation():
            return music, "voiceover skipped: XTTS not installed"
        engine = AudioEngine(cfg)
        if not engine.ref_audio.exists():
            return music, f"voiceover skipped: missing {engine.ref_audio}"
        wav = engine.synthesize(caption, f"{slug}.wav")
        engine.unload()
    except Exception as exc:  # noqa: BLE001
        return music, f"voiceover skipped: {exc}"
    if not music:
        return wav, "voiceover"
    mixed = wav.with_name(f"{slug}_mix.wav")
    cmd = [ffmpeg, "-y", "-i", str(wav), "-i", str(music), "-filter_complex",
           "[1:a]volume=0.25[m];[0:a][m]amix=inputs=2:duration=first[a]", "-map", "[a]", str(mixed)]
    if subprocess.run(cmd, capture_output=True, text=True, timeout=300).returncode == 0:
        return mixed, "voiceover+music"
    return wav, "voiceover (music mix failed)"


def process_topic(cfg: dict, switchboard, topic: str, args: argparse.Namespace, out_dir: Path) -> dict:
    slug = slugify(topic)
    started = time.perf_counter()
    job_id = f"autorun_{datetime.now():%Y%m%d_%H%M%S}_{slug[:20]}"
    text = (write_prompt_and_caption(cfg, topic) if not args.no_llm else
            {"image_prompt": topic + QUALITY_SUFFIX, "caption": topic, "source": "topic_fallback"})
    t_llm = time.perf_counter() - started

    t0 = time.perf_counter()
    image = switchboard.generate(text["image_prompt"], args.style, [args.width, args.height], job_id)
    t_img = time.perf_counter() - t0
    image_report = validate_image(image.output_path, image.width, image.height)
    if not image_report.ok:
        raise RuntimeError(f"image failed validation: {image_report.errors}")

    from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

    audio, audio_note = (Path(args.music) if args.music else None), ("music" if args.music else "silent")
    if args.voiceover:
        audio, audio_note = make_voiceover(cfg, text["caption"], slug, audio, FFmpegKenBurns().ffmpeg)

    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{slug}.mp4"
    t0 = time.perf_counter()
    render_reel(Path(image.output_path), mp4, args.duration, args.fps, 1080, 1920, audio)
    t_reel = time.perf_counter() - t0
    video_report = validate_video(mp4, 1080, 1920, args.fps, args.duration, require_audio=True,
                                  deep=True, input_path=image.output_path)
    if not video_report.ok:
        raise RuntimeError(f"reel failed validation: {video_report.errors}")

    clash = next((c for c in out_dir.parent.glob("*/*.json")
                  if c.stem != slug and _sha(c) == video_report.sha256), None)
    if clash:
        raise RuntimeError(f"reel is byte-identical to another topic's output: {clash.name}")
    sidecar = {
        "status": "ok", "engine_mode": "real", "topic": topic, "slug": slug, "created_at": datetime.now().isoformat(timespec="seconds"),
        "caption": text["caption"], "image_prompt": text["image_prompt"], "prompt_source": text["source"],
        "image": {"provider": image.provider, "model": image.model, "seed": image.seed, "path": str(image.output_path),
                  "width": image.width, "height": image.height, "adjustments": (image.context or {}).get("adjustments", [])},
        "reel": {"path": str(mp4), "width": video_report.width, "height": video_report.height, "fps": video_report.fps,
                 "duration_secs": video_report.duration_secs, "has_audio": video_report.has_audio,
                 "md5": video_report.md5, "sha256": video_report.sha256},
        "audio": audio_note,
        "timings_secs": {"llm": round(t_llm, 1), "image": round(t_img, 1), "reel": round(t_reel, 1),
                         "total": round(time.perf_counter() - started, 1)},
    }
    mp4.with_suffix(".json").write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    return sidecar


def _sha(sidecar: Path) -> str:
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")).get("reel", {}).get("sha256", "")
    except (OSError, ValueError):
        return ""


def lower_priority() -> None:
    try:
        import psutil

        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception:  # noqa: BLE001 - best effort
        pass


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_console()
    ap = argparse.ArgumentParser(description="Aurora autorun: topics -> reels")
    ap.add_argument("--topics", type=Path, required=True, help="text file, one topic per line")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs" / "final")
    ap.add_argument("--music", type=Path, default=None)
    ap.add_argument("--voiceover", action="store_true", help="XTTS voiceover of the caption (CPU; skipped if unavailable)")
    ap.add_argument("--providers", default=None, help="comma list overriding image fallback_order, e.g. local_comfyui")
    ap.add_argument("--style", default="cinematic")
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--no-llm", action="store_true", help="skip the LLM; use the topic text as prompt and caption")
    ap.add_argument("--limit", type=int, default=0, help="max topics to render this run (0 = all)")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.providers:
        cfg.setdefault("providers", {})["fallback_order"] = [p.strip() for p in args.providers.split(",")]
    from backend.app.services.image_switchboard import ImageSwitchboard

    lower_priority()
    switchboard = ImageSwitchboard(cfg)
    out_dir = args.out / date.today().isoformat()
    done = skipped = 0
    failed: list[tuple[str, str]] = []
    for topic in read_topics(args.topics):
        slug = slugify(topic)
        if is_done(args.out, slug):
            print(f"[autorun] skip (done): {topic}")
            skipped += 1
            continue
        if args.limit and done >= args.limit:
            break
        print(f"[autorun] >>> {topic}")
        try:
            info = process_topic(cfg, switchboard, topic, args, out_dir)
            print(f"[autorun] OK {info['reel']['path']} ({info['timings_secs']['total']}s)")
            done += 1
        except Exception as exc:  # noqa: BLE001 - keep going with the next topic
            print(f"[autorun] FAILED {topic}: {exc}")
            failed.append((topic, str(exc)))
    print(f"[autorun] done={done} skipped={skipped} failed={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
