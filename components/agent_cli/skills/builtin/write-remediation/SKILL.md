# write-remediation

This skill is disabled by default. Enabling it only requests write tools; it cannot grant permission by itself.

`restart_deployment`, `scale_deployment`, and `rollback_deployment` still require live evidence and an interactive human `y`. Eval and webhook sessions deny writes.

Do not assume a write ran. After an approved write, report `validate_recovery`.
