import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from PIL import Image

from aurora import cast as cast_mod
from aurora import comic, comic_layout, manga, research, story
from tests.test_chapters import FakeClient

PART = """## Part {n} — "Hook {n}"
1. Wide: a ruined temple at dawn
   **NARRATOR** (calm): "The first light touches the old stones."
2. Kaelen kneels by a glowing rune
   **KAELEN** (earnest): "It is still warm."
3. Close-up: his determined eyes
   **KAELEN**: "Then it is not over."
"""


def make_pack(root: Path, slug="demo", fmt="comic", parts=2, extra_md="", series=None, chars=None):
    d = root / slug
    (d / "characters").mkdir(parents=True)
    (d / "chapters").mkdir()
    (d / "series.yaml").write_text(yaml.safe_dump({"title": "Demo Saga", "channel": "demo", "format": fmt, "parts_per_chapter": 10,
                                                   "opening_card": "A made-up tale.", **(series or {})}, allow_unicode=True), encoding="utf-8")
    ch = chars or [{"name": "Kaelen", "role": "protagonist", "description": "young man with silver spiky hair and a dark cloak",
                    "voice": {"voice": "en-IN-PrabhatNeural", "rate": "+0%", "pitch": "+0Hz"}}]
    for c in ch:
        (d / "characters" / f"{c['name'].lower()}.yaml").write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    if parts:
        (d / "chapters" / "CH_01.md").write_text('# Demo — Chapter 1: "Dawn"\n\n' + "\n".join(PART.format(n=i) for i in range(1, parts + 1)) + extra_md, encoding="utf-8")
    return d


@pytest.fixture
def packs(workspace_tmp_path, monkeypatch):
    root = workspace_tmp_path / "stories"
    root.mkdir()
    monkeypatch.setattr(story, "STORIES_ROOT", root)
    return root


def test_shipped_packs_lint_clean():
    for slug in ("echoes-of-the-aether", "veer"):
        rep = story.lint(slug)
        assert rep["errors"] == [], rep


def test_echoes_pack_keeps_the_legacy_cast_and_state_key():
    assert story.find_pack("shinobi").name == "echoes-of-the-aether"  # legacy channel key resolves to the pack (state_key)
    packed = cast_mod.load_cast("shinobi")
    legacy = json.loads((cast_mod.CAST_ROOT / "shinobi" / "kaelen.json").read_text(encoding="utf-8"))
    assert packed[0]["name"] == "Kaelen" and packed[0]["description"] == legacy["description"]
    assert cast_mod.reference_image("shinobi").exists()  # reference images moved with the pack
    assert [m["name"] for m in cast_mod.load_cast("main")] == ["Kaelen"]  # channels without a pack still use assets/characters/<channel>


def test_lint_gives_friendly_errors(packs):
    bad = make_pack(packs, "bad", fmt="manga", parts=1, extra_md='\n## Part 2 — "x"\n1. Krishna fights a demon\n   **KAELN**: "hi"\n2. a\n   **KAELEN**: "naked truth"\n3. b\n',
                    chars=[{"name": "Kaelen", "description": "short", "voice": {"voice": "bad", "rate": "fast", "pitch": "+0Hz", "fx": "robot"}}])
    (bad / "characters" / "ghost.yaml").write_text(yaml.safe_dump({"name": "Ghost", "description": "a pale ghost with a long grey coat", "references": [{"file": "nope.jpg"}]}), encoding="utf-8")
    text = "\n".join(story.lint("bad")["errors"])
    for needle in ("speaker 'KAELN' has no file in characters/ (did you mean 'KAELEN'?)", "voice.voice must be an Edge-TTS voice", "voice.rate must look like",
                   "voice.fx must be one of", "reference image 'nope.jpg' is missing", "manga series need a voice", "not allowed (all-ages rule)",
                   "respect charter"):
        assert needle in text, (needle, text)
    assert story.lint("missing")["errors"][0].startswith("no story pack 'missing'")
    toomany = make_pack(packs, "many", parts=11)
    assert any("11 parts but parts_per_chapter is 10" in e for e in story.lint("many")["errors"]) and toomany.exists()
    make_pack(packs, "low", series={"parts_per_chapter": 4})
    assert any("parts_per_chapter must be a whole number >= 10" in e for e in story.lint("low")["errors"])


