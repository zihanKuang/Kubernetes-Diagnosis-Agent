# Win rules (T007)

Report these separately. Do not add them into one score:

1. Object correctness — named workload/pod matches the investigated FOCUS object.
2. Supported facts and correct unknowns — evidence-backed statements; “unknown” is right when logs/status cannot name a cause.
3. Coverage — each required object has an inspect result or an explicit failure, not a global tool-call count.
4. Unsupported claims — forbidden causes, copied user guesses, healthy-after-scan-failure.
5. Recovery — PASS/FAIL/UNKNOWN must match the verify tool and must not treat current Ready as “no incident happened”.
6. Advice — targeted, in-policy; unbounded restart is a miss.
7. Cost/time — prompt tokens, completion tokens, LLM calls, retries, wall time; replay tool time is not cluster time.

Severe misses (false healthy, false recovery, wrong object, missed object, unsupported cause) stay visible even if that arm used fewer tokens.

`phrase_hit` is auxiliary only.
