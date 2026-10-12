# healthy (Citrus-adapted external method)

Source: Citrus-adapted investigation phases from geored/sre-skill
commit d8af309. Not the upstream file.

Only after a successful scan in this run, and only if FOCUS is empty.

Do not collect logs, status, or recovery on Ready context pods.
Bounded statement: current list_pods rows match Ready. That is not request-path health and not "nothing ever happened".
Historical restarts on Ready pods are not a current incident.

If the scan failed, this path must not declare healthy.

Finish with Citrus claims JSON: scope, unknowns, claims[].
