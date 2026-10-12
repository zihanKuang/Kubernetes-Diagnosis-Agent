# readiness (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method: running but unhealthy → failed health checks.
Citrus mapping: Running, Ready=False, often zero restarts; events Unhealthy / probe failed.
This is not a crash and not OOM. Zero restarts rules out CrashLoopBackOff.

`validate_recovery` FAIL while Ready is false.

Finish with Citrus claims JSON: scope, unknowns, claims[].
