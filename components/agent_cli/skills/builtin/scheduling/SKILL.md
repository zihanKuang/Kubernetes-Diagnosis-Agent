# scheduling

Investigate pods that were never placed: Pending plus FailedScheduling or Unschedulable.

Pending alone is not scheduling failure. Do not confuse this with ImagePullBackOff.

Read `get_pod_status` PodScheduled condition and the FailedScheduling event message for node selector / resource constraints.

`validate_recovery` FAIL while the pod is unschedulable.
