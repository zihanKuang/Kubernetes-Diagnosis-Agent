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

That live command is implemented; it is **not** authorized by this session.

## Scoring

Each run records object correctness, supported facts / unknowns, object coverage,
unsupported claims, recovery claims, advice, plus tokens/time separately.
There is no composite score that lets cheaper tokens cancel a misdiagnosis.
`phrase_hit` is not accuracy.

## If live eval has not run

对照实验系统已准备并完成离线验证，尚不能判断 Skills 是否改善真实模型诊断表现。
