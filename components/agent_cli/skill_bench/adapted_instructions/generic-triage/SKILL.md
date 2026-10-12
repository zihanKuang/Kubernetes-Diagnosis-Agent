# generic-triage (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309, not the upstream file and not installed into a personal skill directory.

Work read-only. Scan before inspect. Do not treat the first error as the cause.

## Phase 1 — triage
Call `list_pods`. For RCA also call `get_recent_events`.
Judge scope from this run's ledger FOCUS set, not from names in the question.
If the scan errors or times out, stop. Report unknown cluster state. Do not say the namespace is healthy.

## Phase 2 — collect
If FOCUS exists, call `get_pod_status` on each FOCUS object (`from_obs`).
Call `get_pod_logs` only after status, and only if the container could have produced logs.
There is no previous-container log tool and no node/top dump in this runtime.

## Phase 3 — correlate
Use event TIME/FIRST/COUNT and status last_termination. Close timestamps are not the same incident.

## Phase 4 — cause
Keep CrashLoopBackOff, Pending, and Ready=False as symptoms until status/events name a cause.
If evidence is only an exit, leave the cause unknown.

## Phase 5 — recovery advice
Call `validate_recovery` when the verify stage is offered.
PASS is Ready at checked_at. Recommend writes; do not expect them to execute here.

Finish with Citrus claims JSON: scope, unknowns, claims[].
Each claim: id, statement, type, object, window, obs, evidence, confidence.
type is observation | symptom | causal_claim | recovery_claim.
