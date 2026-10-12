# imagepull (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Candidate method: describe/events for Failed to pull image; do not require app logs.
Citrus mapping: `get_pod_status` + `get_recent_events`. `get_pod_logs` is optional and often errors because the process never started.

ImagePullBackOff / ErrImagePull is not an application crash and not FailedScheduling.
Do not demand logs as a completion condition.

`validate_recovery` FAIL while the image has not started.

Finish with Citrus claims JSON: scope, unknowns, claims[].
