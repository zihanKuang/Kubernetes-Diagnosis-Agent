# pod-lifecycle

Investigate live kill/replace cycles.

Identify what was killed from the Killing event object/UID, not from an unrelated Recovered event. A killed pod and its replacement are two objects. Chain events only with the same UID, the same OBJECT plus owner, or an explicit ReplicaSet SuccessfulCreate link.

A question in present/recent tense means the most recent cycle whose resulting pod is in the current `list_pods` output. Older kill cycles are prior history, not this incident.

Frontend leftover events next to a checkout kill stay separate. Do not merge incidents because timestamps are close or names look similar.

A kill the ReplicaSet already replaced can `validate_recovery` PASS. PASS is Ready-at-checked_at for the matched objects, not sustained request-path health.
