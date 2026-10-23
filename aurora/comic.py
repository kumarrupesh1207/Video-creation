"""Motion-comic reel: panel images + per-panel voice -> 9:16 1080x1920 video (free: images only, no video model).

    make_comic_reel(channel, premise, out_dir)  # research -> images -> render -> validate -> sidecar -> series state

Each panel lasts its narration audio + 0.4 s, with Ken-Burns motion (punch zoom for action panels), a white panel
border, paper grain, a narration box and an optional speech bubble. Panels join with xfade page-turn transitions.
Cover card first, "To be continued... Part N+1" end card last. Voice: per-panel Edge TTS; music: ducked MusicGen.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from aurora import comic_layout, research
from aurora.audio import generate_music, mix_audio, synthesize_voiceover
from aurora.costs import COST_DIR, credits_today, log_credits
from aurora.monitoring.media_validator import _ffprobe, validate_video

ROOT = Path(__file__).resolve().parents[1]
W, H, FPS, XF, PAD = 1080, 1920, 24, 0.4, 0.4
COVER_S, END_S = 2.4, 3.0
MAX_NARRATION_WORDS = 12  # keeps a 6-panel reel at ~28-34 s
PANEL_MIN_S = 4.4  # hold at least this long per panel (voice is never slowed; the rest is a pause)
NO_TEXT_NEG = "text, letters, words, speech bubble, caption, watermark, signature, logo, subtitles"
TRANSITIONS = ["slideleft", "wipeleft", "circleopen", "slideright", "wiperight", "smoothleft"]
MOTIONS = ["zoom_in", "pan_left", "zoom_out", "pan_right"]
FONT_DIR = Path("C:/Windows/Fonts")
LATIN_FONTS = ["comicbd.ttf", "arialbd.ttf", "segoeuib.ttf"]
DEVA_FONTS = ["NirmalaB.ttf", "Nirmala.ttf"]


def _font(size: int, text: str = "") -> ImageFont.FreeTypeFont:
    names = DEVA_FONTS if re.search(r"[\u0900-\u097F]", text) else LATIN_FONTS
    for n in names:
        if (FONT_DIR / n).exists():
            return ImageFont.truetype(str(FONT_DIR / n), size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _text_block(draw, text, font, max_w, line_gap=10):
    lines = _wrap(draw, text, font, max_w)
    h = sum(font.getbbox(l)[3] - font.getbbox(l)[1] + line_gap for l in lines)
    w = max((draw.textlength(l, font=font) for l in lines), default=0)
    return lines, int(w), int(h)


def make_overlay(path: Path, narration: str = "", bubble: str = "", title: str = "", subtitle: str = "", border: bool = True,
                 faces: list | None = None) -> Path:
    """Transparent 1080x1920 PNG: panel border, narration box (top), speech bubble (lower), or title-card text."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if border:
        d.rectangle([18, 18, W - 18, H - 18], outline=(255, 255, 255, 255), width=9)
    if narration:
        f = _font(50, narration)
        lines, tw, th = _text_block(d, narration, f, 880)
        x0, y0 = comic_layout.place_narration(tw + 56, th + 44, faces) if faces else (60, 70)  # top or bottom edge, off the faces
        narr_box = (x0, y0, x0 + tw + 56, y0 + th + 44)
        d.rectangle([x0, y0, x0 + tw + 56, y0 + th + 44], fill=(255, 243, 196, 245), outline=(20, 20, 20, 255), width=5)
        y = y0 + 22
        for l in lines:
            d.text((x0 + 28, y), l, font=f, fill=(20, 20, 20, 255))
            y += f.getbbox(l)[3] - f.getbbox(l)[1] + 10
    if bubble:
        f = _font(58, bubble)
        lines, tw, th = _text_block(d, bubble, f, 700)
        bw, bh = tw + 120, th + 90
        if faces:
            x0, y0, target = comic_layout.place_bubble(bw, bh, faces, blocked=[narr_box] if narration else [])
        else:
            x0, y0, target = (W - bw) // 2, 1180, None
        d.ellipse([x0, y0, x0 + bw, y0 + bh], fill=(255, 255, 255, 250), outline=(20, 20, 20, 255), width=6)
        tail = [(x0 + bw * 0.30, y0 + bh - 12), (x0 + bw * 0.22, y0 + bh + 95), (x0 + bw * 0.45, y0 + bh - 6)]
        if target:  # tail leaves the bubble edge facing the speaker and stops short of the face (never over it)
            cx, cy = x0 + bw / 2, y0 + bh / 2
            dx, dy = target[0] - cx, target[1] - cy
            dist = max((dx * dx + dy * dy) ** 0.5, 1.0)
            ux, uy = dx / dist, dy / dist
            edge = 1.0 / (((ux / (bw / 2)) ** 2 + (uy / (bh / 2)) ** 2) ** 0.5)  # ellipse radius along (ux, uy)
            base = (cx + ux * (edge - 6), cy + uy * (edge - 6))
            L = max(45.0, min(110.0, dist - edge - 50))
            tip = (base[0] + ux * L, base[1] + uy * L)
            px, py = -uy * 24, ux * 24
            tail = [(base[0] + px, base[1] + py), tip, (base[0] - px, base[1] - py)]
        d.polygon(tail, fill=(255, 255, 255, 250))
        d.line([tail[0], tail[1], tail[2]], fill=(20, 20, 20, 255), width=6)
        d.line([(tail[0][0] + 2, tail[0][1] + 1), (tail[2][0] - 2, tail[2][1] + 1)], fill=(255, 255, 255, 255), width=10)
        y = y0 + 45
        for l in lines:
            d.text((x0 + (bw - d.textlength(l, font=f)) / 2, y), l, font=f, fill=(20, 20, 20, 255))  # centred in the bubble
            y += f.getbbox(l)[3] - f.getbbox(l)[1] + 10
    if title:
        ft, fs = _font(112, title), _font(70, subtitle)
        lines, tw, th = _text_block(d, title, ft, 900, 18)
        y = 820 - th // 2
        for l in lines:
            d.text(((W - d.textlength(l, font=ft)) / 2, y), l, font=ft, fill=(255, 255, 255, 255), stroke_width=8, stroke_fill=(0, 0, 0, 255))
            y += ft.getbbox(l)[3] - ft.getbbox(l)[1] + 18
        if subtitle:
            for l in _wrap(d, subtitle, fs, 900):
                d.text(((W - d.textlength(l, font=fs)) / 2, y + 30), l, font=fs, fill=(255, 214, 80, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255))
                y += fs.getbbox(l)[3] - fs.getbbox(l)[1] + 12
    img.save(path)
    return path


