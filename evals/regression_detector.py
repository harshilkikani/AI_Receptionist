"""Nightly regression detector.

Runs the eval suite, appends to data/eval_history.jsonl, and if the
pass rate dropped > 5% vs the prior run, posts an alert via the same
transport the daily digest uses.

Intended to be triggered by src/scheduler.py once a day. Can also be run
manually:

    python -m evals.regression_detector
    python -m evals.regression_detector --dry-run   # no alert
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Optional

from evals import runner

log = logging.getLogger("evals.regression")

REGRESSION_THRESHOLD = 0.05  # 5 percentage points


def detect(summary: dict, previous: Optional[dict] = None) -> dict:
    if previous is None:
        return {"regressed": False, "reason": "no_baseline",
                "delta_pct_points": 0.0}
    prev_rate = float(previous.get("pass_rate") or 0)
    cur_rate = float(summary.get("pass_rate") or 0)
    delta = cur_rate - prev_rate
    regressed = (delta < 0) and (abs(delta) > REGRESSION_THRESHOLD)
    return {
        "regressed": regressed,
        "delta_pct_points": round(delta, 4),
        "previous_pass_rate": prev_rate,
        "current_pass_rate": cur_rate,
    }


def _alert(summary: dict, diff: dict) -> bool:
    """Fire an alert through the existing alerts transport. Returns sent flag."""
    try:
        from src import alerts
    except ImportError:
        return False
    subject = (
        f"Eval regression — pass rate dropped "
        f"{diff['previous_pass_rate']*100:.1f}% → "
        f"{diff['current_pass_rate']*100:.1f}% "
        f"(Δ {diff['delta_pct_points']*100:+.1f}pp)"
    )
    body = subject + "\n\nFailed cases:\n" + "\n".join(
        f"  - {r['id']}: " + ", ".join(r["reasons"])
        for r in summary.get("results") or [] if not r["pass"]
    )
    payload = {
        "type": "eval_regression",
        "subject": subject,
        "summary": {k: v for k, v in summary.items() if k != "results"},
        "failures": [r for r in summary.get("results") or [] if not r["pass"]],
        "diff": diff,
    }
    return alerts._dispatch(subject, body, payload)


def run(dry_run: bool = False) -> dict:
    summary = runner.run_cases()
    previous = runner.latest_summary()
    runner.append_history(summary)
    diff = detect(summary, previous)
    if diff["regressed"] and not dry_run:
        sent = _alert(summary, diff)
        diff["alert_sent"] = sent
    else:
        diff["alert_sent"] = False
    return {"summary_meta":
            {k: v for k, v in summary.items() if k != "results"},
            "diff": diff,
            "failures":
                [r for r in summary.get("results") or [] if not r["pass"]]}


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.regression_detector")
    p.add_argument("--dry-run", action="store_true",
                   help="skip firing the alert even if regression detected")
    args = p.parse_args(argv)
    out = run(dry_run=args.dry_run)
    print(json.dumps(out, indent=2))
    return 0 if not out["diff"]["regressed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