def test_pack_cast_and_voices(packs):
    make_pack(packs, "demo")
    assert [m["name"] for m in cast_mod.load_cast("demo")] == ["Kaelen"]
    v = story.pack_voices(story.load_pack("demo"))
    assert v["KAELEN"]["voice"] == "en-IN-PrabhatNeural" and v["NARRATOR"]["voice"].startswith("en-")


def test_cadence_and_invalid_packs_are_skipped(packs, workspace_tmp_path):
    make_pack(packs, "demo")
    make_pack(packs, "broken", fmt="nope")
    sd = workspace_tmp_path / "ser"
    ch = {"name": "c", "series": [{"slug": "broken", "per_day": 1}, {"slug": "off", "per_day": 0}, {"slug": "demo", "per_day": 2}]}
    entry, reports, invalid = story.pick_series(ch, sd, "2026-10-26")
    assert entry["slug"] == "demo" and invalid and "format must be one of" in reports[0]
    research._write_state("demo", {"daily": {"2026-10-26": 2}}, sd)
    assert story.pick_series(ch, sd, "2026-10-26")[0] is None  # cadence reached
    assert story.pick_series(ch, sd, "2026-10-27")[0]["slug"] == "demo"  # next day
    only_bad = {"name": "c", "series": ["broken"]}
    assert story.render_for_channel({}, only_bad, None, workspace_tmp_path, series_dir=sd) is None  # legacy fallback, no crash
    with pytest.raises(RuntimeError, match="daily cadence"):
        story.render_for_channel({}, {"name": "c", "series": [{"slug": "demo", "per_day": 0}]}, None, workspace_tmp_path, series_dir=sd)

class Board:
    def __init__(self, tmp):
        self.tmp, self.n = tmp, 0

    def generate(self, prompt, style, res, job, negative_prompt="", seed=None):
        self.n += 1
        p = self.tmp / f"{job}_{self.n}.jpg"
        Image.effect_noise((576, 1024), 50).convert("RGB").save(p)
        return SimpleNamespace(output_path=str(p), provider="nvidia_nim", latency_ms=1)


def _draft_setup(packs, tmp, monkeypatch):
    """A research-written part (no authored script) with make_comic_reel replaced: `outcome` is an exception to raise, else it succeeds."""
    make_pack(packs, "demo", parts=0)
    monkeypatch.setattr(research, "_call_json", lambda client, model, prompt, schema: client.generate(prompt, schema))
    cfg = {"comic": {"cast_lock": False, "image_mode": "nim"}, "research": {}, "cost_dir": str(tmp / "costs"), "providers": {"nvidia_nim": {"max_credits_per_day": 50}}}
    sd, client, seen = tmp / "ser", FakeClient(), []
    outcome = [None]

    def reel(channel, premise, out_dir, comic=None, chapter=None, series_dir=None, **kw):
        seen.append(comic)
        if outcome[0]:
            raise outcome[0]
        side = tmp / f"part{chapter['part']}.json"
        side.write_text(json.dumps({"hook": comic.get("hook"), "panels": comic["panels"]}), encoding="utf-8")
        research.record_part(channel, comic, chapter, {"part": chapter["part"], "video": "v.mp4", "sidecar": str(side), "title": "t"}, series_dir)
        return {"sidecar": str(side), "title": "t"}

    monkeypatch.setattr(comic, "make_comic_reel", reel)
    return (lambda: story.render_next("demo", cfg, tmp / "out", Board(tmp), music=False, series_dir=sd, client=client)), client, seen, outcome, sd


def test_failed_part_keeps_its_script_until_it_succeeds(packs, workspace_tmp_path, monkeypatch):  # 2026-10-31: a retry re-paid 6 NIM credits for a new script
    render, client, seen, outcome, sd = _draft_setup(packs, workspace_tmp_path, monkeypatch)
    outcome[0] = TimeoutError("ffprobe timed out")
    with pytest.raises(TimeoutError):
        render()
    asked = len(client.prompts)
    with pytest.raises(TimeoutError):
        render()
    assert len(client.prompts) == asked and seen[1]["panels"] == seen[0]["panels"]  # no new Gemini script: same prompts for the image cache
    outcome[0] = None
    render()
    assert seen[2]["panels"] == seen[0]["panels"] and "draft_part" not in research.load_series("demo", sd)
    render()  # the next part writes its own script
    assert len(client.prompts) > asked and seen[3]["series"]["part"] == 2


