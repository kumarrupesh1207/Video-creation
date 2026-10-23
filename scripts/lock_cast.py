"""One-off: lock the characters of a rendered comic as the permanent cast of a channel.

    python scripts/lock_cast.py <sidecar.json> <channel>

Writes assets/characters/<channel>/<slug>.json (verbatim sheet description + role/personality/voice notes) and copies the 1-3
clearest face shots (largest detected face) of that character from the reel's panel images. Never overwrites an existing member.
"""
import glob
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aurora import comic_layout  # noqa: E402

NOTES = {  # hand-written from the story text of "Echoes of the Aether" (Part 1)
    "Kaelen": {
        "role": "protagonist",
        "marks": "silver spiky hair, emerald eyes, tattered dark indigo hooded cloak over tactical gear, glowing blue runes on the forearm",
        "personality": "Curious and brave; hesitant at first, then determined and defiant when cornered. Earnest, short lines "
                       "('Is this the source of the ancients?', 'Come and face me then, monster!').",
        "voice_notes": "Dialogue appears only as speech bubbles. Narration (a separate narrator, never Kaelen) uses the channel voice "
                       "en-US-AvaNeural. Keep Kaelen's lines short and emotional, never sarcastic.",
    },
}


def main(sidecar: str, channel: str) -> None:
    side = json.loads(Path(sidecar).read_text(encoding="utf-8"))
    root = Path("assets/characters") / channel
    root.mkdir(parents=True, exist_ok=True)
    job_stem = "comic_*" + re.sub(r"[^a-z0-9]+", "-", f"{channel}-part{side['part']}-{side['series']['title']}".lower()).strip("-")[:24]
    for c in side["character_sheet"]:
        slug = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-")
        target = root / f"{slug}.json"
        if target.exists():
            print("exists, kept:", target)
            continue
        scored = []
        for i, p in enumerate(side["panels"]):
            if c["name"].lower() not in [n.lower() for n in p.get("characters", [])]:
                continue
            dirs = sorted(glob.glob(f"outputs/{job_stem}_p{i}*"))
            imgs = [f for d in dirs for f in glob.glob(f"{d}/images/nvidia_nim.*")]
            if not imgs:
                continue
            img = Path(imgs[-1])  # the retry/regenerated version when there is one
            faces = comic_layout.detect_faces(img)
            area = max(((f[2] - f[0]) * (f[3] - f[1]) for f in faces), default=0)
            scored.append((area, i, img))
        refs = []
        for k, (area, i, img) in enumerate(sorted(scored, reverse=True)[:3], 1):
            dest = root / f"{slug}_ref{k}{img.suffix}"
            shutil.copyfile(img, dest)
            refs.append({"file": dest.name, "from_panel": i, "face_area_px": int(area)})
        n = NOTES.get(c["name"], {})
        data = {"name": c["name"], "role": n.get("role", "side character"), "description": c["description"],
                "distinguishing_marks": n.get("marks", ""), "personality": n.get("personality", ""),
                "voice_notes": n.get("voice_notes", ""), "first_story": side["series"]["title"], "references": refs}
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print("wrote", target, [r["file"] for r in refs])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