def _zoompan(motion: str, frames: int) -> str:
    c = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    return {
        "zoom_in": f"z='min(1.0+0.12*on/{frames},1.12)':{c}",
        "zoom_out": f"z='max(1.12-0.12*on/{frames},1.0)':{c}",
        "pan_left": f"z=1.12:x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'",
        "pan_right": f"z=1.12:x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'",
        "punch": f"z='if(lt(on,9),1.0+0.025*on,1.225+0.05*on/{frames})':{c}",
    }[motion]


def _run(cmd: list[str], timeout: int = 900) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + " | ".join(proc.stderr.strip().splitlines()[-4:]))


def render_clip(ffmpeg: str, image: Path, overlay: Path, seconds: float, motion: str, out: Path, dim: bool = False) -> Path:
    frames = max(2, round(seconds * FPS))
    vf = (f"[0:v]scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,"
          f"zoompan={_zoompan(motion, frames)}:d={frames}:s={W}x{H}:fps={FPS},"
          f"noise=alls=7:allf=t,eq=saturation=1.08{',eq=brightness=-0.28' if dim else ''}[v];"
          f"[v][1:v]overlay=0:0,format=yuv420p[o]")
    _run([ffmpeg, "-y", "-v", "error", "-loop", "1", "-i", str(image), "-loop", "1", "-i", str(overlay), "-filter_complex", vf,
          "-map", "[o]", "-frames:v", str(frames), "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", str(out)])
    return out