def test_filter_blocked_part_gets_a_new_script(packs, workspace_tmp_path, monkeypatch):
    render, client, seen, outcome, sd = _draft_setup(packs, workspace_tmp_path, monkeypatch)
    blocked = RuntimeError("NIM failed after 1/6 panels")
    blocked.__cause__ = RuntimeError("[NVIDIA NIM] Generation blocked: CONTENT_FILTERED")
    outcome[0] = blocked
    with pytest.raises(RuntimeError):
        render()
    assert "draft_part" not in research.load_series("demo", sd)
    asked = len(client.prompts)
    with pytest.raises(RuntimeError):
        render()
    assert len(client.prompts) > asked


def test_seeded_image_comes_from_the_cache_at_no_cost(workspace_tmp_path, monkeypatch):  # lookup used seed 0, assets are stored under the real seed: 0 hits ever
    from backend.app.providers.base import ImageResult
    from backend.app.services.image_switchboard import ImageSwitchboard

    tmp = workspace_tmp_path.resolve()
    monkeypatch.chdir(tmp)  # the manifest path is relative: never the real one
    model = "black-forest-labs/flux.2-klein-4b"
    calls = []

    class Nim:
        def generate_with_params(self, prompt, style, res, job, negative_prompt="", seed=None):
            calls.append(seed)
            p = tmp / f"{job}.jpg"
            Image.effect_noise((768, 1344), 50).convert("RGB").save(p)
            return ImageResult("nvidia_nim", model, style, 5, str(p), width=768, height=1344, seed=seed, negative_prompt=negative_prompt)

    board = ImageSwitchboard({"providers": {"nvidia_nim": {"models": {"anime": model}}}})
    board.fallback_order, board.providers = ["nvidia_nim"], {"nvidia_nim": Nim()}
    args = ("a panel", "anime", [1024, 1088])
    first = board.generate(*args, "j1", negative_prompt="lowres", seed=77)
    again = board.generate(*args, "j2", negative_prompt="lowres", seed=77)
    assert calls == [77] and again.latency_ms == 0 and again.output_path == first.output_path  # latency 0 = no NIM credit logged
    board.generate(*args, "j3", negative_prompt="lowres", seed=78)  # the filter retry's new seed still reaches NIM
    assert calls == [77, 78]


def test_authored_chapters_are_used_as_is_and_missing_ones_are_researched_and_saved_back(packs, workspace_tmp_path, monkeypatch):
    tmp = workspace_tmp_path
    d = make_pack(packs, "demo", parts=2)
    monkeypatch.setattr(research, "_call_json", lambda client, model, prompt, schema: client.generate(prompt, schema))
    monkeypatch.setattr(comic_layout, "foreground_mask", lambda p: None)
    monkeypatch.setattr(cast_mod, "save_new_members", lambda *a, **k: [])

    def tts(text, out, lang=None, options=None):
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=0.6", str(wav)], check=True)
        return wav, "fake"

    monkeypatch.setattr(comic, "synthesize_voiceover", tts)
    for name, val in (("PANEL_MIN_S", 1.2), ("COVER_S", 1.2), ("END_S", 1.2)):
        monkeypatch.setattr(comic, name, val)
    cfg = {"comic": {"cast_lock": False, "image_mode": "nim"}, "research": {}, "cost_dir": str(tmp / "costs"), "providers": {"nvidia_nim": {"max_credits_per_day": 50}}}
    sd, board, client = tmp / "ser", Board(tmp), FakeClient()
    r1 = story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)
    assert r1["authored"] and r1["title"] == "Demo Saga – Ch.1 Part 1: Hook 1" and client.prompts == []  # no research call for an authored part
    side = json.loads(Path(r1["sidecar"]).read_text(encoding="utf-8"))
    assert side["channel"] == "demo" and side["story"] == "demo" and side["playlist"] == "Demo Saga"
    assert [p["narration"] for p in side["panels"]][0] == "The first light touches the old stones." and side["panels"][1]["bubble"] == "It is still warm."
    r2 = story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)
    assert r2["authored"] and r2["chapter"]["part"] == 2
    r3 = story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)  # part 3 is not authored -> research
    assert not r3["authored"] and r3["chapter"]["part"] == 3 and client.prompts and "World canon" in client.prompts[-1]
    md = (d / "chapters" / "CH_01.md").read_text(encoding="utf-8")
    assert '## Part 3 — "The Relic Wakes"' in md and "**NARRATOR** (calm)" in md  # saved back in the authored schema
    assert story.lint("demo")["errors"] == []  # the saved chapter lints clean and re-parses
    assert [p["number"] for p in manga.parse_chapter(md)["parts"]] == [1, 2, 3]
    assert research.load_series("demo", sd)["daily"]


