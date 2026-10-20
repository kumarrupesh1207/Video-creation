"""
batch_orchestrator.py — Phase 3: Generate 10 Videos from a Topic List
──────────────────────────────────────────────────────────────────────
Runs the full pipeline for each topic sequentially.
One model in VRAM at a time — no concurrency.

Usage:
    # Run all topics in topics.json
    python src/batch_orchestrator.py

    # Custom topic list file
    python src/batch_orchestrator.py --topics my_topics.json

    # Dry-run (scripts only, no GPU)
    python src/batch_orchestrator.py --dry-run

    # Resume from a failed batch (skips already-completed topics)
    python src/batch_orchestrator.py --resume

topics.json format:
    [
      {"topic": "Agile Transformations in Banking", "frames": 12, "motion": 80},
      {"topic": "AI in Credit Risk Scoring",        "frames": 14, "motion": 100},
      ...
    ]
"""

import sys
import json
import time
import yaml
import argparse
import traceback
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import inline to avoid circular
import importlib.util


CONFIG_PATH = "config.yaml"
TOPICS_PATH = "topics.json"

# Default 10-topic fintech batch — senior PM / banking focus
DEFAULT_TOPICS = [
    {"topic": "Agile Transformations in Banking",            "frames": 12, "motion": 80},
    {"topic": "AI Credit Scoring Is Replacing FICO",         "frames": 12, "motion": 90},
    {"topic": "Real-Time Payments: The Rails of Tomorrow",   "frames": 12, "motion": 80},
    {"topic": "How Banks Are Shipping Like Startups",        "frames": 14, "motion": 85},
    {"topic": "Decentralized Identity in Financial Services","frames": 12, "motion": 75},
    {"topic": "The Death of the Branch: Digital-Only Banks", "frames": 14, "motion": 90},
    {"topic": "SAFe vs Scrum: What Works in Banking",        "frames": 12, "motion": 80},
    {"topic": "Embedded Finance: APIs Eating Banking",       "frames": 12, "motion": 85},
    {"topic": "RegTech: Compliance as a Competitive Edge",   "frames": 14, "motion": 80},
    {"topic": "Open Banking APIs and the Ecosystem Play",    "frames": 12, "motion": 90},
]


def load_topics(path: str) -> list:
    p = Path(path)
    if not p.exists():
        print(f"[Batch] topics.json not found at {p.resolve()}")
        print("[Batch] Creating default 10-topic fintech batch...")
        p.write_text(json.dumps(DEFAULT_TOPICS, indent=2), encoding="utf-8")
    return json.loads(p.read_text(encoding="utf-8"))


def load_config(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def slug(topic: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in topic)[:40].lower()


def get_completed(report_path: Path) -> set:
    """Load already-completed topic slugs from batch_report.json."""
    if not report_path.exists():
        return set()
    try:
        data = json.loads(report_path.read_text())
        return {r["slug"] for r in data.get("results", [])
                if r.get("status") == "success"}
    except Exception:
        return set()


def main():
    parser = argparse.ArgumentParser(
        description="Phase 3 Batch Orchestrator — generate N videos sequentially"
    )
    parser.add_argument("--topics",  default=TOPICS_PATH,
                        help="JSON topic list file (default: topics.json)")
    parser.add_argument("--config",  default=CONFIG_PATH)
    parser.add_argument("--dry-run", action="store_true",
                        help="Script generation only — no GPU work")
    parser.add_argument("--resume",  action="store_true",
                        help="Skip already-completed topics from batch_report.json")
    args = parser.parse_args()

    print("=" * 60)
    print("  🏭 AI VIDEO FACTORY — Phase 3 Batch Orchestrator")
    print("=" * 60)

    cfg     = load_config(args.config)
    topics  = load_topics(args.topics)
    total   = len(topics)

    report_path = Path(cfg["paths"]["base_output"]) / "batch_report.json"
    completed   = get_completed(report_path) if args.resume else set()

    print(f"\n[Batch] {total} topics loaded | "
          f"{'DRY RUN' if args.dry_run else 'FULL RENDER'} | "
          f"{len(completed)} already done")

    # Import pipeline runner
    spec = importlib.util.spec_from_file_location(
        "main", Path(__file__).parent.parent / "main.py")
    main_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main_mod)

    results = []
    batch_start = time.time()

    for i, item in enumerate(topics, start=1):
        topic  = item.get("topic", f"Topic {i}")
        frames = item.get("frames", 12)
        motion = item.get("motion", 80)
        fps    = item.get("fps",    6)
        s      = slug(topic)

        print(f"\n{'─'*60}")
        print(f"  [{i}/{total}] {topic}")
        print(f"{'─'*60}")

        # Skip if already done
        if s in completed:
            print(f"[Batch] ⏭  Skipping (already completed).")
            results.append({"slug": s, "topic": topic,
                             "status": "skipped", "output": None})
            continue

        t0 = time.time()
        try:
            output = main_mod.run_pipeline(
                cfg      = cfg,
                topic    = topic,
                frames   = frames,
                motion   = motion,
                fps      = fps,
                dry_run  = args.dry_run,
            )
            elapsed = time.time() - t0
            results.append({
                "slug":        s,
                "topic":       topic,
                "status":      "success",
                "output":      str(output) if output else None,
                "elapsed_sec": round(elapsed, 1),
            })
            print(f"[Batch] ✅ [{i}/{total}] Done in {elapsed:.1f}s")

        except Exception as e:
            elapsed = time.time() - t0
            tb = traceback.format_exc()
            results.append({
                "slug":        s,
                "topic":       topic,
                "status":      "failed",
                "error":       str(e),
                "traceback":   tb[:500],
                "elapsed_sec": round(elapsed, 1),
            })
            print(f"[Batch] ❌ [{i}/{total}] Failed: {e}")
            print("[Batch]    Continuing to next topic...")

        # Save progress after each topic
        _save_report(report_path, results, batch_start, total)

    # Final summary
    batch_elapsed = time.time() - batch_start
    success_count = sum(1 for r in results if r["status"] == "success")
    failed        = [r for r in results if r["status"] == "failed"]

    print("\n" + "=" * 60)
    print(f"  BATCH COMPLETE")
    print("=" * 60)
    print(f"  ✅ Success : {success_count}/{total}")
    print(f"  ❌ Failed  : {len(failed)}/{total}")
    print(f"  ⏱  Total   : {batch_elapsed/60:.1f} minutes")
    print(f"  📋 Report  : {report_path.resolve()}")

    if failed:
        print("\n  Failed topics:")
        for r in failed:
            print(f"    • {r['topic']}: {r.get('error', '')[:80]}")

    print()


def _save_report(path: Path, results: list, start: float, total: int) -> None:
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total":        total,
        "completed":    len([r for r in results if r["status"] == "success"]),
        "elapsed_sec":  round(time.time() - start, 1),
        "results":      results,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    from aurora import ensure_utf8_console

    ensure_utf8_console()  # emoji prints must not crash on Windows cp1252 consoles
    main()
