# Citrus Skills

Citrus Skills are **this project's** diagnostic packages. They are not Cursor or
Codex personal skills. Default CLI routing stays **legacy** (the current
`prompts.py` instruction plus the full tool catalog). Skills are opt-in.

A 180-diagnosis replay bench did **not** justify switching the default. Mixed
quality, a false recovery on the Skills arm, and a shared self-heal inspect gap
are recorded in [benchmark/RESULTS.md](benchmark/RESULTS.md).

## What a Skill is

Each builtin package under `components/agent_cli/skills/builtin/` is:

- `SKILL.toml` — id, routing conditions, entrypoints
- `workflow.toml` — ordered stages (scan → inspect → verify → synthesize)
- `SKILL.md` — investigation notes, loaded only after the skill is selected

The router is deterministic (no extra LLM call). `--skill <id>` locks the id.
It does not skip scan, evidence, tool allow-list, or write approval.

## Builtin packages

| id | When it is for | Writes |
|----|----------------|--------|
| `healthy` | Successful scan, no FOCUS | no |
| `crashloop` | `CrashLoopBackOff` waiting, not OOMKilled | no |
| `imagepull` | ImagePullBackOff / ErrImagePull | no |
| `oom` | OOMKilled last termination | no |
| `readiness` | Running, not Ready (probe) | no |
| `scheduling` | Pending / FailedScheduling | no |
| `pod-lifecycle` | Replacement / self-heal after a kill | no |
| `generic-triage` | Fallback when nothing else matches | no |
| `write-remediation` | Explicit write workflow | disabled by default; still needs the human gate |

## Commands

```powershell
cd components
python -m agent_cli.skills list
python -m agent_cli.skills validate
python -m agent_cli.skills show crashloop
python -m agent_cli.skills preview --query "Is anything unhealthy in citrus?"
python -m agent_cli --skill-routing auto "Is anything unhealthy in the citrus namespace right now?"
python -m agent_cli --skill crashloop "Why is citrus-crashloop unhealthy?"
```

Webhook / eval use the same router when `CITRUS_SKILL_ROUTING=auto` or
`--skill-routing auto`. Alert labels and scenario ids are not a verified root
cause and cannot authorize writes.

## Docs

| File | What |
|------|------|
| [AUTHORING.md](AUTHORING.md) | Package layout, routing conditions, versions |
| [WORKFLOWS.md](WORKFLOWS.md) | Compose / replace stages, protected processors |
| [benchmark/README.md](benchmark/README.md) | A/B/C replay harness |
| [benchmark/RESULTS.md](benchmark/RESULTS.md) | Live round-1 (180 diagnoses). Default stays legacy |
| [../DIAGNOSIS.md](../DIAGNOSIS.md) | Claims, citations, independent review |
