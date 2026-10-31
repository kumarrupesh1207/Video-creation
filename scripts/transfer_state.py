"""Move Aurora to another PC: everything git cannot hold (secrets, queue, series state, rendered videos).

    python scripts/transfer_state.py backup  [--out FOLDER] [--with-models]
    python scripts/transfer_state.py restore ZIP [--force]

The zip holds tokens and .env: keep it on a private drive, never in the repo. Stop the scheduled tasks on the old PC before
the backup (docs/SETUP_NEW_PC.md): two PCs with the same queue would post everything twice. Restore fixes the absolute paths
stored in the JSON state when the project folder is different on the new PC.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEMS = [".env", "presets.json", ".claude/skills", "outputs/tokens", "outputs/series", "outputs/publish_queue.json", "outputs/publish_state",
         "outputs/costs", "outputs/reports", "outputs/episodes", "outputs/final/shinobi", "assets/music", "assets/voices"]
MEMORY_PREFIX = "claude_memory/"
MANIFEST = "aurora_manifest.json"
PATH_JSON = ["outputs/publish_queue.json", "outputs/series", "outputs/publish_state", "outputs/episodes", "outputs/final"]


def memory_dir(root: Path) -> Path:
    """Claude Code keeps project memory under a folder named after the project path."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(root)) / "memory"


def _files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from (p for p in sorted(path.rglob("*")) if p.is_file() and "__pycache__" not in p.parts)


def backup(root: Path, out_dir: Path, with_models: bool = False, memory: Path | None = None, now: datetime | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError("the backup holds secrets: keep it outside the project folder")
    zip_path = out_dir / f"aurora_backup_{(now or datetime.now()):%Y%m%d_%H%M}.zip"
    memory = memory if memory is not None else memory_dir(root)
    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as z:
        for item in ITEMS + (["models"] if with_models else []):
            for f in _files(root / item):
                z.write(f, f.relative_to(root).as_posix())
                count += 1
        for f in _files(memory):
            z.write(f, MEMORY_PREFIX + f.relative_to(memory).as_posix())
            count += 1
        z.writestr(MANIFEST, json.dumps({"root": str(root), "files": count, "created": (now or datetime.now()).isoformat()}))
    return zip_path


def _fix_paths(root: Path, old: str) -> int:
    new = str(root)
    if old == new:
        return 0
    pairs = [(json.dumps(old)[1:-1], json.dumps(new)[1:-1]), (old.replace("\\", "/"), new.replace("\\", "/"))]
    changed = 0
    for item in PATH_JSON:
        for f in _files(root / item):
            if f.suffix != ".json":
                continue
            text = f.read_text(encoding="utf-8")
            fixed = text
            for a, b in pairs:
                fixed = fixed.replace(a, b)
            if fixed != text:
                f.write_text(fixed, encoding="utf-8")
                changed += 1
    return changed


def restore(zip_path: Path, root: Path, force: bool = False, memory: Path | None = None) -> dict:
    if (root / "outputs" / "publish_queue.json").exists() and not force:
        raise FileExistsError("this folder already has a publish queue: pass --force to replace it")
    memory = memory if memory is not None else memory_dir(root)
    with zipfile.ZipFile(zip_path) as z:
        info = json.loads(z.read(MANIFEST))
        for name in z.namelist():
            if name == MANIFEST:
                continue
            base, rel = (memory, name[len(MEMORY_PREFIX):]) if name.startswith(MEMORY_PREFIX) else (root, name)
            target = (base / rel).resolve()
            if not target.is_relative_to(base.resolve()):
                raise ValueError(f"unsafe path in backup: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(name) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return {"files": info["files"], "paths_fixed_in": _fix_paths(root, info["root"]), "created": info["created"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--out", type=Path, default=Path.home() / "AuroraBackup")
    b.add_argument("--with-models", action="store_true", help="also copy models/ (about 120 MB)")
    r = sub.add_parser("restore")
    r.add_argument("zip", type=Path)
    r.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "backup":
        z = backup(ROOT, a.out, a.with_models)
        print(f"backup: {z} ({z.stat().st_size // 1048576} MB). It holds secrets: private drive only, never GitHub or public cloud links.")
    else:
        try:
            print("restored:", restore(a.zip, ROOT, a.force))
        except FileExistsError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
