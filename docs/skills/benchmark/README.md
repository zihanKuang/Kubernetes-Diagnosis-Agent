# Citrus Skills A/B/C bench

Offline comparison harness. It does not start MCP, Kubernetes, or Prometheus.
Default routing stays **legacy**. This bench does not switch production defaults.

## Arms

| Arm | Label | What differs |
|-----|--------|----------------|
| A | legacy | `DEFAULT_SRE_SYSTEM_INSTRUCTION` plus the old full-catalog tool policy. This is **not** `LEGACY_SRE_SYSTEM_INSTRUCTION` (the historical ablation prompt). |
| B | own-skills | Same shared runtime after the T009–T018 fixes, `skill_routing=auto`, builtin SKILL.md. |
| C | external-adapted | Same routing, stages, budget, and claims protocol as B. Investigation text is a **Citrus-authored adaptation** of the geored/sre-skill method. It is not the upstream file running as-is. |

A vs B is the whole change (routing, stage tools, prompt assembly, text).
B vs C is investigation text only.

Do not treat fake-model scores as quality. Do not treat replay latency as cluster latency.

## Commands that exist

Run from `components/`:

```
python -m agent_cli.skill_bench list
python -m agent_cli.skill_bench preflight
python -m agent_cli.skill_bench freeze
python -m agent_cli.skill_bench plan --repeats 5 --out ..\data\skill-bench
python -m agent_cli.skill_bench run --fake-model --repeats 1 --out ..\data\skill-bench\offline
python -m agent_cli.skill_bench report --out ..\data\skill-bench\offline
```

Live model calls are refused unless both flags are present:

```
python -m agent_cli.skill_bench run --live --i-accept-model-charges --budget-diagnoses 9 --split dev --repeats 1 --cases healthy-ready,crash-exit-unknown,scan-unavailable
```

Smoke (`--budget-diagnoses 9`) and round-1 (`--budget-diagnoses 180 --repeats 5`)
already ran. See [RESULTS.md](RESULTS.md). Default `--skill-routing` remains
**legacy**. Do not treat that round as a reason to switch production defaults.

## Scoring

Each run records object correctness, supported facts / unknowns, object coverage,
unsupported claims, recovery claims, advice, plus tokens/time separately.
There is no composite score that lets cheaper tokens cancel a misdiagnosis.
`phrase_hit` is not accuracy.

## Live round-1 (done)

180 diagnoses, DeepSeek `deepseek-v4-flash`, replay tools only, memory off.
Quality is mixed. Skills (B) stopped better on scan-failure and a bit better on
scheduling / user-guessed probe; B added one false recovery; every arm skipped
inspect on the self-heal family. Arm C matched B. Adoption decision: **keep A /
legacy**. Full table: [RESULTS.md](RESULTS.md). Cluster inject is still not run
([LIVE_CLUSTER.md](LIVE_CLUSTER.md)).
