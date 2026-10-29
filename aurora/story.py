"""Story packs: stories are CONTENT, never code.

    stories/<slug>/series.yaml          title, channel, playlist, language, format (comic|manga), parts_per_chapter, style_head, opening_card, narrator ...
    stories/<slug>/characters/<n>.yaml  locked look + seed + voice (voice/rate/pitch/fx) + optional reference images
    stories/<slug>/chapters/CH_NN.md    authored script (format: docs/STORY_PACKS.md); a missing chapter is written by research and SAVED BACK here
    stories/<slug>/world.md             optional canon rules fed into research prompts

    python -m aurora.story lint <series>|--all     check a pack (friendly errors); the scheduler lints before every render
    python -m aurora.story list | render <series>

Channels pick series in config: `channels[].series: [{slug: ..., per_day: 2}]`. A channel without `series:` runs the legacy flow unchanged.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from aurora import research

ROOT = Path(__file__).resolve().parents[1]
STORIES_ROOT = ROOT / "stories"
FORMATS = ("comic", "manga")
DEFAULTS = {"language": "English", "format": "comic", "parts_per_chapter": 10, "style_head": "", "action_style": "", "palette": [], "opening_card": "",
            "narrator": {"voice": "en-US-AvaNeural"}, "extra_voices": {}, "reserve_credits": 0, "rules": {}, "channel": "", "playlist": "", "retention": {}}
BANNED = r"nude|naked|sexual|sexy|porn\w*|rape\w*|gore|gory|dismember\w*|decapitat\w*"  # always banned in scripts (all-ages channel)
VOICE_RE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}-\w+Neural$")


class PackError(RuntimeError):
    """A story pack failed validation; the scheduler skips it with a report line."""


def _yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


# ----------------------------------------------------------------------------- loading
def find_pack(key: str, root: Path | None = None) -> Path | None:
    """Pack directory for a slug OR a state key (a legacy channel key such as `shinobi` maps to the pack that declares state_key: shinobi)."""
    root = Path(root or STORIES_ROOT)
    if (root / key / "series.yaml").exists():
        return root / key
    for f in sorted(root.glob("*/series.yaml")):
        try:
            if _yaml(f).get("state_key") == key:
                return f.parent
        except (OSError, yaml.YAMLError):
            continue
    return None


def list_packs(root: Path | None = None) -> list[str]:
    return [f.parent.name for f in sorted(Path(root or STORIES_ROOT).glob("*/series.yaml")) if not f.parent.name.startswith("_")]


def _characters(pdir: Path) -> list[dict]:
    out = []
    for f in sorted((pdir / "characters").glob("*.yaml")):
        try:
            m = _yaml(f)
        except yaml.YAMLError:
            continue
        if isinstance(m, dict) and m.get("name") and m.get("description"):
            m["_dir"], m["_file"] = str(f.parent), str(f)
            out.append(m)
    return sorted(out, key=lambda m: (m.get("role") != "protagonist", str(m["name"]).lower()))


def load_pack(key: str, root: Path | None = None) -> dict:
    pdir = find_pack(key, root)
    if pdir is None:
        raise PackError(f"no story pack '{key}' (expected stories/{key}/series.yaml; copy stories/_template, see docs/STORY_PACKS.md)")
    try:
        series = {**DEFAULTS, **_yaml(pdir / "series.yaml")}
    except yaml.YAMLError as exc:
        raise PackError(f"stories/{pdir.name}/series.yaml is not valid YAML: {exc}") from exc
    series.setdefault("title", pdir.name)
    series["playlist"] = series.get("playlist") or series.get("title", "")
    chapters = {int(m.group(1)): f for f in sorted((pdir / "chapters").glob("CH_*.md")) if (m := re.match(r"CH_(\d+)\.md$", f.name, re.I))}
    world = (pdir / "world.md").read_text(encoding="utf-8") if (pdir / "world.md").exists() else ""
    return {"slug": pdir.name, "dir": pdir, "series": series, "state_key": series.get("state_key") or pdir.name,
            "characters": _characters(pdir), "chapters": chapters, "world": world}


def pack_cast(key: str, root: Path | None = None) -> list[dict]:
    """Locked cast of a pack, in the same shape as cast.load_cast (empty when `key` has no pack)."""
    pdir = find_pack(key, root)
    return _characters(pdir) if pdir else []


def pack_voices(pack: dict) -> dict[str, dict]:
    """{SPEAKER NAME: {voice, rate, pitch, fx}}: narrator, extra voices, and every character's `voice`."""
    out = {"NARRATOR": dict(pack["series"].get("narrator") or DEFAULTS["narrator"])}
    out.update({str(k).upper(): v for k, v in (pack["series"].get("extra_voices") or {}).items()})
    for m in pack["characters"]:
        if isinstance(m.get("voice"), dict):
            out[str(m["name"]).upper()] = m["voice"]
    return out


