"""P8 — measure actual prompt-caching savings on a live Anthropic key.

Runs the eval suite twice against `llm.chat_with_usage` and reports the
delta in input_tokens vs cache_read_input_tokens between the passes.

Run:
    python -m evals.cache_benchmark
    python -m evals.cache_benchmark --cases 5   # subset

Output is a plain-text summary + a JSON blob on the last line so scripts
can parse it.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from types import SimpleNamespace

import llm
from evals import runner


def _run_once(chat_fn, cases) -> dict:
    total_input = 0
    total_cache_read = 0
    total_latency_ms = 0
    for case in cases:
        caller = runner._caller_for(case)
        turns = case.get("turns") or []
        conv = [{"role": t["role"], "text": t["text"]} for t in turns[:-1]]
        last = turns[-1]["text"] if turns else ""
        t0 = time.monotonic()
        try:
            _reply, (in_tok, out_tok) = llm.chat_with_usage(
                caller, last, conv,
                client=runner.tenant.load_client_by_id(case["client_id"]),
            )
        except Exception as e:
            print(f"  error on {case['id']}: {e}", file=sys.stderr)
            continue
        # last_token_usage returns 3-tuple; chat_with_usage collapses to 2
        # Re-extract from cache_stats delta
        stats = llm.cache_stats()
        total_input = stats["total_input"]
        total_cache_read = stats["total_cache_read"]
        total_latency_ms += int((time.monotonic() - t0) * 1000)
    return {
        "total_input_tokens": total_input,
        "total_cache_read_tokens": total_cache_read,
        "total_latency_ms": total_latency_ms,
    }


def run(cases_limit: int = None) -> dict:
    cases = runner.load_cases()
    if cases_limit:
        cases = cases[:cases_limit]

    llm.reset_cache_stats()
    print(f"Pass 1 — cold cache ({len(cases)} cases)")
    pass1 = _run_once(llm.chat_with_usage, cases)
    print("  input_tokens: ", pass1["total_input_tokens"])
    print("  cache_read:   ", pass1["total_cache_read_tokens"])

    llm.reset_cache_stats()
    print(f"Pass 2 — warm cache ({len(cases)} cases, rerun)")
    pass2 = _run_once(llm.chat_with_usage, cases)
    print("  input_tokens: ", pass2["total_input_tokens"])
    print("  cache_read:   ", pass2["total_cache_read_tokens"])

    saved_tokens = pass2["total_cache_read_tokens"]
    # Anthropic ephemeral cache-read pricing is ~10% of normal input.
    # Rough savings = cache_read_tokens × (regular_rate - cache_read_rate)
    # Use rate_card for the real numbers.
    try:
        from src import usage as _u
        regular = _u._rate("llm_input_per_1k_tokens") or 0.003
    except Exception:
        regular = 0.003
    cache_rate = regular * 0.10
    saved_usd = (saved_tokens / 1000.0) * (regular - cache_rate)

    summary = {
        "cases_run": len(cases),
        "pass1": pass1,
        "pass2": pass2,
        "saved_tokens_second_pass": saved_tokens,
        "estimated_usd_saved_second_pass": round(saved_usd, 4),
    }
    print("\n=== Summary ===")
    print(json.dumps(summary, indent=2))
    return summary


def _cli(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.cache_benchmark")
    p.add_argument("--cases", type=int, default=None,
                   help="run only first N cases")
    args = p.parse_args(argv)
    run(cases_limit=args.cases)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