def trim_words(text: str, n: int = MAX_NARRATION_WORDS) -> str:
    """At most n words, cut at the last sentence/clause end inside the limit when there is one."""
    words = text.split()
    if len(words) <= n:
        return text.strip()
    cut = " ".join(words[:n])
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(", "))
    return (cut[:m + 1] if m > len(cut) // 2 else cut).rstrip(",;: ") + ("" if cut.rstrip().endswith((".", "!", "?")) else ".")


def has_drawn_bubble(path: Path) -> bool:
    """Cheap safety net (no OCR installed): a big near-white, rounded region with several small dark marks inside
    (letters) is a speech bubble the image model drew by itself."""
    import cv2
    import numpy as np

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    img = cv2.resize(img, (400, int(400 * img.shape[0] / img.shape[1])))
    area_total = img.shape[0] * img.shape[1]
    white = cv2.morphologyEx((img > 195).astype("uint8") * 255, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white, 8)
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if not (0.004 * area_total < w * h < 0.25 * area_total) or area / float(w * h) < 0.55:
            continue
        roi = img[y:y + h, x:x + w]
        mask = (labels[y:y + h, x:x + w] == k).astype("uint8")
        cnts, _ = cv2.findContours(mask * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(mask)
        cv2.drawContours(filled, cnts, -1, 1, -1)  # the bubble shape with its letters filled in
        interior = cv2.erode(filled, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))  # drop the border / outside corners
        dark = ((roi < 110) & (interior > 0)).astype("uint8")
        m, _, st, _ = cv2.connectedComponentsWithStats(dark, 8)
        marks = [s for s in st[1:] if 3 <= s[4] <= 0.02 * area_total and s[2] < w * 0.35]
        if len(marks) >= 4:
            return True
    return False


def _duration(path: Path) -> float:
    return float(_ffprobe(Path(path))["format"]["duration"])


def render_comic(ffmpeg: str, comic: dict, images: list[Path], out_mp4: Path, work: Path, voice: str = "en-US-AvaNeural",
                 music: bool = True, language: str = "English", layout_aware: bool = True) -> dict:
    """Panels (+images) -> final mp4. Returns {"seconds", "voice_notes", "music"}."""
    work.mkdir(parents=True, exist_ok=True)
    panels, series = comic["panels"], comic["series"]
    part = series["part"]
    clips: list[tuple[Path, float]] = []  # (clip, seconds)
    voices: list[tuple[Path | None, float]] = []  # (voice file, start seconds)
    notes, t = [], 0.0

    cover = render_clip(ffmpeg, images[0], make_overlay(work / "ov_cover.png", title=series["title"], subtitle=f"PART {part}"),
                        COVER_S, "zoom_in", work / "clip_cover.mp4", dim=True)
    clips.append((cover, COVER_S))
    voices.append((None, 0.0))
    t += COVER_S - XF
    for i, (p, img) in enumerate(zip(panels, images)):
        p = {**p, "narration": trim_words(p["narration"])}
        vfile, note = synthesize_voiceover(p["narration"], work / f"voice_{i}.wav",
                                           options={"voice_engine": "edge", "voice": voice, "language": "hi" if language.lower().startswith("hi") else "en"})
        notes.append(note)
        secs = max((_duration(vfile) if vfile else 3.0) + PAD, PANEL_MIN_S)
        motion = "punch" if p.get("action") else MOTIONS[i % len(MOTIONS)]
        faces = None
        if layout_aware:
            try:
                with Image.open(img) as im0:
                    iw, ih = im0.size
                faces = comic_layout.faces_over_motion(comic_layout.detect_faces(Path(img)), iw, ih, motion)
            except Exception as exc:  # noqa: BLE001 - layout is best effort; fall back to the fixed layout
                notes.append(f"panel {i}: face detection skipped ({str(exc)[:60]})")
        ov = make_overlay(work / f"ov_{i}.png", narration=p["narration"], bubble=p.get("bubble", ""), faces=faces)
        clips.append((render_clip(ffmpeg, img, ov, secs, motion, work / f"clip_{i}.mp4"), secs))
        voices.append((vfile, t + 0.05))
        t += secs - XF
    final = part >= int(series.get("total_parts", 1))
    end_ov = make_overlay(work / "ov_end.png", title="The End" if final else "To be continued...",
                          subtitle="" if final else f"Part {part + 1} coming soon")
    clips.append((render_clip(ffmpeg, images[-1], end_ov, END_S, "zoom_out", work / "clip_end.mp4", dim=True), END_S))
    total = sum(s for _, s in clips) - XF * (len(clips) - 1)

    # video: xfade chain; audio: per-panel voices placed on the timeline, music ducked under them
    voice_track = None
    vlist = [(f, st) for f, st in voices if f]
    if vlist:
        ins, parts = [], []
        for k, (f, st) in enumerate(vlist):
            ins += ["-i", str(f)]
            parts.append(f"[{k}:a]aresample=44100,adelay={int(st * 1000)}|{int(st * 1000)}[a{k}]")
        graph = ";".join(parts) + f";{''.join(f'[a{k}]' for k in range(len(vlist)))}amix=inputs={len(vlist)}:normalize=0:duration=longest[a]"
        voice_track = work / "voice_track.wav"
        _run([ffmpeg, "-y", "-v", "error", *ins, "-filter_complex", graph, "-map", "[a]", "-t", f"{total:.2f}", str(voice_track)])
    music_wav = None
    if music:
        try:
            music_wav = generate_music("epic anime orchestral battle hero", min(total, 30), work / "music.wav")
        except Exception as exc:  # noqa: BLE001 - music is optional
            notes.append(f"music skipped: {str(exc)[:80]}")
    audio = None
    if voice_track or music_wav:
        audio = mix_audio(ffmpeg, work / "audio_mix.wav", total, music_wav, voice_track)

    inputs, fchain, last, offset = [], [], "[0:v]", 0.0
    for k, (c, _) in enumerate(clips):
        inputs += ["-i", str(c)]
    for k in range(1, len(clips)):
        offset += clips[k - 1][1] - XF
        tr = TRANSITIONS[(k - 1) % len(TRANSITIONS)]
        fchain.append(f"{last}[{k}:v]xfade=transition={tr}:duration={XF}:offset={offset:.3f}[x{k}]")
        last = f"[x{k}]"
    cmd = [ffmpeg, "-y", "-v", "error", *inputs]
    if audio:
        cmd += ["-i", str(audio)]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-filter_complex", ";".join(fchain), "-map", last, "-map", f"{len(clips)}:a", "-t", f"{total:.2f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "6M", "-bufsize", "12M", "-pix_fmt", "yuv420p", "-af", "apad", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-shortest", str(out_mp4)]
    _run(cmd, timeout=1800)
    return {"seconds": round(total, 2), "voice_notes": notes, "music": bool(music_wav)}


def _comfy_up(cfg: dict) -> bool:
    import requests

    try:
        url = cfg.get("models", {}).get("comfyui", {}).get("api_url", "http://127.0.0.1:8188")
        return requests.get(f"{url}/queue", timeout=3).ok
    except Exception:  # noqa: BLE001
        return False


def _negative(cfg: dict) -> str:
    base = cfg.get("providers", {}).get("local_comfyui", {}).get("negative_prompt", "lowres, bad anatomy, bad hands, extra fingers, deformed")
    return f"{base}, {NO_TEXT_NEG}"  # the image model must not draw its own bubbles/gibberish text


_SOFTEN = [(r"\b(blood\w*|gore|gory)\b", "crimson light"), (r"\b(kill\w*|murder\w*|stab\w*|impal\w*|decapitat\w*|slaughter\w*)\b", "defeat"),
           (r"\b(slash\w*|slice\w*|cut)\b", "swing at"), (r"\b(wound\w*|injur\w*|bleed\w*)\b", "weary"),
           (r"\b(corpse\w*|dead|death|dying|die)\b", "fallen"), (r"\b(scream\w*|agony|torture\w*)\b", "shout"),
           (r"\b(gun|rifle|shoot\w*)\b", "energy beam"), (r"\b(sexy|nude|naked|seductive)\b", "elegant")]


def soften_prompt(prompt: str, level: int = 1) -> str:
    """Rewrite violent/risky wording so NIM's safety filter accepts the panel (level 2 adds a family-friendly suffix)."""
    for pat, repl in _SOFTEN:
        prompt = re.sub(pat, repl, prompt, flags=re.I)
    return prompt + (", wholesome, safe for all ages, dramatic heroic pose, no violence" if level >= 2 else ", non-violent dramatic action")


def _comfy_panels(cfg, sheet, panels, first, seed, job, neg, ref_path=None):
    """ComfyUI panels [first:] conditioned on ONE reference image via IP-Adapter. Returns (images, timings, ref_path)."""
    import time as _t

    from backend.app.providers.local_comfyui import LocalComfyUIProvider

    ccfg = cfg.get("comic", {})
    prov, steps = LocalComfyUIProvider(cfg), int(ccfg.get("steps", 20))
    res = list(ccfg.get("comfy_resolution", [512, 896]))
    floor, mult = int(ccfg.get("comfy_timeout_sec", 1800)), float(ccfg.get("timeout_multiplier", 3))
    weight = float(cfg.get("providers", {}).get("local_comfyui", {}).get("reference_weight", 0.4))
    images, timings = [], []
    if ref_path is None:
        t0 = _t.time()
        ref_prompt = (f"{research.COMIC_STYLE}, {sheet[0]['name']}: {sheet[0]['description']}, upper body, looking at viewer, "
                      "simple plain background, character reference, vibrant colors, clean lineart")
        ref = prov.generate_with_params(ref_prompt, "anime", res, f"{job}_ref", negative_prompt=neg, seed=seed, steps=steps,
                                        timeout=max(floor, 3600))  # first image: no measurement yet -> generous
        ref_path = ref.output_path
        timings.append(round(_t.time() - t0, 1))
    for i in range(first, len(panels)):
        t0 = _t.time()
        limit = int(max(floor, mult * sorted(timings)[len(timings) // 2])) if timings else max(floor, 3600)
        r = prov.generate_with_reference(ref_path, research.panel_prompt(panels[i], sheet), "anime", res, f"{job}_p{i}",
                                         negative_prompt=neg, seed=seed, steps=steps, weight=weight, timeout=limit)
        images.append(Path(r.output_path))
        timings.append(round(_t.time() - t0, 1))
    return images, timings, ref_path


def _panel_images(cfg, switchboard, comic, seed, job, nim_res, image_mode=None):
    """Panel images. image_mode (config comic.image_mode): "nim" (default: Flux via NIM; ComfyUI+IP-Adapter only if NIM
    fails/is capped), "auto"/"comfy" (ComfyUI first; "comfy" never touches NIM). Every fallback is explicit and logged."""
    import time as _t

    sheet, panels = comic["character_sheet"], comic["panels"]
    mode = image_mode or cfg.get("comic", {}).get("image_mode", "nim")
    neg, cost_dir = _negative(cfg), Path(cfg.get("cost_dir", COST_DIR))
    cap_reel = int(cfg.get("comic", {}).get("max_nim_credits_per_reel", 8))
    cap_day = int(cfg.get("providers", {}).get("nvidia_nim", {}).get("max_credits_per_day", 10))

    retries_left = [2]

    def nim_panels(images, timings, start=0):  # fills the lists in place so finished panels survive a mid-run failure
        saved = getattr(switchboard, "fallback_order", None)
        if saved is not None:
            switchboard.fallback_order = ["nvidia_nim"]  # NIM only here: the ComfyUI fallback below uses the reference
        try:
            for i in range(start, len(panels)):
                if len(images) >= cap_reel:
                    raise RuntimeError(f"comic.max_nim_credits_per_reel={cap_reel} reached")
                if credits_today("nvidia_nim", cost_dir) >= cap_day:
                    raise RuntimeError(f"daily NIM cap {cap_day} credits reached")
                t0 = _t.time()
                prompt = research.panel_prompt(panels[i], sheet)
                for attempt in range(3):  # CONTENT_FILTERED -> retry with softened wording (each call may cost a credit)
                    try:
                        r = switchboard.generate(prompt, "anime", list(nim_res), f"{job}_p{i}_{attempt}" if attempt else f"{job}_p{i}",
                                                 negative_prompt=neg, seed=seed)
                    except Exception as exc:  # noqa: BLE001
                        log_credits("nvidia_nim", 1, cost_dir)
                        if "CONTENT_FILTERED" in str(exc) and attempt < 2:
                            prompt = soften_prompt(prompt, attempt + 1)
                            print(f"[comic] panel {i}: NIM safety filter, retrying with softened prompt (attempt {attempt + 2}); "
                                  f"prompt was: {prompt[:300]}")
                            continue
                        raise
                    break
                if getattr(r, "provider", "") == "nvidia_nim" and getattr(r, "latency_ms", 1):  # cache hits cost nothing
                    log_credits("nvidia_nim", 1, cost_dir)
                    try:
                        drawn = has_drawn_bubble(Path(r.output_path))
                    except Exception:  # noqa: BLE001 - the check is a best-effort net
                        drawn = False
                    if drawn and retries_left[0] > 0:  # at most 2 extra (counted) regenerations per reel
                        retries_left[0] -= 1
                        print(f"[comic] panel {i}: image-drawn speech bubble detected, regenerating once with a new seed")
                        r2 = switchboard.generate(prompt + ", wordless illustration, clean art, no speech balloons, no lettering",
                                                  "anime", list(nim_res), f"{job}_p{i}_retry", negative_prompt=neg, seed=seed + 1000 + i)
                        log_credits("nvidia_nim", 1, cost_dir)
                        r = r2
                images.append(Path(r.output_path))
                timings.append(round(_t.time() - t0, 1))
        finally:
            if saved is not None:
                switchboard.fallback_order = saved

    if mode == "nim":
        images, timings = [], []
        try:
            nim_panels(images, timings)
            return images, timings, "NIM (Flux), consistent via fixed seed + character sheet text", "nim"
        except Exception as exc:  # noqa: BLE001 - quota/credit/API failure -> ComfyUI for the REMAINING panels
            why = f"NIM failed after {len(images)}/{len(panels)} panels ({str(exc)[:120]})"
            print(f"[comic] FALLBACK to ComfyUI IP-Adapter: {why}")
            ref = images[0] if images else None
            more, t2, _ = _comfy_panels(cfg, sheet, panels, len(images), seed, job, neg, ref_path=ref)
            return (images + more, timings + t2, f"{why}; remaining panels via ComfyUI character reference + IP-Adapter "
                    "(look may differ from the NIM panels)", "nim+comfy_fallback")
    if mode in ("auto", "comfy") and _comfy_up(cfg):
        try:
            images, timings, _ = _comfy_panels(cfg, sheet, panels, 0, seed, job, neg)
            return images, timings, "ComfyUI character reference + IP-Adapter (consistent look)", "comfy_ipadapter"
        except Exception as exc:  # noqa: BLE001
            if mode == "comfy":
                raise
            note = f"ComfyUI failed ({str(exc)[:80]}); "
    elif mode == "comfy":
        raise RuntimeError("image_mode=comfy but ComfyUI is not reachable")
    else:
        note = ""
    # auto mode: ComfyUI unavailable -> NIM, explicit and capped
    need = len(panels)
    if need > cap_reel or credits_today("nvidia_nim", cost_dir) + need > cap_day:
        raise RuntimeError(f"{note}NIM fallback would use {need} credits (reel cap {cap_reel}, daily cap {cap_day}, used today "
                           f"{credits_today('nvidia_nim', cost_dir)}); start ComfyUI or raise the cap")
    print(f"[comic] FALLBACK to NIM for {need} panels ({note.strip() or 'ComfyUI not reachable'})")
    images, timings = [], []
    nim_panels(images, timings)
    return (images, timings, f"{note or 'ComfyUI not reachable; '}NIM fallback used {need} credits: character consistency may drop "
            "between panels", "nim_fallback")


def make_comic_reel(channel: str, premise: str, out_dir: Path, cfg: dict | None = None, language: str = "English",
                    panels: int = 6, voice: str = "en-US-AvaNeural", comic: dict | None = None, switchboard=None,
                    music: bool = True, series_dir: Path | None = None, resolution: tuple[int, int] = (576, 1024),
                    client=None, image_mode: str | None = None) -> dict:
    """Research (series-aware) -> panel images (NIM -> local ComfyUI anime) -> render -> validate -> sidecar -> save series state.

    Returns {"video", "sidecar", "title", "caption", "part", "source", "seconds"}. Raises on validation failure."""
    import yaml

    from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

    cfg = cfg if cfg is not None else yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    sdir = Path(series_dir) if series_dir else research.SERIES_DIR
    source = "given"
    if comic is None:
        res = research.research_comic(channel, premise, language, panels, cfg=cfg, client=client, series_dir=sdir)
        comic, source = res["comic"], res["source"]
    state = research.load_series(channel, sdir)
    seed = int(state.get("seed") or abs(hash(channel)) % 2**31 or 7)  # fixed per series -> consistent look
    if switchboard is None:
        from backend.app.services.image_switchboard import ImageSwitchboard

        switchboard = ImageSwitchboard(cfg)
    part = comic["series"]["part"]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{channel}-part{part}-{comic['series']['title']}".lower()).strip("-")[:60]
    job = f"comic_{datetime.now():%Y%m%d_%H%M%S}_{slug[:24]}"
    images, timings, img_note, mode_used = _panel_images(cfg, switchboard, comic, seed, job, resolution, image_mode)
    out_dir = Path(out_dir) / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4 = out_dir / f"{slug}.mp4"
    ffmpeg = FFmpegKenBurns().ffmpeg
    info = render_comic(ffmpeg, comic, images, mp4, ROOT / "outputs" / "comic_work" / slug, voice, music, language)
    rep = validate_video(mp4, W, H, fps=FPS, require_audio=True, deep=True)
    if not rep.ok:
        raise RuntimeError(f"comic reel failed validation: {rep.errors}")
    title = f"{comic['series']['title']} - Part {part}"
    caption = f"{comic['cliffhanger'] or comic['series']['recap']} {comic['cta']}".strip()
    side = {"status": "ok", "format": "comic", "channel": channel, "title": title, "part": part, "caption": caption,
            "source": source, "seed": seed, "series": comic["series"], "image_mode": mode_used, "image_note": img_note,
            "image_timings_secs": timings, "character_sheet": comic["character_sheet"],
            "panels": comic["panels"], "audio": info, "reel": {"path": str(mp4), "width": rep.width, "height": rep.height,
            "fps": rep.fps, "duration_secs": rep.duration_secs, "has_audio": rep.has_audio, "md5": rep.md5, "sha256": rep.sha256}}
    mp4.with_suffix(".json").write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
    research.save_series(channel, comic, sdir)
    return {"video": str(mp4), "sidecar": str(mp4.with_suffix(".json")), "title": title, "caption": caption, "part": part,
            "source": source, "seconds": info["seconds"]}
