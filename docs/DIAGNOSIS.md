# Diagnosis claims and review

The agent can emit free text (legacy routing) or a structured diagnosis draft
(Skills synthesize / `publish_diagnosis`). Neither path lets the generator
grade its own RCA.

Three separate mechanisms. Do not collapse them into “the agent is confident”.

## 1. Evidence stamp (process, always on)

`evidence.py` appends a footer after the published body:

```
Evidence check: HIGH|MEDIUM|LOW (process stamp, not RCA verification)
- diagnosis: not_reviewed — no semantic review; a matching citation is not root-cause verification
- recovery: pass|fail|unknown
- tools used: ...
```

HIGH/MEDIUM/LOW is computed from tool-call counts. Zero tool calls is always
LOW. The webhook never publishes a LOW body as `suspected_root_cause`.

This stamp does **not** prove the causal story is right. A matching `obs-N`
citation is also not RCA verification.

## 2. Structured claims (Skills / publish path)

`publish_diagnosis` accepts JSON claims with types
`observation | symptom | causal_claim | recovery_claim`. Citations must point at
this run’s observation / evidence ids. Invalid drafts are not published as free
text:

> Diagnosis draft invalid; unreviewed free text was not published.

Rendered sections keep causal lines labeled **hypotheses (not reviewed)**.
`validate_recovery` PASS only means Ready at `checked_at`, not lasting
request-path recovery.

Observation rows live in the per-run ledger (`data/runs/`). Incomplete packages
are marked not reviewable.

## 3. Independent reviewer (off the publish path)

`agent_cli.review` is a **separate** judge. It does not execute tools and is
not imported by `agent.py`. Missing `CITRUS_REVIEWER_*` credentials stay
`not_reviewed`. An LLM label can be demoted by deterministic policy (object
out of scope, OOM wording without `OOMKilled` in evidence, Chaos Mesh claimed
from a Killing event, Ready treated as lasting recovery).

Install the extra only if you run the judge:

```powershell
pip install -e ".[review]"
```

```
CITRUS_REVIEWER_PROVIDER=deepseek
CITRUS_REVIEWER_MODEL=deepseek-v4-flash
CITRUS_REVIEWER_BASE_URL=https://api.deepseek.com
CITRUS_REVIEWER_API_KEY=...
```

Do not reuse the generator API key by accident: reviewer config does not inherit
`DEEPSEEK_API_KEY`.

## What eval records

`python -m agent_cli.eval_rca` still scores **phrase_hit** (named the labeled
fault). Each row also records `diagnosis_status` (usually `not_reviewed`) and
`recovery_status`. Phrase hit is not RCA accuracy and not skill-routing hit
rate.

The Skills A/B/C bench (`python -m agent_cli.skill_bench`) scores object
correctness, facts, coverage, unsupported claims, and recovery separately.
See [skills/benchmark/README.md](skills/benchmark/README.md).
