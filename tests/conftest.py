import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def workspace_tmp_path() -> Path:
    """Temp dir inside the workspace for locked-down Windows hosts."""
    path = Path(".test_tmp") / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_live_gemini(monkeypatch):
    """Tests never call the live Gemini API: load_env() may have put a real key into os.environ. Tests that need a key set a fake one."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
