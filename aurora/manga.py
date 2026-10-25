"""Manga-dialogue format: characters speak their OWN lines (one voice per character); narration only for verses and caption cards.

    python -m aurora.manga parse --chapter 1                 # print the parsed script (no cost)
    python -m aurora.manga render --series veer [--force]    # next part of the series (NIM credits permitting)

A chapter script (stories/<series>/chapters/CH_NN.md) is parsed into the normal comic dict (panels + `lines`), then
`comic.make_comic_reel` renders it: images, per-line voices, timed bubbles, sidecar, chapter state, 16:9 compilation.
Respect charter (STORY_BIBLE section 2) is enforced in code: `check_charter`.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw

from aurora import cast as cast_mod
from aurora import research

ROOT = Path(__file__).resolve().parents[1]
CHAPTER_FILE = "CH_{:02d}.md"
RESPECT_CARD = "A work of fiction inspired by the world's mythologies, told with respect."
MANGA_HEAD = ("cel-shaded manga panel, bold black ink outlines, screentone shading, palette of saffron, river-teal, night-indigo and ember-red, "
              "single panel, ")
SPEED = "dramatic radial speed lines, dynamic angle, "
LEAD, GAP, END_PAD, MIN_PANEL_S = 0.3, 0.2, 0.4, 2.5  # panel time = lead + lines + gaps + 0.4 s
DEFAULT_VOICES = {
    "NARRATOR": {"voice": "en-US-AvaNeural", "rate": "-8%", "pitch": "+0Hz"},
    "ANCHOR": {"voice": "en-US-JennyNeural", "rate": "+6%", "pitch": "+0Hz"},
}
# emotion keyword -> (rate %, pitch Hz); the first two matches add up
EMOTION = {"whisper": (-12, -4), "shout": (8, 6), "roar": (6, 8), "scared": (6, 10), "tired": (-10, -6), "grave": (-12, -8), "calm": (-4, 0),
           "awed": (-6, 4), "stunned": (-6, 4), "cheerful": (6, 6), "amused": (0, 3), "laugh": (8, 4), "sharp": (6, 0), "defensive": (4, 2),
           "horrified": (4, 8), "gentl": (-8, 0), "worried": (4, 6), "knowing": (-6, -2), "echoing": (-10, -6)}
SHOUT_WORDS = ("shout", "roar", "scream", "yell")
DEITIES = r"shiva|vishnu|krishna|rama|hanuman|durga|kali|ganesh|brahma|lakshmi|indra|saraswati|parvati|zeus|odin|thor|ra|anubis"
PROPHETS = r"muhammad|mohammed|jesus|christ|moses|abraham|noah|buddha|nanak"
BAD_ACTS = r"fight|fights|fighting|defeat|defeated|kill|kills|killed|mock|mocks|mocked|naked|sexy|humiliat|slain|battle|battles|lose|loses|crush"


# ----------------------------------------------------------------------------- script parser
SPEAKER = re.compile(r"\*\*([A-Z][A-Z .']*?)\*\*\s*(?:\(([^)]*)\))?\s*:\s*(.*)")
CAPTION = re.compile(r"caption card:\s*\*(.+?)\*", re.I)
ACTION_WORDS = re.compile(r"\b(rush\w*|erupt\w*|jump\w*|drop\w*|fight\w*|run\w*|runn\w*|crash\w*|split\w*|blaz\w*|pound\w*|explod\w*|burst\w*|surg\w*|leap\w*|wave)\b", re.I)


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_]+", "", s)).strip(" .:-—")


def _speech(s: str) -> str:
    """Spoken text keeps its closing punctuation (the voice needs it)."""
    return re.sub(r"\s+", " ", re.sub(r"[*_]+", "", s)).strip()


def _quote(rest: str) -> tuple[str, str]:
    """(spoken text, trailing action text) from what follows `SPEAKER (emotion):`."""
    a, b = rest.find('"'), rest.rfind('"')
    if a != -1 and b > a:
        return _speech(rest[a + 1:b]), _plain(rest[b + 1:])
    return _speech(rest), ""


def _line(speaker: str, emotion: str, text: str) -> dict:
    emo = (emotion or "").lower()
    speaker = speaker.strip().upper()
    if speaker == "NARRATOR":
        kind = "verse" if "verse" in emo else "caption"
    elif "whisper" in emo:
        kind = "whisper"
    elif any(w in emo for w in SHOUT_WORDS):
        kind = "shout"
    else:
        kind = "speech"
    return {"speaker": speaker, "emotion": emo, "kind": kind, "text": text}


def _card(text: str, part: int, parts: int) -> tuple[str, str]:
    t = _plain(text).strip('"')
    if re.match(r"part \d+", t, re.I):
        return ("To be continued...", t)
    if re.match(r"chapter \d+", t, re.I):
        head, _, tail = t.partition(". ")
        return (head, tail.replace('"', '').strip()) if tail else (t, "")
    return (t, f"Part {part + 1} tomorrow" if part < parts else "")


def parse_chapter(md: str) -> dict:
    """{number, title, parts: [{number, title, panels: [{n, desc, lines, ...}], end_card}]} from a CHAPTER_NN.md script."""
    head = re.search(r"Chapter\s+(\d+):\s*\"([^\"]+)\"", md)
    chapter = {"number": int(head.group(1)) if head else 1, "title": head.group(2) if head else "", "parts": []}
    part, item, cards = None, None, {}
    for raw in md.splitlines():
        m = re.match(r"^##\s+Part\s+(\d+)\s*[—-]\s*\"([^\"]+)\"", raw)
        if m:
            part = {"number": int(m.group(1)), "title": m.group(2), "panels": [], "card": None}
            chapter["parts"].append(part)
            item = None
            continue
        if part is None:
            continue
        low = raw.strip().lower()
        if low.startswith(("end card:", "cliffhanger card:")):
            part["card"] = _plain(raw.split(":", 1)[1])
            item = None
            continue
        n = re.match(r"^(\d+)\.\s+(.*)", raw)
        if n:
            item = {"n": int(n.group(1)), "raw": [n.group(2)]}
            part["panels"].append(item)
        elif item is not None and raw.startswith((" ", "\t")) and raw.strip():
            item["raw"].append(raw.strip())
    total = len(chapter["parts"])
    for part in chapter["parts"]:
        for item in part["panels"]:
            desc, lines = [], []
            for r in item.pop("raw"):
                cap = CAPTION.search(r)
                if cap:
                    lines.append(_line("NARRATOR", "caption", _plain(cap.group(1))))
                    r = CAPTION.sub("", r)
                for sm in SPEAKER.finditer(r):
                    text, tail = _quote(sm.group(3))
                    if text:
                        lines.append(_line(sm.group(1), sm.group(2) or "", text))
                    if tail:
                        desc.append(tail)
                    r = r[:sm.start()] + " "
                if _plain(r):
                    desc.append(_plain(r))
            item["desc"] = " ".join(d for d in desc if d)
            item["lines"] = lines
        card = part.pop("card")
        part["end_card"] = _card(card, part["number"], total) if card else (
            ("To be continued...", f"Part {part['number'] + 1} tomorrow") if part["number"] < total else ("The End", ""))
    return chapter


# ----------------------------------------------------------------------------- charter + panels
def check_charter(panels: list[dict]) -> None:
    """Respect charter (STORY_BIBLE section 2): deities are never shown fighting / defeated / mocked / sexualised, no prophet is depicted,
    and the prophesied saviour ("Kali Avatar") is never claimed as Veer. Raises ValueError so a bad script never renders."""
    for p in panels:
        scene = p.get("image_prompt", "")
        if re.search(rf"\b({DEITIES})\b", scene, re.I) and re.search(rf"\b({BAD_ACTS})", scene, re.I):
            raise ValueError(f"respect charter: a deity is shown in a fight/defeat/mockery scene: {scene[:80]}")
        if re.search(rf"\b({PROPHETS})\b", scene, re.I):
            raise ValueError(f"respect charter: no depiction of prophets: {scene[:80]}")
        said = " ".join(ln["text"] for ln in p.get("lines", [])) + " " + scene
        if re.search(r"kali\s+avatar", said, re.I) and re.search(r"\bveer\b", said, re.I):
            raise ValueError("respect charter: the saviour of the prophecy is never claimed as Veer")


def part_to_comic(chapter: dict, part: dict, cast: list[dict], series_title: str, style_head: str = MANGA_HEAD, opening_card: str = RESPECT_CARD,
                  mode: str = "manga", action_style: str = SPEED) -> dict:
    """One scripted part -> the comic dict used by comic.make_comic_reel (panels carry `lines`; style_head makes the manga look)."""
    names = [m["name"] for m in cast]
    panels = []
    for item in part["panels"]:
        desc = item["desc"]
        text_all = desc + " " + " ".join(f"{ln['speaker']}" for ln in item["lines"] if ln["kind"] not in ("verse", "caption") and "voice message" not in ln["emotion"])
        chars = [n for n in names if re.search(rf"\b{re.escape(n.split()[0])}\b", text_all, re.I)]
        shot = "wide" if re.match(r"(wide|full panel)", desc, re.I) else "close-up" if re.match(r"(close-up|insert)", desc, re.I) else "medium"
        action = bool(ACTION_WORDS.search(desc)) or any(ln["kind"] == "shout" for ln in item["lines"])
        scene = re.sub(r"^(wide|close-up|insert|full panel)\s*:\s*", "", re.sub(r"\([^)]*\)", "", desc), flags=re.I)
        scene = re.sub(r"[\"“”'‘’].{0,60}?[\"“”'‘’]", "", scene)  # no quoted on-screen text in the image prompt
        panels.append({"shot": shot, "characters": chars, "image_prompt": (scene or desc)[:300], "narration": "", "bubble": "", "action": action,
                       "scenery": not chars, "desc": desc, "lines": item["lines"]})
        if style_head:
            panels[-1]["style_head"] = style_head + (action_style if action else "")
    check_charter(panels)
    if mode != "manga":  # single narrator comic: lines -> narration (+ the first spoken line as the bubble)
        for p in panels:
            told = " ".join(ln["text"] for ln in p["lines"] if ln["kind"] in ("caption", "verse"))
            spoken = [ln["text"] for ln in p["lines"] if ln["kind"] not in ("caption", "verse")]
            p["bubble"] = spoken[0] if spoken else ""
            p["narration"] = told or (spoken[0] if spoken else p["desc"][:80])
            p.pop("lines")
    first = " ".join(p.get("narration") or " ".join(ln["text"] for ln in p.get("lines", [])) for p in panels)[:200]
    total = len(chapter["parts"])
    e_title, e_sub = part["end_card"]
    return {"series": {"title": series_title, "part": part["number"], "total_parts": total, "recap": first},
            "hook": part["title"], "cta": e_sub or e_title, "cliffhanger": e_title if part["number"] < total else "", "panels": panels,
            "character_sheet": [{"name": m["name"], "description": m["description"], "style": research.COMIC_STYLE} for m in cast],
            "end_card": [e_title, e_sub], "cover_note": opening_card}


# ----------------------------------------------------------------------------- voices
def load_voices(series: str) -> dict[str, dict]:
    """{SPEAKER NAME: {voice, rate, pitch, fx}} of a story pack (characters' `voice`, narrator, extra_voices)."""
    from aurora import story

    return story.pack_voices(story.load_pack(series))


def voice_for(speaker: str, voices: dict) -> dict:
    return voices.get(speaker.upper()) or voices.get(speaker.upper().split()[-1]) or voices.get("NARRATOR") or DEFAULT_VOICES["NARRATOR"]


def _pct(v: str) -> int:
    return int(re.sub(r"[^\d+-]", "", v or "0") or 0)


def prosody(voice: dict, emotion: str, kind: str = "speech") -> tuple[str, str]:
    """Edge-TTS (rate, pitch): the character's base voice shifted by the emotion (whisper slower/lower, shout faster/higher)."""
    r, p = _pct(voice.get("rate", "+0%")), _pct(voice.get("pitch", "+0Hz"))
    hits = [v for k, v in EMOTION.items() if k in (emotion or "")][:2] or ([EMOTION["whisper"]] if kind == "whisper" else [EMOTION["shout"]] if kind == "shout" else [])
    for dr, dp in hits:
        r, p = r + dr, p + dp
    return f"{max(min(r, 50), -50):+d}%", f"{max(min(p, 40), -40):+d}Hz"


FX = {"echo": "aecho=0.8:0.85:70:0.35,apad=pad_dur=0.45", "reverb": "aecho=0.8:0.9:40|90|150:0.45|0.3|0.2,apad=pad_dur=0.6"}


def apply_fx(ffmpeg: str, src: Path, fx: str, out: Path, quiet: bool = False) -> Path:
    """Echo/reverb (deep entities) and a softer level for whispers."""
    from aurora.comic import _run

    chain = [FX[fx]] if fx in FX else []
    if quiet:
        chain.append("volume=0.7")
    _run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-af", ",".join(chain) or "anull", "-ar", "44100", "-ac", "1", str(out)])
    return out


def panel_audio(ffmpeg: str, lines: list[dict], work: Path, i: int, voices: dict, synth=None) -> dict:
    """One audio file per line, placed on the panel timeline: {"items": [{line, file, start, end}], "secs", "notes"}."""
    from aurora.audio import synthesize_voiceover
    from aurora.comic import _duration

    synth = synth or synthesize_voiceover
    t, items, notes = LEAD, [], []
    for k, ln in enumerate(lines):
        v = voice_for(ln["speaker"], voices)
        rate, pitch = prosody(v, ln["emotion"], ln["kind"])
        f, note = synth(ln["text"], Path(work) / f"line_{i}_{k}.wav",
                        options={"voice_engine": "edge", "voice": v["voice"], "rate": rate, "pitch": pitch, "language": "en"})
        notes.append(note)
        if f and (v.get("fx") in FX or ln["kind"] == "whisper"):
            try:
                f = apply_fx(ffmpeg, f, v.get("fx", ""), Path(work) / f"line_{i}_{k}_fx.wav", quiet=ln["kind"] == "whisper")
            except Exception as exc:  # noqa: BLE001 - FX is cosmetic
                notes.append(f"fx skipped: {str(exc)[:60]}")
        dur = _duration(f) if f else max(1.2, 0.38 * len(ln["text"].split()))
        items.append({"line": ln, "file": f, "start": round(t, 2), "end": round(t + dur, 2)})
        t += dur + GAP
    secs = (t - GAP + END_PAD) if items else MIN_PANEL_S
    return {"items": items, "secs": max(secs, MIN_PANEL_S), "notes": notes}


def speaker_head(speaker: str, panel: dict, faces_img: list, iw: int, ih: int, motion: str, occ, head_zone):
    """Where the tail points: the face that belongs to the speaker (faces sorted left->right, speakers in order of the panel's characters);
    off-screen speakers (voice messages, anchors, narrators) get no tail."""
    from aurora import comic_layout

    names = [c.split()[0].upper() for c in panel.get("characters", [])]
    who = speaker.split()[0].upper()
    if not faces_img or who not in names:
        return None
    faces = sorted(faces_img, key=lambda b: b[0])
    idx = names.index(who)
    face = faces[idx] if len(faces) == len(names) else max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    return comic_layout.head_point(occ, comic_layout.faces_over_motion([face], iw, ih, motion), head_zone)


# ----------------------------------------------------------------------------- manga look overlays
def tone_png(path: Path, w: int, h: int, action: bool) -> Path:
    """Screentone (halftone dots, denser toward the bottom) and, on action panels, radial speed lines with a clear centre."""
    import math

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    step = 22
    for y in range(0, h, step):
        for x in range(((y // step) % 2) * (step // 2), w, step):
            r = 1.2 + 3.2 * (y / h) ** 2
            d.ellipse([x - r, y - r, x + r, y + r], fill=(0, 0, 0, 38))
    if action:
        cx, cy = w / 2, h * 0.45
        for k in range(46):
            a = k * 2 * math.pi / 46 + (k % 3) * 0.02
            r0, r1 = (0.30 + 0.12 * ((k * 7) % 5) / 4) * h, 1.4 * h
            wdt = 0.012 + 0.01 * (k % 4)
            d.polygon([(cx + math.cos(a - wdt) * r1, cy + math.sin(a - wdt) * r1), (cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                       (cx + math.cos(a + wdt) * r1, cy + math.sin(a + wdt) * r1)], fill=(255, 255, 255, 70))
    img.save(path)
    return path


def make_manga_reel(series: str, out_dir: Path, **kw) -> dict:
    """Kept for compatibility: the series driver is `aurora.story.render_next` (story packs)."""
    from aurora import story

    return story.render_next(series, out_dir=out_dir, **kw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manga-dialogue series (VEER)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parse")
    p.add_argument("--chapter", type=int, default=1)
    p.add_argument("--dir", default="stories/veer/chapters")
    r = sub.add_parser("render")
    r.add_argument("--series", default="veer")
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "parse":
        ch = parse_chapter((ROOT / a.dir / CHAPTER_FILE.format(a.chapter)).read_text(encoding="utf-8"))
        for pt in ch["parts"]:
            print(f"Part {pt['number']} {pt['title']!r}: {len(pt['panels'])} panels, {sum(len(x['lines']) for x in pt['panels'])} lines, card={pt['end_card']}")
        return 0
    res = make_manga_reel(a.series, None, force=a.force)
    print(json.dumps({k: res[k] for k in ("video", "title", "seconds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