def test_config_series_entries_point_at_real_packs():
    cfg = yaml.safe_load((story.ROOT / "config.yaml").read_text(encoding="utf-8"))
    shinobi = next(c for c in cfg["autopilot"]["channels"] if c["name"] == "shinobi")
    slugs = [e["slug"] for e in story.entries(shinobi)]
    assert slugs == ["echoes-of-the-aether", "veer"] and set(slugs) <= set(story.list_packs())
    veer = story.entries(shinobi)[1]
    assert veer["per_day"] == 0 or veer["enabled"] is False  # Veer is not scheduled yet
    assert "series" not in next(c for c in cfg["autopilot"]["channels"] if c["name"] == "main")


def test_template_pack_lints_clean_and_is_not_a_series():
    assert story.lint("_template")["errors"] == []
    assert "_template" not in story.list_packs() and story.find_pack("_template") is not None


# ---------------------------------------------------------------- hard rules: forbidden relationships (engine-generic)
RULES = {"rules": {"forbidden_relationships": [{"pair": ["Kaelen", "Mira"], "type": "romantic", "note": "NEVER any romance between Kaelen and Mira."}]}}
TWO = [{"name": "Kaelen", "role": "protagonist", "description": "young man with silver spiky hair and a dark cloak", "hard_rules": ["Kaelen is always kind."]},
       {"name": "Mira", "role": "friend", "description": "young woman with short teal hair and a yellow scarf"}]


def test_lint_validates_hard_rule_schema(packs):
    make_pack(packs, "ok", series=RULES, chars=TWO, parts=0)
    assert story.lint("ok")["errors"] == []
    bad = {"rules": {"forbidden_relationships": [{"pair": ["Kaelen", "Mria"], "type": "romantic"}, {"pair": ["Kaelen", "Kaelen"]}, {"pair": ["only-one"]}, "oops"],
                     "hard_rules": "not a list"}}
    make_pack(packs, "bad", series=bad, chars=TWO, parts=0)
    text = "\n".join(story.lint("bad")["errors"])
    for needle in ("'Mria' is not a character in characters/ (did you mean 'Mira'?)", "must be two different characters", "type is required", "needs pair: [NameA, NameB]",
                   "rules.hard_rules must be a list"):
        assert needle in text, (needle, text)
    make_pack(packs, "badchar", chars=[{**TWO[0], "hard_rules": "no"}], parts=0)
    assert any("hard_rules must be a list" in e for e in story.lint("badchar")["errors"])


def test_authored_romance_between_a_forbidden_pair_fails_lint(packs):
    d = make_pack(packs, "romance", series=RULES, chars=TWO, parts=0)
    (d / "chapters" / "CH_01.md").write_text('# D — Chapter 1: "X"\n\n## Part 1 — "A"\n1. Kaelen blushes as Mira smiles at him\n   **KAELEN**: "Hello."\n2. Wide: ruins\n3. Close-up: eyes\n', encoding="utf-8")
    assert any("hard rule broken" in e and "Kaelen and Mira" in e for e in story.lint("romance")["errors"])


def test_pack_rules_are_verbatim_and_deduplicated(packs):
    make_pack(packs, "ok", series=RULES, chars=TWO, parts=0)
    assert story.pack_rules(story.load_pack("ok")) == ["NEVER any romance between Kaelen and Mira.", "Kaelen is always kind."]


