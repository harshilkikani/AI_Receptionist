# Evals

_Last updated: 2026-04-21 (commit 9ebbe65)_

A lightweight eval harness for catching behavior regressions in the
receptionist prompt. 20 seed cases ship in `evals/cases.jsonl`; the
runner replays them through `llm.chat` and the regression detector
compares the current pass rate against the previous run, alerting when
the drop exceeds 5 percentage points.

## Files

- `evals/cases.jsonl` — one JSON object per line. Fields:
  - `id` — short identifier, unique across the suite
  - `client_id` — which tenant to load (e.g. `ace_hvac`)
  - `caller_phone` — the `From` the case pretends to be
  - `turns` — list of `{role, text}` turns; the LAST turn is what the
    LLM receives, all prior turns become `conversation` context
  - `expected_intent` — one of `Emergency|Scheduling|Quote|Follow-up|General`
  - `expected_priority` — `high` | `low`
  - `must_contain` — substrings (case-insensitive) that MUST appear in
    the reply
  - `must_not_contain` — substrings that must NOT appear in the reply
    (useful for catching forbidden phrases like "Certainly")

- `evals/runner.py` — loads cases, calls `llm.chat` per case, scores.
- `evals/regression_detector.py` — runs the suite, appends to
  `data/eval_history.jsonl`, diffs vs previous, fires an alert on
  regression via the same transport `config/alerts.json` configures.

## Scoring

A case PASSES if:
- `reply.intent == case.expected_intent`
- `reply.priority == case.expected_priority`
- Every `must_contain` keyword appears (case-insensitive)
- Zero `must_not_contain` keywords appear

Any failure produces a `reasons` list so the admin view + history can
show why a case flipped.

## Running

```bash
# Run all 20 cases against the real LLM (costs ~$0.02)
python -m evals.runner

# Also append to data/eval_history.jsonl
python -m evals.runner --save

# Just one case
python -m evals.runner --case e1_emerg_burst

# Nightly regression detector (runs suite + compares to previous)
python -m evals.regression_detector
python -m evals.regression_detector --dry-run    # no alert
```

The `/admin/evals` page shows the latest pass rate and the last 10
recorded runs.

## Automating the nightly check

```
ENFORCE_EVAL_REGRESSION=true
EVAL_REGRESSION_HOUR_UTC=7    # 03:00 ET
```

The scheduler (`src/scheduler.py`) runs the regression detector once
per day at that UTC hour. Defaults to OFF because the run costs tokens.

## Adding cases

Best practice: when a live interaction surprises you (good or bad),
copy the transcript turns into a new line in `cases.jsonl` and set
expectations that reflect what the AI should have done.

- Use `id` prefixes that cluster by theme: `s` scheduling, `e` emergency,
  `q` quote, `f` follow-up, `g` general/miscellaneous.
- Keep `must_contain` minimal — prefer checking behavior via intent +
  priority. Pin a keyword only when the CONTENT matters (like "$129" for
  the service-call price line).
- Keep `must_not_contain` short too — target phrases the prompt
  explicitly forbids. Over-specification here creates flakiness.

## When the suite regresses

1. Open `/admin/evals` — see which cases flipped.
2. Re-run just the flipped ones: `python -m evals.runner --case <id>`.
3. If the regression is real:
   - Inspect the reply. Diff it against what was expected.
   - Adjust `prompts/receptionist_core.md` OR the case expectations
     (not both in the same commit).
4. If the regression is noise (LLM flakiness on a borderline case):
   - Tighten the case — narrow the `must_contain` list, or re-phrase the
     case's final turn to be less ambiguous.

## Cost

Running the full suite against Claude Haiku 4.5 is roughly $0.01–0.02
per run (20 cases × ~500 input tokens × 0.003/1k + response). Nightly
runs at 30 days = <$1/month. Not a meaningful cost center.

## Related files

- `evals/cases.jsonl` — seed set
- `data/eval_history.jsonl` — append-only log of runs
- `src/admin.py::evals_view` — admin dashboard surface
- `src/scheduler.py::_maybe_run_eval_regression` — nightly trigger
