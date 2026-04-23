"""Eval runner — replays evals/cases.jsonl against llm.chat and scores.

A case is PASS when:
  - intent matches expected_intent
  - priority matches expected_priority
  - every must_contain keyword appears in the reply (case-insensitive)
  - no must_not_contain keyword appears

Run:
    python -m evals.runner                 # print scoreboard
    python -m evals.runner --save          # append to data/eval_history.jsonl
    python -m evals.runner --case <id>     # run just one case

`run_cases` is exposed for the /admin/evals endpoint + the regression
detector. The scoring function does not touch disk.

Tests inject a fake `chat_fn` so the suite can run offline.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from src import tenant
import memory

log = logging.getLogger("evals.runner")

_ROOT = Path(__file__).parent.parent
CASES_PATH = Path(__file__).parent / "cases.jsonl"
HISTORY_PATH = _ROOT / "data" / "eval_history.jsonl"


def load_cases(path: Path = CASES_PATH) -> list:
    cases = []
    if not path.exists():
        return cases
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def _default_chat_fn():
    """Real Claude call. Imported lazily so --help works without ANTHROPIC_API_KEY."""
    import llm
    return llm.chat


def _caller_for(case: dict) -> dict:
    phone = case.get("caller_phone") or ""
    return {
        "id": phone.replace("+", "") or case["id"],
        "name": "Test Caller",
        "phone": phone,
        "address": None,
        "equipment": None,
        "notes": None,
        "type": "new",
        "history": [],
        "conversation": [],
    }


def score(case: dict, reply, expected: dict = None) -> dict:
    """Given a case + the ChatResponse, decide pass/fail with reasons."""
    expected = expected or {
        "intent": case.get("expected_intent"),
        "priority": case.get("expected_priority"),
    }
    intent = getattr(reply, "intent", None) or (
        reply.get("intent") if isinstance(reply, dict) else None)
    priority = getattr(reply, "priority", None) or (
        reply.get("priority") if isinstance(reply, dict) else None)
    text = getattr(reply, "reply", None) or (
        reply.get("reply") if isinstance(reply, dict) else "")

    reasons = []
    if intent != expected["intent"]:
        reasons.append(f"intent={intent!r} expected={expected['intent']!r}")
    if priority != expected["priority"]:
        reasons.append(f"priority={priority!r} expected={expected['priority']!r}")

    low = (text or "").lower()
    for kw in case.get("must_contain") or []:
        if kw.lower() not in low:
            reasons.append(f"missing_keyword={kw!r}")
    for kw in case.get("must_not_contain") or []:
        if kw.lower() in low:
            reasons.append(f"forbidden_keyword={kw!r}")

    return {
        "id": case["id"],
        "pass": not reasons,
        "intent": intent,
        "priority": priority,
        "reply": text,
        "reasons": reasons,
    }


def run_case(case: dict,
             chat_fn: Optional[Callable] = None,
             client: Optional[dict] = None,
             use_cache: bool = True) -> dict:
    """Run one case. chat_fn signature matches llm.chat(caller, msg, conv, client).

    V3.16 — wraps chat_fn in a response-level cache so identical prompts
    don't re-hit the API. Pass use_cache=False to force-refresh.
    """
    base_fn = chat_fn or _default_chat_fn()
    # Wrap with the per-case cache (noop if use_cache=False)
    from evals.cache import CachingChatFn
    wrapped = CachingChatFn(base_fn, case_id=case["id"],
                             use_cache=use_cache)
    client = client or tenant.load_client_by_id(case["client_id"])
    caller = _caller_for(case)

    turns = case.get("turns") or []
    conversation = [{"role": t["role"], "text": t["text"]} for t in turns[:-1]]
    last = turns[-1] if turns else {"role": "user", "text": ""}

    t0 = time.monotonic()
    try:
        reply = wrapped(caller, last["text"], conversation, client=client)
    except Exception as e:
        return {"id": case["id"], "pass": False, "reasons": [f"exception:{type(e).__name__}:{e}"],
                "intent": None, "priority": None, "reply": "", "latency_ms": 0,
                "cache_hit": False}
    latency_ms = int((time.monotonic() - t0) * 1000)
    result = score(case, reply)
    result["latency_ms"] = latency_ms
    result["cache_hit"] = wrapped.last_hit
    return result


def run_cases(chat_fn: Optional[Callable] = None,
              case_ids: Optional[list] = None,
              use_cache: bool = True) -> dict:
    """Run every case in cases.jsonl (or the subset specified by case_ids)."""
    cases = load_cases()
    if case_ids:
        cases = [c for c in cases if c["id"] in set(case_ids)]

    results = [run_case(c, chat_fn=chat_fn, use_cache=use_cache)
               for c in cases]
    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    pass_rate = (passed / total) if total else 0.0
    avg_latency = (
        sum(r.get("latency_ms", 0) for r in results) / total
    ) if total else 0
    return {
        "ts": int(time.time()),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(pass_rate, 4),
        "avg_latency_ms": int(avg_latency),
        "results": results,
    }


def append_history(summary: dict, path: Optional[Path] = None) -> None:
    path = path or HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: v for k, v in summary.items() if k != "results"}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(slim) + "\n")


def load_history(path: Optional[Path] = None) -> list:
    path = path or HISTORY_PATH
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def latest_summary(path: Optional[Path] = None) -> Optional[dict]:
    h = load_history(path or HISTORY_PATH)
    return h[-1] if h else None


def _cli(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evals.runner")
    p.add_argument("--save", action="store_true",
                   help="append run to data/eval_history.jsonl")
    p.add_argument("--case", default=None, help="run only this case id")
    p.add_argument("--no-cache", action="store_true",
                   help="bypass the response cache; always hit the LLM")
    args = p.parse_args(argv)

    summary = run_cases(case_ids=[args.case] if args.case else None,
                         use_cache=(not args.no_cache))
    print(f"{summary['passed']}/{summary['total']} passed "
          f"({summary['pass_rate']*100:.1f}%). "
          f"avg latency {summary['avg_latency_ms']}ms")
    for r in summary["results"]:
        marker = "OK  " if r["pass"] else "FAIL"
        reasons = ("; ".join(r["reasons"])) if r["reasons"] else ""
        reply = (r.get("reply") or "")[:80]
        print(f"  {marker}  {r['id']:<24} {reply!r}  {reasons}")

    if args.save:
        append_history(summary)
        print(f"\nwrote history line to {HISTORY_PATH}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
