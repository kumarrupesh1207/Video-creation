from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROVIDERS = [
    ("framepack_provider", "high action"),
    ("svdxt_provider", "atmospheric"),
    ("wan2gp_provider", "standard"),
]


def run_one(asset_key: str, topic: str, provider: str, visual_complexity: str, config: str) -> dict:
    started = time.time()
    cmd = [
        sys.executable,
        "-X",
        "utf8",
        "scripts/render_from_asset.py",
        "--asset-key",
        asset_key,
        "--topic",
        topic,
        "--mode",
        "static_cinematic",
        "--motion-mode",
        "full_i2v_motion",
        "--visual-complexity",
        visual_complexity,
        "--config",
        config,
    ]
    result = subprocess.run(cmd, text=True)
    elapsed = round(time.time() - started, 1)
    latest = latest_job()
    return {
        "provider": provider,
        "visual_complexity": visual_complexity,
        "status": "success" if result.returncode == 0 else "failed",
        "time_sec": elapsed,
        "motion_provider_used": latest.get("motion_provider_used"),
        "motion_remote_status": latest.get("motion_remote_status"),
        "mock": (latest.get("motion_gpu_metrics") or {}).get("mock"),
        "warm_start": (latest.get("motion_gpu_metrics") or {}).get("warm_start"),
        "final_output": latest.get("final_output"),
        "fidelity_review": "manual_review_required",
        "temporal_stability_review": "manual_review_required",
        "compute_cost_hint": latest.get("motion_remote_latency_ms"),
    }


def latest_job() -> dict:
    jobs = sorted(Path("outputs").glob("*/logs/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jobs:
        return {}
    return json.loads(jobs[0].read_text(encoding="utf-8"))


def print_table(rows: list[dict]) -> None:
    headers = ["Provider", "Complexity", "Status", "Time", "Motion Used", "Remote Status", "Mock", "Warm"]
    widths = [18, 14, 10, 8, 22, 16, 6, 6]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-" * (sum(widths) + 15))
    for row in rows:
        print(" | ".join([
            str(row["provider"]).ljust(widths[0]),
            str(row["visual_complexity"]).ljust(widths[1]),
            str(row["status"]).ljust(widths[2]),
            f"{row['time_sec']}s".ljust(widths[3]),
            str(row.get("motion_provider_used", "")).ljust(widths[4]),
            str(row.get("motion_remote_status", "")).ljust(widths[5]),
            str(row.get("mock", "")).ljust(widths[6]),
            str(row.get("warm_start", "")).ljust(widths[7]),
        ]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark remote I2V providers against a cached asset.")
    parser.add_argument("--asset-key", required=True)
    parser.add_argument("--topic", default="I2V benchmark sample")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    rows = []
    for provider, visual_complexity in PROVIDERS:
        rows.append(run_one(args.asset_key, f"{args.topic} {provider}", provider, visual_complexity, args.config))
    print_table(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
