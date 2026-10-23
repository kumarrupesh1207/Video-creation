# Plans
Write one file per bigger change: `docs/plans/<short-name>.md`. Get the user's approval before coding.
Check `CLAUDE.md` Rules and `docs/solutions/` first.

## Template
```markdown
# <Plan title>
Status: draft | approved | done        Date: YYYY-MM-DD

## Goal
What the user gets, in 2-3 lines.

## Files to change
- path/to/file.py — what changes (reuse existing code where possible)

## Risks
- What could break (performance, cost/credits, uploads, ComfyUI queue) and how we guard it.

## Test plan
- Unit tests to add/run; one live check if needed (no uploads, credit limits stated).

## Done when
- Concrete, checkable outcomes (tests green, output validated, user confirmed).
```