class RomanticClient(FakeClient):
    """Writes a romantic part first; a clean one once the prompt carries the 'previous draft broke a hard rule' note. The Gemini yes/no check says violates for 'longing'."""

    def __init__(self):
        super().__init__()
        self.violating_calls = 0

    def generate(self, prompt, schema):
        if schema is story.Verdict:
            return story.Verdict(violates="longing" in prompt.split("TEXT:", 1)[1], reason="longing look")
        got = super().generate(prompt, schema)
        if schema is research.Comic:
            got.character_sheet = [research.Character(name="Kaelen", description="young man with silver spiky hair and a dark cloak"),
                                   research.Character(name="Mira", description="young woman with short teal hair and a yellow scarf")]
            if "broke a hard rule" not in prompt:
                self.violating_calls += 1
                got.panels[0].narration = "Kaelen gazes at Mira with longing"  # no keyword cue: only the Gemini check catches this
        return got


def test_research_includes_hard_rules_and_regenerates_a_violating_part(packs, workspace_tmp_path, monkeypatch):
    make_pack(packs, "demo", series=RULES, chars=TWO, parts=0)
    monkeypatch.setattr(research, "_call_json", lambda client, model, prompt, schema: client.generate(prompt, schema))
    pack = story.load_pack("demo")
    client = RomanticClient()
    r = research.research_part("demo", "p", cfg={"comic": {"chapter_parts": 10, "cast_lock": False}, "research": {}}, client=client, series_dir=workspace_tmp_path / "s",
                               rules=story.pack_rules(pack), guard=story.make_guard(pack))
    prompts = [p for p in client.prompts if "HARD RULES" in p]
    assert len(prompts) >= 3 and all("NEVER any romance between Kaelen and Mira." in p for p in prompts)  # outline + both part drafts carry the rule verbatim
    assert client.violating_calls == 1 and any("broke a hard rule" in p for p in client.prompts)
    assert "longing" not in " ".join(p["narration"] for p in r["comic"]["panels"])


def test_a_part_that_keeps_violating_is_never_returned(packs, workspace_tmp_path, monkeypatch):
    make_pack(packs, "demo", series=RULES, chars=TWO, parts=0)
    monkeypatch.setattr(research, "_call_json", lambda client, model, prompt, schema: client.generate(prompt, schema))
    pack = story.load_pack("demo")

    class Stubborn(RomanticClient):
        def generate(self, prompt, schema):
            got = super().generate(prompt, schema)
            if schema is research.Comic:
                got.panels[0].narration = "Kaelen kisses Mira"
            return got

    r = research.research_part("demo", "p", cfg={"comic": {"chapter_parts": 10, "cast_lock": False}, "research": {}}, client=Stubborn(), series_dir=workspace_tmp_path / "s",
                               rules=story.pack_rules(pack), guard=story.make_guard(pack))
    # live attempts all violated -> offline fallback (clean, generic text) is used, and it is guarded too
    assert r["source"] == "topic_fallback" and "part fallback" in r["note"] and "kisses" not in " ".join(p["narration"] for p in r["comic"]["panels"])


def _research_env(tmp, monkeypatch):
    monkeypatch.setattr(research, "_call_json", lambda client, model, prompt, schema: client.generate(prompt, schema))
    monkeypatch.setattr(comic_layout, "foreground_mask", lambda p: None)
    monkeypatch.setattr(cast_mod, "save_new_members", lambda *a, **k: [])

    def tts(text, out, lang=None, options=None):
        wav = Path(out).with_suffix(".wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=0.6", str(wav)], check=True)
        return wav, "fake"

    monkeypatch.setattr(comic, "synthesize_voiceover", tts)
    for name, val in (("PANEL_MIN_S", 1.2), ("COVER_S", 1.2), ("END_S", 1.2)):
        monkeypatch.setattr(comic, name, val)
    return {"comic": {"cast_lock": False, "image_mode": "nim"}, "research": {}, "cost_dir": str(tmp / "costs"), "providers": {"nvidia_nim": {"max_credits_per_day": 50}}}


