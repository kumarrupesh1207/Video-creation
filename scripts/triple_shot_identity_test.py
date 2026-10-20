from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_ASSET_KEY = "28a108349bceb1e5fe6c8aa7aee05c80ef71baa827d310371b00bc3d5973ba56"


def _load_manifest() -> list[dict]:
    manifest_path = ROOT / "assets" / "images" / "manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _default_audio_files() -> list[Path]:
    audio_dir = ROOT / "outputs" / "audio"
    candidates = sorted(audio_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(candidates) < 3:
        raise RuntimeError("Need at least three narration audio files in outputs/audio for the triple-shot test.")
    return list(reversed(candidates[:3]))


def _run_render_job(
    *,
    asset_key: str,
    topic: str,
    audio_path: Path,
    identity_lock: bool,
    extra_args: list[str] | None = None,
) -> Path:
    cmd = [
        "python",
        "-u",
        "scripts/render_from_asset.py",
        "--asset-key",
        asset_key,
        "--topic",
        topic,
        "--mode",
        "cinematic",
        "--motion-mode",
        "talking_motion",
        "--audio-narration-path",
        str(audio_path),
        "--config",
        "config.yaml",
    ]
    if identity_lock:
        cmd.append("--identity-lock")
    if extra_args:
        cmd.extend(extra_args)

    before = set((ROOT / "outputs").glob("*/logs/job.json"))
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    after = set((ROOT / "outputs").glob("*/logs/job.json"))
    new_logs = sorted(after - before, key=lambda p: p.stat().st_mtime, reverse=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Render job failed for '{topic}'.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT tail:\n{_tail(result.stdout)}\n"
            f"STDERR tail:\n{_tail(result.stderr)}"
        )
    if not new_logs:
        raise RuntimeError(f"Could not locate a new job log for '{topic}'.")
    return new_logs[0]


def _tail(text: str, lines: int = 20) -> str:
    parts = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(parts[-lines:])


def _load_job(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_variant_count(asset_key: str) -> int:
    for record in _load_manifest():
        if record.get("asset_key") == asset_key:
            return int(record.get("motion_variant_count", 0) or 0)
    return 0


def _build_summary_rows(job_logs: list[Path]) -> list[dict]:
    rows = []
    for job_log in job_logs:
        job = _load_job(job_log)
        motion_metrics = job.get("motion_gpu_metrics") or {}
        g12 = job.get("g12_check") or {}
        audio_path = job.get("audio_narration_path") or job.get("steps", {}).get("audio_gen", {}).get("output")
        rows.append(
            {
                "job_id": job.get("job_id"),
                "narration_file": Path(audio_path).name if audio_path else "—",
                "warm_start": motion_metrics.get("warm_start"),
                "appearance_cache_hit": motion_metrics.get("appearance_cache_hit"),
                "mse_score": g12.get("mse"),
                "vram_peak_mb": motion_metrics.get("vram_peak_mb"),
                "generation_seed": job.get("generation_seed"),
                "reference_anchor_asset": job.get("reference_anchor_asset"),
                "audio_duration_delta_sec": (job.get("fr011_check") or {}).get("duration_delta_sec"),
                "total_elapsed_sec": job.get("total_elapsed_sec"),
            }
        )
    return rows


def _print_summary(rows: list[dict], manifest_before: int, manifest_after: int) -> None:
    headers = [
        "Job ID",
        "Narration File",
        "Warm Start?",
        "Cache Hit?",
        "MSE Score",
        "VRAM Peak",
        "Seed",
    ]
    table_rows = [
        [
            str(row.get("job_id", "")),
            str(row.get("narration_file", "")),
            str(row.get("warm_start", "")),
            str(row.get("appearance_cache_hit", "")),
            str(row.get("mse_score", "")),
            str(row.get("vram_peak_mb", "")),
            str(row.get("generation_seed", "")),
        ]
        for row in rows
    ]
    widths = [
        max(len(headers[idx]), max((len(r[idx]) for r in table_rows), default=0))
        for idx in range(len(headers))
    ]
    print("\nTriple-Shot Identity Summary")
    print(" | ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers))))
    print("-+-".join("-" * widths[idx] for idx in range(len(headers))))
    for row in table_rows:
        print(" | ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))))
    print(f"\nManifest variants before: {manifest_before}")
    print(f"Manifest variants after : {manifest_after}")
    print(f"New variants added      : {manifest_after - manifest_before}")


def _validate_consistency(rows: list[dict], asset_key: str) -> None:
    seeds = {row.get("generation_seed") for row in rows}
    anchors = {row.get("reference_anchor_asset") for row in rows}
    if len(seeds) != 1:
        raise RuntimeError(f"Consistency failure: generation seeds differ across jobs: {seeds}")
    if anchors != {asset_key}:
        raise RuntimeError(f"Consistency failure: reference anchors differ across jobs: {anchors}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the triple-shot identity stress test.")
    parser.add_argument("--asset-key", default=DEFAULT_ASSET_KEY)
    parser.add_argument("--audio", nargs=3, help="Three narration audio files to use in order.")
    parser.add_argument("--no-identity-lock", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="Validate inputs and print the planned shots without running them.")
    args = parser.parse_args()

    asset = next((record for record in _load_manifest() if record.get("asset_key") == args.asset_key), None)
    if not asset:
        raise SystemExit(f"Asset key not found in manifest: {args.asset_key}")

    audio_files = [Path(path).resolve() for path in args.audio] if args.audio else _default_audio_files()
    for audio in audio_files:
        if not audio.exists():
            raise SystemExit(f"Narration audio not found: {audio}")

    shots = [
        {"topic": "Triple Shot 1 - Anchor Initialization", "audio": audio_files[0], "extra_args": []},
        {"topic": "Triple Shot 2 - Identity Persistence", "audio": audio_files[1], "extra_args": []},
        {
            "topic": "Triple Shot 3 - Anchor Stress Variation",
            "audio": audio_files[2],
            "extra_args": [],
        },
    ]

    print("Triple-shot plan:")
    for idx, shot in enumerate(shots, start=1):
        print(f"{idx}. {shot['topic']} -> {shot['audio'].name}")
    if args.check_only:
        return 0

    manifest_before = _manifest_variant_count(args.asset_key)
    job_logs: list[Path] = []
    for shot in shots:
        job_log = _run_render_job(
            asset_key=args.asset_key,
            topic=shot["topic"],
            audio_path=shot["audio"],
            identity_lock=not args.no_identity_lock,
            extra_args=shot["extra_args"],
        )
        job_logs.append(job_log)
        time.sleep(1)

    rows = _build_summary_rows(job_logs)
    _validate_consistency(rows, args.asset_key)
    manifest_after = _manifest_variant_count(args.asset_key)
    _print_summary(rows, manifest_before, manifest_after)
    return 0


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    raise SystemExit(main())
