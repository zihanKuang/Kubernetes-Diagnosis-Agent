# pod-lifecycle (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method: timeline first — what happened first, which object, then current state.
Citrus mapping: Killing / SuccessfulCreate events plus current `list_pods`.
A killed pod and its replacement are two objects (different UID).
Do not merge a stale leftover event with a new workload because the clocks are close.
Present-tense "what just happened" means the newest cycle whose replacement is in the current pod list.

A replaced kill may `validate_recovery` PASS. PASS is Ready at checked_at, not "no incident occurred".

Finish with Citrus claims JSON: scope, unknowns, claims[].
