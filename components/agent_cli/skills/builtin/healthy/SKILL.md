# healthy

Only use this path after a successful `list_pods` scan in this run.

If FOCUS is empty, do not call `get_pod_status`, `get_pod_logs`, or `validate_recovery`. Do not invent an incident.

Report a bounded observation: the pods in this `list_pods` result currently match Ready/phase/state conditions. That does not prove request-path health, cluster-wide health, or that no incident ever occurred.

If the scan did not succeed, this skill must not run.
