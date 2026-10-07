# oom

Investigate memory-limit kills.

Confirm `last_termination=OOMKilled` (or State OOMKilled) on `get_pod_status`. Waiting may still be CrashLoopBackOff after the kill; that backoff is not a separate process bug.

An exit code of 137 alone is not sufficient proof. Need the OOMKilled termination reason.

Do not take the word "OOM" in the user question as confirmation. Cite this run's status evidence.

Then `validate_recovery` on the FOCUS object. Still-not-Ready is FAIL.
