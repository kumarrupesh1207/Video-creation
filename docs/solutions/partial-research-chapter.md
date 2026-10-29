# Partial research-written chapter skipped a whole series (2026-10-29)

**Symptom:** the 09:05 scheduled render skipped Echoes: `chapters/CH_01.md` held only `## Part 3`, and lint failed with
"parts must be numbered 1..1 in order (found [3])". Research had appended just the latest part; parts 1-2 were never in the file.

**Fix (aurora/story.py, generic):**
- `sync_chapter_md` writes the WHOLE chapter so far (every part in the series state, in order) atomically (tmp + `os.replace`). Parts already in the
  file stay verbatim (authored or hand-edited); missing ones are rebuilt from the rendered parts' sidecars. Research-written files carry a
  `<!-- written-by: research -->` marker.
- `render_next` / `render_for_channel` repair the file from the series state BEFORE linting. Lint: a gap is only an ERROR in authored files;
  in a research-written file (marker) or when the series state covers the missing parts it is a WARNING. Authored parts are looked up by number, not list index.
- A lint failure is never silent: `render_for_channel(on_event=...)` emits `pack_lint_failed` (autopilot passes `_event`), which lands in the daily
  report / Approve tab events next to `render_failed`.

**Rule:** a derived file (research write-back) must be a full, atomic projection of the state, never an incremental append.
Tests: `tests/test_story.py::test_partial_research_chapter_is_repaired_*`, `test_authored_chapter_with_a_gap_stays_strict_*`.
