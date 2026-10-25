import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from aurora import cast as cast_mod
from aurora import comic, comic_layout, manga, research, story

SCRIPT = (manga.ROOT / "stories" / "veer" / "chapters" / "CH_01.md").read_text(encoding="utf-8")


def test_parser_reads_the_real_chapter():
    ch = manga.parse_chapter(SCRIPT)
    assert ch["number"] == 1 and ch["title"] == "The Face in the Stone" and len(ch["parts"]) == 10
    p1 = ch["parts"][0]
    assert p1["title"] == "Navkashi" and len(p1["panels"]) == 4
    lines = [ln for pn in p1["panels"] for ln in pn["lines"]]
    assert [(l["speaker"], l["kind"]) for l in lines] == [("NARRATOR", "caption"), ("VEER", "speech"), ("HRITHVIKA", "speech"), ("VEER", "whisper")]
    assert lines[1]["text"].startswith("Six more panels") and p1["end_card"] == ("To be continued...", "Part 2 tomorrow")
    assert ch["parts"][4]["panels"][0]["lines"][0]["kind"] == "verse"  # parchment-box verse
    assert ch["parts"][8]["panels"][3]["lines"][0]["kind"] == "shout"  # "NOT - MY - CITY!"
    assert ch["parts"][2]["end_card"][0] == "What are we actually?"
    last = ch["parts"][9]
    assert last["end_card"][0].startswith("Chapter 1") and "Chapter 2" in last["end_card"][1]
    assert [l["speaker"] for pn in last["panels"] for l in pn["lines"]] == ["HRIDYANSH", "VEER", "HRIDYANSH", "VEER", "VYOM", "SMARAN"]
    assert "'When the seventh river forgets" in ch["parts"][1]["panels"][2]["lines"][0]["text"]


def test_part_to_comic_marks_scenery_action_and_respect_card():
    ch = manga.parse_chapter(SCRIPT)
    cast = cast_mod.load_cast("veer")
    assert [m["name"] for m in cast][0] == "Veer" and {m["name"] for m in cast} >= {"Hrithvika", "Tara", "Hridyansh", "Vyom", "Jalaghna", "Smaran"}
    c = manga.part_to_comic(ch, ch["parts"][0], cast, "VEER – Wrath of the Forgotten God")
    assert c["panels"][0]["scenery"] and c["panels"][0]["characters"] == [] and c["panels"][0]["shot"] == "wide"
    assert c["cover_note"] == manga.RESPECT_CARD and c["end_card"] == ["To be continued...", "Part 2 tomorrow"] and c["hook"] == "Navkashi"
    assert "ink" in c["panels"][1]["style_head"] and "Veer" in c["panels"][1]["characters"]
    nine = manga.part_to_comic(ch, ch["parts"][8], cast, "S")
    assert any(p["action"] and "speed lines" in p["style_head"] for p in nine["panels"])
    assert research.panel_prompt(c["panels"][1], c["character_sheet"]).count("Veer:") == 1  # locked description is in the prompt
    assert "Veer:" not in research.panel_prompt(c["panels"][0], c["character_sheet"])  # establishing shot has no cast


@pytest.mark.parametrize("scene,ok", [
    ("Krishna fights a demon in the sky", False), ("Hanuman is mocked by a crowd", False), ("the prophet Muhammad walks", False),
    ("silhouette of a Rakshak guardian relief on an ancient wall", True), ("Veer fights a wave of black water", True)])
def test_respect_charter_guard(scene, ok):
    panels = [{"image_prompt": scene, "lines": []}]
    if ok:
        manga.check_charter(panels)
    else:
        with pytest.raises(ValueError, match="respect charter"):
            manga.check_charter(panels)
    with pytest.raises(ValueError, match="never claimed as Veer"):
        manga.check_charter([{"image_prompt": "x", "lines": [{"text": "Veer is the Kali Avatar"}]}])


def test_voices_and_prosody():
    v = manga.load_voices("veer")
    assert v["VEER"]["voice"] == "en-IN-PrabhatNeural" and v["TARA"]["voice"] == "en-IN-NeerjaNeural" and v["NARRATOR"]["voice"]
    assert v["JALAGHNA"]["fx"] == "echo" and v["SMARAN"]["fx"] == "reverb" and v["VYOM"]["rate"].startswith("-")
    assert len({v[n]["voice"] for n in ("VEER", "HRIDYANSH", "VYOM", "JALAGHNA", "SMARAN")}) == 5  # distinct voices
    assert manga.voice_for("ACHARYA VYOM", v) == v["VYOM"] and manga.voice_for("NOBODY", v) == v["NARRATOR"]
    base = v["VEER"]
    assert int(manga.prosody(base, "whisper", "whisper")[0].rstrip("%")) < int(manga.prosody(base, "", "speech")[0].rstrip("%"))
    assert int(manga.prosody(base, "roaring", "shout")[0].rstrip("%")) > int(manga.prosody(base, "", "speech")[0].rstrip("%"))