# ----------------------------------------------------------------------------- lint
def lint(key: str, root: Path | None = None, series_dir: Path | None = None) -> dict:
    """{"errors": [...], "warnings": [...], "info": [...]}: friendly messages, never raises for content problems."""
    from aurora import manga

    rep = {"errors": [], "warnings": [], "info": []}
    err, warn = rep["errors"].append, rep["warnings"].append
    try:
        pack = load_pack(key, root)
    except PackError as exc:
        err(str(exc))
        return rep
    pdir, s = pack["dir"], pack["series"]
    rel = f"stories/{pdir.name}"
    for k in ("title", "channel"):
        if not str(s.get(k) or "").strip():
            err(f"{rel}/series.yaml: '{k}' is required")
    if s.get("format") not in FORMATS:
        err(f"{rel}/series.yaml: format must be one of {', '.join(FORMATS)} (got {s.get('format')!r})")
    ppc = s.get("parts_per_chapter")
    if not isinstance(ppc, int) or ppc < 10:
        err(f"{rel}/series.yaml: parts_per_chapter must be a whole number >= 10 (got {ppc!r})")
        ppc = 10
    if s.get("format") == "manga" and not str(s.get("opening_card") or "").strip():
        warn(f"{rel}/series.yaml: manga series should have an opening_card (respect line shown on every cover)")
    nv = s.get("narrator") or {}
    if not VOICE_RE.match(str(nv.get("voice", ""))):
        err(f"{rel}/series.yaml: narrator.voice must be an Edge-TTS voice like en-US-AvaNeural (got {nv.get('voice')!r})")
    # characters
    chars = pack["characters"]
    if not chars:
        err(f"{rel}/characters/: add at least one <name>.yaml with name, description (and voice for manga)")
    seen = set()
    for m in chars:
        name, f = str(m["name"]), Path(m["_file"]).name
        if name.lower() in seen:
            err(f"{rel}/characters/{f}: duplicate character name '{name}'")
        seen.add(name.lower())
        if len(str(m["description"])) < 15:
            warn(f"{rel}/characters/{f}: description is very short; a locked visual description keeps the look consistent")
        if len(str(m["description"])) > 260:
            warn(f"{rel}/characters/{f}: description is over 260 characters; it will be shortened to fit the 800-character image prompt limit")
        v = m.get("voice")
        if s.get("format") == "manga" and not isinstance(v, dict):
            err(f"{rel}/characters/{f}: manga series need a voice: {{voice: en-IN-PrabhatNeural, rate: '+0%', pitch: '+0Hz'}}")
        if isinstance(v, dict):
            if not VOICE_RE.match(str(v.get("voice", ""))):
                err(f"{rel}/characters/{f}: voice.voice must be an Edge-TTS voice like en-IN-PrabhatNeural (got {v.get('voice')!r})")
            if v.get("rate") is not None and not re.match(r"^[+-]\d+%$", str(v["rate"])):
                err(f"{rel}/characters/{f}: voice.rate must look like '+10%' or '-8%' (got {v['rate']!r})")
            if v.get("pitch") is not None and not re.match(r"^[+-]\d+Hz$", str(v["pitch"])):
                err(f"{rel}/characters/{f}: voice.pitch must look like '+0Hz' or '-12Hz' (got {v['pitch']!r})")
            if v.get("fx") and v["fx"] not in manga.FX:
                err(f"{rel}/characters/{f}: voice.fx must be one of {', '.join(manga.FX)} (got {v['fx']!r})")
        if m.get("seed") is not None and not isinstance(m["seed"], int):
            err(f"{rel}/characters/{f}: seed must be a whole number")
        for ref in m.get("references") or []:
            if not (Path(m["_dir"]) / str(ref.get("file", ""))).exists():
                err(f"{rel}/characters/{f}: reference image '{ref.get('file')}' is missing from characters/")
    # retention (hook-first opening)
    ret = s.get("retention")
    if ret is not None:
        known = {"hook_first": bool, "previously": bool, "punch_zoom": bool, "hook_s": (int, float), "title_s": (int, float), "previously_s": (int, float)}
        if not isinstance(ret, dict):
            err(f"{rel}/series.yaml: retention must be a mapping like {{hook_first: true, hook_s: 1.5, previously: true, previously_s: 1.2}}")
        else:
            for k, v in ret.items():
                if k not in known:
                    err(f"{rel}/series.yaml: retention.{k} is not a setting (use {', '.join(known)})")
                elif not isinstance(v, known[k]) or isinstance(v, bool) != (known[k] is bool):
                    err(f"{rel}/series.yaml: retention.{k} must be {'true/false' if known[k] is bool else 'a number of seconds'}")
                elif k in ("hook_s", "title_s") and not 0.5 <= v <= 1.5:
                    err(f"{rel}/series.yaml: retention.{k} must be between 0.5 and 1.5 seconds (first-frame hook)")
                elif k == "previously_s" and not 0.5 <= v <= 1.2:
                    err(f"{rel}/series.yaml: retention.previously_s must be between 0.5 and 1.2 seconds")
    # hard rules
    rules = s.get("rules") or {}
    cnames = {str(m["name"]).split()[0].lower(): str(m["name"]) for m in chars} | {str(m["name"]).lower(): str(m["name"]) for m in chars}
    if rules.get("hard_rules") is not None and not (isinstance(rules["hard_rules"], list) and all(isinstance(x, str) and x.strip() for x in rules["hard_rules"])):
        err(f"{rel}/series.yaml: rules.hard_rules must be a list of non-empty sentences")
    fr = rules.get("forbidden_relationships")
    if fr is not None and not isinstance(fr, list):
        err(f"{rel}/series.yaml: rules.forbidden_relationships must be a list of {{pair: [A, B], type: romantic, note: ...}}")
        fr = []
    for k, r in enumerate(fr or []):
        at = f"{rel}/series.yaml: rules.forbidden_relationships[{k}]"
        pair = r.get("pair") if isinstance(r, dict) else None
        if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)):
            err(f"{at}: needs pair: [NameA, NameB]")
            continue
        if pair[0].lower() == pair[1].lower():
            err(f"{at}: the pair must be two different characters")
        for nm in pair:
            if nm.lower() not in cnames:
                close = difflib.get_close_matches(nm.lower(), list(cnames), n=1)
                err(f"{at}: '{nm}' is not a character in characters/" + (f" (did you mean '{cnames[close[0]]}'?)" if close else ""))
        if not str(r.get("type") or "").strip():
            err(f"{at}: type is required (e.g. romantic)")
        elif str(r["type"]).lower() != "romantic":
            warn(f"{at}: only type 'romantic' is checked by keywords; other types are given to research as a rule and checked by Gemini")
        if not str(r.get("note") or "").strip():
            warn(f"{at}: add a note (the sentence shown to research verbatim)")
    for m in chars:
        hr = m.get("hard_rules")
        if hr is not None and not (isinstance(hr, list) and all(isinstance(x, str) and x.strip() for x in hr)):
            err(f"{rel}/characters/{Path(m['_file']).name}: hard_rules must be a list of non-empty sentences")
    # chapters
    allowed = {str(m["name"]).upper() for m in chars} | {str(m["name"]).split()[0].upper() for m in chars} | {"NARRATOR"} | \
        {str(k).upper() for k in (s.get("extra_voices") or {})}
    voices = pack_voices(pack)
    banned = re.compile(rf"\b({BANNED}" + "".join(f"|{re.escape(str(w))}" for w in (s.get("rules") or {}).get("banned_words", [])) + r")\b", re.I)
    nums = sorted(pack["chapters"])
    if not nums:
        rep["info"].append(f"{rel}: no authored chapters; research writes them and saves them to chapters/CH_NN.md")
    if nums and nums != list(range(1, nums[-1] + 1)):
        warn(f"{rel}/chapters/: chapter numbers have gaps {nums}; chapters render in order and a missing one is written by research")
    for n, f in pack["chapters"].items():
        where = f"{rel}/chapters/{f.name}"
        try:
            ch = manga.parse_chapter(f.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            err(f"{where}: cannot be read ({str(exc)[:80]})")
            continue
        if ch["number"] != n:
            warn(f"{where}: the heading says Chapter {ch['number']} but the file is CH_{n:02d}")
        if not ch["parts"]:
            err(f"{where}: no parts found; each part starts with a line like: ## Part 1 — \"Title\"")
            continue
        found = [p["number"] for p in ch["parts"]]
        if found != list(range(1, len(found) + 1)):
            missing = [k for k in range(1, max(found) + 1) if k not in found]
            st_ch = _state_chapter(research.load_series(pack["state_key"], Path(series_dir or research.SERIES_DIR)), n) or {}
            known = {int(r["part"]) for r in st_ch.get("rendered", [])}
            if len(set(found)) == len(found) and found == sorted(found) and (RESEARCH_MARK in f.read_text(encoding="utf-8") or (missing and set(missing) <= known)):
                warn(f"{where}: parts {missing} are missing; they are rebuilt from the series state before the next render (research-written chapter)")
            else:
                err(f"{where}: parts must be numbered 1..{len(found)} in order (found {found})")
        if len(ch["parts"]) > ppc:
            err(f"{where}: {len(ch['parts'])} parts but parts_per_chapter is {ppc}")
        elif len(ch["parts"]) < ppc:
            warn(f"{where}: {len(ch['parts'])} of {ppc} parts written; research writes the rest")
        for p in ch["parts"]:
            if not 3 <= len(p["panels"]) <= 10:
                err(f"{where}, part {p['number']}: needs 3-10 numbered panels (found {len(p['panels'])})")
            for pn in p["panels"]:
                at = f"{where}, part {p['number']}, panel {pn['n']}"
                if not pn["desc"] and not pn["lines"]:
                    err(f"{at}: empty panel (describe the picture or add a line)")
                for ln in pn["lines"]:
                    spk = ln["speaker"]
                    if spk not in allowed:
                        close = difflib.get_close_matches(spk, sorted(allowed), n=1)
                        err(f"{at}: speaker '{spk}' has no file in characters/" + (f" (did you mean '{close[0]}'?)" if close else ""))
                    elif s.get("format") == "manga" and spk not in voices and spk.split()[0] not in voices and not any(spk.split()[0] == k.split()[0] for k in voices):
                        err(f"{at}: speaker '{spk}' has no voice (add voice: to characters/ or extra_voices in series.yaml)")
                    if len(ln["text"]) > 220:
                        warn(f"{at}: a line of {len(ln['text'])} characters will not fit one bubble; split it")
                hit = scan_forbidden(pn["desc"] + ". " + " ".join(ln["text"] for ln in pn["lines"]), s)
                if hit:
                    err(f"{at}: hard rule broken, {hit}")
                bad = banned.search(pn["desc"] + " " + " ".join(ln["text"] for ln in pn["lines"]))
                if bad:
                    err(f"{at}: '{bad.group(0)}' is not allowed (all-ages rule)")
            try:
                manga.part_to_comic(ch, p, chars, s["title"], mode=s.get("format", "manga"))
            except ValueError as exc:
                err(f"{where}, part {p['number']}: {exc}")
    return rep


def format_report(slug: str, rep: dict) -> str:
    lines = [f"[story] {slug}: {len(rep['errors'])} error(s), {len(rep['warnings'])} warning(s)"]
    lines += [f"  ERROR   {m}" for m in rep["errors"]] + [f"  warning {m}" for m in rep["warnings"]] + [f"  info    {m}" for m in rep["info"]]
    return "\n".join(lines)


# ----------------------------------------------------------------------------- hard rules (forbidden relationships etc.)
ROMANCE = re.compile(r"\b(love[sd]?|loving|in love|lover\w*|kiss\w*|romanc\w*|romantic\w*|crush\w*|blush\w*|sweetheart|darling|beloved|dating|date night|"
                     r"affection\w*|passion\w*|embrac\w*|cuddl\w*|couple|girlfriend|boyfriend|flirt\w*|attracted|attraction|longing|"
                     r"hold(?:s|ing)? hands|heart (?:races|raced|flutter\w*|skips?)|butterflies|tender gaze|gazes? into (?:her|his) eyes|yearn\w*)\b", re.I)


def _name_re(name: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(str(name).split()[0])}\b", re.I)


def forbidden_relationships(series: dict) -> list[dict]:
    return [r for r in ((series.get("rules") or {}).get("forbidden_relationships") or []) if isinstance(r, dict)]


def pack_rules(pack: dict) -> list[str]:
    """Every hard rule of the pack, VERBATIM and de-duplicated: series rules.hard_rules, forbidden_relationships notes, characters' hard_rules."""
    out = [str(x) for x in ((pack["series"].get("rules") or {}).get("hard_rules") or [])]
    for r in forbidden_relationships(pack["series"]):
        a, b = (r.get("pair") or ["?", "?"])[:2]
        out.append(str(r.get("note") or f"There must NEVER be any {r.get('type', 'forbidden')} relationship, interest or hint between {a} and {b}."))
    for m in pack["characters"]:
        out += [str(x) for x in (m.get("hard_rules") or [])]
    return list(dict.fromkeys(out))


class Verdict(research.BaseModel):
    violates: bool
    reason: str = ""


def scan_forbidden(text: str, series: dict) -> str | None:
    """Keyword check: a segment (line/panel/sentence) naming BOTH members of a forbidden pair together with a cue of that relationship."""
    for r in forbidden_relationships(series):
        a, b = (r.get("pair") or [None, None])[:2]
        if not a or not b or str(r.get("type", "romantic")).lower() != "romantic":
            continue
        na, nb = _name_re(a), _name_re(b)
        for seg in re.split(r"[\n.!?]+", text):
            if na.search(seg) and nb.search(seg) and ROMANCE.search(seg):
                return f"romance cue between {a} and {b}: '{seg.strip()[:80]}'"
    return None


def make_guard(pack: dict):
    """guard(text, client, model) -> violation message or None: keyword scan, then (when Gemini is available) a yes/no check per forbidden pair."""
    series = pack["series"]

    def guard(text: str, client=None, model: str | None = None) -> str | None:
        hit = scan_forbidden(text, series)
        if hit:
            return hit
        if client is not None:
            for r in forbidden_relationships(series):
                a, b = (r.get("pair") or ["?", "?"])[:2]
                try:
                    v = research._call_json(client, model, f"Does the following story text contain ANY {r.get('type', 'romantic')} relationship, interest, tension or hint between "
                                            f"{a} and {b} (including subtle hints, jealousy or longing)? Answer with violates=true or false and a short reason. TEXT: {text[:6000]}", Verdict)
                except Exception:  # noqa: BLE001 - the keyword scan already ran; an unavailable checker never blocks
                    continue
                if v.violates:
                    return f"{a}/{b} {r.get('type', 'romantic')} hint (checker: {v.reason[:80]})"
        return None

    return guard


# ----------------------------------------------------------------------------- cadence
def entries(channel: dict) -> list[dict]:
    """channels[].series -> [{slug, per_day, enabled}] (a plain string means one part a day)."""
    out = []
    for e in channel.get("series") or []:
        e = {"slug": e} if isinstance(e, str) else dict(e)
        e.setdefault("per_day", 1)
        e.setdefault("enabled", True)
        out.append(e)
    return out


def _daily(state: dict, today: str) -> int:
    return int((state.get("daily") or {}).get(today, 0))


def pick_series(channel: dict, series_dir: Path | None = None, today: str | None = None, root: Path | None = None):
    """First enabled series that is still due today and whose pack lints clean. Returns (entry or None, report lines, any_invalid)."""
    sdir, today = Path(series_dir or research.SERIES_DIR), today or date.today().isoformat()
    reports, invalid = [], False
    for e in entries(channel):
        if not e["enabled"] or int(e["per_day"]) <= 0:
            continue
        rep = lint(e["slug"], root, sdir)
        if rep["errors"]:
            invalid = True
            reports.append(format_report(e["slug"], rep))
            continue
        pack = load_pack(e["slug"], root)
        if _daily(research.load_series(pack["state_key"], sdir), today) >= int(e["per_day"]):
            continue
        return e, reports, invalid
    return None, reports, invalid


# ----------------------------------------------------------------------------- saving researched parts back into the pack
RESEARCH_MARK = "<!-- written-by: research (edit freely; parts missing here are rebuilt from the series state) -->"


def _part_block(pack: dict, part: int, comic: dict) -> str:
    """One `## Part N` block in the authored schema."""
    names = {str(m["name"]).upper() for m in pack["characters"]}
    out = [f"## Part {part} — \"{(comic.get('hook') or f'Part {part}').strip().rstrip('.')}\""]
    for i, p in enumerate(comic["panels"], 1):
        out.append("%d. %s" % (i, " ".join(p["image_prompt"].split())))
        if p.get("lines"):
            for ln in p["lines"]:
                emo = f" ({ln['emotion']})" if ln.get("emotion") else ""
                out.append(f"   **{ln['speaker']}**{emo}: \"{ln['text']}\"")
        else:
            if p.get("narration"):
                out.append(f"   **NARRATOR** (calm): \"{p['narration'].strip()}\"")
            if p.get("bubble"):
                who, _, said = p["bubble"].partition(":")
                spk = who.strip().upper() if said and who.strip().upper() in names else (str(pack['characters'][0]['name']).upper() if pack["characters"] else "NARRATOR")
                out.append(f"   **{spk}**: \"{(said if said and who.strip().upper() in names else p['bubble']).strip()}\"")
    if comic.get("cta"):
        out.append(f"End card: *{comic['cta']}*")
    return "\n".join(out) + "\n"


def _state_chapter(state: dict, number: int) -> dict | None:
    ch = (state.get("chapters") or {}).get(str(number))
    cur = state.get("chapter")
    return ch or (cur if cur and int(cur.get("number", 0)) == number else None)


def sync_chapter_md(pack: dict, state: dict, number: int | None = None) -> Path | None:
    """Make chapters/CH_NN.md contain EVERY part the series state has rendered, in order (atomic write). Parts already in the file are kept
    verbatim (authored or edited); missing ones are rebuilt from the part sidecars. Returns the path when the file was (re)written."""
    cur = state.get("chapter") or {}
    number = int(number if number is not None else cur.get("number", 0) or 0)
    ch = _state_chapter(state, number) if number else None
    if not ch or not ch.get("rendered"):
        return None
    path = Path(pack["dir"]) / "chapters" / f"CH_{number:02d}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    chunks = re.split(r"(?m)^(?=##\s+Part\s+\d+\b)", text)
    head = chunks[0] if text else f"# {pack['series']['title']} — Chapter {number}: \"{ch.get('title') or number}\"\n{RESEARCH_MARK}\n"
    blocks = {int(re.match(r"##\s+Part\s+(\d+)", c).group(1)): c.strip("\n") + "\n" for c in chunks[1:]}
    added = False
    for r in sorted(ch["rendered"], key=lambda r: int(r["part"])):
        n = int(r["part"])
        if n in blocks:
            continue
        try:
            side = json.loads(Path(r["sidecar"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue  # the sidecar is gone: that part stays a gap (lint warns, the series state still drives rendering)
        cta = f"Part {n + 1} tomorrow" if n < int(ch["parts"]) else f"Chapter {number} finale"
        blocks[n] = _part_block(pack, n, {"hook": side.get("hook"), "panels": side["panels"], "cta": cta})
        added = True
    if not added and path.exists():
        return None
    body = head.rstrip("\n") + "\n\n" + "\n".join(blocks[k] for k in sorted(blocks))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)  # atomic: a crash never leaves a half-written chapter
    return path


def comic_mod_retention(series: dict) -> dict:
    """The series-pack `retention:` block (hook-first opening, Previously… card); empty/absent -> the classic cover card."""
    return dict(series.get("retention") or {})


def _research_to_lines(comic: dict, pack: dict) -> dict:
    """Researched panels (narration + bubble) -> the authored schema (`lines`) for a manga series; style head applied for any format."""
    names = {str(m["name"]).upper(): m for m in pack["characters"]}
    s = pack["series"]
    for p in comic["panels"]:
        action = bool(p.get("action"))
        if s.get("style_head"):
            p["style_head"] = s["style_head"] + (s.get("action_style", "") if action else "")
        if s.get("format") == "manga":
            lines = []
            if p.get("narration"):
                lines.append({"speaker": "NARRATOR", "emotion": "calm", "kind": "caption", "text": p["narration"].strip()})
            if p.get("bubble"):
                who, _, said = p["bubble"].partition(":")
                spk = who.strip().upper() if said and who.strip().upper() in names else (str(pack["characters"][0]["name"]).upper() if pack["characters"] else "NARRATOR")
                lines.append({"speaker": spk, "emotion": "", "kind": "speech", "text": (said if said and who.strip().upper() in names else p["bubble"]).strip()})
            p["lines"], p["narration"], p["bubble"] = lines, "", ""
    return comic


# ----------------------------------------------------------------------------- rendering
def _start_chapter(pack: dict, state: dict, number: int, script: dict | None, parts: int) -> dict:
    s = pack["series"]
    outline = ([{"role": "script", "summary": p["title"]} for p in script["parts"]] if script else [])
    outline += [{"role": "continue", "summary": "Continue the story naturally from the previous parts; build toward the chapter's ending."}] * (parts - len(outline))
    outline[-1] = {"role": "finale", "summary": outline[-1]["summary"] if script and len(script["parts"]) >= parts else "Resolve this chapter's main conflict and open a hook for the next chapter."}
    return {"number": number, "title": (script or {}).get("title") or f"Chapter {number}", "summary": "", "outline": outline, "parts": parts,
            "parts_done": 0, "recaps": [], "rendered": [], "complete": False}


def render_next(key: str, cfg: dict | None = None, out_dir: Path | None = None, switchboard=None, music: bool = True, series_dir: Path | None = None,
                root: Path | None = None, voice: str | None = None, force: bool = False, client=None, **kw) -> dict:
    """Render the NEXT part of a series from its pack: authored script if present, else research (saved back into the pack).
    Raises PackError for an invalid pack and RuntimeError when the shared NIM cap leaves less than the series' reserve."""
    from aurora import cast as cast_mod
    from aurora import comic, manga
    from aurora.costs import COST_DIR, credits_today

    cfg = cfg if cfg is not None else yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    panels_n = int(kw.pop("panels", 6) or 6)
    sdir = Path(series_dir) if series_dir else research.SERIES_DIR
    try:  # repair a chapter file with missing parts from the series state BEFORE linting (never skip a series over a gap)
        pre = load_pack(key, root)
        sync_chapter_md(pre, research.load_series(pre["state_key"], sdir))
    except Exception as exc:  # noqa: BLE001 - the lint below reports real problems
        print(f"[story] {key}: chapter repair skipped ({str(exc)[:80]})", file=sys.stderr)
    rep = lint(key, root, sdir)
    if rep["errors"]:
        raise PackError(format_report(key, rep))
    pack = load_pack(key, root)
    s, sk = pack["series"], pack["state_key"]
    state = research.load_series(sk, sdir)
    ch = state.get("chapter")
    parts = int(s["parts_per_chapter"])
    if not ch or ch.get("complete"):
        number = int(state.get("chapters_done", 0)) + 1
        script = manga.parse_chapter(pack["chapters"][number].read_text(encoding="utf-8")) if number in pack["chapters"] else None
        ch = _start_chapter(pack, state, number, script, parts) if script else None
    else:
        number = int(ch["number"])
        script = manga.parse_chapter(pack["chapters"][number].read_text(encoding="utf-8")) if number in pack["chapters"] else None
    if state.get("series_title") != s["title"]:  # the pack is the source of truth for the series title
        state.update(series_title=s["title"], title=s["title"])
    state.setdefault("seed", int(__import__("zlib").crc32(sk.encode()) % 2**31 or 7))
    state.update(channel=sk)
    if ch is not None:
        state["chapter"] = ch
    research._write_state(sk, state, sdir)
    cfg = {**cfg, "comic": {**cfg.get("comic", {}), "chapter_parts": parts, "voices": pack_voices(pack),
                                                   "retention": comic_mod_retention(s)}}
    cast = cast_mod.load_cast(sk)
    m = int((state.get("chapter") or {}).get("parts_done", 0)) + 1
    spart = next((p for p in (script or {}).get("parts", []) if p["number"] == m), None)
    authored = spart is not None
    if authored:
        comic_dict = manga.part_to_comic(script, spart, cast, s["title"], style_head=s["style_head"], action_style=s["action_style"],
                                         opening_card=s["opening_card"], mode=s["format"])
        research.finish_retention(comic_dict, (ch or {}).get("recaps", []), m)
        info = {"number": int(ch["number"]), "title": ch["title"], "part": m, "parts": int(ch["parts"]),
                "beat": {"role": "script", "summary": spart["title"]}, "series_title": s["title"], "final": m >= int(ch["parts"])}
    else:
        r = research.research_part(sk, "", language=s["language"], panels=panels_n, cfg=cfg, client=client, series_dir=sdir, world=pack["world"], rules=pack_rules(pack), guard=make_guard(pack))
        comic_dict, info = _research_to_lines(r["comic"], pack), r["chapter"]
        if s.get("opening_card"):
            comic_dict["cover_note"] = s["opening_card"]
        if s["format"] == "manga":
            comic_dict["end_card"] = list(research.end_card_for(info["number"], info["part"], info["parts"]))
    comic_dict["playlist"] = s["playlist"]
    cap = int(os.environ.get("AURORA_NIM_DAILY_CAP") or cfg.get("providers", {}).get("nvidia_nim", {}).get("max_credits_per_day", 25))
    need, reserve = len(comic_dict["panels"]), int(s.get("reserve_credits", 0))
    used = credits_today("nvidia_nim", Path(cfg.get("cost_dir", COST_DIR)))
    if reserve and not force and used + need > cap - reserve:
        raise RuntimeError(f"NIM credits: {used} used today + {need} needed > cap {cap} - {reserve} kept for the other series; try again after the reset (or --force)")
    out_dir = Path(out_dir or ROOT / "outputs" / "final" / s["channel"] / "story" / pack["slug"])
    res = comic.make_comic_reel(sk, "", out_dir, cfg=cfg, language=s["language"], voice=voice or (s.get("narrator") or {}).get("voice", "en-US-AvaNeural"),
                                comic=comic_dict, chapter=info, switchboard=switchboard, music=music, series_dir=sdir, **kw)
    side_path = Path(res["sidecar"])
    side = json.loads(side_path.read_text(encoding="utf-8"))
    side.update(story=pack["slug"], series_key=sk, channel=s["channel"] or side.get("channel"), playlist=s["playlist"])
    side_path.write_text(json.dumps(side, indent=2, ensure_ascii=False), encoding="utf-8")
    if not authored:  # write the WHOLE chapter so far (all parts of the series state, in order), atomically
        sync_chapter_md(pack, research.load_series(sk, sdir), info["number"])
    st = research.load_series(sk, sdir)
    daily = st.setdefault("daily", {})
    daily[date.today().isoformat()] = int(daily.get(date.today().isoformat(), 0)) + 1
    research._write_state(sk, st, sdir)
    return {**res, "story": pack["slug"], "authored": authored}


def render_for_channel(cfg: dict, channel: dict, switchboard, out_dir: Path, voice: str | None = None, series_dir: Path | None = None,
                       on_event=None, **kw):
    """Scheduler entry. Returns the render result, or None to let the caller run the channel's legacy flow (no usable series).
    Raises RuntimeError when every series of the channel has had its daily parts."""
    entry, reports, invalid = pick_series(channel, series_dir)
    for line in reports:
        print(line, file=sys.stderr)
        if on_event:  # daily report / Approve tab: a skipped series is never silent
            first = next((l.strip() for l in line.splitlines()[1:] if "ERROR" in l), line.splitlines()[0])
            on_event("pack_lint_failed", line.splitlines()[0].split(":")[0].replace("[story] ", "") + " skipped: " + first.replace("ERROR", "").strip())
    if entry is None:
        if invalid:
            return None
        raise RuntimeError(f"{channel['name']}: every series is at its daily cadence (nothing due)")
    return render_next(entry["slug"], cfg, out_dir, switchboard, series_dir=series_dir, voice=voice, **kw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Story packs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("lint")
    l.add_argument("series", nargs="?")
    l.add_argument("--all", action="store_true")
    sub.add_parser("list")
    r = sub.add_parser("render")
    r.add_argument("series")
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for slug in list_packs():
            p = load_pack(slug)
            print(f"{slug}: {p['series']['title']} [{p['series']['format']}] channel={p['series']['channel']} chapters={sorted(p['chapters'])}")
        return 0
    if a.cmd == "lint":
        bad = 0
        for slug in (list_packs() if a.all or not a.series else [a.series]):
            rep = lint(slug)
            print(format_report(slug, rep))
            bad += bool(rep["errors"])
        return 1 if bad else 0
    res = render_next(a.series, force=a.force)
    print(json.dumps({k: res[k] for k in ("video", "title", "seconds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
