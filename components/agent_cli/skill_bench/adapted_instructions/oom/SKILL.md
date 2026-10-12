# oom (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method correlates memory spike → OOMKilled → restart and wants `kubectl top`.
Citrus mapping: confirm `last_termination=OOMKilled` (or State OOMKilled) on `get_pod_status`.
Waiting may still be CrashLoopBackOff; that is backoff after the kill.
`kubectl top` / live node metrics are not in the replay tool set. Do not invent usage graphs.
Exit 137 alone is not enough. The user saying "OOM" is not evidence.

`validate_recovery` FAIL while not Ready.

Finish with Citrus claims JSON: scope, unknowns, claims[].
