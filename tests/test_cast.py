import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from aurora import cast, research

KAELEN = {"name": "Kaelen", "role": "protagonist", "description": "Young man with silver spiky hair, sharp emerald eyes, wearing a tattered dark indigo cloak "
          "over tactical gear, glowing blue runes on his forearm.", "references": []}


def _cast_dir(tmp, channel="main"):
    d = tmp / "cast" / channel
    d.mkdir(parents=True)
    (d / "kaelen.json").write_text(json.dumps(KAELEN), encoding="utf-8")
    return tmp / "cast"


def test_repo_cast_for_main_is_locked_with_references():
    members = cast.load_cast("main")
    kael = next(m for m in members if m["name"] == "Kaelen")
    assert kael["role"] == "protagonist" and "emerald eyes" in kael["description"] and kael["personality"] and kael["voice_notes"]
    assert 1 <= len(kael["references"]) <= 3
    assert all((Path(kael["_dir"]) / r["file"]).exists() for r in kael["references"])
    assert cast.reference_image("main").exists()


def test_merge_forces_the_locked_description_and_keeps_new_side_characters(workspace_tmp_path):
    root = _cast_dir(workspace_tmp_path)
    drifted = [{"name": "Kaelen", "description": "blond boy with red eyes in a white coat", "style": "x"},
               {"name": "Mira", "description": "red-haired swordswoman in gold armor", "style": "x"}]
    merged = cast.merge_cast(drifted, cast.load_cast("main", root))
    assert [c["name"] for c in merged] == ["Kaelen", "Mira"]  # protagonist first, side character kept
    assert merged[0]["description"] == KAELEN["description"]  # verbatim, the model's redesign is thrown away


def test_research_prompt_contains_the_locked_description_and_output_is_forced_back(workspace_tmp_path):
    root = _cast_dir(workspace_tmp_path)
    story = {"series": {"title": "T", "part": 1, "total_parts": 2, "recap": "r"},
             "character_sheet": [{"name": "Kaelen", "description": "blond boy with red eyes in a white coat, totally new look"}],
             "panels": [{"image_prompt": "a ruined city at dusk", "narration": "It began tonight.", "characters": ["Kaelen"]}] * 5,
             "cliffhanger": "c", "cta": "Part 2 coming soon"}
    seen = {}

    def gen(**kw):
        seen["prompt"] = kw["contents"]
        return SimpleNamespace(text=json.dumps(story))

    cfg = {"comic": {"cast_lock": True}}
    out = research.research_comic("main", "p", cfg=cfg, client=SimpleNamespace(models=SimpleNamespace(generate_content=gen)),
                                  series_dir=workspace_tmp_path / "s", cast_root=root)
    assert KAELEN["description"] in seen["prompt"] and "LOCKED CAST" in seen["prompt"] and "protagonist" in seen["prompt"]
    sheet = out["comic"]["character_sheet"]
    assert sheet[0]["name"] == "Kaelen" and sheet[0]["description"] == KAELEN["description"]
    # the image prompts of every panel embed the locked description verbatim
    p = research.panel_prompt(out["comic"]["panels"][0], sheet)
    assert KAELEN["description"] in p and len(p) <= 780


def test_cast_lock_off_ignores_the_cast_and_fallback_uses_it_when_on(workspace_tmp_path, monkeypatch):
    root = _cast_dir(workspace_tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    off = research.research_comic("main", "a swordsman", cfg={"comic": {"cast_lock": False}}, series_dir=workspace_tmp_path / "a", cast_root=root)
    assert off["comic"]["character_sheet"][0]["name"] == "Kaito"  # the template's default hero, no cast
    on = research.research_comic("main", "a swordsman", cfg={"comic": {"cast_lock": True}}, series_dir=workspace_tmp_path / "b", cast_root=root)
    sheet = on["comic"]["character_sheet"]
    assert sheet[0]["name"] == "Kaelen" and sheet[0]["description"] == KAELEN["description"]
    assert all(p["characters"] == ["Kaelen"] for p in on["comic"]["panels"]) and "Kaito" not in json.dumps(on["comic"]["panels"])


def test_new_side_characters_join_the_cast_after_first_appearance_locked_ones_untouched(workspace_tmp_path):
    root = _cast_dir(workspace_tmp_path)
    imgs = []
    for i in range(3):
        p = workspace_tmp_path / f"p{i}.png"
        Image.new("RGB", (64, 96), (i * 60, 10, 10)).save(p)
        imgs.append(p)
    comic = {"series": {"title": "T", "part": 1}, "character_sheet": [{"name": "Kaelen", "description": "CHANGED"},
             {"name": "Mira", "description": "red-haired swordswoman in gold armor"}],
             "panels": [{"characters": ["Kaelen"]}, {"characters": ["Kaelen", "Mira"]}, {"characters": ["Mira"]}]}
    assert cast.save_new_members("main", comic, imgs, root) == ["Mira"]
    mira = json.loads((root / "main" / "mira.json").read_text(encoding="utf-8"))
    assert mira["role"] == "side character" and mira["references"][0]["from_panel"] == 1 and (root / "main" / "mira_ref1.png").exists()
    assert json.loads((root / "main" / "kaelen.json").read_text(encoding="utf-8"))["description"] == KAELEN["description"]  # never overwritten
    assert cast.save_new_members("main", comic, imgs, root) == []  # already in the cast
    assert [m["name"] for m in cast.load_cast("main", root)] == ["Kaelen", "Mira"]
