# crashloop (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method wants events first, then logs, then previous logs.
Citrus mapping: `get_pod_status` then `get_pod_logs`. Previous logs are unsupported — do not invent them.

CrashLoopBackOff is a restart symptom. Read waiting reason, last_termination, and exit code.
If last_termination is OOMKilled, this is a memory kill, not a generic app crash.
If logs are missing because the container is not running, say logs were unavailable.
An exit without a cause in status/logs means the cause is unknown.

`validate_recovery` FAIL while not Ready.

Finish with Citrus claims JSON: scope, unknowns, claims[].
