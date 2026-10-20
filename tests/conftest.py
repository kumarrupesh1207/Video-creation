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
