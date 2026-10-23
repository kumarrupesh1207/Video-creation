"""Locked cast per channel: assets/characters/<channel>/<name>.json (+ reference images).

The locked description is copied VERBATIM into every research prompt and image prompt, so a character never gets a new look.
New side characters are saved into the cast after their first appearance (first panel image becomes their reference).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAST_ROOT = ROOT / "assets" / "characters"
STYLE = "cel-shaded anime comic panel"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "character"


def load_cast(channel: str, root: Path = CAST_ROOT) -> list[dict]:
    """Locked members of a channel, protagonist first (then by name)."""
    members = []
    for f in sorted((Path(root) / channel).glob("*.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("name") and m.get("description"):
            m["_dir"] = str(f.parent)
            members.append(m)
    return sorted(members, key=lambda m: (m.get("role") != "protagonist", m["name"].lower()))


def merge_cast(sheet: list[dict], cast: list[dict]) -> list[dict]:
    """Character sheet with every locked member present, name + description VERBATIM (the model may not redesign them),
    protagonist first; characters the story added stay (they get saved after their first appearance)."""
    locked = {m["name"].lower(): m for m in cast}
    out = [{"name": m["name"], "description": m["description"], "style": STYLE} for m in cast]
    for c in sheet:
        if c["name"].lower() not in locked:
            out.append(c)
    return out


def prompt_text(cast: list[dict]) -> str:
    """Instruction block for the research prompt."""
    if not cast:
        return ""
    lines = "; ".join(f"{m['name']} ({m.get('role', 'character')}): {m['description']}" for m in cast)
    return ("LOCKED CAST (permanent characters of this channel): reuse them EXACTLY. Copy their name and description verbatim into "
            f"character_sheet; the first one is the protagonist of every story. Never change their look. {lines}. "
            "You may add NEW side characters, each with its own fixed description. ")


def reference_image(channel: str, root: Path = CAST_ROOT) -> Path | None:
    """Best reference picture of the protagonist (used as the IP-Adapter reference when ComfyUI has to draw the panels)."""
    for m in load_cast(channel, root):
        for ref in m.get("references", []):
            p = Path(m["_dir"]) / ref["file"]
            if p.exists():
                return p
    return None


def save_new_members(channel: str, comic: dict, panel_images: list[Path], root: Path = CAST_ROOT) -> list[str]:
    """After a successful render: characters that appeared in a panel but are not in the cast yet are saved (description from the
    sheet, first panel image they appear in as reference). Returns the names added. Locked members are never overwritten."""
    known = {m["name"].lower() for m in load_cast(channel, root)}
    added = []
    for c in comic["character_sheet"]:
        if c["name"].lower() in known:
            continue
        first = next((i for i, p in enumerate(comic["panels"]) if c["name"].lower() in [n.lower() for n in p.get("characters", [])]), None)
        if first is None or first >= len(panel_images):
            continue
        d = Path(root) / channel
        d.mkdir(parents=True, exist_ok=True)
        slug = _slug(c["name"])
        refs = []
        src = Path(panel_images[first])
        if src.exists():
            dest = d / f"{slug}_ref1{src.suffix}"
            shutil.copyfile(src, dest)
            refs.append({"file": dest.name, "from_panel": first, "use": "first appearance"})
        (d / f"{slug}.json").write_text(json.dumps({
            "name": c["name"], "role": "side character", "description": c["description"], "distinguishing_marks": "",
            "personality": "", "voice_notes": "", "first_story": comic["series"]["title"], "references": refs,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        added.append(c["name"])
    return added