def test_partial_research_chapter_is_repaired_from_the_series_state_not_skipped(packs, workspace_tmp_path, monkeypatch):
    """Production bug 2026-10-29: research wrote only '## Part 3' to CH_01.md and lint then skipped the whole series."""
    tmp = workspace_tmp_path
    d = make_pack(packs, "demo", parts=0)
    cfg = _research_env(tmp, monkeypatch)
    sd, board, client = tmp / "ser", Board(tmp), FakeClient()
    for _ in range(3):
        story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)
    md_path = d / "chapters" / "CH_01.md"
    nums = lambda: [p["number"] for p in manga.parse_chapter(md_path.read_text(encoding="utf-8"))["parts"]]  # noqa: E731
    assert nums() == [1, 2, 3]  # the WHOLE chapter so far, in order (not just the latest part)
    assert not list(md_path.parent.glob("*.tmp"))  # atomic write leaves no temp file
    # simulate the bad file: header + only Part 3, no research marker
    full = md_path.read_text(encoding="utf-8")
    part3 = full[full.index("## Part 3"):]
    md_path.write_text('# Demo Saga — Chapter 1: "Dawn"\n\n' + part3, encoding="utf-8")
    rep = story.lint("demo", series_dir=sd)
    assert rep["errors"] == [] and any("parts [1, 2] are missing" in w for w in rep["warnings"])  # warning, not an error
    ch = {"name": "c", "series": [{"slug": "demo", "per_day": 9}]}
    entry, reports, invalid = story.pick_series(ch, sd, "2026-10-29")
    assert entry["slug"] == "demo" and not invalid  # the series is NOT skipped
    r4 = story.render_next("demo", cfg, tmp / "out", board, music=False, series_dir=sd, client=client)
    assert r4["chapter"]["part"] == 4 and nums() == [1, 2, 3, 4]  # repaired + extended
    assert not any("missing" in w for w in story.lint("demo", series_dir=sd)["warnings"])


def test_scheduler_refuses_the_offline_template_and_retries_cleanly(packs, workspace_tmp_path, monkeypatch):
    tmp = workspace_tmp_path
    make_pack(packs, "demo", parts=0)
    cfg = {"comic": {"cast_lock": False, "image_mode": "nim"}, "research": {}, "cost_dir": str(tmp / "costs"), "providers": {"nvidia_nim": {"max_credits_per_day": 50}}}
    sd, client, seen = tmp / "ser", FakeClient(), []

    def down(*a, **k):
        raise RuntimeError("503 UNAVAILABLE")

    def reel(channel, premise, out_dir, comic=None, chapter=None, series_dir=None, **kw):
        seen.append(comic)
        side = tmp / f"part{chapter['part']}.json"
        side.write_text(json.dumps({"hook": comic.get("hook"), "panels": comic["panels"]}), encoding="utf-8")
        research.record_part(channel, comic, chapter, {"part": chapter["part"], "video": "v.mp4", "sidecar": str(side), "title": "t"}, series_dir)
        return {"sidecar": str(side), "title": "t"}

    monkeypatch.setattr(comic, "make_comic_reel", reel)
    monkeypatch.setattr(research, "_call_json", down)
    channel = {"name": "c", "series": ["demo"]}
    with pytest.raises(RuntimeError, match="no real script.*503"):
        story.render_for_channel(cfg, channel, Board(tmp), tmp / "out", series_dir=sd, music=False, client=client)
    state = research.load_series("demo", sd)
    assert seen == [] and not state.get("chapter") and not state.get("draft_part")  # no template outline, no panels paid
    monkeypatch.setattr(research, "_call_json", lambda c, model, prompt, schema: c.generate(prompt, schema))
    assert story.render_for_channel(cfg, channel, Board(tmp), tmp / "out", series_dir=sd, music=False, client=client)["title"] == "t"
    assert len(seen) == 1


def test_authored_chapter_with_a_gap_stays_strict_and_a_lint_failure_is_reported(packs, workspace_tmp_path):
    tmp = workspace_tmp_path
    d = make_pack(packs, "demo", parts=0)
    (d / "chapters" / "CH_01.md").write_text('# Demo — Chapter 1: "Dawn"\n\n' + PART.format(n=1) + "\n" + PART.format(n=3), encoding="utf-8")
    assert any("parts must be numbered 1..2 in order (found [1, 3])" in e for e in story.lint("demo", series_dir=tmp / "ser")["errors"])
    events = []
    out = story.render_for_channel({}, {"name": "c", "series": ["demo"]}, None, tmp, series_dir=tmp / "ser", on_event=lambda k, m: events.append((k, m)))
    assert out is None and events and events[0][0] == "pack_lint_failed" and "demo skipped" in events[0][1] and "parts must be numbered" in events[0][1]