def test_bubble_styles_look_different(workspace_tmp_path):
    out = {}
    for style in ("speech", "shout", "whisper"):
        p = comic.make_overlay(workspace_tmp_path / f"{style}.png", bubble="Hello there friend", border=False, head=(540, 900), bubble_style=style)
        out[style] = Image.open(p).tobytes()
    out["verse"] = Image.open(comic.make_overlay(workspace_tmp_path / "v.png", narration="When the seventh river forgets", border=False, narr_style="verse")).tobytes()
    out["caption"] = Image.open(comic.make_overlay(workspace_tmp_path / "c.png", narration="When the seventh river forgets", border=False)).tobytes()
    assert len(set(out.values())) == 5
    verse = Image.open(workspace_tmp_path / "v.png").convert("RGBA")
    assert any(px[3] and px[:3] == (232, 208, 160) for px in verse.getdata())  # parchment colour


class Board:
    def __init__(self, tmp):
        self.tmp, self.n = tmp, 0

    def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
        self.n += 1
        p = self.tmp / f"{job}_{self.n}.jpg"
        Image.effect_noise((576, 1024), 50).convert("RGB").save(p)
        return SimpleNamespace(output_path=str(p), provider="nvidia_nim", latency_ms=1)


def _fake_tts(calls):
    def tts(text, out, lang=None, options=None):
        calls.append((text, dict(options or {})))
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=0.8", str(wav)], check=True)
        return wav, "fake"
    return tts


def _cfg(tmp, **sc):
    return {"comic": {"cast_lock": False, "image_mode": "nim", "chapter_parts": 10}, "cost_dir": str(tmp / "costs"),
            "providers": {"nvidia_nim": {"max_credits_per_day": 25}}}


def test_manga_part_renders_with_per_line_voices_and_timed_bubbles(workspace_tmp_path, monkeypatch):
    tmp, calls = workspace_tmp_path, []
    monkeypatch.setattr("aurora.audio.synthesize_voiceover", _fake_tts(calls))
    monkeypatch.setattr(comic_layout, "foreground_mask", lambda p: None)
    monkeypatch.setattr(cast_mod, "save_new_members", lambda *a, **k: [])
    res = story.render_next("veer", out_dir=tmp / "out", cfg=_cfg(tmp), switchboard=Board(tmp), music=False, series_dir=tmp / "ser")
    assert [c[1]["voice"] for c in calls] == ["en-US-AvaNeural", "en-IN-PrabhatNeural", "en-US-AnaNeural", "en-IN-PrabhatNeural"]  # narrator, Veer, Hrithvika, Veer
    assert calls[3][1]["rate"].startswith("-")  # whispered "...Not again." is slower than Veer's base +4%
    side = json.loads(Path(res["sidecar"]).read_text(encoding="utf-8"))
    assert res["title"] == "VEER – Wrath of the Forgotten God – Ch.1 Part 1: Navkashi" and side["playlist"].startswith("VEER")
    times = side["panel_times"]
    assert len(times) == 4 and side["chapter"]["part"] == 1 and side["chapter"]["parts"] == 10
    for t in times:  # panel time = lead + lines + gaps + 0.4 s (>= 2.5 s)
        assert t["end"] - t["start"] > 2.0
    assert times[1]["lines"][0]["speaker"] == "VEER" and times[1]["lines"][0]["start"] > times[1]["start"]
    assert res["seconds"] > 9 and Path(res["video"]).exists()
    state = research.load_series("veer", tmp / "ser")
    assert state["chapter"]["parts_done"] == 1 and state["series_title"].startswith("VEER") and state["seed"]


def test_manga_waits_for_the_credits_reserved_for_echoes(workspace_tmp_path):
    from aurora.costs import log_credits

    tmp = workspace_tmp_path
    log_credits("nvidia_nim", 10, tmp / "costs")
    with pytest.raises(RuntimeError, match="kept for the other series"):
        story.render_next("veer", out_dir=tmp / "out", cfg=_cfg(tmp), switchboard=Board(tmp), series_dir=tmp / "ser")  # 10 + 4 > 25 - 14
    assert not (tmp / "ser" / "veer.json").exists() or research.load_series("veer", tmp / "ser")["chapter"]["parts_done"] == 0
