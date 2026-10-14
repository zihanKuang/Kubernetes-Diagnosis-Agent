# Paid live plan (T065–T072) — executed

Round-1 finished 180/180. Numbers and adoption decision: [RESULTS.md](RESULTS.md).
This file is the plan that was used, not a new authorization.

Shared conditions are in `agent_cli.skill_bench.conditions.SHARED`.
Three arms use the same model, engine, step budget, retries, and exam clock.
They differ only on the declared experimental factors.

## Intended first round

- 12 synthetic cases (8 dev, 4 holdout)
- 3 arms
- 5 repeats
- Interleaved with seed `20260315`
- Concurrency 1
- Memory off, writes denied, replay tools only

That is **180 diagnoses**, not 180 model calls. Each diagnosis may use multiple LLM turns (cap `max_steps=10`). Upper bound is 1800 LLM HTTP calls if every run hits the step cap; typical runs should use fewer.

Holdout cases are not for prompt tuning.

## Budget fields to fill before a live run

| Field | Value in this session |
|-------|------------------------|
| Authorization | granted this session (user: 有预算，跑) |
| Model id | `deepseek-v4-flash` (default; do not swap models to help an arm) |
| Provider | `deepseek` |
| Price | unknown here — do not invent a dollar amount |
| `--budget-diagnoses` | unset |
| Smoke (3 cases × 3 arms × 1) | 9 diagnoses, not started |
| Reviewer LLM | off unless separately budgeted |

## Live command (exists, blocked without flags)

```
python -m agent_cli.skill_bench run --live --i-accept-model-charges --budget-diagnoses 180 --repeats 5 --out ..\data\skill-bench\round-1
```

Smoke (`data/skill-bench/smoke-live`) completed 9/9. Round-1 (`data/skill-bench/round-1`) completed 180/180. See RESULTS.md.

Failed runs stay in the result dir. Do not drop them to improve averages.

## After scores

Report per-case counts, A vs B pairs, B vs C pairs, severe failures, dev vs holdout, tokens/calls/time separately.
Do not change default `--skill-routing`.
Replay winners still need a separate resettable cluster plan (`LIVE_CLUSTER.md`).
