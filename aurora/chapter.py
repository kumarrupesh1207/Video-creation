"""Chapter compilation: all parts of a chapter -> ONE 16:9 1920x1080 long video (~5 min).

Reuses the already rendered Shorts (their cached panels, narration and music): each part's story section is cut out using the stored
`panel_times` (cover and end card dropped) and reframed 9:16 -> 16:9 with a blurred-background fill. No new images, no NIM credits.
Adds a chapter title card + a finale card, chapter markers (one per part), an SRT, a thumbnail and a sidecar for the publisher.
Failure never affects the parts; retry with `python -m aurora.chapter compile --channel <name>`.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from aurora import comic, episode, research
from aurora.monitoring.media_validator import validate_image, validate_video

ROOT = Path(__file__).resolve().parents[1]
FINAL_ROOT = ROOT / "outputs" / "final"
CW, CH, FPS = 1920, 1080, 24
CARD_S, FINALE_S = 3.0, 4.0
IST = timezone(timedelta(hours=5, minutes=30))
SLOT = {"weekday": 5, "hour": 18, "minute": 0}  # Saturday 18:00 IST
MIN_LEAD_H = 6  # a compilation is never due sooner than this after the chapter completes (upload-ahead window)
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "8M", "-bufsize", "16M", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]


def next_saturday_1800(now: datetime | None = None, lead_h: int = MIN_LEAD_H) -> datetime:
    """The next Saturday 18:00 IST that is at least `lead_h` hours after `now` (a naive `now` is read as IST)."""
    now = (now or datetime.now(IST))
    now = now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    day = now.replace(hour=SLOT["hour"], minute=SLOT["minute"], second=0, microsecond=0)
    day += timedelta(days=(SLOT["weekday"] - day.weekday()) % 7)
    while day < now + timedelta(hours=lead_h):
        day += timedelta(days=7)
    return day


def _run(cmd: list[str], timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + " | ".join(proc.stderr.strip().splitlines()[-4:]))


def _clean_title(title: str, number: int) -> str:
    return re.sub(rf"^chapter\s*{number}\s*[:.\-–]?\s*", "", title or "", flags=re.I).strip() or title


def story_window(side: dict) -> tuple[float, float]:
    """(start, end) seconds of a part's story panels inside its Short: from panel 1 (its voice starts 0.05 s later, so the cut keeps
    the cover->panel blend rather than clip the first word) to the end of the last panel, before the end card."""
    pt = side["panel_times"]
    return float(pt[0]["start"]), float(pt[-1]["end"])


def reframe_part(ffmpeg: str, video: Path, a: float, b: float, out: Path) -> Path:
    """Cut [a, b] and reframe 9:16 -> 1920x1080: the portrait video centred over a blurred, darkened, enlarged copy of itself."""
    graph = (f"[0:v]split[s0][s1];[s0]scale={CW}:{CH}:force_original_aspect_ratio=increase,crop={CW}:{CH},boxblur=36:4,eq=brightness=-0.18[bg];"
             f"[s1]scale=-2:{CH}[fg];[bg][fg]overlay=(W-w)/2:0,setsar=1[v]")
    _run([ffmpeg, "-y", "-v", "error", "-i", str(video), "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-filter_complex", graph,
          "-map", "[v]", "-map", "0:a:0", *ENC, "-movflags", "+faststart", str(out)])
    return out


def make_card(ffmpeg: str, bg_image: Path, lines: list[tuple[str, int, tuple]], seconds: float, out: Path) -> Path:
    """A title/finale card: blurred dark panel + centred text lines [(text, size, rgb)], with silent audio (so clips concat cleanly)."""
    im = Image.open(bg_image).convert("RGB")
    s = max(CW / im.width, CH / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    im = im.crop(((im.width - CW) // 2, (im.height - CH) // 2, (im.width - CW) // 2 + CW, (im.height - CH) // 2 + CH))
    im = Image.blend(im.filter(ImageFilter.GaussianBlur(14)), Image.new("RGB", (CW, CH), (0, 0, 0)), 0.55)
    d = ImageDraw.Draw(im)
    wrapped = []
    for text, size, rgb in lines:
        f = comic._font(size, text)
        for ln in comic._balanced(d, text, f, 1500):
            wrapped.append((ln, f, size, rgb))
    y = (CH - sum(int(w[2] * 1.2) for w in wrapped)) // 2
    for ln, f, size, rgb in wrapped:
        w = d.textlength(ln, font=f)
        d.text(((CW - w) / 2, y), ln, font=f, fill=rgb, stroke_width=max(3, size // 14), stroke_fill=(0, 0, 0))
        y += int(size * 1.2)
    png = Path(out).with_suffix(".png")
    im.save(png)
    _run([ffmpeg, "-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(png), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
          "-t", f"{seconds:.2f}", *ENC, "-movflags", "+faststart", str(out)])
    return out


def _chapter_state(channel: str, number: int, series_dir: Path) -> tuple[dict, dict]:
    state = research.load_series(channel, series_dir)
    ch = (state.get("chapters") or {}).get(str(number))
    if not ch:
        cur = state.get("chapter")
        ch = cur if cur and int(cur.get("number", 0)) == number else None
    if not ch or not ch.get("complete"):
        raise RuntimeError(f"chapter {number} of '{channel}' is not complete yet ({(ch or {}).get('parts_done', 0)}/{(ch or {}).get('parts', '?')} parts)")
    return state, ch


def compile_chapter(channel: str, number: int, cfg: dict | None = None, series_dir: Path = research.SERIES_DIR, out_root: Path | None = None,
                    now: datetime | None = None, ffmpeg: str | None = None, force: bool = False) -> dict:
    """Build the chapter video + SRT + thumbnail + sidecar from the rendered parts. Idempotent: an existing result is returned unless `force`."""
    state, ch = _chapter_state(channel, number, series_dir)
    series = state.get("series_title") or state.get("title") or "Series"
    out_dir = Path(out_root or FINAL_ROOT) / channel / "chapters" / f"ch{number}"
    done = out_dir / "chapter.json"
    if done.exists() and not force:
        return json.loads(done.read_text(encoding="utf-8"))
    if ffmpeg is None:
        from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

        ffmpeg = FFmpegKenBurns().ffmpeg
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)
    parts = []
    for r in sorted(ch["rendered"], key=lambda r: int(r["part"])):
        side = json.loads(Path(r["sidecar"]).read_text(encoding="utf-8"))
        if "panel_times" not in side or not Path(r["video"]).exists():
            raise RuntimeError(f"part {r['part']} has no panel_times / video file; it cannot be compiled")
        parts.append((r, side))
    if [int(r["part"]) for r, _ in parts] != list(range(1, int(ch["parts"]) + 1)):
        raise RuntimeError(f"chapter {number} parts are not 1..{ch['parts']}: {[r['part'] for r, _ in parts]}")
    first_img = Path(parts[0][1]["panel_images"][0])
    ctitle = _clean_title(ch["title"], number)
    pieces, times, marks, t = [], [], [], 0.0
    card = make_card(ffmpeg, first_img, [(series.upper(), 64, (255, 214, 80)), (f"CHAPTER {number}", 110, (255, 255, 255)), (ctitle, 72, (235, 235, 235))],
                     CARD_S, work / "card_open.mp4")
    pieces.append(card)
    t += CARD_S
    for r, side in parts:
        a, b = story_window(side)
        clip = reframe_part(ffmpeg, Path(r["video"]), a, b, work / f"part{int(r['part']):02d}.mp4")
        pieces.append(clip)
        hook = (side.get("hook") or "").strip().rstrip(".")
        marks.append({"start": 0.0 if not marks else round(t, 2), "title": f"Part {r['part']}" + (f": {hook}" if hook else "")})
        for pt in side["panel_times"]:  # global seconds of the compilation = piece start + (voice time - cut start)
            times.append({**pt, "voice_start": round(t + pt["voice_start"] - a, 2), "voice_end": round(t + pt["voice_end"] - a, 2)})
        t += b - a
    nxt = int(ch["number"]) + 1
    pieces.append(make_card(ffmpeg, Path(parts[-1][1]["panel_images"][-1]), [(f"Chapter {number} finale", 100, (255, 255, 255)),
                                                                           (f"Chapter {nxt} is coming", 60, (255, 214, 80))], FINALE_S, work / "card_end.mp4"))
    total = t + FINALE_S
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in pieces), encoding="utf-8")
    video = out_dir / f"{channel}-ch{number}.mp4"
    _run([ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(video)])
    srt = out_dir / f"{channel}-ch{number}.srt"
    srt.write_text(episode.build_srt(times), encoding="utf-8")
    panels, images = [], []
    for _, side in parts:
        panels += side["panels"]
        images += [Path(p) for p in side["panel_images"]]
    ti = episode.pick_thumbnail_panel(panels, images)
    thumb = episode.make_thumbnail(images[ti], f"{series}: {ctitle}", out_dir / "thumbnail.jpg", number, label="CHAPTER")
    rep = validate_video(video, CW, CH, fps=FPS, require_audio=True)
    if not rep.ok:
        raise RuntimeError(f"chapter video failed validation: {rep.errors}")
    thumb_rep = validate_image(thumb, 1280, 720)
    hint = next_saturday_1800(now)
    title = f"{series} – Chapter {number}: {ctitle}"
    desc = (f"{ch.get('summary', '')}\n\nThe full Chapter {number} of {series} in one go ({len(parts)} parts).\n\n" + episode.fmt_chapters(marks)).strip()
    sidecar = {"status": "ok", "format": "chapter", "kind": "chapter", "channel": channel, "title": title, "series": series, "playlist": series,
               "chapter": number, "chapter_title": ctitle, "parts": len(parts), "description": desc,
               "keywords": [series, "anime story", "motion comic", f"chapter {number}", "full chapter"],
               "hashtags": ["#anime", "#story", "#motioncomic"], "chapters": marks, "chapters_text": episode.fmt_chapters(marks),
               "captions_srt": str(srt), "thumbnail": str(thumb), "thumbnail_ok": thumb_rep.ok, "thumbnail_panel": ti,
               "video": {"path": str(video), "width": rep.width, "height": rep.height, "fps": rep.fps, "duration_secs": rep.duration_secs,
                         "has_audio": rep.has_audio, "sha256": rep.sha256},
               "part_videos": [r["video"] for r, _ in parts], "planned_secs": round(total, 1), "nim_credits": 0,
               "publish_at_hint": hint.isoformat(), "publish_slot": "saturday 18:00 IST"}
    done.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    state = research.load_series(channel, series_dir)  # re-read: record where the compilation lives
    target = (state.get("chapters") or {}).get(str(number))
    if target is not None:
        target["compilation"] = str(done)
        research._write_state(channel, state, series_dir)
    return sidecar


def pending(channel: str, series_dir: Path = research.SERIES_DIR) -> list[int]:
    """Completed chapters whose compilation has not been built yet."""
    state = research.load_series(channel, series_dir)
    return [int(n) for n, ch in sorted((state.get("chapters") or {}).items(), key=lambda kv: int(kv[0]))
            if ch.get("complete") and not (ch.get("compilation") and Path(ch["compilation"]).exists())]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chapter compilations (16:9) from rendered parts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="build the compilation of a completed chapter (default: every pending one)")
    c.add_argument("--channel", required=True)
    c.add_argument("--chapter", type=int)
    c.add_argument("--force", action="store_true")
    p = sub.add_parser("pending", help="list completed chapters without a compilation")
    p.add_argument("--channel", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "pending":
        print(json.dumps(pending(a.channel)))
        return 0
    import yaml

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    for n in ([a.chapter] if a.chapter else pending(a.channel)):
        res = compile_chapter(a.channel, n, cfg, force=a.force)
        print(f"chapter {n}: {res['video']['path']} ({res['video']['duration_secs']}s) publish_at_hint={res['publish_at_hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
