# readiness

Investigate pods that are Running but not Ready, often with zero restarts.

Read `get_pod_status` Ready/State and events for Unhealthy. Distinguish readiness from liveness. Zero restarts is not a crash loop.

Do not rewrite this as CrashLoopBackOff or OOM.

`validate_recovery` FAIL while the object is not Ready.
