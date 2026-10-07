# crashloop

Investigate pods whose waiting reason or State is CrashLoopBackOff.

Read `get_pod_status` for waiting reason, last_termination, and exit code. Application logs are useful when the container actually started.

CrashLoopBackOff is a symptom (the process keeps exiting). It is not itself the root cause. Do not claim OOM, image pull, or probe failure without matching status/event evidence.

If last_termination is OOMKilled, stop treating this as a generic crash loop; the OOM skill applies.

A pod that is still not Ready should `validate_recovery` FAIL.
