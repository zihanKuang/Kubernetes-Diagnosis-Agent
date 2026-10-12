# Later live-cluster check (T079) — not run this round

Replay success is not production verification.

If a later round wants a cluster check for a promising arm:

1. Reset the namespace to a known fixture (or empty + apply one case).
2. Wait until the same Ready/event conditions the case defined.
3. Interleave A/B/C on that same field, concurrency 1, memory off.
4. Use the same scorer. Do not change truth after seeing answers.
5. Only inject faults that the case already names. This round must not auto-run inject/deploy.

Required operations would be listed per case (apply stuck pod, wait, diagnose, delete).
None of that is executed by `skill_bench`.
