"""Tiny monthly cost ledger (Rule 4) + budget guard for paid providers: outputs/costs/YYYY-MM.json."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COST_DIR = ROOT / "outputs" / "costs"


class BudgetExceeded(RuntimeError):
    pass


def _path(cost_dir: Path, when: datetime | None = None) -> Path:
    return Path(cost_dir) / f"{(when or datetime.now()):%Y-%m}.json"


def load_ledger(cost_dir: Path = COST_DIR, when: datetime | None = None) -> dict:
    p = _path(cost_dir, when)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"entries": [], "total_usd": 0.0}


def month_spend_usd(cost_dir: Path = COST_DIR) -> float:
    return float(load_ledger(cost_dir).get("total_usd", 0.0))


def log_cost(provider: str, model: str, seconds: float, cost_usd: float, job_id: str = "", cost_dir: Path = COST_DIR) -> None:
    ledger = load_ledger(cost_dir)
    ledger["entries"].append({"ts": datetime.now().isoformat(timespec="seconds"), "provider": provider, "model": model,
                              "seconds": seconds, "cost_usd": round(cost_usd, 4), "job_id": job_id})
    ledger["total_usd"] = round(sum(e["cost_usd"] for e in ledger["entries"]), 4)
    p = _path(cost_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def check_budget(estimate_usd: float, monthly_budget_inr: float, usd_inr: float = 85.0, cost_dir: Path = COST_DIR) -> None:
    """Refuse (raise BudgetExceeded) if this month's logged spend + the estimate exceeds the INR budget (0 = no spending)."""
    budget_usd = float(monthly_budget_inr) / float(usd_inr)
    spent = month_spend_usd(cost_dir)
    if spent + estimate_usd > budget_usd + 1e-9:
        raise BudgetExceeded(
            f"budget cap: spent ${spent:.2f} + this call ${estimate_usd:.2f} > monthly budget "
            f"Rs {monthly_budget_inr:g} (${budget_usd:.2f}). Raise motion.i2v.google.monthly_budget_inr to allow it.")
