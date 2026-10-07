# generic-triage

Start with a live scan. Call `list_pods`. For RCA or recovery questions also call `get_recent_events`.

Use the observation ledger FOCUS set. Do not treat names in the user question as confirmed targets.

If the scan fails or times out, stop. Report that cluster state is unknown. Do not say the namespace is healthy.

If the scan succeeds and FOCUS is empty, the healthy skill should take over. This skill is the fallback when state is unknown or unmatched.

If FOCUS exists but no specific failure class is confirmed, inspect `get_pod_status` on FOCUS objects. Keep CrashLoopBackOff as a symptom. Do not invent an exit cause.

Then call `validate_recovery` on FOCUS objects when the workflow reaches verification. PASS is Ready-at-checked_at only.
