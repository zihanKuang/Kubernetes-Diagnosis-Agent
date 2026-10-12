# scheduling (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method treats Pending as resource/constraint investigation, not image pull.
Citrus mapping: phase Pending plus FailedScheduling / Unschedulable on status or events.
Pending alone is not a root cause. Do not rewrite as ImagePullBackOff.
Node inventory and quota objects are unsupported here; use the event message you actually received.

`validate_recovery` FAIL while unschedulable.

Finish with Citrus claims JSON: scope, unknowns, claims[].
