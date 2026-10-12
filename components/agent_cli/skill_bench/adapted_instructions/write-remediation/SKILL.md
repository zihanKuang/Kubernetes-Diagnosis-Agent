# write-remediation (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method is read-only recommendations with manual review.
Citrus mapping: you may propose restart_deployment / scale_deployment / rollback_deployment
targeting the FOCUS object. Bench replay and eval deny execution.
Do not claim a write ran. After any allowed write, report `validate_recovery`.

Finish with Citrus claims JSON: scope, unknowns, claims[].
