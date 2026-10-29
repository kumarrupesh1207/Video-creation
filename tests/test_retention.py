import json
import subprocess
from pathlib import Path

import pytest
import yaml
from PIL import Image

from aurora import comic, comic_layout, research, story
from tests.test_story import Board, _research_env, make_pack, packs  # noqa: F401 (packs = fixture)
from tests.test_chapters import FakeClient


class HookClient(FakeClient):
    """Part prompt -> a Comic that fills the retention fields (panel 4 is the dramatic one)."""

    def generate(self, prompt, schema):
        got = super().generate(prompt, schema)
        if schema is research.Comic:
            got.hook_panel, got.hook_line = 4, "It is awake!"
            got.hook_title = "The relic opened its eyes and looked straight at him"  # > 45 chars on purpose
            got.engagement_question = "Would you touch the relic?"
            got.previously = "Kaelen found a glowing relic under the ruins and felt it answer his call."
        return got


def _panels(**k):
    base = [{"narration": "a", "bubble": "", "sfx": "", "action": False} for _ in range(6)]
    return {"panels": base, **k}


def test_hook_panel_prefers_research_then_action_sfx_then_action_then_sfx_then_buildup():
    c = _panels(hook_panel=3)
    assert comic.pick_hook_panel(c) == 2
    c = _panels(hook_panel=99)  # invalid -> heuristics
    c["panels"][1]["action"] = True
    c["panels"][4].update(action=True, sfx="BOOM")
    assert comic.pick_hook_panel(c) == 4  # action + sfx beats a plain action panel
    c["panels"][4].update(action=False)
    assert comic.pick_hook_panel(c) == 1  # then any action panel
    c["panels"][1]["action"] = False
    assert comic.pick_hook_panel(c) == 4  # then any sfx panel
    c["panels"][4]["sfx"] = ""
    assert comic.pick_hook_panel(c) == 4  # last-but-one of 6 panels
    assert comic.pick_hook_line({**c, "hook_line": "Run now please do it before dawn arrives and everything ends"}, 0).split()[-1] != "ends"  # trimmed
    c["panels"][2]["sfx"] = "CRACK"
    assert comic.pick_hook_line(c, 2) == "CRACK"


def test_finish_retention_limits_and_previously_only_from_part_2():
    c = {"panels": [{}] * 5, "hook_panel": 9, "hook_title": "x" * 80 + " end", "hook": "Fallback hook", "previously": "ignored on part 1", "engagement_question": ""}
    out = research.finish_retention(dict(c), ["Part one recap line."], 1)
    assert out["hook_panel"] == 0 and len(out["hook_title"]) <= 45 and out["previously"] == "" and out["engagement_question"]
    out = research.finish_retention({**c, "previously": ""}, ["Part one recap line."], 2)
    assert out["previously"] == "Part one recap line."  # falls back to the series state
    assert len(research.finish_retention({**c, "previously": "word " * 60}, [], 3)["previously"]) <= 110


def test_retention_settings_are_linted(packs):
    make_pack(packs, "ok", series={"retention": {"hook_first": True, "hook_s": 1.5, "previously_s": 1.2}})
    assert story.lint("ok")["errors"] == []
    make_pack(packs, "bad", series={"retention": {"hook_first": "yes", "hook_s": 3, "previously_s": 2, "nope": 1}})
    text = "\n".join(story.lint("bad")["errors"])
    for needle in ("retention.hook_first must be true/false", "retention.hook_s must be between 0.5 and 1.5", "retention.previously_s must be between", "retention.nope is not a setting"):
        assert needle in text, (needle, text)
    shipped = yaml.safe_load((story.ROOT / "stories" / "echoes-of-the-aether" / "series.yaml").read_text(encoding="utf-8"))["retention"]
    assert shipped["hook_first"] is True  # Echoes opts in (analytics 2026-10)
    make_pack(packs, "shipped", series={"retention": shipped})
    assert story.lint("shipped")["errors"] == []


def test_hook_first_opening_recap_from_part_2_and_publisher_fields(packs, workspace_tmp_path, monkeypatch):
    tmp = workspace_tmp_path
    make_pack(packs, "demo", parts=0, series={"retention": {"hook_first": True}})
    make_pack(packs, "classic", parts=0)
    cfg = _research_env(tmp, monkeypatch)
    sd, board, client = tmp / "ser", Board(tmp), HookClient()
    r1 = story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)
    r2 = story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)
    s1, s2 = (json.loads(Path(r["sidecar"]).read_text(encoding="utf-8")) for r in (r1, r2))
    assert s1["intro"] == {"hook_panel": 4, "hook_line": "It is awake!", "hook_s": 1.5}  # Part 1: hook only, no "Previously…"
    assert s2["intro"]["previously_s"] == 1.2 and s2["intro"]["hook_panel"] == 4
    for s in (s1, s2):
        assert 0 < len(s["hook_title"]) <= 45 and s["engagement_question"] == "Would you touch the relic?"
    assert s1["panel_times"][0]["start"] == pytest.approx(1.5 - comic.XF, abs=0.02)  # story starts right after the 1.5 s hook
    assert s2["panel_times"][0]["start"] == pytest.approx(1.5 + 1.2 - 2 * comic.XF, abs=0.02)
    assert s2["panel_times"][0]["start"] > s1["panel_times"][0]["start"] and len(s1["panel_times"]) == 6  # story panels unchanged and in order
    classic = story.render_next("classic", cfg, tmp / "out", board, music=False, series_dir=sd, client=HookClient())
    sc = json.loads(Path(classic["sidecar"]).read_text(encoding="utf-8"))
    assert sc["intro"] is None and sc["panel_times"][0]["start"] == pytest.approx(comic.COVER_S - comic.XF, abs=0.02)  # packs without retention keep the cover card
