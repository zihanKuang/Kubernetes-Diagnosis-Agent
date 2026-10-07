# imagepull

Investigate containers that never started: ImagePullBackOff or ErrImagePull.

Use `get_pod_status` and pull-related events. Do not require application logs; the process never ran.

Separate image, auth, and network pull failures only when the event or status text names them. Do not confuse this with Pending+FailedScheduling.

`validate_recovery` on a still-unstarted pod should FAIL.
