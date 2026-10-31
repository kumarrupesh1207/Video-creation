import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("transfer_state", Path(__file__).resolve().parents[1] / "scripts" / "transfer_state.py")
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)


def _project(root: Path) -> None:
    (root / "outputs" / "tokens").mkdir(parents=True)
    (root / "outputs" / "series").mkdir()
    (root / "outputs" / "final" / "shinobi").mkdir(parents=True)
    (root / ".env").write_text("KEY=1", encoding="utf-8")
    (root / "outputs" / "tokens" / "facebook_shinobi.json").write_text('{"page_id": "1"}', encoding="utf-8")
    (root / "outputs" / "final" / "shinobi" / "p1.mp4").write_bytes(b"video")
    (root / "outputs" / "final" / "shinobi" / "p1.json").write_text(json.dumps({"path": str(root / "outputs" / "final" / "shinobi" / "p1.mp4")}), encoding="utf-8")
    queue = {"items": [{"path": str(root / "outputs" / "final" / "shinobi" / "p1.mp4"), "thumbnail": root.as_posix() + "/t.jpg"}]}
    (root / "outputs" / "publish_queue.json").write_text(json.dumps(queue), encoding="utf-8")
    (root / "outputs" / "series" / "shinobi.json").write_text('{"chapter": {"number": 2}}', encoding="utf-8")


def test_backup_and_restore_move_state_and_fix_paths(workspace_tmp_path):
    old, new, mem_old, mem_new = (workspace_tmp_path / n for n in ("old project", "new", "mem_old", "mem_new"))
    old.mkdir(), new.mkdir(), mem_old.mkdir()
    _project(old)
    (old / "outputs" / "junk.bin").write_bytes(b"x")
    (mem_old / "MEMORY.md").write_text("note", encoding="utf-8")
    z = ts.backup(old, workspace_tmp_path / "bk", memory=mem_old)
    names = zipfile.ZipFile(z).namelist()
    assert ".env" in names and "outputs/tokens/facebook_shinobi.json" in names and "claude_memory/MEMORY.md" in names
    assert "outputs/junk.bin" not in names
    out = ts.restore(z, new, memory=mem_new)
    assert out["paths_fixed_in"] == 2 and (new / "outputs" / "final" / "shinobi" / "p1.mp4").read_bytes() == b"video"
    item = json.loads((new / "outputs" / "publish_queue.json").read_text(encoding="utf-8"))["items"][0]
    assert item["path"] == str(new / "outputs" / "final" / "shinobi" / "p1.mp4") and item["thumbnail"] == new.as_posix() + "/t.jpg"
    assert (mem_new / "MEMORY.md").read_text(encoding="utf-8") == "note"


def test_restore_refuses_to_replace_a_live_queue_and_backup_stays_out_of_the_repo(workspace_tmp_path):
    a, b = workspace_tmp_path / "a", workspace_tmp_path / "b"
    a.mkdir(), b.mkdir()
    _project(a), _project(b)
    with pytest.raises(ValueError):
        ts.backup(a, a / "backups", memory=workspace_tmp_path / "none")
    z = ts.backup(a, workspace_tmp_path / "bk", memory=workspace_tmp_path / "none")
    with pytest.raises(FileExistsError):
        ts.restore(z, b, memory=workspace_tmp_path / "m")
    assert ts.restore(z, b, force=True, memory=workspace_tmp_path / "m")["files"] > 0


def test_restore_blocks_paths_that_leave_the_project(workspace_tmp_path):
    z = workspace_tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr(ts.MANIFEST, json.dumps({"root": "x", "files": 1, "created": "now"}))
        f.writestr("../evil.txt", "x")
    root = workspace_tmp_path / "r"
    root.mkdir()
    with pytest.raises(ValueError):
        ts.restore(z, root, memory=workspace_tmp_path / "m")
    assert not (workspace_tmp_path / "evil.txt").exists()
