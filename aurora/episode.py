"""Long-form story episode (16:9 1920x1080, ~4-5 min, ~45 panels in 3 acts) + vertical Shorts cut from the same panels.

    python -m aurora.episode --channel main --premise "..." [--panels 45] [--shorts 4]

Resumable across days: panels are generated one by one (NIM only, ComfyUI OFF) under the DAILY credit cap and saved in
outputs/episodes/<slug>/ (state.json + panels/). Run it again tomorrow to continue; when all panels exist it renders act by act
(acts are cached), concatenates the episode, writes the SRT / chapters / thumbnail and cuts the Shorts. Never uploads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from aurora import cast as cast_mod
from aurora import comic, comic_layout, research
from aurora.costs import COST_DIR, credits_today, log_credits
from aurora.monitoring.media_validator import validate_image, validate_video

ROOT = Path(__file__).resolve().parents[1]
EP_ROOT = ROOT / "outputs" / "episodes"
FINAL_ROOT = ROOT / "outputs" / "final"
EP_W, EP_H = 1920, 1080
ACT_MUSIC = ["calm quiet mysterious ruins", "epic battle fight war power", "emotional heroic memory love"]  # mood keywords -> MusicGen prompts


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "episode"


def daily_cap(cfg: dict) -> int:
    return int(os.environ.get("AURORA_NIM_DAILY_CAP") or cfg.get("providers", {}).get("nvidia_nim", {}).get("max_credits_per_day", 25))


# ----------------------------------------------------------------------------- state
def _flat_panels(ep: dict) -> list[dict]:
    return [p for a in ep["acts"] for p in a["panels"]]


def find_or_plan(channel: str, premise: str, cfg: dict, root: Path = EP_ROOT, n_panels: int = 45, client=None, new: bool = False,
                 series_dir: Path | None = None, cast_root: Path | None = None, analytics: dict | None = None) -> tuple[Path, dict]:
    """The in-progress episode of this channel (resume) or a freshly researched one. Returns (episode dir, state)."""
    root = Path(root)
    if not new:
        for d in sorted(root.glob(f"{channel}-*"), reverse=True):
            st = d / "state.json"
            if st.exists():
                state = json.loads(st.read_text(encoding="utf-8"))
                if state.get("status") != "done":
                    return d, state
    kw = {"series_dir": series_dir} if series_dir else {}
    res = research.research_episode(channel, premise, cfg=cfg, n_panels=n_panels, client=client, cast_root=cast_root, analytics=analytics, **kw)
    ep = res["episode"]
    d = root / f"{channel}-ep{ep['series']['part']:02d}-{_slug(ep['title'])}"
    (d / "panels").mkdir(parents=True, exist_ok=True)
    seed = int(hashlib.sha1(d.name.encode()).hexdigest()[:7], 16) or 7
    state = {"channel": channel, "premise": premise, "status": "in_progress", "created": datetime.now().isoformat(timespec="seconds"),
             "source": res["source"], "note": res["note"], "seed": seed, "episode": ep, "panels": {}, "credits": 0, "log": []}
    save_state(d, state)
    return d, state


def save_state(d: Path, state: dict) -> None:
    (Path(d) / "state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------- stage 1: panels under the daily cap
def generate_panels(d: Path, state: dict, cfg: dict, switchboard=None, cost_dir: Path = COST_DIR, max_regens: int = 4) -> dict:
    """Generate the missing panels with NIM (ComfyUI OFF) until done or the DAILY credit cap would be exceeded.
    Every call (also blocked/regenerated ones) is counted; panels already on disk are never regenerated."""
    ep, flat = state["episode"], _flat_panels(state["episode"])
    sheet = ep["character_sheet"]
    cap = daily_cap(cfg)
    if switchboard is None:
        from backend.app.services.image_switchboard import ImageSwitchboard

        switchboard = ImageSwitchboard(cfg)
    saved = getattr(switchboard, "fallback_order", None)
    if saved is not None:
        switchboard.fallback_order = ["nvidia_nim"]  # NIM only: no silent ComfyUI / manual fallback
    neg = comic._negative(cfg)
    regens = int(state.get("regens", 0))
    paused = ""
    try:
        for i, p in enumerate(flat):
            key = str(i)
            if key in state["panels"] and Path(state["panels"][key]["image"]).exists():
                continue
            prompt = research.panel_prompt(p, sheet, wide=True)
            img, spent = None, 0
            for attempt in range(2):
                if credits_today("nvidia_nim", cost_dir) + 1 > cap:
                    paused = f"daily NIM cap {cap} reached after {sum(1 for k in state['panels'])} panels"
                    break
                try:
                    r = switchboard.generate(prompt, "anime", [EP_W, EP_H], f"{d.name}_p{i:02d}" + (f"_{attempt}" if attempt else ""),
                                             negative_prompt=neg, seed=state["seed"] + attempt * 7919)
                    log_credits("nvidia_nim", 1, cost_dir)
                    spent += 1
                    img = Path(r.output_path)
                    if comic.has_drawn_bubble(img) and regens < max_regens and credits_today("nvidia_nim", cost_dir) + 1 <= cap:
                        regens += 1  # the model drew its own bubble: one counted regeneration with a new seed
                        r2 = switchboard.generate(research._cut(prompt, 720) + ", wordless illustration, no speech balloons", "anime", [EP_W, EP_H],
                                                  f"{d.name}_p{i:02d}_rg", negative_prompt=neg, seed=state["seed"] + 104729 + i)
                        log_credits("nvidia_nim", 1, cost_dir)
                        spent += 1
                        img = Path(r2.output_path)
                    break
                except Exception as exc:  # noqa: BLE001
                    log_credits("nvidia_nim", 1, cost_dir)
                    spent += 1
                    if "CONTENT_FILTERED" in str(exc) and attempt == 0:
                        prompt = comic.soften_prompt(prompt, 1)
                        continue
                    state["log"].append(f"panel {i}: {str(exc)[:160]}")
                    paused = f"NIM error on panel {i}: {str(exc)[:100]}"
                    break
            state["credits"] += spent
            if img is not None:
                dest = Path(d) / "panels" / f"p{i:02d}{img.suffix}"
                shutil.copyfile(img, dest)
                state["panels"][key] = {"image": str(dest), "credits": spent, "width": Image.open(dest).size[0], "height": Image.open(dest).size[1]}
            state["regens"] = regens
            save_state(d, state)  # crash-safe: progress survives any interruption
            if paused or img is None:
                break
    finally:
        if saved is not None:
            switchboard.fallback_order = saved
    done = sum(1 for k in state["panels"] if Path(state["panels"][k]["image"]).exists())
    remaining = len(flat) - done
    left = max(cap - credits_today("nvidia_nim", cost_dir), 0)
    return {"done": done, "total": len(flat), "remaining": remaining, "paused": paused, "credits_left_today": left,
            "resume": ("complete: ready to render" if not remaining else
                       f"{remaining} panels left; run the same command again after the daily cap resets (about {-(-remaining // max(cap, 1))} more day(s) at {cap} credits/day)")}


# ----------------------------------------------------------------------------- subtitles / chapters / thumbnail
def _ts(sec: float) -> str:
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _wrap_srt(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return "\n".join(lines + ([cur] if cur else []))


def build_srt(times: list[dict]) -> str:
    """SRT from the narration timings (global seconds): one cue per panel, voice start -> voice end."""
    cues = []
    for n, t in enumerate((t for t in times if t.get("narration")), 1):
        end = max(t["voice_end"], t["voice_start"] + 0.8)
        cues.append(f"{n}\n{_ts(t['voice_start'])} --> {_ts(end)}\n{_wrap_srt(t['narration'])}\n")
    return "\n".join(cues)


def chapter_list(acts: list[dict], starts: list[float]) -> list[dict]:
    """YouTube chapters: the first must start at 0:00, each >= 10 s; (start seconds, title)."""
    out = []
    for a, s in zip(acts, starts):
        out.append({"start": round(0.0 if not out else s, 2), "title": re.sub(r"^act\s*\d+\s*[:.-]?\s*", "", a["name"], flags=re.I).strip() or a["name"]})
    return out


def fmt_chapters(chapters: list[dict]) -> str:
    return "\n".join(f"{int(c['start']) // 60}:{int(c['start']) % 60:02d} {c['title']}" for c in chapters)


def pick_thumbnail_panel(panels: list[dict], images: list[Path]) -> int:
    """Best thumbnail frame: a clear subject (largest face), sharp, and dramatic (action/close-up shots score higher)."""
    import cv2

    best, best_score = 0, -1.0
    for i, (p, img) in enumerate(zip(panels, images)):
        try:
            g = cv2.imread(str(img), cv2.IMREAD_GRAYSCALE)
            sharp = float(cv2.Laplacian(g, cv2.CV_64F).var())
            faces = comic_layout.detect_faces(Path(img))
            face = max(((f[2] - f[0]) * (f[3] - f[1]) for f in faces), default=0) / float(g.shape[0] * g.shape[1])
        except Exception:  # noqa: BLE001
            sharp, face = 0.0, 0.0
        score = min(sharp / 400.0, 2.0) + min(face * 12, 2.0) + (1.0 if p.get("action") else 0.0) + (0.5 if p.get("shot") == "close-up" else 0.0)
        if score > best_score:
            best, best_score = i, score
    return best


def make_thumbnail(image: Path, title: str, out: Path, episode_no: int = 1, faces: list | None = None, label: str = "EPISODE") -> Path:
    """1280x720 thumbnail: the best panel, darkened on the text side, big outlined title on the side away from the face, episode badge."""
    im = Image.open(image).convert("RGB")
    iw, ih = im.size
    s = max(1280 / iw, 720 / ih)
    im = im.resize((int(iw * s) + 1, int(ih * s) + 1), Image.LANCZOS)
    faces = comic_layout.detect_faces(Path(image)) if faces is None else faces
    left, top = (im.width - 1280) // 2, (im.height - 720) // 2
    if faces and im.height > 720:  # portrait panels: keep the face (not the belly) in the 16:9 crop
        f0 = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        top = int(min(max((f0[1] + f0[3]) / 2 * s - 720 * 0.38, 0), im.height - 720))
    im = im.crop((left, top, left + 1280, top + 720))
    fx = ((faces and max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))) or (0, 0, iw, ih))
    face_cx = ((fx[0] + fx[2]) / 2 * s - left) if faces else 640
    text_left = face_cx > 640  # face on the right -> text on the left
    shade = Image.new("L", (1280, 720), 0)
    sd = ImageDraw.Draw(shade)
    for x in range(1280):
        a = int(170 * max(0.0, 1 - (x / 760 if text_left else (1280 - x) / 760)))
        sd.line([(x, 0), (x, 720)], fill=a)
    im = Image.composite(Image.new("RGB", (1280, 720), (0, 0, 0)), im, shade.filter(ImageFilter.GaussianBlur(8)))
    d = ImageDraw.Draw(im)
    box_w = 640
    x0 = 50 if text_left else 1280 - 50 - box_w
    for size in range(120, 52, -6):
        f = comic._font(size, title)
        lines = comic._balanced(d, title.upper(), f, box_w)
        if len(lines) <= 3 and len(lines) * int(size * 1.1) <= 460:
            break
    y = 60
    for l in lines:
        d.text((x0, y), l, font=f, fill=(255, 255, 255), stroke_width=8, stroke_fill=(0, 0, 0))
        y += int(size * 1.1)
    bf = comic._font(44, label)
    d.rounded_rectangle([x0, 600, x0 + 300, 672], radius=14, fill=(255, 214, 80), outline=(0, 0, 0), width=4)
    d.text((x0 + 22, 606), f"{label} {episode_no}", font=bf, fill=(20, 20, 20))
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=92)
    return out


# ----------------------------------------------------------------------------- stage 2: render the episode
def render_episode(d: Path, state: dict, cfg: dict, final_dir: Path | None = None, music: bool = True, voice: str = "en-US-AvaNeural",
                   series_dir: Path | None = None) -> dict:
    """All panels exist -> act-by-act render (cached) -> concat -> SRT + chapters + thumbnail + sidecar. Returns the sidecar dict."""
    import subprocess

    from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

    ep = state["episode"]
    flat = _flat_panels(ep)
    images = [Path(state["panels"][str(i)]["image"]) for i in range(len(flat))]
    ff = FFmpegKenBurns().ffmpeg
    final_dir = Path(final_dir or FINAL_ROOT / state["channel"] / "episodes" / Path(d).name)
    final_dir.mkdir(parents=True, exist_ok=True)
    acts_info, offset, base = [], 0.0, 0
    with comic.frame(EP_W, EP_H):
        for k, act in enumerate(ep["acts"]):
            act_mp4, info_f = Path(d) / "work" / f"act{k + 1}.mp4", Path(d) / "work" / f"act{k + 1}.json"
            n = len(act["panels"])
            if not (act_mp4.exists() and info_f.exists()):
                last = k == len(ep["acts"]) - 1
                spec = {"series": {"title": ep["title"], "part": ep["series"]["part"], "total_parts": ep["series"]["part"]},
                        "panels": act["panels"]}
                info = comic.render_comic(ff, spec, images[base: base + n], act_mp4, Path(d) / "work" / f"act{k + 1}_files", voice=voice, music=music,
                                          cover=(k == 0), end_card=(("The End", "Thanks for watching") if last else None),
                                          music_text=ACT_MUSIC[k % 3], max_words=16, cover_label="EPISODE")
                info_f.write_text(json.dumps(info, indent=2), encoding="utf-8")
            info = json.loads(info_f.read_text(encoding="utf-8"))
            acts_info.append({"mp4": act_mp4, "info": info, "start": offset})
            offset += info["seconds"]
            base += n
    # concat acts (same codec/size/fps -> stream copy)
    lst = Path(d) / "work" / "acts.txt"
    lst.write_text("".join(f"file '{a['mp4'].resolve().as_posix()}'\n" for a in acts_info), encoding="utf-8")
    video = final_dir / f"{Path(d).name}.mp4"
    proc = subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(video)],
                          capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError("episode concat failed: " + proc.stderr[-300:])
    # global timings
    times = []
    for a in acts_info:
        for t in a["info"]["panel_times"]:
            times.append({**t, "voice_start": round(t["voice_start"] + a["start"], 2), "voice_end": round(t["voice_end"] + a["start"], 2)})
    # the cover card of act 1 shifts nothing: panel_times already include it
    srt = final_dir / f"{Path(d).name}.srt"
    srt.write_text(build_srt(times), encoding="utf-8")
    chapters = chapter_list(ep["acts"], [a["start"] for a in acts_info])
    t_i = pick_thumbnail_panel(flat, images)
    thumb = make_thumbnail(images[t_i], ep["title"], final_dir / "thumbnail.jpg", ep["series"]["part"])
    rep = validate_video(video, EP_W, EP_H, fps=24, require_audio=True)
    if not rep.ok:
        raise RuntimeError(f"episode failed validation: {rep.errors}")
    thumb_rep = validate_image(thumb, 1280, 720)
    side = {"status": "ok", "format": "episode", "channel": state["channel"], "title": ep["title"], "episode": ep["series"]["part"],
            "description": ep["description"], "keywords": ep["keywords"], "hashtags": ep["hashtags"], "hook": ep["hook"],
            "chapters": chapters, "chapters_text": fmt_chapters(chapters), "captions_srt": str(srt), "thumbnail": str(thumb),
            "thumbnail_ok": thumb_rep.ok, "thumbnail_panel": t_i,
            "video": {"path": str(video), "width": rep.width, "height": rep.height, "fps": rep.fps, "duration_secs": rep.duration_secs,
                      "has_audio": rep.has_audio, "sha256": rep.sha256},
            "panels": len(flat), "source": state["source"], "credits": state["credits"], "voice_notes": [n for a in acts_info for n in a["info"]["voice_notes"]][:3]}
    if cfg.get("comic", {}).get("cast_lock", False):  # characters that debuted in this episode join the permanent cast
        side["cast_added"] = cast_mod.save_new_members(state["channel"], {"character_sheet": ep["character_sheet"], "panels": flat, "series": ep["series"]}, images)
    (final_dir / "episode.json").write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
    state["status"] = "rendered"
    state["panel_times"] = times
    save_state(d, state)
    research.save_episode_state(state["channel"], ep, series_dir or research.SERIES_DIR)
    return side


# ----------------------------------------------------------------------------- stage 3: vertical Shorts from the same panels
def choose_short_windows(flat: list[dict], n: int = 4, per_short: int = 5) -> list[dict]:
    """n Shorts: each = a hook panel first (the most dramatic one of its window or the next ones) + `per_short` consecutive story panels.
    Windows are spread over the episode so every Short shows a different part of the story."""
    total = len(flat)
    per_short = min(per_short, max(total - 1, 1))
    starts = sorted({min(int(round(total * f)), total - per_short) for f in [i / max(n, 1) * 0.9 for i in range(n)]})
    out = []
    for s in starts:
        window = list(range(s, s + per_short))
        ahead = list(range(s + per_short, min(s + per_short + 6, total)))  # a more dramatic panel from just after the window can be the hook
        cands = window + ahead

        def drama(i: int) -> float:
            p = flat[i]
            return (2.0 if p.get("action") else 0.0) + (1.0 if p.get("bubble") else 0.0) + (0.5 if p.get("shot") in ("close-up", "action") else 0.0) - 0.01 * (i - s)

        hook = max(cands, key=drama)
        order = [hook] + [i for i in window if i != hook]
        out.append({"start": s, "hook": hook, "panels": order[: per_short + 1]})
    return out


def cut_shorts(d: Path, state: dict, cfg: dict, n: int = 4, final_dir: Path | None = None, music: bool = True, voice: str = "en-US-AvaNeural") -> list[dict]:
    """Vertical 9:16 ~30 s Shorts: panel subsets cropped around the subject, rendered with the existing full-bleed comic renderer;
    end card = "Full story on the channel"."""
    from aurora.motion.providers.ffmpeg_kenburns import FFmpegKenBurns

    ep, flat = state["episode"], _flat_panels(state["episode"])
    ff = FFmpegKenBurns().ffmpeg
    final_dir = Path(final_dir or FINAL_ROOT / state["channel"] / "episodes" / Path(d).name) / "shorts"
    final_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for k, w in enumerate(choose_short_windows(flat, n), 1):
        work = Path(d) / "work" / f"short{k}"
        work.mkdir(parents=True, exist_ok=True)
        crops = []
        for j, i in enumerate(w["panels"]):
            crops.append(comic_layout.portrait_crop(Path(state["panels"][str(i)]["image"]), work / f"crop{j}.png"))
        panels = [flat[i] for i in w["panels"]]
        spec = {"series": {"title": ep["title"], "part": ep["series"]["part"], "total_parts": ep["series"]["part"]}, "panels": panels}
        mp4 = final_dir / f"short{k}.mp4"
        info = comic.render_comic(ff, spec, crops, mp4, work / "files", voice=voice, music=music, cover=False,
                                  end_card=("Full story on the channel", ep["title"]), music_text=ACT_MUSIC[1 if k % 2 else 2], max_words=13)
        rep = validate_video(mp4, 1080, 1920, fps=24, require_audio=True)
        if not rep.ok:
            raise RuntimeError(f"short {k} failed validation: {rep.errors}")
        hook_line = panels[0]["narration"].rstrip(".!? ")
        side = {"status": "ok", "format": "short", "channel": state["channel"], "short": k, "title": f"{hook_line[:60]} | {ep['title']}"[:100],
                "description": f"{ep['hook']}\n\nFull story on the channel: {ep['title']}.", "hashtags": ["#shorts"] + ep["hashtags"][:4],
                "parent_episode": Path(d).name, "panels": w["panels"], "hook_panel": w["hook"], "end_card": "Full story on the channel",
                "video": {"path": str(mp4), "width": rep.width, "height": rep.height, "fps": rep.fps, "duration_secs": rep.duration_secs,
                          "has_audio": rep.has_audio, "sha256": rep.sha256}}
        mp4.with_suffix(".json").write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append(side)
    state["shorts"] = [r["video"]["path"] for r in results]
    save_state(d, state)
    return results


# ----------------------------------------------------------------------------- driver
def run_episode(channel: str, premise: str, cfg: dict | None = None, panels: int = 45, shorts: int = 4, new: bool = False, switchboard=None,
                client=None, root: Path = EP_ROOT, final_dir: Path | None = None, cost_dir: Path = COST_DIR, music: bool = True,
                series_dir: Path | None = None, cast_root: Path | None = None, analytics: dict | None = None) -> dict:
    """One resumable pass: plan/resume -> panels under the daily cap -> (if complete) render + Shorts. Returns a status dict."""
    import yaml

    cfg = cfg if cfg is not None else yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    d, state = find_or_plan(channel, premise, cfg, root, panels, client, new, series_dir, cast_root, analytics)
    prog = generate_panels(d, state, cfg, switchboard, cost_dir)
    out = {"episode_dir": str(d), "title": state["episode"]["title"], "source": state["source"], **prog}
    if prog["remaining"]:
        out["status"] = "paused"
        return out
    side = render_episode(d, state, cfg, final_dir, music, series_dir=series_dir)
    out.update(status="rendered", episode=side)
    if shorts:
        out["shorts"] = cut_shorts(d, state, cfg, shorts, final_dir, music)
    state["status"] = "done"
    save_state(d, state)
    return out


def main(argv: list[str] | None = None) -> int:
    from aurora import ensure_utf8_console, load_env

    ensure_utf8_console()
    load_env()
    ap = argparse.ArgumentParser(description="Aurora long episode (16:9) + Shorts, resumable under the daily NIM cap")
    ap.add_argument("--channel", default="main")
    ap.add_argument("--premise", default=None)
    ap.add_argument("--panels", type=int, default=45)
    ap.add_argument("--shorts", type=int, default=4)
    ap.add_argument("--new", action="store_true", help="ignore an in-progress episode and plan a new one")
    ap.add_argument("--no-music", action="store_true")
    a = ap.parse_args(argv)
    import yaml

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    premise = a.premise or next((c.get("niche") for c in cfg.get("autopilot", {}).get("channels", []) if c.get("name") == a.channel), "an original anime story")
    res = run_episode(a.channel, premise, cfg, a.panels, a.shorts, a.new, music=not a.no_music)
    print(json.dumps({k: v for k, v in res.items() if k not in ("episode", "shorts")}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
