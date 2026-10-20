from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def read_topics(args: argparse.Namespace) -> list[str]:
    if args.topics:
        return [topic.strip() for topic in args.topics if topic.strip()]
    if args.file:
        lines = Path(args.file).read_text(encoding="utf-8").splitlines()
        return [line.strip() for line in lines if line.strip()]
    return []


def load_job_summary(output_root: Path) -> dict:
    log_json = output_root / "log.json"
    if not log_json.exists():
        return {}
    pipeline_log = json.loads(log_json.read_text(encoding="utf-8"))
    session_id = pipeline_log.get("session_id")
    if not session_id:
        return {"status": "failed", "provider": "unknown"}

    job_json = output_root / session_id / "logs" / "job.json"
    job_data = {}
    if job_json.exists():
        job_data = json.loads(job_json.read_text(encoding="utf-8"))

    return {
        "job_id": session_id,
        "provider": job_data.get("provider_used", "unknown"),
        "status": (
            "success"
            if pipeline_log.get("completed_at") and pipeline_log.get("final_output")
            else "failed"
        ),
        "time_sec": pipeline_log.get("total_elapsed_sec"),
        "progress_percent": job_data.get("progress_percent"),
        "final_output": pipeline_log.get("final_output"),
        "error": next(
            (
                step.get("error")
                for step in pipeline_log.get("steps", {}).values()
                if step.get("status") == "failed" and step.get("error")
            ),
            None,
        ),
    }


def format_cell(value: object, width: int) -> str:
    text = "" if value is None else str(value)
    return text[:width].ljust(width)


def print_summary(rows: list[dict]) -> None:
    headers = ("Topic", "Provider", "Time", "Status")
    widths = (48, 16, 8, 10)
    print("\nBatch Summary")
    print(" | ".join(format_cell(h, w) for h, w in zip(headers, widths)))
    print("-" * (sum(widths) + 9))
    for row in rows:
        print(" | ".join([
            format_cell(row.get("topic"), widths[0]),
            format_cell(row.get("provider"), widths[1]),
            format_cell(f"{row.get('time_sec', '')}s" if row.get("time_sec") is not None else "", widths[2]),
            format_cell(row.get("status"), widths[3]),
        ]))


def run_batch(topics: list[str], mode: str, motion_mode: str, visual_complexity: str, config: str) -> int:
    output_root = Path("outputs")
    rows: list[dict] = []

    for topic in topics:
        started = time.time()
        cmd = [
            sys.executable,
            "-X",
            "utf8",
            "main.py",
            "--topic",
            topic,
            "--mode",
            mode,
            "--motion-mode",
            motion_mode,
            "--visual-complexity",
            visual_complexity,
            "--config",
            config,
        ]
        print(f"\n[Batch] Starting: {topic}")
        result = subprocess.run(cmd, text=True)
        summary = load_job_summary(output_root)
        elapsed = round(time.time() - started, 1)
        row = {
            "topic": topic,
            "provider": summary.get("provider", "unknown"),
            "time_sec": summary.get("time_sec", elapsed),
            "status": "success" if result.returncode == 0 else "failed",
            "job_id": summary.get("job_id"),
            "progress_percent": summary.get("progress_percent"),
            "final_output": summary.get("final_output"),
            "error": summary.get("error"),
        }
        rows.append(row)
        if result.returncode != 0:
            print(f"[Batch] Continuing after failure: {topic}")

    print_summary(rows)
    failed = [row for row in rows if row["status"] != "success"]
    if failed:
        print("\nFailures")
        for row in failed:
            print(f"- {row['topic']}: {row.get('error') or 'Unknown error'}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sequential batch runner for AI Video Factory topics."
    )
    parser.add_argument("topics", nargs="*", help="Topics to render sequentially.")
    parser.add_argument("--file", help="Optional UTF-8 text file with one topic per line.")
    parser.add_argument("--mode", default="static_cinematic")
    parser.add_argument("--motion-mode", default="static_cinematic")
    parser.add_argument("--visual-complexity", default="standard")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    topics = read_topics(args)
    if not topics:
        print("No topics provided. Use positional topics or --file.")
        return 1

    return run_batch(topics, args.mode, args.motion_mode, args.visual_complexity, args.config)


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    raise SystemExit(main())
