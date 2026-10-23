# Tests started failing after GEMINI_API_KEY was added to .env

Problem: `test_comic.py::test_fallback_comic_series_state_continues` failed (and tests could hit the live Gemini API) once the user put a real key in `.env`.
Root cause: other tests call `load_env()`, which loaded the real key into the environment, so "no key" fallback paths stopped being exercised and live calls became possible.
Fix: autouse fixture in `tests/conftest.py` deletes `GEMINI_API_KEY` for every test; tests use mocked SDK clients only (7adc20c).
How to recognise: a test passes alone but fails in the full suite, or the suite gets slow/flaky right after secrets are added.
