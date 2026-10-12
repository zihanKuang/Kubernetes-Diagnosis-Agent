# Round-1 live results (T073–T080)

Model: `deepseek-v4-flash` / DeepSeek `https://api.deepseek.com`. Same model for A/B/C.
Engine: ReAct. Memory off. Replay tools only. Writes: 0 attempts.
Elapsed wall: about 24 minutes for 180 diagnoses (not cluster time).
Price: not stated here (no official figure frozen in-repo).

Smoke (`data/skill-bench/smoke-live`, 9 diagnoses) used an earlier scorer that treated “not OOMKilled” as an unsupported cause. That scorer was corrected **before** round-1. Smoke scores are not mixed into this table.

Arm C is the **Citrus-adapted external Skill**, not geored/sre-skill running as-is.

Default `--skill-routing` remains **legacy**. This round does not change it.

## What ran

| Item | Count |
|------|-------|
| Case types | 12 synthetic (8 dev, 4 holdout) |
| Arms | A legacy, B own-skills, C external-adapted |
| Repeats | 5 |
| Diagnoses | **180** (all `scored`, 0 infra_failed, 0 fake) |
| LLM HTTP calls | A 235, B 261, C 263 (total 759, not 180) |
| Not run | live cluster inject, independent reviewer LLM |

180 runs are 12 templates × 5 repeats, not 180 independent production incidents.

## Per-case quality (n=5 each cell)

Facts/coverage are automatic substring and tool-binding checks. Several fact misses are exact-phrase misses (for example `current pods Ready`, `zero restarts`, `CrashLoopBackOff waiting`) even when the answer described the same situation. Severe flags are listed separately and are **not** offset by cheaper tokens.

| Case | split | A severe | B severe | C severe | A vs B | B vs C | Notes |
|------|-------|----------|----------|----------|--------|--------|-------|
| healthy-ready | dev | 0 | 0 | 0 | tie | tie | No false-healthy. Phrase `current pods Ready` missed by all. |
| storefront-self-heal | dev | 5 missed_object | 5 missed + 1 unsupported | 5 missed_object | A | C | **All arms** skipped `get_pod_status` (scan only). |
| crash-exit-unknown | dev | 0 | 0 | 0 | tie | tie | Unknown cause kept. |
| image-pull | dev | 0 | 1 false_recovery | 0 | A | C | B r5 claimed recovery while verify should FAIL. |
| memory-kill | dev | 0 | 0 | 0 | tie* | tie | *A: OOMKilled absent in all 5 records. B/C: OOMKilled present 5/5; phrase `CrashLoopBackOff waiting` missed. |
| probe-unready | dev | 0 | 0 | 0 | tie | tie | Phrase `zero restarts` missed by all. |
| unschedulable | dev | 0 | 0 | 0 | **B** | tie | Facts 2/5 A vs 5/5 B and C. |
| mixed-stale-and-fresh | dev | 5 missed_object | 5 missed_object | 5 missed_object | tie | tie | Same skip-status pattern as other self-heals. |
| checkout-self-heal | holdout | 5 missed_object | 5 missed_object | 5 missed_object | tie | tie | Same. Names were not frontend-specific. |
| multi-fault | holdout | 0 | 0 | 0 | tie | tie | Both objects inspected. |
| user-guessed-oom | holdout | 0 | 0 | 0 | **B** | tie | Did not copy the user’s OOM guess as cause. Facts 4/5 A vs 5/5 B,C. |
| scan-unavailable | holdout | 1 unsupported | 0 | 0 | **B** | tie | A r1 wrote forbidden “namespace is healthy” while also saying the scan timed out; A used 40 LLM calls vs 10. |

Automated pair rule: fewer severe first, then more passed dimensions (advice excluded). Ties are ties.

## Severe failures (kept visible)

- **false_healthy**: 0
- **wrong_object**: 0
- **false_recovery**: 1 (B image-pull r5)
- **unsupported_cause**: 2 (A scan-unavailable r1; B storefront-self-heal r2)
- **missed_object**: 45 — every run of the three self-heal cases, every arm. Tools were `list_pods` + `get_recent_events` only. This is shared FOCUS/inspect behavior on a currently-Ready replacement, not an A-only or B-only miss.

Replay gaps (not counted as silent arm failures): `query_prometheus` unmatched 4 times; two odd `validate_recovery` selectors. Writes: 0.

Invalid diagnosis drafts (“compound statement; split restart, OOM, and recovery”) unpublished the model text on 16/18/16 runs (A/B/C). That hits publish, not just Skills.

## Cost and time (replay wall, not cluster)

| Arm | diagnoses | prompt tokens | completion tokens | LLM calls | tool calls | duration_s |
|-----|-----------|---------------|-------------------|-----------|------------|------------|
| A | 60 | 628927 | 53414 | 235 | 258 | 422 |
| B | 60 | 441500 | 69011 | 261 | 280 | 493 |
| C | 60 | 467038 | 74772 | 263 | 284 | 508 |

A used more prompt tokens (full tool catalog; plus scan-unavailable burning steps) and fewer completion tokens. B/C used more LLM turns. Replay seconds are not Kubernetes latency.

## Where improvement came from (examples)

- **B/C scan-unavailable** (`B__scan-unavailable__r1__5fa567ede8c1`): two tools, stop, unknown. **A** (`A__scan-unavailable__r1__79b72a28005e`): retried list_pods five times, called unmatched Prometheus, and the published text still contained “namespace is healthy”. This is routing/stage stop + instruction, not “Skills are magic”.
- **B unschedulable** (`B__unschedulable__r1__b7151db4b7e2`): published nodeSelector / FailedScheduling claims. **A** r1 draft was invalid so facts never landed.
- **memory-kill**: B/C records contain `OOMKilled` 5/5; A 0/5. Automated facts still fail all arms on the extra phrase `CrashLoopBackOff waiting`, so the pair table shows a tie. Manual read of the records is why this is not “Skills did nothing”.
- **Self-heal family**: all arms skipped inspect. Cannot credit Skills for RCA completeness there.
- **B image-pull r5**: ImagePull facts were right, then a recovery_claim with PASS while the fixture is FAIL. That is a Skills-arm severe miss A did not have on that case.

Causal attribution is incomplete: model randomness (5 repeats) and the claims JSON publisher both move scores. Do not assign every A/B delta to on-demand loading alone.

## Adoption

Keep **A / legacy** as the default.

Do not switch default to B. Quality is mixed: B is clearly better on scan-failure stopping and a bit better on scheduling / user-guessed probe; B added a false recovery; self-heal inspect is broken for everyone; token tradeoff is not a win on all axes.

Do not adopt C as default. C matched B on almost every case. The two automated C wins over B are B’s own severe misses, not a general superiority of the adapted geored method.

There is still no live-cluster interleaved proof (`LIVE_CLUSTER.md`). Replay winners are not production verification.

Raw jobs: `data/skill-bench/round-1/jobs/` (gitignored). Re-report: `python -m agent_cli.skill_bench report --out ..\data\skill-bench\round-1`.
